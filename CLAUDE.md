# CLAUDE.md — Solar Thermal Analyzer

## Project overview

Streamlit dashboard for Steca TR A503 TTR solar thermal controller data. DuckDB reads CSVs directly; only single-day views use pandas. Never load the full dataset into RAM.

## Running the app

```bash
streamlit run app.py
```

## Key architectural constraints

- **DuckDB for aggregations** — all multi-day queries go through `data/db.py`. Use `build_view(con, data_dir)` then query `solar_raw`.
- **pandas only for daily view** — `data/loader.py:load_day()` returns a 1,440-row DataFrame for a single day.
- **No full dataset loads** — do not use pandas to read all CSVs at once.
- **Caching** — `@st.cache_resource` on the DuckDB connection, `@st.cache_data(ttl=300)` on all query functions. Cache keys include `data_dir`, `start`, `end` as strings.

## CSV format quirks

- `P[kW]` and `p[bar]` use European comma decimal inside quoted fields: `"1,6"` → 1.6
  - DuckDB: `decimal_separator=','` in `read_csv()`
  - pandas: `str.replace(",", ".")` before `pd.to_numeric()`
- `Err` strings → NULL: `nullstr='Err'` (DuckDB) / `na_values=["Err"]` (pandas)
- `Qday[kWh]` resets at midnight — daily yield = `MAX(Qday) - MIN(Qday)` per day
- `R1 PWM[%]` = pump speed (0–100%), `R1[%]` = relay duty cycle (different signals)

## DuckDB SQL notes

- Interval unit in `DATE_TRUNC` must use single quotes: `DATE_TRUNC('day', ...)`
- Column names with special characters use double-quotes: `"DATE & TIME"`, `"V'[l/min]"`
- View is named `solar_raw`; recreated with `CREATE OR REPLACE` on each `build_view()` call

## File structure

```
app.py                      # Entry point, tab layout
config.py                   # Config dataclasses + TOML loader
config.toml                 # User-editable settings
data/
  db.py                     # DuckDB connection + query helpers
  loader.py                 # pandas single-day loader
analytics/
  daily.py                  # KPI computation (no DB)
  yield_tracking.py         # Daily/monthly/yearly yield
  degradation.py            # 3 degradation metrics
  weather.py                # Open-Meteo API client
ui/
  sidebar.py                # Returns state dict
  tab_daily.py
  tab_yield.py
  tab_degradation.py
```

## Column rename map

| CSV header | Internal name |
|------------|---------------|
| `DATE & TIME` | `timestamp` |
| `T1[C]` | `T_collector` |
| `T2[C]` | `T_tank` |
| `T4[C]` | `T_flow` |
| `T5[C]` | `T_return` |
| `V'[l/min]` | `flow_rate` |
| `P[kW]` | `power_kw` |
| `Qday[kWh]` | `Qday` |
| `Qsum[kWh]` | `Qsum` |
| `R1 PWM[%]` | `pump_speed` |

## Sample data

`example/sample_different_times_in_day.csv` — 8 rows from 2024-07-09 (4 at midnight, 4 at 13:52 when pump runs). Also copied to `data_files/2024/07/20240709.csv` for testing.

Expected results from sample:
- Yield: 3 kWh (Qday 640 → 643)
- Max power: 1.6 kW
- Peak collector temp: 82 °C
- Max flow rate: 3 l/min

## Dependencies

```
streamlit>=1.35, pandas>=2.1, duckdb>=0.10, plotly>=5.20,
requests>=2.31, statsmodels>=0.14, tomli>=2.0 (Python <3.11 only)
```
