import duckdb
import pandas as pd
import streamlit as st
from pathlib import Path


@st.cache_resource
def get_connection() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(database=":memory:")


def get_csv_glob(data_dir: str) -> str:
    return str(Path(data_dir) / "**" / "*.csv")


def build_view(con: duckdb.DuckDBPyConnection, data_dir: str) -> None:
    glob = get_csv_glob(data_dir)
    con.execute(f"""
        CREATE OR REPLACE VIEW solar_raw AS
        SELECT * FROM read_csv(
            '{glob}',
            header=true,
            decimal_separator=',',
            nullstr='Err',
            columns={{
                'DATE & TIME':  'TIMESTAMP',
                'T1[C]':        'DOUBLE',
                'T2[C]':        'DOUBLE',
                'T3[C]':        'DOUBLE',
                'T4[C]':        'DOUBLE',
                'T5[C]':        'DOUBLE',
                'T E1[C]':      'DOUBLE',
                'T E2[C]':      'DOUBLE',
                "V'[l/min]":    'DOUBLE',
                'p[bar]':       'DOUBLE',
                'P[kW]':        'DOUBLE',
                'Qday[kWh]':    'DOUBLE',
                'Qyear[kWh]':   'DOUBLE',
                'Qsum[kWh]':    'DOUBLE',
                'R1[%]':        'DOUBLE',
                'R2[%]':        'DOUBLE',
                'R3[%]':        'DOUBLE',
                'Rs[%]':        'DOUBLE',
                'R1 PWM[%]':    'DOUBLE',
                'R2 PWM[%]':    'DOUBLE'
            }}
        )
    """)


@st.cache_data(ttl=300)
def query_daily_yield(data_dir: str, start: str, end: str) -> pd.DataFrame:
    """
    Returns daily energy yield for all days in [start, end].
    Qday is a cumulative intraday counter → daily yield = MAX(Qday) - MIN(Qday) per date,
    but since it resets at midnight, MAX(Qday) per day gives the total yield for that day.
    We also compute yield as max-min to handle partial starting values.
    """
    con = get_connection()
    build_view(con, data_dir)
    return con.execute(f"""
        SELECT
            DATE_TRUNC('day', "DATE & TIME") AS date,
            MAX("Qday[kWh]") - MIN("Qday[kWh]") AS yield_kwh,
            MAX("Qsum[kWh]") AS Qsum_max,
            COUNT(*) AS record_count
        FROM solar_raw
        WHERE "DATE & TIME" >= '{start}' AND "DATE & TIME" < DATE '{end}'::DATE + INTERVAL '1 day'
        GROUP BY 1
        ORDER BY 1
    """).df()


@st.cache_data(ttl=300)
def query_flow_rate_trend(data_dir: str, start: str, end: str) -> pd.DataFrame:
    """
    Monthly 95th-percentile flow rate when pump is at full speed (R1 PWM = 100).
    """
    con = get_connection()
    build_view(con, data_dir)
    return con.execute(f"""
        SELECT
            DATE_TRUNC('month', "DATE & TIME") AS month,
            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY "V'[l/min]") AS p95_flow_rate,
            COUNT(*) AS record_count
        FROM solar_raw
        WHERE "DATE & TIME" >= '{start}'
          AND "DATE & TIME" < DATE '{end}'::DATE + INTERVAL '1 day'
          AND "R1 PWM[%]" = 100
          AND "V'[l/min]" IS NOT NULL
        GROUP BY 1
        ORDER BY 1
    """).df()


@st.cache_data(ttl=300)
def query_heat_exchanger_trend(data_dir: str, start: str, end: str) -> pd.DataFrame:
    """
    Monthly median of (T_flow - T_return) / power_kw as a thermal resistance proxy.
    """
    con = get_connection()
    build_view(con, data_dir)
    return con.execute(f"""
        SELECT
            DATE_TRUNC('month', "DATE & TIME") AS month,
            MEDIAN(("T4[C]" - "T5[C]") / "P[kW]") AS median_resistance,
            COUNT(*) AS record_count
        FROM solar_raw
        WHERE "DATE & TIME" >= '{start}'
          AND "DATE & TIME" < DATE '{end}'::DATE + INTERVAL '1 day'
          AND "R1 PWM[%]" > 0
          AND "P[kW]" > 0.1
          AND "T4[C]" IS NOT NULL
          AND "T5[C]" IS NOT NULL
        GROUP BY 1
        ORDER BY 1
    """).df()


@st.cache_data(ttl=300)
def query_collector_yoy(data_dir: str, start: str, end: str, clear_day_dates: tuple[str, ...]) -> pd.DataFrame:
    """
    Peak power at solar noon (11:00–13:00) on clear days, grouped by year and month.
    clear_day_dates is a tuple of 'YYYY-MM-DD' strings (hashable for caching).
    """
    if not clear_day_dates:
        return pd.DataFrame()
    con = get_connection()
    build_view(con, data_dir)
    date_list = ", ".join(f"DATE '{d}'" for d in clear_day_dates)
    return con.execute(f"""
        SELECT
            YEAR("DATE & TIME") AS year,
            MONTH("DATE & TIME") AS month,
            MAX("P[kW]") AS peak_power_kw,
            COUNT(*) AS record_count
        FROM solar_raw
        WHERE "DATE & TIME" >= '{start}'
          AND "DATE & TIME" < DATE '{end}'::DATE + INTERVAL '1 day'
          AND DATE("DATE & TIME") IN ({date_list})
          AND HOUR("DATE & TIME") BETWEEN 11 AND 13
          AND "P[kW]" IS NOT NULL
        GROUP BY 1, 2
        ORDER BY 1, 2
    """).df()
