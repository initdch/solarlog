import pandas as pd
from datetime import date
from data.db import query_flow_rate_trend, query_heat_exchanger_trend, query_collector_yoy


def flow_rate_trend(data_dir: str, start: str, end: str) -> pd.DataFrame:
    """
    Monthly 95th-percentile flow rate when pump is at full speed.
    A declining trend indicates pump/piping blockage.
    """
    df = query_flow_rate_trend(data_dir, start, end)
    if not df.empty:
        df["month"] = pd.to_datetime(df["month"])
    return df


def heat_exchanger_trend(data_dir: str, start: str, end: str) -> pd.DataFrame:
    """
    Monthly median thermal resistance (ΔT / power).
    An increasing trend indicates heat exchanger scaling.
    """
    df = query_heat_exchanger_trend(data_dir, start, end)
    if not df.empty:
        df["month"] = pd.to_datetime(df["month"])
    return df


def collector_yoy(
    data_dir: str,
    start: str,
    end: str,
    clear_day_dates: list[date],
) -> pd.DataFrame:
    """
    Peak midday power on clear days, grouped by year/month.
    A declining trend per year indicates collector soiling or degradation.
    """
    if not clear_day_dates:
        return pd.DataFrame()
    date_strs = tuple(d.strftime("%Y-%m-%d") for d in clear_day_dates)
    df = query_collector_yoy(data_dir, start, end, date_strs)
    return df
