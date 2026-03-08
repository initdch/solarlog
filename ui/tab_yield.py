import streamlit as st
import plotly.graph_objects as go
import pandas as pd

from analytics.yield_tracking import get_daily_yield, get_monthly_yield, get_yearly_yield, get_lifetime_total


def render_tab_yield(state: dict) -> None:
    data_dir = state["data_dir"]
    start = state["start_date"].isoformat()
    end = state["end_date"].isoformat()

    st.header("Energy Yield Tracking")

    # Lifetime total
    try:
        lifetime = get_lifetime_total(data_dir)
        if lifetime is not None:
            st.metric("Lifetime Total (Qsum)", f"{lifetime:,.1f} kWh")
    except Exception as e:
        st.error(f"Could not compute lifetime total: {e}")

    st.markdown("---")

    granularity = st.radio(
        "Granularity", ["Daily", "Monthly", "Yearly"], horizontal=True, key="yield_granularity"
    )

    try:
        if granularity == "Daily":
            df = get_daily_yield(data_dir, start, end)
            _render_yield_chart(df, x_col="date", label="Daily Yield")
        elif granularity == "Monthly":
            df = get_monthly_yield(data_dir, start, end)
            _render_yield_chart(df, x_col="month", label="Monthly Yield", has_partial=False)
        else:
            df = get_yearly_yield(data_dir, start, end)
            _render_yield_chart(df, x_col="year", label="Yearly Yield", has_partial=False)
    except Exception as e:
        st.error(f"Query failed: {e}")
        return


def _render_yield_chart(
    df: pd.DataFrame,
    x_col: str,
    label: str,
    has_partial: bool = True,
) -> None:
    if df.empty:
        st.info("No yield data available for the selected range.")
        return

    fig = go.Figure()

    if has_partial and "partial_day" in df.columns:
        complete = df[~df["partial_day"]]
        partial = df[df["partial_day"]]

        if not complete.empty:
            fig.add_trace(
                go.Bar(
                    x=complete[x_col],
                    y=complete["yield_kwh"],
                    name="Full day",
                    marker_color="#27ae60",
                )
            )
        if not partial.empty:
            fig.add_trace(
                go.Bar(
                    x=partial[x_col],
                    y=partial["yield_kwh"],
                    name="Partial day",
                    marker_color="#f1c40f",
                )
            )
    else:
        fig.add_trace(
            go.Bar(
                x=df[x_col],
                y=df["yield_kwh"],
                name=label,
                marker_color="#27ae60",
            )
        )

    total = df["yield_kwh"].sum()
    fig.update_layout(
        title=f"{label} — Total: {total:,.1f} kWh",
        yaxis_title="Energy (kWh)",
        hovermode="x unified",
        height=400,
        margin=dict(l=0, r=0, t=50, b=0),
        barmode="overlay",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )

    st.plotly_chart(fig, use_container_width=True)

    with st.expander("Data table"):
        st.dataframe(df, use_container_width=True)
        csv_data = df.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="Download CSV",
            data=csv_data,
            file_name=f"yield_{x_col}.csv",
            mime="text/csv",
        )
