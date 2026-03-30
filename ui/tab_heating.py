"""Heating Optimizer tab — sub-tab layout with full simulation + component playgrounds."""
from __future__ import annotations

import streamlit as st

from ui.tab_heating_simulation import render_simulation
from ui.tab_heating_collector import render_collector_playground
from ui.tab_heating_tank import render_tank_playground
from ui.tab_heating_consumption import render_consumption_playground


def render_tab_heating(state: dict, cfg) -> None:
    st.header("Heating Optimizer")

    tab_sim, tab_collector, tab_tank, tab_consumption = st.tabs(
        ["Full Simulation", "Collector", "Tank", "Consumption"]
    )

    with tab_sim:
        render_simulation(state, cfg)

    with tab_collector:
        render_collector_playground(state, cfg)

    with tab_tank:
        render_tank_playground(state, cfg)

    with tab_consumption:
        render_consumption_playground(state, cfg)
