import streamlit as st
import requests
from datetime import date, timedelta
from pathlib import Path
from config import Config
from data.loader import count_available_files


@st.cache_data(ttl=86400)
def _geocode(query: str) -> list[dict]:
    """Return Nominatim results for a place/address query."""
    resp = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": query, "format": "json", "limit": 5},
        headers={"User-Agent": "SolarThermalAnalyzer/1.0"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


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

    location_query = st.sidebar.text_input(
        "Search location",
        placeholder="e.g. Affoltern am Albis or a street address",
        key="location_search",
    )

    # Session state holds the active lat/lon so the search can update them
    if "loc_lat" not in st.session_state:
        st.session_state.loc_lat = cfg.location.latitude
    if "loc_lon" not in st.session_state:
        st.session_state.loc_lon = cfg.location.longitude

    if location_query:
        try:
            results = _geocode(location_query)
        except Exception as e:
            st.sidebar.error(f"Geocoding failed: {e}")
            results = []

        if not results:
            st.sidebar.warning("No results found.")
        elif len(results) == 1:
            st.session_state.loc_lat = float(results[0]["lat"])
            st.session_state.loc_lon = float(results[0]["lon"])
            st.sidebar.caption(f"📍 {results[0]['display_name']}")
        else:
            labels = [r["display_name"] for r in results]
            choice = st.sidebar.selectbox("Select location", labels, key="loc_choice")
            chosen = results[labels.index(choice)]
            st.session_state.loc_lat = float(chosen["lat"])
            st.session_state.loc_lon = float(chosen["lon"])

    latitude = st.sidebar.number_input(
        "Latitude", value=st.session_state.loc_lat, min_value=-90.0, max_value=90.0, step=0.01, format="%.4f",
        key="loc_lat_input",
    )
    longitude = st.sidebar.number_input(
        "Longitude", value=st.session_state.loc_lon, min_value=-180.0, max_value=180.0, step=0.01, format="%.4f",
        key="loc_lon_input",
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
