"""Solar collector model — EN 12975 lumped-parameter calibration and prediction.

Physics: Q_to_tank = max(0, c1 * G - c2 * (T_in - T_amb))

c1 = lumped optical gain [kW per W/m2]  (absorbs collector area, eta_0, HX penalty)
c2 = lumped thermal loss [kW/K]         (absorbs collector area, a1, HX penalty)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd
import streamlit as st

from data.db import query_hourly_collector_data
from analytics.weather import fetch_weather


@dataclass
class CollectorModel:
    """Calibrated collector model parameters."""

    c1: float | None = None       # lumped optical gain [kW per W/m2]
    c2: float | None = None       # lumped thermal loss [kW/K]
    r_squared: float = 0.0
    n_points: int = 0
    scatter_df: pd.DataFrame | None = None
    fallback: bool = False        # True if using single-variable regression

    @property
    def is_valid(self) -> bool:
        return self.c1 is not None

    def predict(self, G: float, T_in: float, T_amb: float) -> float:
        """Predict solar power delivered to tank [kW]."""
        if not self.is_valid:
            return 0.0
        return max(0.0, self.c1 * G - self.c2 * (T_in - T_amb))

    def predict_series(self, G: pd.Series, T_in: float, T_amb: pd.Series) -> pd.Series:
        """Vectorized prediction for a DataFrame of weather data."""
        if not self.is_valid:
            return pd.Series(0.0, index=G.index)
        result = self.c1 * G - self.c2 * (T_in - T_amb)
        return result.clip(lower=0.0)


@st.cache_data(ttl=3600)
def calibrate_collector(
    data_dir: str,
    lat: float,
    lon: float,
    tz: str,
    lookback_days: int = 180,
    c1_override: float = 0.0,
    c2_override: float = 0.0,
) -> CollectorModel:
    """Calibrate collector from historical P[kW] vs (G, T_in - T_amb).

    If c1_override/c2_override are non-zero, uses those instead of fitting.
    Falls back to single-variable regression if T5 data is sparse.
    """
    if c1_override > 0 and c2_override > 0:
        return CollectorModel(c1=c1_override, c2=c2_override)

    end_date = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=lookback_days)
    start_str, end_str = start_date.isoformat(), end_date.isoformat()

    empty = CollectorModel()

    # Get collector data (pump running, P[kW] available)
    collector = query_hourly_collector_data(data_dir, start_str, end_str)
    if collector.empty:
        return empty

    collector["hour"] = pd.to_datetime(collector["hour"])

    # Get weather data
    hourly_weather, _ = fetch_weather(lat, lon, start_str, end_str, tz)
    if hourly_weather.empty or "shortwave_radiation" not in hourly_weather.columns:
        return empty

    weather = hourly_weather[["shortwave_radiation"]].copy()
    if "temperature_2m" in hourly_weather.columns:
        weather["temperature_2m"] = hourly_weather["temperature_2m"]
    if weather.index.tz is not None:
        weather.index = weather.index.tz_localize(None)
    weather = weather.reset_index()
    weather.columns = ["hour"] + list(weather.columns[1:])
    weather["hour"] = pd.to_datetime(weather["hour"])

    merged = pd.merge(collector, weather, on="hour", how="inner").dropna(
        subset=["avg_power_kw", "shortwave_radiation"]
    )
    merged = merged[merged["shortwave_radiation"] > 50]  # daytime, pump running

    if len(merged) < 20:
        return CollectorModel(n_points=len(merged), scatter_df=merged)

    # Try two-variable fit: P = c1*G - c2*(T_in - T_amb)
    has_temp = (
        "avg_T5_return" in merged.columns
        and merged["avg_T5_return"].notna().sum() > len(merged) * 0.5
        and "temperature_2m" in merged.columns
    )

    if has_temp:
        valid = merged.dropna(subset=["avg_T5_return", "temperature_2m"])
        if len(valid) >= 50:
            G = valid["shortwave_radiation"].values
            dT = valid["avg_T5_return"].values - valid["temperature_2m"].values
            P = valid["avg_power_kw"].values

            # P = c1*G - c2*dT → [G, -dT] @ [c1, c2] = P
            A = np.column_stack([G, -dT])
            result, residuals, _, _ = np.linalg.lstsq(A, P, rcond=None)
            c1, c2 = float(result[0]), float(result[1])

            if c1 > 0 and c2 > 0:
                P_pred = c1 * G - c2 * dT
                ss_res = ((P - P_pred) ** 2).sum()
                ss_tot = ((P - P.mean()) ** 2).sum()
                r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

                valid = valid.copy()
                valid["predicted_kw"] = P_pred
                return CollectorModel(
                    c1=c1, c2=c2, r_squared=r2,
                    n_points=len(valid), scatter_df=valid, fallback=False,
                )

    # Fallback: single-variable regression P = slope*G + intercept
    G = merged["shortwave_radiation"].values
    P = merged["avg_power_kw"].values
    m, b = np.polyfit(G, P, 1)
    P_pred = m * G + b
    ss_res = ((P - P_pred) ** 2).sum()
    ss_tot = ((P - P.mean()) ** 2).sum()
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

    merged = merged.copy()
    merged["predicted_kw"] = P_pred
    return CollectorModel(
        c1=float(m), c2=0.0, r_squared=r2,
        n_points=len(merged), scatter_df=merged, fallback=True,
    )
