"""Heating optimizer — hourly yield model, tank simulation, setpoint recommendation."""
from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st
from datetime import date, timedelta

from data.db import query_hourly_power, query_hourly_tank_temp
from analytics.weather import fetch_weather

# ── Energy constants for consumption profile ─────────────────────────────────
SHOWER_KWH = 1.5
BATH_KWH = 4.0
BASELINE_KWH_PER_DAY = 0.5

PRESETS = {
    "Away":   {"showers_morning": 0, "showers_evening": 0, "baths_evening": 0},
    "Light":  {"showers_morning": 1, "showers_evening": 0, "baths_evening": 0},
    "Normal": {"showers_morning": 2, "showers_evening": 1, "baths_evening": 0},
    "Heavy":  {"showers_morning": 3, "showers_evening": 0, "baths_evening": 1},
}


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

    # Strip timezone from weather index so it can join with tz-naive DuckDB timestamps
    weather = hourly_weather[["shortwave_radiation"]].copy()
    if weather.index.tz is not None:
        weather.index = weather.index.tz_localize(None)
    weather = weather.reset_index().rename(columns={"time": "hour", "index": "hour"})
    weather.columns = ["hour", "shortwave_radiation"]
    weather["hour"] = pd.to_datetime(weather["hour"])

    merged = pd.merge(power[["hour", "avg_power_kw"]], weather, on="hour", how="inner")
    merged = merged.dropna()
    merged = merged[merged["shortwave_radiation"] > 10]  # daytime only

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
) -> pd.DataFrame:
    """Predict hourly solar power for the next N days using weather forecast + regression."""
    if model["slope"] is None:
        return pd.DataFrame()

    start = date.today()
    end = start + timedelta(days=days - 1)

    hourly, _ = fetch_weather(lat, lon, start.isoformat(), end.isoformat(), tz)
    if hourly.empty or "shortwave_radiation" not in hourly.columns:
        return pd.DataFrame()

    df = hourly[["shortwave_radiation"]].copy()
    if "cloud_cover" in hourly.columns:
        df["cloud_cover"] = hourly["cloud_cover"]
    if "temperature_2m" in hourly.columns:
        df["temperature_2m"] = hourly["temperature_2m"]

    # Strip timezone for consistency
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)

    df["predicted_kw"] = np.maximum(0.0, model["slope"] * df["shortwave_radiation"] + model["intercept"])
    # Zero out nighttime predictions (irradiance < 10 W/m²)
    df.loc[df["shortwave_radiation"] < 10, "predicted_kw"] = 0.0

    df = df.reset_index().rename(columns={"time": "datetime", "index": "datetime"})
    df.columns = [c if c != df.columns[0] else "datetime" for c in df.columns]
    return df


