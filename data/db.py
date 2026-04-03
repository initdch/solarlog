import duckdb
import pandas as pd
import streamlit as st
from pathlib import Path


@st.cache_resource
def get_connection() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(database=":memory:")


_COLUMNS = """{
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
}"""


def build_view(con: duckdb.DuckDBPyConnection, data_dir: str) -> None:
    """Build the solar_raw view, handling two CSV formats:

    Old format (.csv): comma-delimited, timestamps with seconds (HH:MM:SS),
                       P[kW]/p[bar] quoted with comma decimal ("1,6").
    New format (.CSV): semicolon-delimited, timestamps without seconds (HH:MM),
                       P[kW]/p[bar] unquoted with comma decimal (1,6).
    """
    root = Path(data_dir)
    old_glob = str(root / "**" / "*.csv")
    new_glob = str(root / "**" / "*.CSV")

    has_old = bool(list(root.rglob("*.csv")))
    has_new = bool(list(root.rglob("*.CSV")))

    parts = []
    if has_old:
        parts.append(f"""
            SELECT * FROM read_csv(
                ['{old_glob}'],
                header=true,
                delim=',',
                decimal_separator=',',
                nullstr='Err',
                timestampformat='%Y-%m-%d %H:%M:%S',
                columns={_COLUMNS}
            )""")
    if has_new:
        parts.append(f"""
            SELECT * FROM read_csv(
                ['{new_glob}'],
                header=true,
                delim=';',
                decimal_separator=',',
                nullstr='Err',
                timestampformat='%Y-%m-%d %H:%M',
                columns={_COLUMNS}
            )""")

    if not parts:
        # No files yet — create an empty view with the right schema
        con.execute(f"""
            CREATE OR REPLACE VIEW solar_raw AS
            SELECT
                CAST(NULL AS TIMESTAMP) AS "DATE & TIME",
                CAST(NULL AS DOUBLE)    AS "T1[C]",
                CAST(NULL AS DOUBLE)    AS "T2[C]",
                CAST(NULL AS DOUBLE)    AS "T3[C]",
                CAST(NULL AS DOUBLE)    AS "T4[C]",
                CAST(NULL AS DOUBLE)    AS "T5[C]",
                CAST(NULL AS DOUBLE)    AS "T E1[C]",
                CAST(NULL AS DOUBLE)    AS "T E2[C]",
                CAST(NULL AS DOUBLE)    AS "V'[l/min]",
                CAST(NULL AS DOUBLE)    AS "p[bar]",
                CAST(NULL AS DOUBLE)    AS "P[kW]",
                CAST(NULL AS DOUBLE)    AS "Qday[kWh]",
                CAST(NULL AS DOUBLE)    AS "Qyear[kWh]",
                CAST(NULL AS DOUBLE)    AS "Qsum[kWh]",
                CAST(NULL AS DOUBLE)    AS "R1[%]",
                CAST(NULL AS DOUBLE)    AS "R2[%]",
                CAST(NULL AS DOUBLE)    AS "R3[%]",
                CAST(NULL AS DOUBLE)    AS "Rs[%]",
                CAST(NULL AS DOUBLE)    AS "R1 PWM[%]",
                CAST(NULL AS DOUBLE)    AS "R2 PWM[%]"
            WHERE false
        """)
        return

    sql = " UNION ALL ".join(parts)
    con.execute(f"CREATE OR REPLACE VIEW solar_raw AS {sql}")


