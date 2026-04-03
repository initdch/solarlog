import streamlit as st
import plotly.graph_objects as go
import pandas as pd
import numpy as np

from analytics.degradation import flow_rate_trend, heat_exchanger_trend, collector_yoy
from analytics.weather import fetch_weather, classify_clear_days


def render_tab_degradation(state: dict) -> None:
    data_dir = state["data_dir"]
    start = state["start_date"].isoformat()
    end = state["end_date"].isoformat()
    lat = state["latitude"]
    lon = state["longitude"]
    tz = state["timezone"]
    clear_threshold = state["clear_threshold"]

    st.header("Degradation Signals")
    st.caption(
        "These charts surface long-term trends that may indicate equipment degradation. "
        "Individual data points are less meaningful than multi-year trends."
    )

    sub1, sub2, sub3 = st.tabs(["Flow Rate (Pump/Piping)", "Heat Exchanger", "Collector YoY"])

    with sub1:
        _render_flow_rate(data_dir, start, end)

    with sub2:
        _render_heat_exchanger(data_dir, start, end)

    with sub3:
        _render_collector_yoy(data_dir, start, end, lat, lon, tz, clear_threshold)


def _render_flow_rate(data_dir: str, start: str, end: str) -> None:
    st.subheader("Flow Rate Trend (Pump at Full Speed)")
    st.caption(
        "Monthly 95th-percentile flow rate when pump PWM = 100%. "
        "A declining trend suggests pump wear or piping blockage."
    )

    try:
        df = flow_rate_trend(data_dir, start, end)
    except Exception as e:
        st.error(f"Query failed: {e}")
        return

    if df.empty:
        st.info("No data available. The pump may not have run at full speed in the selected range.")
        return

    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=df["month"],
            y=df["p95_flow_rate"],
            mode="markers",
            name="P95 Flow Rate",
            marker=dict(
                size=df["record_count"].apply(lambda x: max(6, min(20, x / 50))),
                color="#3498db",
                opacity=0.7,
            ),
            hovertemplate="%{x|%b %Y}<br>P95 Flow: %{y:.2f} l/min<extra></extra>",
        )
    )

    # OLS trendline
    if len(df) >= 3:
        _add_trendline(fig, df["month"], df["p95_flow_rate"])

    fig.update_layout(
        yaxis_title="Flow Rate (l/min)",
        height=400,
        margin=dict(l=0, r=0, t=30, b=60),
        legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="left", x=0),
    )
    st.plotly_chart(fig, width="stretch")

    with st.expander("Data table"):
        st.dataframe(df, width="stretch")


def _render_heat_exchanger(data_dir: str, start: str, end: str) -> None:
    st.subheader("Heat Exchanger Thermal Resistance")
    st.caption(
        "Monthly median of ΔT/Power (°C/kW) while pump is running. "
        "An upward trend indicates scaling or fouling of the heat exchanger."
    )

    try:
        df = heat_exchanger_trend(data_dir, start, end)
    except Exception as e:
        st.error(f"Query failed: {e}")
        return

    if df.empty:
        st.info("No data available for the selected range.")
        return

    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=df["month"],
            y=df["median_resistance"],
            mode="lines+markers",
            name="Median Thermal Resistance",
            line=dict(color="#e74c3c", width=2),
            marker=dict(size=6),
            hovertemplate="%{x|%b %Y}<br>ΔT/P: %{y:.2f} °C/kW<extra></extra>",
        )
    )

    if len(df) >= 3:
        _add_trendline(fig, df["month"], df["median_resistance"])

    fig.update_layout(
        yaxis_title="Thermal Resistance (°C/kW)",
        height=400,
        margin=dict(l=0, r=0, t=30, b=60),
        legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="left", x=0),
    )
    st.plotly_chart(fig, width="stretch")

    with st.expander("Data table"):
        st.dataframe(df, width="stretch")


def _render_collector_yoy(
    data_dir: str, start: str, end: str, lat: float, lon: float, tz: str, clear_threshold: int
) -> None:
    st.subheader("Collector Year-over-Year Performance")
    st.caption(
        "Peak midday power on clear days, grouped by month and year. "
        "A downward shift between years suggests collector soiling or degradation."
    )

    with st.spinner("Fetching weather data…"):
        try:
            _, daily_weather = fetch_weather(lat, lon, start, end, tz)
        except Exception as e:
            st.error(f"Weather fetch failed: {e}")
            return

    if daily_weather.empty:
        st.warning("Could not retrieve weather data. Check your internet connection and coordinates.")
        return

    clear_days = classify_clear_days(daily_weather, clear_threshold)

    st.info(
        f"Found {len(clear_days)} clear days (cloud cover ≤ {clear_threshold}%) "
        f"between {start} and {end}."
    )

    if not clear_days:
        st.warning("No clear days found. Try increasing the cloud cover threshold.")
        return

    try:
        df = collector_yoy(data_dir, start, end, clear_days)
    except Exception as e:
        st.error(f"Query failed: {e}")
        return

    if df.empty:
        st.info("No solar data found on the identified clear days.")
        return

    fig = go.Figure()

    for year, group in df.groupby("year"):
        fig.add_trace(
            go.Scatter(
                x=group["month"],
                y=group["peak_power_kw"],
                mode="lines+markers",
                name=str(year),
                hovertemplate=f"Year {year}<br>Month %{{x}}<br>Peak: %{{y:.2f}} kW<extra></extra>",
            )
        )

    fig.update_layout(
        xaxis=dict(
            tickmode="linear",
            tick0=1,
            dtick=1,
            title="Month",
            ticktext=["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
            tickvals=list(range(1, 13)),
        ),
        yaxis_title="Peak Power (kW)",
        height=450,
        margin=dict(l=0, r=0, t=30, b=60),
        legend=dict(title="Year", orientation="v"),
        hovermode="x unified",
    )
    st.plotly_chart(fig, width="stretch")

    with st.expander("Data table"):
        st.dataframe(df, width="stretch")


def _add_trendline(fig: go.Figure, x_series: pd.Series, y_series: pd.Series) -> None:
    """Add an OLS trendline to the figure."""
    try:
        import statsmodels.api as sm

        x_num = (x_series - x_series.min()).dt.days.values.astype(float)
        y = y_series.values.astype(float)
        valid = ~np.isnan(y) & ~np.isnan(x_num)

        if valid.sum() < 3:
            return

        X = sm.add_constant(x_num[valid])
        model = sm.OLS(y[valid], X).fit()
        y_pred = model.predict(X)

        fig.add_trace(
            go.Scatter(
                x=x_series[valid],
                y=y_pred,
                mode="lines",
                name="Trend (OLS)",
                line=dict(dash="dash", color="rgba(100, 100, 100, 0.6)", width=1.5),
                hoverinfo="skip",
            )
        )
    except Exception:
        pass  # Trendline is optional — don't crash on statsmodels issues
