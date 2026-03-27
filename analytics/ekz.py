"""EKZ electricity consumption — local storage + API sync.

Local data lives in ekz_cfg.data_dir/consumption.csv.
The API is only called when syncing new days; the app reads from the local file.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

_BASE_URL = (
    "https://my.ekz.ch/api/portal-services/consumption-view/v1/consumption-data"
)
_CSV_COLUMNS = ["date", "consumption_ht", "consumption_nt", "consumption_kwh", "estimated"]


# ── Local storage ─────────────────────────────────────────────────────────────

def _csv_path(data_dir: str) -> Path:
    return Path(data_dir) / "consumption.csv"


def _read_local(data_dir: str) -> pd.DataFrame:
    path = _csv_path(data_dir)
    if not path.exists():
        return pd.DataFrame(columns=_CSV_COLUMNS)
    df = pd.read_csv(path, parse_dates=["date"])
    df["estimated"] = df["estimated"].astype(bool)
    return df.sort_values("date").reset_index(drop=True)


def _write_local(data_dir: str, df: pd.DataFrame) -> None:
    path = _csv_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    df_out = df.sort_values("date").reset_index(drop=True)
    df_out.to_csv(path, index=False, date_format="%Y-%m-%d")


# ── API fetch ─────────────────────────────────────────────────────────────────

def _parse_series(values: list[dict]) -> pd.DataFrame:
    rows = [
        {"date": v["date"], "value": v["value"]}
        for v in values
        if v.get("status") != "MISSING" and v.get("value") is not None
    ]
    if not rows:
        return pd.DataFrame(columns=["date", "value"])
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df


def _fetch_from_api(
    installation_id: str,
    cookie: str,
    csrf_token: str,
    start: str,
    end: str,
) -> pd.DataFrame:
    """Fetch a date range from the EKZ API. Raises requests.HTTPError on failure."""
    params = {
        "installationId": installation_id,
        "from": start,
        "to": end,
        "type": "PK_VERB_TAG_METER",
    }
    headers = {
        "Accept": "application/json",
        "Cookie": cookie,
        "X-CSRF-TOKEN": csrf_token,
        "X-Requested-With": "XMLHttpRequest",
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
        ),
    }
    resp = requests.get(_BASE_URL, params=params, headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    ht_vals = (data.get("seriesHt") or {}).get("values", [])
    nt_vals = (data.get("seriesNt") or {}).get("values", [])

    ht_df = _parse_series(ht_vals).rename(columns={"value": "consumption_ht"})
    nt_df = _parse_series(nt_vals).rename(columns={"value": "consumption_nt"})

    if ht_df.empty and nt_df.empty:
        return pd.DataFrame(columns=_CSV_COLUMNS)

    if ht_df.empty:
        merged = nt_df.copy()
        merged["consumption_ht"] = 0.0
    elif nt_df.empty:
        merged = ht_df.copy()
        merged["consumption_nt"] = 0.0
    else:
        merged = pd.merge(ht_df, nt_df, on="date", how="outer").fillna(0.0)

    merged["consumption_kwh"] = merged["consumption_ht"] + merged["consumption_nt"]

    estimated_dates: set[str] = set()
    for series_key in ("seriesHt", "seriesNt"):
        for v in (data.get(series_key) or {}).get("values", []):
            if v.get("status") == "ESTIMATED":
                estimated_dates.add(v["date"])
    merged["estimated"] = merged["date"].dt.strftime("%Y-%m-%d").isin(estimated_dates)

    return merged.sort_values("date").reset_index(drop=True)


# ── Sync ──────────────────────────────────────────────────────────────────────

def sync_ekz(
    data_dir: str,
    installation_id: str,
    cookie: str,
    csrf_token: str,
    data_start: str,
) -> tuple[int, str]:
    """Fetch any missing days from the API and append to the local CSV.

    Returns (rows_added, message).
    Raises requests.HTTPError or ValueError on failure.
    """
    if not installation_id or not cookie or not csrf_token:
        raise ValueError("EKZ credentials not configured.")

    existing = _read_local(data_dir)
    today = date.today()

    if existing.empty:
        fetch_from = data_start
    else:
        last_date = pd.to_datetime(existing["date"]).max().date()
        fetch_from = (last_date + timedelta(days=1)).isoformat()

    if fetch_from > today.isoformat():
        return 0, "Already up to date."

    new_data = _fetch_from_api(
        installation_id, cookie, csrf_token,
        fetch_from, today.isoformat(),
    )

    if new_data.empty:
        return 0, "No new data returned by API."

    # Re-fetch the last 7 days already on disk to pick up ESTIMATED→VALID updates
    if not existing.empty:
        recheck_from = (pd.to_datetime(existing["date"]).max().date() - timedelta(days=6)).isoformat()
        refresh = _fetch_from_api(
            installation_id, cookie, csrf_token,
            recheck_from, today.isoformat(),
        )
        # Remove old rows in the recheck window, replace with fresh data
        cutoff = pd.to_datetime(recheck_from)
        existing = existing[pd.to_datetime(existing["date"]) < cutoff]
        combined = pd.concat([existing, refresh], ignore_index=True)
    else:
        combined = new_data

    combined = combined.drop_duplicates(subset=["date"], keep="last")
    _write_local(data_dir, combined)

    rows_added = len(new_data[pd.to_datetime(new_data["date"]) >= pd.to_datetime(fetch_from)])
    return rows_added, f"Added {rows_added} new day(s) through {today}."


# ── Public read API ───────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def load_ekz_consumption(data_dir: str, start: str, end: str) -> pd.DataFrame:
    """Load EKZ consumption from local CSV, filtered to [start, end]."""
    df = _read_local(data_dir)
    if df.empty:
        return df
    mask = (df["date"] >= pd.to_datetime(start)) & (df["date"] <= pd.to_datetime(end))
    return df[mask].reset_index(drop=True)


def ekz_data_status(data_dir: str) -> dict:
    """Return info about local EKZ data: row count, date range, staleness."""
    df = _read_local(data_dir)
    if df.empty:
        return {"rows": 0, "first": None, "last": None, "days_stale": None}
    last = pd.to_datetime(df["date"]).max().date()
    days_stale = (date.today() - last).days
    return {
        "rows": len(df),
        "first": pd.to_datetime(df["date"]).min().date(),
        "last": last,
        "days_stale": days_stale,
    }