@st.cache_data(ttl=300)
def query_daily_yield(data_dir: str, start: str, end: str) -> pd.DataFrame:
    """
    Returns daily energy yield for all days in [start, end].
    Qday is a cumulative intraday counter → daily yield = MAX(Qday) - MIN(Qday) per date.
    Fallback: when Qday shows 0 but P[kW] is non-zero (controller accumulator bug),
    yield is estimated by integrating P[kW] over 1-minute rows (SUM(P) / 60).
    yield_source: 'Qday' or 'P[kW] integrated'.
    """
    con = get_connection()
    build_view(con, data_dir)
    return con.execute(f"""
        SELECT
            DATE_TRUNC('day', "DATE & TIME") AS date,
            CASE
                WHEN MAX("Qday[kWh]") - MIN("Qday[kWh]") > 0
                THEN MAX("Qday[kWh]") - MIN("Qday[kWh]")
                ELSE ROUND(SUM(COALESCE("P[kW]", 0)) / 60.0, 2)
            END AS yield_kwh,
            CASE
                WHEN MAX("Qday[kWh]") - MIN("Qday[kWh]") > 0
                THEN 'Qday'
                ELSE 'P[kW] integrated'
            END AS yield_source,
            MAX("Qsum[kWh]") AS Qsum_max,
            COUNT(*) AS record_count,
            MIN("DATE & TIME") AS first_ts,
            MAX("DATE & TIME") AS last_ts
        FROM solar_raw
        WHERE "DATE & TIME" >= '{start}' AND "DATE & TIME" < DATE '{end}'::DATE + INTERVAL '1 day'
        GROUP BY 1
        ORDER BY 1
    """).df()


@st.cache_data(ttl=300)
def query_hourly_power(data_dir: str, start: str, end: str) -> pd.DataFrame:
    """Hourly average P[kW] (≈ kWh per hour). Filters partial hours (<30 records)."""
    con = get_connection()
    build_view(con, data_dir)
    return con.execute(f"""
        SELECT
            DATE_TRUNC('hour', "DATE & TIME") AS hour,
            AVG("P[kW]") AS avg_power_kw,
            COUNT(*) AS record_count
        FROM solar_raw
        WHERE "DATE & TIME" >= '{start}'
          AND "DATE & TIME" < DATE '{end}'::DATE + INTERVAL '1 day'
          AND "P[kW]" IS NOT NULL
        GROUP BY 1
        HAVING COUNT(*) >= 30
        ORDER BY 1
    """).df()


@st.cache_data(ttl=300)
def query_hourly_tank_temp(data_dir: str, start: str, end: str) -> pd.DataFrame:
    """Hourly average tank temperature (T2) for overlay on simulation charts."""
    con = get_connection()
    build_view(con, data_dir)
    return con.execute(f"""
        SELECT
            DATE_TRUNC('hour', "DATE & TIME") AS hour,
            AVG("T2[C]") AS avg_tank_temp,
            COUNT(*) AS record_count
        FROM solar_raw
        WHERE "DATE & TIME" >= '{start}'
          AND "DATE & TIME" < DATE '{end}'::DATE + INTERVAL '1 day'
          AND "T2[C]" IS NOT NULL
        GROUP BY 1
        HAVING COUNT(*) >= 30
        ORDER BY 1
    """).df()


@st.cache_data(ttl=300)
def query_latest_tank_temp(data_dir: str) -> tuple[float | None, str | None]:
    """Return (latest T2 reading, timestamp) or (None, None)."""
    con = get_connection()
    build_view(con, data_dir)
    result = con.execute("""
        SELECT "T2[C]", "DATE & TIME"
        FROM solar_raw
        WHERE "T2[C]" IS NOT NULL
        ORDER BY "DATE & TIME" DESC
        LIMIT 1
    """).df()
    if result.empty:
        return None, None
    return float(result.iloc[0, 0]), str(result.iloc[0, 1])


@st.cache_data(ttl=300)
def query_hourly_collector_data(data_dir: str, start: str, end: str) -> pd.DataFrame:
    """Hourly collector data when pump is running (for collector model calibration)."""
    con = get_connection()
    build_view(con, data_dir)
    return con.execute(f"""
        SELECT
            DATE_TRUNC('hour', "DATE & TIME") AS hour,
            AVG("T2[C]") AS avg_T2,
            AVG("T5[C]") AS avg_T5_return,
            AVG("P[kW]") AS avg_power_kw,
            AVG("R1 PWM[%]") AS avg_pump,
            COUNT(*) AS record_count
        FROM solar_raw
        WHERE "DATE & TIME" >= '{start}'
          AND "DATE & TIME" < DATE '{end}'::DATE + INTERVAL '1 day'
          AND "R1 PWM[%]" > 0
          AND "P[kW]" IS NOT NULL
        GROUP BY 1
        HAVING COUNT(*) >= 30
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