def actual_hourly_data(
    data_dir: str, target_date: date, days: int = 1
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load actual solar power and tank temperature for a past date range.

    Returns (power_df, temp_df) both with 'datetime' column.
    power_df has 'predicted_kw' (actual, named for compatibility with simulate).
    temp_df has 'actual_T' for chart overlay.
    """
    start = target_date.isoformat()
    end = (target_date + timedelta(days=days - 1)).isoformat()

    power = query_hourly_power(data_dir, start, end)
    temp = query_hourly_tank_temp(data_dir, start, end)

    power_df = pd.DataFrame()
    if not power.empty:
        power_df = power.rename(columns={"hour": "datetime", "avg_power_kw": "predicted_kw"})
        power_df["datetime"] = pd.to_datetime(power_df["datetime"])
        power_df["shortwave_radiation"] = 0.0  # not available from solar data

    temp_df = pd.DataFrame()
    if not temp.empty:
        temp_df = temp.rename(columns={"hour": "datetime", "avg_tank_temp": "actual_T"})
        temp_df["datetime"] = pd.to_datetime(temp_df["datetime"])

    return power_df, temp_df


def estimate_consumption_from_t2(
    power_df: pd.DataFrame,
    temp_df: pd.DataFrame,
    tank_cfg,
) -> dict[str, list[float]]:
    """Estimate hourly consumption from actual T2 data, per day.

    Consumption is detected when T2 drops during hours with no solar gain.
    During solar hours, the relationship between solar input and T2 rise is
    too complex (efficiency varies with temperature) to reliably separate
    consumption from reduced solar efficiency, so we only estimate
    consumption from non-solar hours (evening/night/morning).

    Returns a dict mapping date string -> 24-element list of kWh per hour.
    Also includes an "_average" key for multi-day summary.
    """
    frac = tank_cfg.heater_fraction
    vol_bottom = tank_cfg.volume_liters * (1 - frac)

    merged = pd.merge(
        temp_df[["datetime", "actual_T"]],
        power_df[["datetime", "predicted_kw"]],
        on="datetime", how="inner",
    ).sort_values("datetime").reset_index(drop=True)

    # Group by date
    merged["_date"] = pd.to_datetime(merged["datetime"]).dt.date
    per_day: dict[str, list[float]] = {}

    for day, group in merged.groupby("_date"):
        profile = [0.0] * 24
        group = group.sort_values("datetime").reset_index(drop=True)

        for i in range(1, len(group)):
            h = group["datetime"].iloc[i]
            hour_of_day = h.hour if hasattr(h, "hour") else pd.Timestamp(h).hour

            T_prev = group["actual_T"].iloc[i - 1]
            T_curr = group["actual_T"].iloc[i]
            solar_kw = group["predicted_kw"].iloc[i]

            if solar_kw > 0.1:
                continue

            T_ambient = 20.0
            standby_k_per_h = 0.025 * max(T_prev - T_ambient, 0.0)

            drop = T_prev - T_curr - standby_k_per_h
            if drop > 0.5:
                mix_frac = drop / max(T_prev - tank_cfg.mains_temp, 1.0)
                liters_drawn = mix_frac * vol_bottom
                consumption_kwh = liters_drawn * 4.186 / 3600 * max(T_prev - tank_cfg.mains_temp, 1.0)
                profile[hour_of_day] += consumption_kwh

        per_day[str(day)] = profile

    # Compute average across days
    if per_day:
        avg = [0.0] * 24
        for profile in per_day.values():
            for h in range(24):
                avg[h] += profile[h]
        for h in range(24):
            avg[h] /= len(per_day)
        per_day["_average"] = avg

    return per_day


def build_consumption_profile(
    showers_morning: int = 2,
    showers_evening: int = 1,
    baths_evening: int = 0,
    shower_hour: int = 7,
    bath_hour: int = 20,
) -> list[float]:
    """Build a 24-element list of kWh per hour based on shower/bath counts.

    Showers/baths are spread ±1h around the configured hour if multiple.
    """
    profile = [BASELINE_KWH_PER_DAY / 24.0] * 24

    # Distribute morning showers around shower_hour
    for i in range(showers_morning):
        h = (shower_hour + i) % 24
        profile[h] += SHOWER_KWH

    # Distribute evening showers around bath_hour - 1
    for i in range(showers_evening):
        h = (bath_hour - 1 + i) % 24
        profile[h] += SHOWER_KWH

    # Distribute baths around bath_hour
    for i in range(baths_evening):
        h = (bath_hour + i) % 24
        profile[h] += BATH_KWH

    return profile


def _heater_active(h: int, start: int, end: int) -> bool:
    """Check if hour h falls within the heater window, handling wrap-around."""
    if start <= end:
        return start <= h < end
    return h >= start or h < end


def simulate_tank_hourly(
    forecast_df: pd.DataFrame,
    consumption_profile_24h: list[float],
    tank_cfg,
    current_temp: float,
    heater_setpoint: float,
    per_hour_consumption: list[float] | None = None,
) -> pd.DataFrame:
    """Simulate tank temperature hour-by-hour with a two-node stratified model.

    Top node (~heater_fraction of volume): heated by electric element, hot water drawn from here.
    Bottom node (~1-heater_fraction): heated by solar, cold mains refills here, T2 sensor location.

    If per_hour_consumption is provided, it overrides consumption_profile_24h with
    a specific kWh value for each row in forecast_df (for per-day profiles on past dates).
    """
    frac = tank_cfg.heater_fraction
    vol_top = tank_cfg.volume_liters * frac
    vol_bottom = tank_cfg.volume_liters * (1 - frac)
    C_top = vol_top * 4.186 / 3600      # kWh per K
    C_bottom = vol_bottom * 4.186 / 3600
    T_ambient = 20.0
    k_standby = 0.025  # K/hour per K of ΔT to ambient (Newton's law of cooling)
    # Stable stratification (hot on top): almost no mixing — only slow conduction.
    # Unstable (hot on bottom): instant buoyancy-driven mixing.
    k_conduction = 0.01  # kWh/K/hour — very slow downward heat transfer

    T_top = current_temp
    T_bottom = current_temp
    rows = []

    for idx, (_, hour_row) in enumerate(forecast_df.iterrows()):
        dt = hour_row["datetime"]
        h = dt.hour if hasattr(dt, "hour") else pd.Timestamp(dt).hour

        # 1. Standby loss — proportional to ΔT to ambient (Newton's cooling)
        T_top -= k_standby * max(T_top - T_ambient, 0.0)
        T_bottom -= k_standby * max(T_bottom - T_ambient, 0.0)

        # 2. Heater — only heats top node
        heater_kw = 0.0
        if _heater_active(h, tank_cfg.heater_start_hour, tank_cfg.heater_end_hour):
            if T_top < heater_setpoint:
                energy_needed = (heater_setpoint - T_top) * C_top
                heater_kwh = min(energy_needed, tank_cfg.heater_power_kw * 1.0)
                T_top += heater_kwh / C_top
                heater_kw = heater_kwh

        # 3. Solar — heats bottom node
        solar_kw = hour_row.get("predicted_kw", 0.0)
        T_bottom += solar_kw / C_bottom

        # 4. Convection
        if T_bottom > T_top:
            # Unstable: bottom hotter than top → instant buoyancy-driven mixing
            T_mixed = (T_top * C_top + T_bottom * C_bottom) / (C_top + C_bottom)
            T_top = T_mixed
            T_bottom = T_mixed
        elif T_top > T_bottom:
            # Stable stratification: very slow conduction, no convective mixing
            delta = T_top - T_bottom
            transfer_kwh = min(k_conduction * delta, delta * C_top * C_bottom / (C_top + C_bottom))
            T_top -= transfer_kwh / C_top
            T_bottom += transfer_kwh / C_bottom

        # 5. Consumption — draw hot water from top; bottom water rises to replace; mains enters bottom
        if per_hour_consumption is not None and idx < len(per_hour_consumption):
            consumption_kw = per_hour_consumption[idx]
        else:
            consumption_kw = consumption_profile_24h[h]
        if consumption_kw > 0 and T_top > tank_cfg.mains_temp:
            # Liters of hot water drawn (at T_top, replaced by T_mains)
            liters_drawn = consumption_kw / (4.186 / 3600 * max(T_top - tank_cfg.mains_temp, 1.0))

            # Top zone: hot water leaves, bottom water rises up to replace it
            mix_up = min(liters_drawn / vol_top, 1.0)
            T_top = T_top * (1 - mix_up) + T_bottom * mix_up

            # Bottom zone: water moved up, cold mains enters to replace
            mix_in = min(liters_drawn / vol_bottom, 1.0)
            T_bottom = T_bottom * (1 - mix_in) + tank_cfg.mains_temp * mix_in

        T_top = max(T_top, 10.0)
        T_bottom = max(T_bottom, 10.0)

        rows.append({
            "datetime": dt,
            "T_top": round(T_top, 1),
            "T_bottom": round(T_bottom, 1),
            "heater_kw": round(heater_kw, 2),
            "solar_kw": round(solar_kw, 2),
            "consumption_kw": round(consumption_kw, 2),
        })

    return pd.DataFrame(rows)


def recommend_setpoint(
    forecast_df: pd.DataFrame,
    consumption_profile_24h: list[float],
    tank_cfg,
    current_temp: float,
    sp_min: int = 30,
    sp_max: int = 75,
) -> pd.DataFrame:
    """Sweep setpoints over tomorrow (first 24h) and compute heater energy + min temp.

    min_temp uses T_top — that's where hot water is drawn from.
    """
    tomorrow = forecast_df.head(24)
    if tomorrow.empty:
        return pd.DataFrame()

    rows = []
    for sp in range(sp_min, sp_max + 1):
        sim = simulate_tank_hourly(tomorrow, consumption_profile_24h, tank_cfg, current_temp, float(sp))
        if sim.empty:
            continue
        rows.append({
            "setpoint": sp,
            "total_heater_kwh": round(sim["heater_kw"].sum(), 1),
            "min_temp": round(sim["T_top"].min(), 1),
        })
    return pd.DataFrame(rows)
