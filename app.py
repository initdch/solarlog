import streamlit as st
from config import load_config
from ui.sidebar import render_sidebar
from ui.tab_daily import render_tab_daily
from ui.tab_yield import render_tab_yield
from ui.tab_degradation import render_tab_degradation
from ui.tab_ekz import render_tab_ekz

st.set_page_config(
    page_title="Solar Thermal Analyzer",
    page_icon="☀️",
    layout="wide",
    initial_sidebar_state="expanded",
)

cfg = load_config("config.toml")
state = render_sidebar(cfg)

tab_daily, tab_yield, tab_degradation, tab_ekz = st.tabs(
    ["Daily View", "Yield Tracking", "Degradation Signals", "Energy & Grid"]
)

with tab_daily:
    render_tab_daily(state)

with tab_yield:
    render_tab_yield(state)

with tab_degradation:
    render_tab_degradation(state)

with tab_ekz:
    render_tab_ekz(state, cfg)
