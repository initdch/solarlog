"""Heating optimizer — facade delegating to analytics.simulation package.

Maintains backward-compatible function signatures used by ui/tab_heating.py.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import streamlit as st

from data.db import query_hourly_power, query_hourly_tank_temp
from analytics.weather import fetch_weather
from analytics.simulation.collector import CollectorModel, calibrate_collector
from analytics.simulation.tank import TankModel, simulate_tank, recommend_setpoint
from analytics.simulation.consumption import (
    PRESETS,
    SHOWER_KWH,
    BATH_KWH,
    BASELINE_KWH_PER_DAY,
    build_consumption_profile,
    estimate_consumption_from_t2,
)

__all__ = [
    "PRESETS", "SHOWER_KWH", "BATH_KWH", "BASELINE_KWH_PER_DAY",
    "build_consumption_profile", "estimate_consumption_from_t2",
    "build_hourly_yield_model", "forecast_hourly_yield",
    "actual_hourly_data", "simulate_tank_hourly", "recommend_setpoint_sweep",
    "calibrate_collector", "CollectorModel", "TankModel",
    "simulate_tank", "recommend_setpoint",
]


@st.cache_data(ttl=3600)
def build_hourly_yield_model(
    data_dir: str, lat: float, lon: float, tz: str, lookback_days: int = 180
) -> dict:
    """Fit hourly power_kw = slope * shortwave_radiation + intercept.

    Returns dict with: slope, intercept, r_squared, n_points, scatter_df.
    """
    end_date = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=lookback_days)
    start_str, end_str = start_date.isoformat(), end_date.isoformat()

    empty = {"slope": None, "intercept": None, "r_squared": None, "n_points": 0, "scatter_df": pd.DataFrame()}

    power = query_hourly_power(data_dir, start_str, end_str)
    if power.empty:
        return empty

    power["hour"] = pd.to_datetime(power["hour"])

    hourly_weather, _ = fetch_weather(lat, lon, start_str, end_str, tz)
    if hourly_weather.empty or "shortwave_radiation" not in hourly_weather.columns:
        return empty

    weather = hourly_weather[["shortwave_radiation"]].copy()
    if weather.index.tz is not None:
        weather.index = weather.index.tz_localize(None)
    weather = weather.reset_index().rename(columns={"time": "hour", "index": "hour"})
    weather.columns = ["hour", "shortwave_radiation"]
    weather["hour"] = pd.to_datetime(weather["hour"])

    merged = pd.merge(power[["hour", "avg_power_kw"]], weather, on="hour", how="inner")
    merged = merged.dropna()
    merged = merged[merged["shortwave_radiation"] > 10]

    if len(merged) < 20:
        return {**empty, "n_points": len(merged), "scatter_df": merged}

    m, b = np.polyfit(merged["shortwave_radiation"], merged["avg_power_kw"], 1)
    y_pred = m * merged["shortwave_radiation"] + b
    ss_res = ((merged["avg_power_kw"] - y_pred) ** 2).sum()
    ss_tot = ((merged["avg_power_kw"] - merged["avg_power_kw"].mean()) ** 2).sum()
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

    return {
        "slope": float(m),
        "intercept": float(b),
        "r_squared": float(r2),
        "n_points": len(merged),
        "scatter_df": merged,
    }


def forecast_hourly_yield(
    model: dict, lat: float, lon: float, tz: str, days: int = 3
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Predict hourly solar power for the next N days.

    Returns (forecast_df, weather_df) where weather_df has raw weather columns
    for use with the collector model.
    """
    if model["slope"] is None:
        return pd.DataFrame(), pd.DataFrame()

    start = date.today()
    end = start + timedelta(days=days - 1)

    hourly, _ = fetch_weather(lat, lon, start.isoformat(), end.isoformat(), tz)
    if hourly.empty or "shortwave_radiation" not in hourly.columns:
        return pd.DataFrame(), pd.DataFrame()

    df = hourly[["shortwave_radiation"]].copy()
    if "cloud_cover" in hourly.columns:
        df["cloud_cover"] = hourly["cloud_cover"]
    if "temperature_2m" in hourly.columns:
        df["temperature_2m"] = hourly["temperature_2m"]

    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)

    df["predicted_kw"] = np.maximum(0.0, model["slope"] * df["shortwave_radiation"] + model["intercept"])
    df.loc[df["shortwave_radiation"] < 10, "predicted_kw"] = 0.0

    df = df.reset_index().rename(columns={"time": "datetime", "index": "datetime"})
    df.columns = [c if c != df.columns[0] else "datetime" for c in df.columns]

    # Weather df for collector model
    weather_df = df[["datetime"]].copy()
    if "shortwave_radiation" in df.columns:
        weather_df["shortwave_radiation"] = df["shortwave_radiation"]
    if "temperature_2m" in df.columns:
        weather_df["temperature_2m"] = df["temperature_2m"]

    return df, weather_df


def actual_hourly_data(
    data_dir: str, target_date: date, days: int = 1
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load actual solar power and tank temperature for a past date range.

    Returns (power_df, temp_df) both with 'datetime' column.
    """
    start = target_date.isoformat()
    end = (target_date + timedelta(days=days - 1)).isoformat()

    power = query_hourly_power(data_dir, start, end)
    temp = query_hourly_tank_temp(data_dir, start, end)

    power_df = pd.DataFrame()
    if not power.empty:
        power_df = power.rename(columns={"hour": "datetime", "avg_power_kw": "predicted_kw"})
        power_df["datetime"] = pd.to_datetime(power_df["datetime"])
        power_df["shortwave_radiation"] = 0.0

    temp_df = pd.DataFrame()
    if not temp.empty:
        temp_df = temp.rename(columns={"hour": "datetime", "avg_tank_temp": "actual_T"})
        temp_df["datetime"] = pd.to_datetime(temp_df["datetime"])

    return power_df, temp_df


# ── Legacy wrappers (backward-compatible signatures) ────────────────────────

def simulate_tank_hourly(
    forecast_df: pd.DataFrame,
    consumption_profile_24h: list[float],
    tank_cfg,
    current_temp: float,
    heater_setpoint: float,
    per_hour_consumption: list[float] | None = None,
    collector_model: CollectorModel | None = None,
    weather_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Legacy wrapper — delegates to TankModel-based simulate_tank."""
    tank = TankModel.from_config(tank_cfg)
    sim = simulate_tank(
        forecast_df, consumption_profile_24h, tank,
        current_temp, heater_setpoint,
        per_hour_consumption=per_hour_consumption,
        collector_model=collector_model,
        weather_df=weather_df,
    )
    if sim.empty:
        return sim
    # Add legacy column names for backward compatibility with UI
    sim["T_top"] = sim["T_1"]
    sim["T_bottom"] = sim[f"T_{tank.n_nodes}"]
    return sim


def recommend_setpoint_sweep(
    forecast_df: pd.DataFrame,
    consumption_profile_24h: list[float],
    tank_cfg,
    current_temp: float,
    sp_min: int = 30,
    sp_max: int = 75,
    collector_model: CollectorModel | None = None,
    weather_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Legacy wrapper — delegates to TankModel-based recommend_setpoint."""
    tank = TankModel.from_config(tank_cfg)
    return recommend_setpoint(
        forecast_df, consumption_profile_24h, tank,
        current_temp, sp_min, sp_max,
        collector_model=collector_model,
        weather_df=weather_df,
    )
