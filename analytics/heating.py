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
) -> list[float]:
    """Estimate hourly consumption from actual T2 data for a past date.

    Consumption is detected when T2 drops during hours with no solar gain.
    During solar hours, the relationship between solar input and T2 rise is
    too complex (efficiency varies with temperature) to reliably separate
    consumption from reduced solar efficiency, so we only estimate
    consumption from non-solar hours (evening/night/morning).

    Returns a 24-element list of estimated kWh per hour.
    """
    frac = tank_cfg.heater_fraction
    vol_bottom = tank_cfg.volume_liters * (1 - frac)
    C_bottom = vol_bottom * 4.186 / 3600

    merged = pd.merge(
        temp_df[["datetime", "actual_T"]],
        power_df[["datetime", "predicted_kw"]],
        on="datetime", how="inner",
    ).sort_values("datetime").reset_index(drop=True)

    profile = [0.0] * 24
    counts = [0] * 24

    for i in range(1, len(merged)):
        h = merged["datetime"].iloc[i]
        hour_of_day = h.hour if hasattr(h, "hour") else pd.Timestamp(h).hour

        T_prev = merged["actual_T"].iloc[i - 1]
        T_curr = merged["actual_T"].iloc[i]
        solar_kw = merged["predicted_kw"].iloc[i]

        # Only estimate consumption when solar is negligible — during solar hours
        # the T2 dynamics are dominated by collector efficiency which we can't model here
        if solar_kw > 0.1:
            counts[hour_of_day] += 1
            continue

        # T2 drop beyond normal standby loss (~0.5 K/hour empirically) = consumption
        standby_k_per_h = 0.5
        drop = T_prev - T_curr - standby_k_per_h
        if drop > 0.3:  # threshold to filter noise
            # The drop is caused by cold mains mixing into the bottom volume
            # drop = mix_frac * (T_curr_approx - mains_temp)
            mix_frac = drop / max(T_prev - tank_cfg.mains_temp, 1.0)
            liters_drawn = mix_frac * vol_bottom
            # Estimate energy: hot water drawn at T_top (estimated as T_prev + 10K)
            T_top_est = T_prev + 10.0
            consumption_kwh = liters_drawn * 4.186 / 3600 * max(T_top_est - tank_cfg.mains_temp, 1.0)
            profile[hour_of_day] += consumption_kwh

        counts[hour_of_day] += 1

    # Average if multiple days
    for h in range(24):
        if counts[h] > 1:
            profile[h] /= counts[h]

    return profile


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
) -> pd.DataFrame:
    """Simulate tank temperature hour-by-hour with a two-node stratified model.

    Top node (~heater_fraction of volume): heated by electric element, hot water drawn from here.
    Bottom node (~1-heater_fraction): heated by solar, cold mains refills here, T2 sensor location.
    """
    frac = tank_cfg.heater_fraction
    vol_top = tank_cfg.volume_liters * frac
    vol_bottom = tank_cfg.volume_liters * (1 - frac)
    C_top = vol_top * 4.186 / 3600      # kWh per K
    C_bottom = vol_bottom * 4.186 / 3600
    standby_loss_k_per_h = 1.0 / 24     # 1 K/day total, spread per hour
    # Stable stratification (hot on top): almost no mixing — only slow conduction.
    # Unstable (hot on bottom): instant buoyancy-driven mixing.
    k_conduction = 0.01  # kWh/K/hour — very slow downward heat transfer

    T_top = current_temp
    T_bottom = current_temp
    rows = []

    for _, hour_row in forecast_df.iterrows():
        dt = hour_row["datetime"]
        h = dt.hour if hasattr(dt, "hour") else pd.Timestamp(dt).hour

        # 1. Standby loss — both nodes lose to ambient
        T_top -= standby_loss_k_per_h
        T_bottom -= standby_loss_k_per_h

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
