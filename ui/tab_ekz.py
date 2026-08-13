"""Energy & Grid tab — EKZ electricity consumption vs solar yield."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from datetime import date, timedelta

from analytics.ekz import load_ekz_consumption, sync_ekz, ekz_data_status
from analytics.yield_tracking import get_daily_yield


def _merge_solar_ekz(solar_df: pd.DataFrame, ekz_df: pd.DataFrame) -> pd.DataFrame:
    if solar_df.empty or ekz_df.empty:
        return pd.DataFrame()
    s = solar_df[~solar_df["partial_day"]][["date", "yield_kwh"]].copy()
    s["date"] = pd.to_datetime(s["date"]).dt.normalize()
    e = ekz_df[["date", "consumption_kwh", "estimated"]].copy()
    e["date"] = pd.to_datetime(e["date"]).dt.normalize()
    return pd.merge(s, e, on="date", how="inner").sort_values("date").reset_index(drop=True)


def _correlation_and_heater_estimate(merged: pd.DataFrame) -> dict:
    result = {"r": None, "heater_kwh": None, "baseline_kwh": None, "n": len(merged)}
    if len(merged) < 5:
        return result
    result["r"] = float(merged["yield_kwh"].corr(merged["consumption_kwh"]))
    solar_thresh = 1.0
    solar_days = merged[merged["yield_kwh"] >= solar_thresh]
    no_solar_days = merged[merged["yield_kwh"] < solar_thresh]
    if len(solar_days) >= 3 and len(no_solar_days) >= 3:
        avg_with = float(solar_days["consumption_kwh"].mean())
        avg_without = float(no_solar_days["consumption_kwh"].mean())
        result["baseline_kwh"] = avg_with
        result["heater_kwh"] = max(0.0, avg_without - avg_with)
    return result


def render_tab_ekz(state: dict, cfg) -> None:
    st.header("Energy & Grid")
    st.caption(
        "Daily electricity consumption from EKZ compared to solar thermal yield. "
        "High solar days should correlate with lower electricity use."
    )

    ekz_cfg = cfg.ekz
    data_dir = ekz_cfg.data_dir

    # ── Sync status + button ─────────────────────────────────────────────────
    status = ekz_data_status(data_dir)
    has_credentials = bool(ekz_cfg.installation_id and ekz_cfg.cookie and ekz_cfg.csrf_token)

    with st.container():
        scol1, scol2 = st.columns([3, 1])
        if status["rows"] == 0:
            scol1.warning("No local EKZ data yet. Click **Sync** to download.")
        else:
            stale_msg = (
                f"Up to date." if status["days_stale"] == 0
                else f"{status['days_stale']} day(s) behind."
            )
            scol1.caption(
                f"Local data: {status['rows']} days  |  "
                f"{status['first']} → {status['last']}  |  {stale_msg}"
            )

        sync_clicked = scol2.button("Sync", disabled=not has_credentials, width="stretch")
        if not has_credentials:
            st.warning(
                "EKZ credentials missing. Add `installation_id`, `cookie`, and `csrf_token` "
                "to `config.local.toml` (gitignored). Refresh from browser DevTools when they expire."
            )

    if sync_clicked:
        with st.spinner("Fetching from EKZ…"):
            try:
                rows_added, msg = sync_ekz(
                    data_dir,
                    ekz_cfg.installation_id,
                    ekz_cfg.cookie,
                    ekz_cfg.csrf_token,
                    ekz_cfg.data_start,
                )
                st.success(msg)
                load_ekz_consumption.clear()
                st.rerun()
            except Exception as e:
                st.error(
                    f"Sync failed: {e}\n\n"
                    "The session may have expired — update credentials in `config.local.toml`."
                )

    if status["rows"] == 0:
        return

    today = date.today()
    ekz_start = ekz_cfg.data_start

    # ── Date range selector ──────────────────────────────────────────────────
    col_from, col_to = st.columns(2)
    range_start = col_from.date_input(
        "From",
        value=date.fromisoformat(ekz_start),
        min_value=date.fromisoformat(ekz_start),
        max_value=today,
        key="ekz_from",
    )
    range_end = col_to.date_input(
        "To",
        value=today - timedelta(days=1),
        min_value=date.fromisoformat(ekz_start),
        max_value=today,
        key="ekz_to",
    )
    if range_start >= range_end:
        st.error("Start date must be before end date.")
        return

    start_str = range_start.isoformat()
    end_str = range_end.isoformat()

    ekz_df = load_ekz_consumption(data_dir, start_str, end_str)
    if ekz_df.empty:
        st.warning("No EKZ data for the selected period.")
        return

    solar_df = get_daily_yield(state["data_dir"], start_str, end_str)
    merged = _merge_solar_ekz(solar_df, ekz_df)
    stats = _correlation_and_heater_estimate(merged)

    # ── Summary metrics ──────────────────────────────────────────────────────
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Avg electricity/day", f"{ekz_df['consumption_kwh'].mean():.1f} kWh")
    col2.metric(
        "Avg solar yield/day",
        f"{solar_df['yield_kwh'].mean():.1f} kWh" if not solar_df.empty else "—",
    )
    col3.metric(
        "Correlation (solar ↔ grid)",
        f"{stats['r']:.2f}" if stats["r"] is not None else "—",
    )
    if stats["heater_kwh"] is not None:
        col4.metric(
            "Est. heater electricity",
            f"{stats['heater_kwh']:.1f} kWh/day",
            help=(
                f"Extra electricity on no-solar days vs solar days (≥1 kWh solar). "
                f"Baseline (solar days): {stats['baseline_kwh']:.1f} kWh/day."
            ),
        )
    else:
        col4.metric("Est. heater electricity", "—", help="Need ≥3 days in each category.")

    # ── Main chart ───────────────────────────────────────────────────────────
    st.subheader("Daily electricity vs solar")

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=ekz_df["date"], y=ekz_df["consumption_kwh"],
        name="Electricity (kWh)", marker_color="#3498db", opacity=0.85,
    ))
    if not solar_df.empty:
        yd = solar_df[~solar_df["partial_day"]].copy()
        yd["date"] = pd.to_datetime(yd["date"])
        fig.add_trace(go.Scatter(
            x=yd["date"], y=yd["yield_kwh"],
            name="Solar yield", line=dict(color="#27ae60", width=2),
            mode="lines+markers", marker=dict(size=5), yaxis="y2",
        ))
    estimated = ekz_df[ekz_df["estimated"]]
    if not estimated.empty:
        fig.add_trace(go.Scatter(
            x=estimated["date"], y=estimated["consumption_kwh"],
            name="EKZ estimated", mode="markers",
            marker=dict(symbol="x", size=8, color="#e67e22"),
        ))
    fig.update_layout(
        barmode="stack", height=400, hovermode="x unified",
        legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="left", x=0),
        margin=dict(l=0, r=0, t=50, b=60),
        yaxis=dict(title="Electricity (kWh)"),
        yaxis2=dict(title="Solar yield (kWh)", overlaying="y", side="right", showgrid=False),
    )
    st.plotly_chart(fig, width="stretch")

    # ── Correlation scatter ──────────────────────────────────────────────────
    if not merged.empty and len(merged) >= 5:
        st.subheader("Solar yield vs electricity consumption")
        color_vals = list(range(len(merged)))
        try:
            import numpy as np
            m, b = np.polyfit(merged["yield_kwh"], merged["consumption_kwh"], 1)
            x_line = [float(merged["yield_kwh"].min()), float(merged["yield_kwh"].max())]
            y_line = [m * x + b for x in x_line]
            has_fit = True
        except Exception:
            has_fit = False

        fig_sc = go.Figure()
        fig_sc.add_trace(go.Scatter(
            x=merged["yield_kwh"], y=merged["consumption_kwh"],
            mode="markers",
            marker=dict(color=color_vals, colorscale="Viridis", size=8,
                        colorbar=dict(title="Day index"), showscale=True),
            text=merged["date"].dt.strftime("%Y-%m-%d"),
            hovertemplate="<b>%{text}</b><br>Solar: %{x:.1f} kWh<br>Grid: %{y:.1f} kWh<extra></extra>",
            name="Days",
        ))
        if has_fit:
            fig_sc.add_trace(go.Scatter(
                x=x_line, y=y_line, mode="lines",
                line=dict(color="#e74c3c", dash="dash", width=1.5),
                name=f"Fit (r={stats['r']:.2f})",
            ))
        fig_sc.update_layout(
            height=350, xaxis_title="Solar yield (kWh)", yaxis_title="Grid electricity (kWh)",
            margin=dict(l=0, r=0, t=20, b=60),
            legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="left", x=0),
        )
        st.plotly_chart(fig_sc, width="stretch")

    # ── Heater estimate breakdown ────────────────────────────────────────────
    if stats["heater_kwh"] is not None:
        st.subheader("Electric heater estimate")
        solar_thresh = 1.0
        solar_days = merged[merged["yield_kwh"] >= solar_thresh]
        no_solar_days = merged[merged["yield_kwh"] < solar_thresh]
        bdf = pd.DataFrame({
            "Category": [
                f"Days with solar ≥{solar_thresh:.0f} kWh ({len(solar_days)} days)",
                f"Days with solar <{solar_thresh:.0f} kWh ({len(no_solar_days)} days)",
                "Estimated heater contribution",
            ],
            "Avg electricity (kWh/day)": [
                round(float(solar_days["consumption_kwh"].mean()), 1),
                round(float(no_solar_days["consumption_kwh"].mean()), 1),
                round(stats["heater_kwh"], 1),
            ],
        })
        st.dataframe(bdf, hide_index=True, width="content")
        st.caption(
            "Heater contribution = avg electricity on no-solar days minus avg on solar days. "
            "Rough lower bound — solar also offsets some usage on partial-solar days."
        )

    # ── Raw data expander ────────────────────────────────────────────────────
    with st.expander("Raw data"):
        if not merged.empty:
            display = merged.copy()
            display["date"] = display["date"].dt.strftime("%Y-%m-%d")
            display = display.drop(columns=["consumption_ht", "consumption_nt"], errors="ignore")
            display = display.rename(columns={
                "yield_kwh": "Solar yield (kWh)",
                "consumption_kwh": "Grid total (kWh)",
                "estimated": "Estimated",
            })
            st.dataframe(display, hide_index=True, width="stretch")
        else:
            st.info("No overlapping dates between solar and EKZ data.")
