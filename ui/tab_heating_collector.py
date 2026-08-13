"""Collector playground sub-tab — calibration results, efficiency curve, interactive tuning."""
from __future__ import annotations

import numpy as np
import plotly.graph_objects as go
import streamlit as st

from analytics.heating import calibrate_collector


def render_collector_playground(state: dict, cfg) -> None:
    st.subheader("Solar Collector Model")
    st.caption(
        "EN 12975 lumped-parameter model: Q = max(0, c1 * G - c2 * (T_in - T_amb)). "
        "Calibrated from historical P[kW] data when the pump is running."
    )

    data_dir = state["data_dir"]
    lat, lon, tz = state["latitude"], state["longitude"], state["timezone"]

    with st.spinner("Calibrating collector model..."):
        collector = calibrate_collector(
            data_dir, lat, lon, tz,
            c1_override=cfg.collector.eta0_area,
            c2_override=cfg.collector.a1_area,
        )

    if not collector.is_valid:
        st.warning(f"Not enough data to calibrate collector ({collector.n_points} points, need >= 50).")
        return

    # ── Calibration metrics ──────────────────────────────────────────────────
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("c1 (optical gain)", f"{collector.c1:.5f} kW/(W/m²)")
    m2.metric("c2 (thermal loss)", f"{collector.c2:.4f} kW/K" if collector.c2 > 0 else "N/A (fallback)")
    m3.metric("R²", f"{collector.r_squared:.3f}")
    m4.metric("Training points", f"{collector.n_points:,}")

    if collector.fallback:
        st.info("Using single-variable regression (fallback). Two-variable fit requires T5 and temperature data.")

    # ── Scatter: actual vs predicted ─────────────────────────────────────────
    scatter = collector.scatter_df
    if scatter is not None and not scatter.empty and "avg_power_kw" in scatter.columns:
        st.markdown("#### Actual vs predicted power")
        fig = go.Figure()

        if "predicted_kw" in scatter.columns:
            fig.add_trace(go.Scatter(
                x=scatter["predicted_kw"], y=scatter["avg_power_kw"],
                mode="markers", marker=dict(size=4, color="#3498db", opacity=0.4),
                name="Data points",
            ))
            # Perfect fit line
            pmin = min(scatter["predicted_kw"].min(), scatter["avg_power_kw"].min())
            pmax = max(scatter["predicted_kw"].max(), scatter["avg_power_kw"].max())
            fig.add_trace(go.Scatter(
                x=[pmin, pmax], y=[pmin, pmax],
                mode="lines", line=dict(color="#e74c3c", dash="dash", width=1),
                name="Perfect fit",
            ))
            fig.update_layout(
                height=350,
                xaxis_title="Predicted P [kW]",
                yaxis_title="Actual P [kW]",
                margin=dict(l=0, r=0, t=20, b=60),
                legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="left", x=0),
            )
        else:
            fig.add_trace(go.Scatter(
                x=scatter["shortwave_radiation"], y=scatter["avg_power_kw"],
                mode="markers", marker=dict(size=4, color="#3498db", opacity=0.4),
                name="Data points",
            ))
            fig.update_layout(
                height=350,
                xaxis_title="Shortwave radiation (W/m²)",
                yaxis_title="P [kW]",
                margin=dict(l=0, r=0, t=20, b=60),
            )

        st.plotly_chart(fig, width="stretch")

    # ── Interactive tuning ───────────────────────────────────────────────────
    st.markdown("#### Interactive tuning")

    with st.form("collector_tune"):
        tc1, tc2, tc3 = st.columns(3)
        G_val = tc1.slider("Irradiance G (W/m²)", 0, 1200, 600, 50, key="coll_G")
        T_in_val = tc2.slider("Collector inlet T_in (°C)", 10, 90, 40, 5, key="coll_Tin")
        T_amb_val = tc3.slider("Ambient T_amb (°C)", -10, 40, 20, 1, key="coll_Tamb")

        oc1, oc2 = st.columns(2)
        c1_val = oc1.number_input(
            "c1 override", value=collector.c1, format="%.5f", step=0.00001, key="coll_c1",
        )
        c2_val = oc2.number_input(
            "c2 override", value=collector.c2 if collector.c2 > 0 else 0.01,
            format="%.4f", step=0.001, key="coll_c2",
        )
        st.form_submit_button("Calculate", type="primary")

    Q = max(0.0, c1_val * G_val - c2_val * (T_in_val - T_amb_val))
    eta = Q / (G_val / 1000) if G_val > 0 else 0.0

    rc1, rc2 = st.columns(2)
    rc1.metric("Q_useful", f"{Q:.2f} kW")
    rc2.metric("Efficiency", f"{eta * 100:.1f}%")

    # ── Family of curves ─────────────────────────────────────────────────────
    st.markdown("#### Q vs irradiance at different inlet temperatures")
    G_range = np.linspace(0, 1200, 50)
    fig_fam = go.Figure()
    for T_in in [20, 40, 60, 80]:
        Q_arr = np.maximum(0, c1_val * G_range - c2_val * (T_in - T_amb_val))
        fig_fam.add_trace(go.Scatter(
            x=G_range, y=Q_arr,
            mode="lines",
            name=f"T_in = {T_in}°C",
            line=dict(width=3 if T_in == T_in_val else 1.5),
        ))

    fig_fam.update_layout(
        height=350,
        xaxis_title="Irradiance G (W/m²)",
        yaxis_title="Q_useful (kW)",
        margin=dict(l=0, r=0, t=20, b=60),
        legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="left", x=0),
    )
    st.plotly_chart(fig_fam, width="stretch")
