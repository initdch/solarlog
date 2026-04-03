"""Full Simulation sub-tab — 4-node tank, collector model, setpoint recommendation."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from analytics.heating import (
    PRESETS,
    build_hourly_yield_model,
    forecast_hourly_yield,
    actual_hourly_data,
    estimate_consumption_from_t2,
    build_consumption_profile,
    simulate_tank_hourly,
    recommend_setpoint_sweep,
    calibrate_collector,
)
from analytics.simulation.tank import TankModel
from data.db import query_latest_tank_temp


# Node colors for temperature lines
_NODE_COLORS = ["#e74c3c", "#e67e22", "#3498db", "#2980b9"]
_NODE_NAMES = ["Top (hot water)", "Upper (heater)", "Mid-low (solar coil)", "Bottom (T2 sensor)"]


def render_simulation(state: dict, cfg) -> None:
    st.caption(
        "Predict solar yield from the weather forecast and simulate hourly tank temperature "
        "to find the optimal overnight heating setpoint. "
        "Select a past date to compare simulation against actual T2 data."
    )

    tank_cfg = cfg.tank
    data_dir = state["data_dir"]
    lat, lon, tz = state["latitude"], state["longitude"], state["timezone"]

    # ── Build yield model + collector model ──────────────────────────────────
    with st.spinner("Building models..."):
        model = build_hourly_yield_model(data_dir, lat, lon, tz)
        collector = calibrate_collector(
            data_dir, lat, lon, tz,
            c1_override=cfg.collector.eta0_area,
            c2_override=cfg.collector.a1_area,
        )

    if model["slope"] is None:
        st.warning(
            f"Not enough data to build the yield model ({model['n_points']} data points, need >= 20). "
            "Need historical solar data and weather data for the same period."
        )
        return

    # ── Metrics row ──────────────────────────────────────────────────────────
    last_temp, last_ts = query_latest_tank_temp(data_dir)

    col_model, col_n, col_tank = st.columns(3)
    if collector.is_valid and not collector.fallback:
        col_model.metric(
            "Collector model",
            f"HW (R²={collector.r_squared:.2f})",
            help=f"c1={collector.c1:.5f}, c2={collector.c2:.4f}, {collector.n_points} pts",
        )
    else:
        col_model.metric(
            "Collector model",
            f"Linear (R²={model['r_squared']:.2f})",
            help=f"Fallback: slope={model['slope']:.5f}, {model['n_points']} pts",
        )
    col_n.metric("Training hours", f"{max(model['n_points'], collector.n_points):,}")
    if last_temp is not None:
        col_tank.metric("Last T2 (bottom)", f"{last_temp:.1f} °C", help=f"At {last_ts}")
    else:
        col_tank.metric("Last T2 (bottom)", "---")

    # ── Date selection ───────────────────────────────────────────────────────
    st.markdown("---")
    dcol1, dcol2 = st.columns([2, 1])
    today = date.today()
    tomorrow = today + timedelta(days=1)
    sim_date = dcol1.date_input(
        "Simulation start date",
        value=tomorrow,
        min_value=date(2024, 1, 1),
        max_value=today + timedelta(days=14),
        key="heating_sim_date",
    )
    is_past = sim_date < today
    if is_past:
        dcol2.info("Past date -- using actual solar data")
    elif sim_date == today:
        dcol2.info("Today -- using forecast")
    else:
        dcol2.info("Future -- using forecast")

    # ── Controls ─────────────────────────────────────────────────────────────
    st.markdown("---")
    ctrl1, ctrl2, ctrl3 = st.columns(3)
    current_temp = ctrl1.number_input(
        "Starting tank temp (°C)",
        min_value=10.0, max_value=95.0,
        value=last_temp if last_temp is not None else tank_cfg.target_temp,
        step=1.0, key="heating_current_temp",
        help="T2 reading (bottom of tank). All nodes start at this temperature.",
    )
    setpoint = ctrl2.slider(
        "Heater setpoint (°C)",
        min_value=30, max_value=80,
        value=int(tank_cfg.target_temp + 10),
        step=1, key="heating_setpoint",
    )
    forecast_days = ctrl3.slider(
        "Days", min_value=1, max_value=7, value=3, key="heating_days",
    )

    # ── Consumption profile ──────────────────────────────────────────────────
    st.subheader("Hot water usage")

    use_estimated = False
    estimated_profiles = None
    if is_past:
        power_df_preview, temp_df_preview = actual_hourly_data(data_dir, sim_date, days=forecast_days)
        if not power_df_preview.empty and not temp_df_preview.empty:
            estimated_profiles = estimate_consumption_from_t2(power_df_preview, temp_df_preview, tank_cfg)
            use_estimated = st.checkbox(
                "Use estimated consumption from T2 data",
                value=True, key="heating_use_estimated",
                help="Estimates actual hot water draws from T2 temperature drops, per day.",
            )

    if use_estimated and estimated_profiles is not None:
        first_day_key = str(sim_date)
        if first_day_key in estimated_profiles:
            profile = estimated_profiles[first_day_key]
        elif "_average" in estimated_profiles:
            profile = estimated_profiles["_average"]
        else:
            profile = [0.0] * 24
        total_consumption = sum(profile)
        hours_with = [(h, kw) for h, kw in enumerate(profile) if kw > 0.1]
        if hours_with:
            detail = ", ".join(f"{h:02d}:00 ({kw:.1f} kWh)" for h, kw in hours_with)
            st.caption(f"Estimated from T2 drops ({sim_date}): **{total_consumption:.1f} kWh** -- {detail}")
        else:
            st.caption(f"Estimated from T2 drops ({sim_date}): **{total_consumption:.1f} kWh** (no significant draws detected)")
    else:
        preset = st.radio(
            "Usage preset", list(PRESETS.keys()), index=2, horizontal=True, key="heating_preset",
        )
        defaults = PRESETS[preset]

        c1, c2, c3 = st.columns(3)
        showers_morning = c1.number_input(
            "Morning showers", min_value=0, max_value=6,
            value=defaults["showers_morning"], key="heating_showers_am",
        )
        showers_evening = c2.number_input(
            "Evening showers", min_value=0, max_value=6,
            value=defaults["showers_evening"], key="heating_showers_pm",
        )
        baths_evening = c3.number_input(
            "Evening baths", min_value=0, max_value=3,
            value=defaults["baths_evening"], key="heating_baths",
        )

        profile = build_consumption_profile(showers_morning, showers_evening, baths_evening)
        total_consumption = sum(profile)
        st.caption(
            f"Estimated daily consumption: **{total_consumption:.1f} kWh** "
            f"({showers_morning + showers_evening} showers x 1.5 kWh, "
            f"{baths_evening} bath x 4 kWh, baseline 0.5 kWh)"
        )

    # ── Load data: actual (past) or forecast (future) ────────────────────────
    actual_temp_df = pd.DataFrame()
    weather_df = pd.DataFrame()

    if is_past:
        if estimated_profiles is not None:
            power_df, actual_temp_df = power_df_preview, temp_df_preview
        else:
            power_df, actual_temp_df = actual_hourly_data(data_dir, sim_date, days=forecast_days)
        if power_df.empty:
            st.warning("No solar data for the selected date.")
            return
        forecast = power_df

        if use_estimated and estimated_profiles is not None:
            extended_profile = _build_extended_profile(forecast, estimated_profiles, sim_date)
        else:
            extended_profile = None
    else:
        forecast, weather_df = forecast_hourly_yield(model, lat, lon, tz, days=forecast_days)
        if forecast.empty:
            st.warning("Could not fetch weather forecast. Try again later.")
            return
        extended_profile = None

    # Use collector model for dynamic solar prediction if available
    use_collector = collector.is_valid and not collector.fallback and not weather_df.empty
    sim = simulate_tank_hourly(
        forecast,
        profile if extended_profile is None else profile,
        tank_cfg, current_temp, float(setpoint),
        per_hour_consumption=extended_profile,
        collector_model=collector if use_collector else None,
        weather_df=weather_df if use_collector else None,
    )

    # ── Recommendation (only for future dates) ───────────────────────────────
    if not is_past:
        rec_df = recommend_setpoint_sweep(
            forecast, profile, tank_cfg, current_temp,
            collector_model=collector if use_collector else None,
            weather_df=weather_df if use_collector else None,
        )
        if not rec_df.empty:
            safe = rec_df[rec_df["min_temp"] >= tank_cfg.target_temp]
            if not safe.empty:
                best_sp = int(safe.iloc[0]["setpoint"])
                best_kwh = safe.iloc[0]["total_heater_kwh"]
                max_kwh = rec_df["total_heater_kwh"].max()
                savings = max_kwh - best_kwh
                st.success(
                    f"**Tonight, set heater to {best_sp} °C** -- "
                    f"uses {best_kwh:.1f} kWh tomorrow "
                    f"(saves {savings:.1f} kWh vs max). "
                    f"Tank stays above {tank_cfg.target_temp:.0f} °C."
                )
            else:
                st.warning(
                    f"No setpoint keeps tank above {tank_cfg.target_temp:.0f} °C through tomorrow. "
                    "Consider reducing usage or increasing heater capacity."
                )

    # ── Hero chart: first day's 24h curve ────────────────────────────────────
    tank_model = TankModel.from_config(tank_cfg)
    n_nodes = tank_model.n_nodes
    day_label = sim_date.strftime("%A %d %b") if is_past else "Tomorrow"
    st.subheader(f"{day_label} -- hourly simulation")
    first_day_sim = sim.head(24)
    first_day_forecast = forecast.head(24)
    first_day_actual_T = pd.DataFrame()
    if not actual_temp_df.empty:
        day_end = pd.Timestamp(sim_date) + pd.Timedelta(days=1)
        mask = (actual_temp_df["datetime"] >= pd.Timestamp(sim_date)) & (actual_temp_df["datetime"] < day_end)
        first_day_actual_T = actual_temp_df[mask]

    if not first_day_sim.empty:
        _render_hourly_chart(first_day_sim, first_day_forecast, tank_cfg, setpoint, first_day_actual_T, is_past, n_nodes)

    # ── Extended forecast ────────────────────────────────────────────────────
    if forecast_days > 1 and len(sim) > 24:
        st.subheader(f"Extended {'history' if is_past else 'forecast'} ({forecast_days} days)")
        _render_extended_chart(sim, tank_cfg, setpoint, actual_temp_df, n_nodes)

    # ── Energy vs setpoint (future only) ─────────────────────────────────────
    if not is_past:
        rec_df = recommend_setpoint_sweep(
            forecast, profile, tank_cfg, current_temp,
            collector_model=collector if use_collector else None,
            weather_df=weather_df if use_collector else None,
        )
        if not rec_df.empty:
            st.subheader("Heater energy vs setpoint (tomorrow)")
            step5 = rec_df[rec_df["setpoint"] % 5 == 0].copy()
            if not step5.empty:
                colors = [
                    "#27ae60" if t >= tank_cfg.target_temp else "#e74c3c"
                    for t in step5["min_temp"]
                ]
                fig_bar = go.Figure()
                fig_bar.add_trace(go.Bar(
                    x=step5["setpoint"].astype(str) + "°C",
                    y=step5["total_heater_kwh"],
                    marker_color=colors,
                    text=[f"{t:.0f}°C min" for t in step5["min_temp"]],
                    textposition="outside",
                ))
                fig_bar.update_layout(
                    height=300,
                    xaxis_title="Heater setpoint",
                    yaxis_title="Heater energy (kWh)",
                    margin=dict(l=0, r=0, t=20, b=60),
                )
                st.plotly_chart(fig_bar, width="stretch")

    # ── Model details ────────────────────────────────────────────────────────
    with st.expander("Model details"):
        scatter = model["scatter_df"]
        if not scatter.empty and "shortwave_radiation" in scatter.columns:
            fig_sc = go.Figure()
            fig_sc.add_trace(go.Scatter(
                x=scatter["shortwave_radiation"], y=scatter["avg_power_kw"],
                mode="markers", marker=dict(size=4, color="#3498db", opacity=0.3),
                name="Historical hours",
            ))
            x_line = [float(scatter["shortwave_radiation"].min()), float(scatter["shortwave_radiation"].max())]
            y_line = [model["slope"] * x + model["intercept"] for x in x_line]
            fig_sc.add_trace(go.Scatter(
                x=x_line, y=y_line, mode="lines",
                line=dict(color="#e74c3c", dash="dash", width=2),
                name=f"Fit (R²={model['r_squared']:.2f})",
            ))
            fig_sc.update_layout(
                height=350,
                xaxis_title="Shortwave radiation (W/m²)",
                yaxis_title="Solar thermal power (kW)",
                margin=dict(l=0, r=0, t=20, b=60),
                legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="left", x=0),
            )
            st.plotly_chart(fig_sc, width="stretch")
            st.caption(
                f"Linear model: power = {model['slope']:.5f} x radiation + {model['intercept']:.3f}  |  "
                f"R² = {model['r_squared']:.3f}  |  {model['n_points']:,} training hours"
            )


def _build_extended_profile(
    forecast: pd.DataFrame, profiles: dict, sim_date: date,
) -> list[float]:
    """Build a per-hour consumption list for multi-day simulation using per-day profiles."""
    result = []
    for _, row in forecast.iterrows():
        dt = pd.Timestamp(row["datetime"])
        day_key = str(dt.date())
        if day_key in profiles:
            result.append(profiles[day_key][dt.hour])
        elif "_average" in profiles:
            result.append(profiles["_average"][dt.hour])
        else:
            result.append(0.0)
    return result


def _render_hourly_chart(
    sim: pd.DataFrame,
    forecast: pd.DataFrame,
    tank_cfg,
    setpoint: int,
    actual_temp: pd.DataFrame,
    is_past: bool,
    n_nodes: int,
) -> None:
    """Render the hero 24h temperature chart with N-node model."""
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    # Temperature lines for each node
    for i in range(n_nodes):
        col = f"T_{i+1}"
        if col not in sim.columns:
            continue
        fig.add_trace(
            go.Scatter(
                x=sim["datetime"], y=sim[col],
                name=_NODE_NAMES[i] if i < len(_NODE_NAMES) else f"Node {i+1}",
                line=dict(color=_NODE_COLORS[i % len(_NODE_COLORS)], width=2.5 if i in (0, n_nodes-1) else 1.5),
                mode="lines+markers" if i in (0, n_nodes-1) else "lines",
                marker=dict(size=4) if i in (0, n_nodes-1) else None,
            ),
            secondary_y=False,
        )

    # Actual T2 overlay
    if not actual_temp.empty:
        fig.add_trace(
            go.Scatter(
                x=actual_temp["datetime"], y=actual_temp["actual_T"],
                name="Actual T2", line=dict(color="#8e44ad", width=2.5, dash="dot"),
                mode="lines+markers", marker=dict(size=4),
            ),
            secondary_y=False,
        )

    # Solar power on secondary axis
    solar_col = "predicted_kw"
    solar_label = "Solar (actual)" if is_past else "Solar (predicted)"
    if solar_col in forecast.columns:
        fig.add_trace(
            go.Scatter(
                x=forecast["datetime"], y=forecast[solar_col],
                name=solar_label, line=dict(color="#27ae60", width=1.5),
                fill="tozeroy", fillcolor="rgba(39, 174, 96, 0.15)",
                mode="lines",
            ),
            secondary_y=True,
        )

    # Consumption bars
    if "consumption_kw" in sim.columns:
        consumption_nonzero = sim[sim["consumption_kw"] > 0.1]
        if not consumption_nonzero.empty:
            fig.add_trace(
                go.Bar(
                    x=consumption_nonzero["datetime"],
                    y=consumption_nonzero["consumption_kw"],
                    name="Hot water use",
                    marker_color="rgba(231, 76, 60, 0.3)",
                    width=3_600_000 * 0.6,
                ),
                secondary_y=True,
            )

    # Heater window shading
    dt0 = pd.Timestamp(sim["datetime"].iloc[0]).normalize()
    heater_start = dt0 + pd.Timedelta(hours=tank_cfg.heater_start_hour)
    heater_end = dt0 + pd.Timedelta(hours=tank_cfg.heater_end_hour)
    fig.add_vrect(
        x0=heater_start, x1=heater_end,
        fillcolor="rgba(231, 76, 60, 0.08)", line_width=0,
        annotation_text="Heater window", annotation_position="top left",
    )

    fig.add_hline(
        y=tank_cfg.target_temp, line_dash="dash", line_color="#e67e22", line_width=1,
        annotation_text=f"Target {tank_cfg.target_temp:.0f}°C",
        secondary_y=False,
    )
    fig.add_hline(
        y=setpoint, line_dash="dash", line_color="#95a5a6", line_width=1,
        annotation_text=f"Setpoint {setpoint}°C",
        secondary_y=False,
    )

    fig.update_layout(
        height=420, hovermode="x unified",
        margin=dict(l=0, r=0, t=50, b=60),
        legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="left", x=0),
        barmode="overlay",
    )
    fig.update_yaxes(title_text="Temperature (°C)", secondary_y=False)
    fig.update_yaxes(title_text="Power (kW)", rangemode="tozero", secondary_y=True)

    st.plotly_chart(fig, width="stretch")


def _render_extended_chart(
    sim: pd.DataFrame, tank_cfg, setpoint: int, actual_temp: pd.DataFrame, n_nodes: int,
) -> None:
    """Continuous hourly temperature chart across all forecast days."""
    fig = go.Figure()

    for i in range(n_nodes):
        col = f"T_{i+1}"
        if col not in sim.columns:
            continue
        fig.add_trace(go.Scatter(
            x=sim["datetime"], y=sim[col],
            name=_NODE_NAMES[i] if i < len(_NODE_NAMES) else f"Node {i+1}",
            line=dict(color=_NODE_COLORS[i % len(_NODE_COLORS)], width=2 if i in (0, n_nodes-1) else 1),
            mode="lines",
        ))

    if not actual_temp.empty:
        fig.add_trace(go.Scatter(
            x=actual_temp["datetime"], y=actual_temp["actual_T"],
            name="Actual T2", line=dict(color="#8e44ad", width=2, dash="dot"),
            mode="lines",
        ))

    fig.add_hline(
        y=tank_cfg.target_temp, line_dash="dash", line_color="#e67e22", line_width=1,
        annotation_text=f"Target {tank_cfg.target_temp:.0f}°C",
    )
    fig.add_hline(
        y=setpoint, line_dash="dash", line_color="#95a5a6", line_width=1,
        annotation_text=f"Setpoint {setpoint}°C",
    )
    fig.update_layout(
        height=300, hovermode="x unified",
        yaxis_title="Temperature (°C)",
        margin=dict(l=0, r=0, t=20, b=60),
        legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="left", x=0),
    )
    st.plotly_chart(fig, width="stretch")
