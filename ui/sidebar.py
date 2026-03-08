import streamlit as st
from datetime import date, timedelta
from pathlib import Path
from config import Config
from data.loader import count_available_files


def render_sidebar(cfg: Config) -> dict:
    """Render the persistent sidebar and return runtime state dict."""
    st.sidebar.title("Solar Thermal Analyzer")
    st.sidebar.markdown("---")

    # Data directory
    data_dir = st.sidebar.text_input(
        "Data directory",
        value=cfg.data.directory,
        help="Path to the root of the YYYY/MM/YYYYMMDD.csv tree",
    )

    # Date range
    st.sidebar.subheader("Date range")
    today = date.today()
    default_start = date(today.year - 1, 1, 1)

    start_date = st.sidebar.date_input("Start date", value=default_start, max_value=today)
    end_date = st.sidebar.date_input("End date", value=today, min_value=start_date, max_value=today)

    # Location
    st.sidebar.subheader("Location")
    latitude = st.sidebar.number_input(
        "Latitude", value=cfg.location.latitude, min_value=-90.0, max_value=90.0, step=0.01, format="%.4f"
    )
    longitude = st.sidebar.number_input(
        "Longitude", value=cfg.location.longitude, min_value=-180.0, max_value=180.0, step=0.01, format="%.4f"
    )
    timezone = st.sidebar.text_input("Timezone", value=cfg.location.timezone)

    # Clear sky threshold
    st.sidebar.subheader("Degradation settings")
    clear_threshold = st.sidebar.slider(
        "Clear day cloud cover threshold (%)",
        min_value=5,
        max_value=50,
        value=cfg.app.clear_day_cloud_cover_max,
        step=5,
        help="Days with mean daytime cloud cover below this value are treated as clear days",
    )

    # File count indicator
    st.sidebar.markdown("---")
    if Path(data_dir).exists():
        with st.sidebar.container():
            file_count = count_available_files(data_dir, start_date, end_date)
            if file_count > 0:
                st.sidebar.success(f"~{file_count} CSV files found in range")
            else:
                st.sidebar.warning("No CSV files found in selected range")
    else:
        st.sidebar.error(f"Directory not found: `{data_dir}`")

    return {
        "data_dir": data_dir,
        "start_date": start_date,
        "end_date": end_date,
        "latitude": latitude,
        "longitude": longitude,
        "timezone": timezone,
        "clear_threshold": clear_threshold,
    }
