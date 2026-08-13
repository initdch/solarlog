import pandas as pd
import streamlit as st
from pathlib import Path
from datetime import date


COLUMN_RENAME = {
    "DATE & TIME": "timestamp",
    "T1[C]": "T_collector",
    "T2[C]": "T_tank",
    "T3[C]": "T3",
    "T4[C]": "T_flow",
    "T5[C]": "T_return",
    "T E1[C]": "T_E1",
    "T E2[C]": "T_E2",
    "V'[l/min]": "flow_rate",
    "p[bar]": "pressure",
    "P[kW]": "power_kw",
    "Qday[kWh]": "Qday",
    "Qyear[kWh]": "Qyear",
    "Qsum[kWh]": "Qsum",
    "R1[%]": "R1_pct",
    "R2[%]": "R2_pct",
    "R3[%]": "R3_pct",
    "Rs[%]": "Rs_pct",
    "R1 PWM[%]": "pump_speed",
    "R2 PWM[%]": "R2_PWM",
}


def find_csv_for_date(data_dir: str, d: date) -> Path | None:
    root = Path(data_dir)
    stem = d.strftime('%Y%m%d')
    candidates = [
        root / f"{d.year}" / f"{d.month:02d}" / f"{stem}.csv",
        root / f"{d.year}" / f"{d.month:02d}" / f"{stem}.CSV",
        root / f"{stem}.csv",
        root / f"{stem}.CSV",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


@st.cache_data(ttl=300)
def load_day(data_dir: str, d: date) -> pd.DataFrame:
    path = find_csv_for_date(data_dir, d)
    if path is None:
        return pd.DataFrame()

    sep = ";" if path.suffix == ".CSV" else ","
    df = pd.read_csv(path, dtype=str, na_values=["Err"], sep=sep)
    df = df.rename(columns=COLUMN_RENAME)

    if "timestamp" not in df.columns:
        return pd.DataFrame()

    df["timestamp"] = pd.to_datetime(df["timestamp"], format="mixed")

    for col in df.columns:
        if col != "timestamp":
            if hasattr(df[col], "str"):
                df[col] = df[col].str.replace(",", ".", regex=False)
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df.set_index("timestamp")


def count_available_files(data_dir: str, start: date, end: date) -> int:
    """Count available CSV files in the date range (capped at 400 for speed)."""
    count = 0
    current = start
    from datetime import timedelta
    delta = (end - start).days + 1
    step = max(1, delta // 400)  # sample if range is very large
    while current <= end:
        if find_csv_for_date(data_dir, current) is not None:
            count += 1
        current += timedelta(days=step)
    # Scale back up if we sampled
    if step > 1:
        count = int(count * step)
    return count
