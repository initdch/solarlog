import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
from datetime import date

from data.loader import load_day
from analytics.daily import compute_kpis
from analytics.weather import fetch_irradiance_for_day


def render_tab_daily(state: dict) -> None:
    data_dir = state["data_dir"]
    start_date = state["start_date"]
    end_date = state["end_date"]

    st.header("Daily View")

    selected_date = st.date_input(
        "Select date",
        value=end_date,
        min_value=start_date,
        max_value=end_date,
        key="daily_date_picker",
    )

    df = load_day(data_dir, selected_date)
    irr = fetch_irradiance_for_day(
        state["latitude"], state["longitude"],
        selected_date.isoformat(), state["timezone"],
    )

    if df.empty:
        st.warning(f"No data available for {selected_date}. Check that `{data_dir}` contains the CSV file.")
        return

    kpis = compute_kpis(df)

    # KPI cards
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        val = kpis.get("peak_collector_temp")
        st.metric("Peak Collector Temp", f"{val:.1f} °C" if val is not None else "—")
    with col2:
        val = kpis.get("peak_power_kw")
        st.metric("Peak Power", f"{val:.2f} kW" if val is not None else "—")
    with col3:
        val = kpis.get("pump_runtime_hours")
        st.metric("Pump Runtime", f"{val:.1f} h" if val is not None else "—")
    with col4:
        val = kpis.get("max_flow_rate")
        st.metric("Max Flow Rate", f"{val:.1f} l/min" if val is not None else "—")

    st.markdown("---")

    # Chart 1: Temperatures (left) + pump speed (right)
    _render_temperature_chart(df)

    st.markdown("---")

    # Chart 2: Solar irradiance (hourly, from Open-Meteo)
    if not irr.empty:
        _render_irradiance_chart(irr)
        st.markdown("---")

    # Chart 3: Power (left) + flow rate (right)
    _render_power_flow_chart(df)


def _render_temperature_chart(df: pd.DataFrame) -> None:
    st.subheader("Temperatures & Pump Speed")

    fig = make_subplots(specs=[[{"secondary_y": True}]])

    temp_cols = {
        "T_collector": ("T Collector", "#e74c3c"),
        "T_tank": ("T Tank", "#3498db"),
        "T_flow": ("T Flow", "#e67e22"),
        "T_return": ("T Return", "#27ae60"),
    }

    for col, (label, color) in temp_cols.items():
        if col in df.columns:
            fig.add_trace(
                go.Scatter(
                    x=df.index,
                    y=df[col],
                    name=label,
                    line=dict(color=color, width=1.5),
                ),
                secondary_y=False,
            )

    if "pump_speed" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["pump_speed"],
                name="Pump Speed",
                fill="tozeroy",
                fillcolor="rgba(155, 89, 182, 0.2)",
                line=dict(color="rgba(155, 89, 182, 0.8)", width=1),
            ),
            secondary_y=True,
        )

    fig.update_layout(
        hovermode="x unified",
        height=400,
        margin=dict(l=0, r=0, t=30, b=0),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    fig.update_yaxes(title_text="Temperature (°C)", secondary_y=False)
    fig.update_yaxes(title_text="Pump Speed (%)", range=[0, 120], secondary_y=True)

    st.plotly_chart(fig, use_container_width=True)


def _render_irradiance_chart(irr: pd.Series) -> None:
    st.subheader("Solar Irradiance")
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=irr.index, y=irr.values,
        name="Irradiance (W/m²)",
        line=dict(color="#f9ca24", width=2, shape="hv"),
        fill="tozeroy",
        fillcolor="rgba(249, 202, 36, 0.15)",
    ))
    fig.update_layout(
        hovermode="x unified", height=250,
        margin=dict(l=0, r=0, t=30, b=0),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    fig.update_yaxes(title_text="Irradiance (W/m²)", rangemode="tozero")
    st.plotly_chart(fig, use_container_width=True)


def _render_power_flow_chart(df: pd.DataFrame) -> None:
    st.subheader("Power & Flow Rate")

    fig = make_subplots(specs=[[{"secondary_y": True}]])

    if "power_kw" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["power_kw"],
                name="Power (kW)",
                line=dict(color="#f39c12", width=2),
                fill="tozeroy",
                fillcolor="rgba(243, 156, 18, 0.15)",
            ),
            secondary_y=False,
        )

    if "flow_rate" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["flow_rate"],
                name="Flow Rate (l/min)",
                line=dict(color="#1abc9c", width=1.5),
            ),
            secondary_y=True,
        )

    fig.update_layout(
        hovermode="x unified",
        height=350,
        margin=dict(l=0, r=0, t=30, b=0),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    fig.update_yaxes(title_text="Power (kW)", secondary_y=False)
    fig.update_yaxes(title_text="Flow Rate (l/min)", secondary_y=True)

    st.plotly_chart(fig, use_container_width=True)
