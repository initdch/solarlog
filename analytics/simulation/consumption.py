"""Hot water consumption profiles and T2-based estimation."""
from __future__ import annotations

import pandas as pd

# ── Energy constants ────────────────────────────────────────────────────────
SHOWER_KWH = 1.5
BATH_KWH = 4.0
BASELINE_KWH_PER_DAY = 0.5

PRESETS = {
    "Away":   {"showers_morning": 0, "showers_evening": 0, "baths_evening": 0},
    "Light":  {"showers_morning": 1, "showers_evening": 0, "baths_evening": 0},
    "Normal": {"showers_morning": 2, "showers_evening": 1, "baths_evening": 0},
    "Heavy":  {"showers_morning": 3, "showers_evening": 0, "baths_evening": 1},
}


def build_consumption_profile(
    showers_morning: int = 2,
    showers_evening: int = 1,
    baths_evening: int = 0,
    shower_hour: int = 7,
    bath_hour: int = 20,
) -> list[float]:
    """Build a 24-element list of kWh per hour based on shower/bath counts.

    Showers/baths are spread +1h around the configured hour if multiple.
    """
    profile = [BASELINE_KWH_PER_DAY / 24.0] * 24

    for i in range(showers_morning):
        h = (shower_hour + i) % 24
        profile[h] += SHOWER_KWH

    for i in range(showers_evening):
        h = (bath_hour - 1 + i) % 24
        profile[h] += SHOWER_KWH

    for i in range(baths_evening):
        h = (bath_hour + i) % 24
        profile[h] += BATH_KWH

    return profile


def estimate_consumption_from_t2(
    power_df: pd.DataFrame,
    temp_df: pd.DataFrame,
    tank_cfg,
) -> dict[str, list[float]]:
    """Estimate hourly consumption from actual T2 data, per day.

    Consumption is detected when T2 drops during hours with no solar gain.
    During solar hours, the relationship between solar input and T2 rise is
    too complex to reliably separate consumption from reduced solar efficiency,
    so we only estimate consumption from non-solar hours.

    Returns a dict mapping date string -> 24-element list of kWh per hour.
    Also includes an "_average" key for multi-day summary.
    """
    # Use volume_liters and compute bottom fraction from node boundaries if available
    if hasattr(tank_cfg, "node_boundaries") and tank_cfg.node_boundaries:
        bounds = tank_cfg.node_boundaries
        total_height = bounds[-1] - bounds[0]
        bottom_height = bounds[1] - bounds[0]  # node 4 (bottom)
        vol_bottom = tank_cfg.volume_liters * (bottom_height / total_height)
    elif hasattr(tank_cfg, "heater_fraction"):
        vol_bottom = tank_cfg.volume_liters * (1 - tank_cfg.heater_fraction)
    else:
        vol_bottom = tank_cfg.volume_liters * 0.25

    merged = pd.merge(
        temp_df[["datetime", "actual_T"]],
        power_df[["datetime", "predicted_kw"]],
        on="datetime", how="inner",
    ).sort_values("datetime").reset_index(drop=True)

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
            standby_k_per_h = 0.0085 * max(T_prev - T_ambient, 0.0)

            drop = T_prev - T_curr - standby_k_per_h
            if drop > 0.5:
                mix_frac = drop / max(T_prev - tank_cfg.mains_temp, 1.0)
                liters_drawn = mix_frac * vol_bottom
                consumption_kwh = liters_drawn * 4.186 / 3600 * max(T_prev - tank_cfg.mains_temp, 1.0)
                profile[hour_of_day] += consumption_kwh

        per_day[str(day)] = profile

    if per_day:
        avg = [0.0] * 24
        for p in per_day.values():
            for h in range(24):
                avg[h] += p[h]
        for h in range(24):
            avg[h] /= len(per_day)
        per_day["_average"] = avg

    return per_day
