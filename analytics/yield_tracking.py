import pandas as pd
from data.db import query_daily_yield, get_connection, build_view


def get_daily_yield(data_dir: str, start: str, end: str) -> pd.DataFrame:
    """
    Daily energy yield. Flags partial days (record_count < 60 minutes per hour * some threshold).
    A full day has 1440 records. We flag days with < 1380 records as partial.
    """
    df = query_daily_yield(data_dir, start, end)
    if df.empty:
        return df
    df["partial_day"] = df["record_count"] < 1380
    df["date"] = pd.to_datetime(df["date"])
    return df


def get_monthly_yield(data_dir: str, start: str, end: str) -> pd.DataFrame:
    """
    Monthly energy yield, summing only complete days.
    """
    daily = get_daily_yield(data_dir, start, end)
    if daily.empty:
        return daily
    # Include all days but mark months with many partial days
    daily["month"] = daily["date"].dt.to_period("M").dt.to_timestamp()
    monthly = (
        daily[~daily["partial_day"]]
        .groupby("month")
        .agg(yield_kwh=("yield_kwh", "sum"), day_count=("yield_kwh", "count"))
        .reset_index()
    )
    return monthly


def get_yearly_yield(data_dir: str, start: str, end: str) -> pd.DataFrame:
    """
    Yearly energy yield, summing only complete days.
    """
    daily = get_daily_yield(data_dir, start, end)
    if daily.empty:
        return daily
    daily["year"] = daily["date"].dt.to_period("Y").dt.to_timestamp()
    yearly = (
        daily[~daily["partial_day"]]
        .groupby("year")
        .agg(yield_kwh=("yield_kwh", "sum"), day_count=("yield_kwh", "count"))
        .reset_index()
    )
    return yearly


def get_lifetime_total(data_dir: str) -> float | None:
    """Return the maximum Qsum value across all data (lifetime kWh)."""
    con = get_connection()
    build_view(con, data_dir)
    result = con.execute('SELECT MAX("Qsum[kWh]") AS qsum_max FROM solar_raw').df()
    if result.empty or result["qsum_max"].isna().all():
        return None
    return float(result["qsum_max"].iloc[0])
