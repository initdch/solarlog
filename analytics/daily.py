import pandas as pd
from datetime import date


def compute_kpis(df: pd.DataFrame) -> dict:
    """Compute KPI summary metrics for a single day's DataFrame."""
    if df.empty:
        return {}

    kpis = {}

    if "T_collector" in df.columns:
        kpis["peak_collector_temp"] = df["T_collector"].max()

    if "power_kw" in df.columns:
        kpis["peak_power_kw"] = df["power_kw"].max()
        kpis["daily_yield_kwh"] = df["Qday"].max() - df["Qday"].min() if "Qday" in df.columns else None

    if "pump_speed" in df.columns:
        # 1-minute intervals → minutes active / 60 = hours
        kpis["pump_runtime_hours"] = (df["pump_speed"] > 0).sum() / 60.0

    if "flow_rate" in df.columns:
        kpis["max_flow_rate"] = df["flow_rate"].max()

    return kpis


def get_active_periods(df: pd.DataFrame) -> pd.DataFrame:
    """Return only rows where the pump is running."""
    if df.empty or "pump_speed" not in df.columns:
        return df
    return df[df["pump_speed"] > 0]
