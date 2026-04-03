import requests
import pandas as pd
import streamlit as st
from datetime import date, timedelta


ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
RECENT_THRESHOLD_DAYS = 5


def _fetch_archive(lat: float, lon: float, start: date, end: date, tz: str) -> pd.DataFrame:
    params = {
        "latitude": lat, "longitude": lon,
        "hourly": "cloud_cover,temperature_2m,shortwave_radiation",
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "timezone": tz,
    }
    resp = requests.get(ARCHIVE_URL, params=params, timeout=30)
    resp.raise_for_status()
    hourly = resp.json().get("hourly", {})
    df = pd.DataFrame(hourly)
    if df.empty:
        return df
    df["time"] = pd.to_datetime(df["time"])
    return df.set_index("time")


def _fetch_forecast(lat: float, lon: float, start: date, end: date, tz: str) -> pd.DataFrame:
    params = {
        "latitude": lat, "longitude": lon,
        "hourly": "cloud_cover,temperature_2m,shortwave_radiation",
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "timezone": tz,
    }
    resp = requests.get(FORECAST_URL, params=params, timeout=30)
    resp.raise_for_status()
    hourly = resp.json().get("hourly", {})
    df = pd.DataFrame(hourly)
    if df.empty:
        return df
    df["time"] = pd.to_datetime(df["time"])
    return df.set_index("time")


@st.cache_data(ttl=3600)
def fetch_weather(
    lat: float, lon: float, start: str, end: str, tz: str
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Fetch hourly weather data for the full date range, chunked into yearly requests.
    Returns (hourly_df, daily_df).
    daily_df includes mean_daytime_cloud_pct (mean cloud_cover 08:00–18:00).
    """
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)

    # Archive API has a ~5-day lag; split requests at that boundary.
    # Also chunk by year to stay within API limits.
    today = date.today()
    archive_end = today - timedelta(days=RECENT_THRESHOLD_DAYS)

    chunks = []
    chunk_start = start_date
    while chunk_start <= end_date:
        # Year boundary to stay within API limits
        chunk_end = min(date(chunk_start.year, 12, 31), end_date)

        # Split this chunk at the archive/forecast boundary if needed
        if chunk_start <= archive_end < chunk_end:
            boundaries = [(chunk_start, archive_end), (archive_end + timedelta(days=1), chunk_end)]
        else:
            boundaries = [(chunk_start, chunk_end)]

        for seg_start, seg_end in boundaries:
            use_archive = seg_end <= archive_end
            fetch_fn = _fetch_archive if use_archive else _fetch_forecast
            try:
                seg_df = fetch_fn(lat, lon, seg_start, seg_end, tz)
                if not seg_df.empty:
                    chunks.append(seg_df)
            except Exception:
                pass  # Skip failed segments silently

        chunk_start = date(chunk_start.year + 1, 1, 1)

    if not chunks:
        return pd.DataFrame(), pd.DataFrame()

    hourly_df = pd.concat(chunks).sort_index()
    hourly_df = hourly_df[~hourly_df.index.duplicated(keep="first")]

    # Build daily summary: mean daytime cloud cover (08:00–18:00)
    daytime = hourly_df.between_time("08:00", "18:00")
    if "cloud_cover" in daytime.columns:
        daily_cloud = daytime["cloud_cover"].resample("D").mean().rename("mean_daytime_cloud_pct")
    else:
        daily_cloud = pd.Series(dtype=float, name="mean_daytime_cloud_pct")

    daily_df = daily_cloud.reset_index()
    daily_df.columns = ["date", "mean_daytime_cloud_pct"]
    daily_df["date"] = daily_df["date"].dt.date

    return hourly_df, daily_df


@st.cache_data(ttl=3600)
def fetch_irradiance_for_day(lat: float, lon: float, d: str, tz: str) -> pd.Series:
    """Return hourly shortwave_radiation (W/m²) for a single day. Empty Series on error."""
    try:
        day = date.fromisoformat(d)
        today = date.today()
        archive_end = today - timedelta(days=RECENT_THRESHOLD_DAYS)
        fetch_fn = _fetch_archive if day <= archive_end else _fetch_forecast
        hourly_df = fetch_fn(lat, lon, day, day, tz)
        if hourly_df.empty or "shortwave_radiation" not in hourly_df.columns:
            return pd.Series(dtype=float)
        return hourly_df["shortwave_radiation"]
    except Exception:
        return pd.Series(dtype=float)


def classify_clear_days(daily_df: pd.DataFrame, max_cloud_pct: int) -> list[date]:
    """Return list of dates where mean daytime cloud cover <= max_cloud_pct."""
    if daily_df.empty or "mean_daytime_cloud_pct" not in daily_df.columns:
        return []
    mask = daily_df["mean_daytime_cloud_pct"] <= max_cloud_pct
    return list(daily_df.loc[mask, "date"])
