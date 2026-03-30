"""Tank playground sub-tab — interactive 4-node stratification model."""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from analytics.simulation.tank import TankModel, simulate_tank


_NODE_COLORS = ["#e74c3c", "#e67e22", "#3498db", "#2980b9"]
_NODE_LABELS = [
    "Top (hot water)",
    "Upper (heater)",
    "Mid-low (solar coil top)",
    "Bottom (solar coil, T2)",
]


def render_tank_playground(state: dict, cfg) -> None:
    st.subheader("Tank Stratification Model")
    st.caption(
        "Weishaupt WASol 510-2 (444L) — 4-node TRNSYS-style model. "
        "Node boundaries aligned with physical port positions."
    )

    tank = TankModel.from_config(cfg.tank)

    # ── Node info ────────────────────────────────────────────────────────────
    with st.expander("Tank geometry"):
        node_info = []
        for i in range(tank.n_nodes):
            lo, hi = tank._height_ranges[i]
            label = _NODE_LABELS[i] if i < len(_NODE_LABELS) else ""
            node_info.append({
                "Node": f"Node {i+1} — {label}",
                "Height": f"{lo}–{hi} mm",
                "Volume": f"{tank.node_volumes[i]:.0f} L",
                "C (kWh/K)": f"{tank.node_C[i]:.3f}",
                "UA (kW/K)": f"{tank.node_UA[i]:.5f}",
            })
        st.dataframe(pd.DataFrame(node_info), hide_index=True, width="stretch")

    # ── Input controls ───────────────────────────────────────────────────────
    st.markdown("#### Initial conditions")
    tcols = st.columns(tank.n_nodes)
    init_temps = []
    labels_short = ["Top", "Upper", "Mid-low", "Bottom"]
    for i in range(tank.n_nodes):
        label = labels_short[i] if i < len(labels_short) else f"Node {i+1}"
        t = tcols[i].number_input(
            f"{label} (°C)", min_value=5.0, max_value=95.0,
            value=50.0, step=1.0, key=f"tank_init_T{i+1}",
        )
        init_temps.append(t)

    st.markdown("#### Inputs per hour")
    ic1, ic2, ic3, ic4 = st.columns(4)
    solar_kw = ic1.slider("Solar (kW)", 0.0, 5.0, 0.0, 0.1, key="tank_solar")
    heater_on = ic2.checkbox("Heater on", value=False, key="tank_heater_on")
    heater_sp = ic2.slider("Setpoint (°C)", 30, 80, 55, 1, key="tank_heater_sp")
    consumption_kwh = ic3.slider("Consumption (kWh)", 0.0, 5.0, 0.0, 0.1, key="tank_consumption")
    mains = ic4.number_input("Mains temp (°C)", 5.0, 25.0, tank.mains_temp, 1.0, key="tank_mains")

    # Override tank params for playground
    tank.mains_temp = mains
    if not heater_on:
        tank.heater_power_kw = 0.0  # disable heater

    # ── Run 24h simulation ───────────────────────────────────────────────────
    st.markdown("---")

    base_dt = datetime(2025, 1, 1, 0, 0)
    hours = [base_dt + timedelta(hours=h) for h in range(24)]
    forecast_df = pd.DataFrame({
        "datetime": hours,
        "predicted_kw": [solar_kw] * 24,
    })
    profile_24 = [consumption_kwh] * 24

    if heater_on:
        tank.heater_start_hour = 0
        tank.heater_end_hour = 24

    sim = simulate_tank(forecast_df, profile_24, tank, init_temps, float(heater_sp))

    if sim.empty:
        st.warning("Simulation produced no results.")
        return

    # ── Temperature chart ────────────────────────────────────────────────────
    st.markdown("#### 24h temperature evolution")
    fig = go.Figure()
    for i in range(tank.n_nodes):
        col = f"T_{i+1}"
        if col in sim.columns:
            label = _NODE_LABELS[i] if i < len(_NODE_LABELS) else f"Node {i+1}"
            fig.add_trace(go.Scatter(
                x=list(range(24)), y=sim[col],
                mode="lines+markers",
                name=label,
                line=dict(color=_NODE_COLORS[i % len(_NODE_COLORS)], width=2.5),
                marker=dict(size=5),
            ))

    fig.update_layout(
        height=400,
        xaxis_title="Hour",
        yaxis_title="Temperature (°C)",
        margin=dict(l=0, r=0, t=20, b=60),
        legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="left", x=0),
        hovermode="x unified",
    )
    st.plotly_chart(fig, width="stretch")

    # ── Energy balance ───────────────────────────────────────────────────────
    st.markdown("#### Energy balance (24h totals)")
    ec1, ec2, ec3 = st.columns(3)
    ec1.metric("Solar input", f"{sim['solar_kw'].sum():.1f} kWh")
    ec2.metric("Heater input", f"{sim['heater_kw'].sum():.1f} kWh")
    ec3.metric("Consumption", f"{sim['consumption_kw'].sum():.1f} kWh")

    # ── Final temperatures ───────────────────────────────────────────────────
    st.markdown("#### Final node temperatures")
    fcols = st.columns(tank.n_nodes)
    for i in range(tank.n_nodes):
        col = f"T_{i+1}"
        if col in sim.columns:
            label = labels_short[i] if i < len(labels_short) else f"Node {i+1}"
            final_t = sim[col].iloc[-1]
            delta = final_t - init_temps[i]
            fcols[i].metric(label, f"{final_t:.1f} °C", f"{delta:+.1f}")
