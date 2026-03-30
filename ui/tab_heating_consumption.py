"""Consumption playground sub-tab — profile builder, comparison with T2 estimates."""
from __future__ import annotations

from datetime import date, timedelta

import plotly.graph_objects as go
import streamlit as st

from analytics.heating import (
    PRESETS,
    build_consumption_profile,
    estimate_consumption_from_t2,
    actual_hourly_data,
)


def render_consumption_playground(state: dict, cfg) -> None:
    st.subheader("Hot Water Consumption Profiles")
    st.caption(
        "Build consumption profiles from presets or manual inputs, "
        "and compare against T2-estimated profiles from past dates."
    )

    data_dir = state["data_dir"]

    # ── Profile builder ──────────────────────────────────────────────────────
    st.markdown("#### Profile builder")
    preset = st.radio(
        "Usage preset", list(PRESETS.keys()), index=2, horizontal=True, key="cons_preset",
    )
    defaults = PRESETS[preset]

    c1, c2, c3 = st.columns(3)
    showers_am = c1.number_input(
        "Morning showers", 0, 6, defaults["showers_morning"], key="cons_showers_am",
    )
    showers_pm = c2.number_input(
        "Evening showers", 0, 6, defaults["showers_evening"], key="cons_showers_pm",
    )
    baths = c3.number_input(
        "Evening baths", 0, 3, defaults["baths_evening"], key="cons_baths",
    )

    profile = build_consumption_profile(showers_am, showers_pm, baths)
    total = sum(profile)
    st.metric("Daily total", f"{total:.1f} kWh")

    # ── Past date comparison ─────────────────────────────────────────────────
    st.markdown("#### Compare with actual T2 data")
    today = date.today()
    comp_date = st.date_input(
        "Select past date",
        value=today - timedelta(days=1),
        max_value=today - timedelta(days=1),
        key="cons_comp_date",
    )

    power_df, temp_df = actual_hourly_data(data_dir, comp_date, days=1)
    estimated = None
    if not power_df.empty and not temp_df.empty:
        estimated = estimate_consumption_from_t2(power_df, temp_df, cfg.tank)

    est_profile = None
    if estimated:
        day_key = str(comp_date)
        if day_key in estimated:
            est_profile = estimated[day_key]
        elif "_average" in estimated:
            est_profile = estimated["_average"]

    # ── 24h profile chart ────────────────────────────────────────────────────
    st.markdown("#### 24h consumption profile")
    fig = go.Figure()

    fig.add_trace(go.Bar(
        x=list(range(24)),
        y=profile,
        name=f"Preset ({preset})",
        marker_color="rgba(52, 152, 219, 0.7)",
    ))

    if est_profile is not None:
        est_total = sum(est_profile)
        fig.add_trace(go.Bar(
            x=list(range(24)),
            y=est_profile,
            name=f"T2-estimated ({est_total:.1f} kWh)",
            marker_color="rgba(142, 68, 173, 0.7)",
        ))

    fig.update_layout(
        height=350,
        xaxis_title="Hour of day",
        yaxis_title="Consumption (kWh)",
        barmode="group",
        margin=dict(l=0, r=0, t=20, b=60),
        legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="left", x=0),
    )
    st.plotly_chart(fig, width="stretch")

    # ── Comparison table ─────────────────────────────────────────────────────
    if est_profile is not None:
        st.markdown("#### Hour-by-hour comparison")
        import pandas as pd
        rows = []
        for h in range(24):
            p = profile[h]
            e = est_profile[h]
            diff = e - p
            if p > 0.05 or e > 0.05:
                rows.append({
                    "Hour": f"{h:02d}:00",
                    "Preset (kWh)": round(p, 2),
                    "T2-estimated (kWh)": round(e, 2),
                    "Difference": round(diff, 2),
                })
        if rows:
            df = pd.DataFrame(rows)
            st.dataframe(df, hide_index=True, width="stretch")
    elif comp_date < today:
        st.info("No T2 data available for the selected date.")
