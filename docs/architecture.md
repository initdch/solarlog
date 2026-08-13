# Architecture — Solar Thermal Analyzer

## Tech Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| UI | Streamlit 1.35+ | Dashboard framework, widgets, caching |
| Charting | Plotly 5.20+ | Interactive time-series and scatter charts |
| Analytics DB | DuckDB 0.10+ | CSV aggregation, multi-day queries |
| DataFrames | pandas 2.1+ | Single-day views, data transformation |
| Statistics | statsmodels 0.14+, numpy | OLS trendlines, linear regression |
| Weather API | Open-Meteo (archive + forecast) | Hourly irradiance, cloud cover, temperature |
| Electricity API | EKZ my.ekz.ch | Daily grid consumption (HT/NT tariffs) |
| Config | TOML (tomllib / tomli) | `config.toml` + `config.local.toml` (gitignored) |

## Architectural Boundary: DuckDB vs pandas

This is the most important constraint in the codebase:

- **DuckDB** (`data/db.py`): All multi-day and aggregation queries. DuckDB reads CSVs directly via glob patterns in the `solar_raw` view. Query functions return small DataFrames (daily/monthly/hourly aggregates). Never loads the full dataset into RAM.
- **pandas** (`data/loader.py`): Strictly for single-day views. `load_day()` returns one 1,440-row DataFrame for a single date. Used only in the Daily View tab.

**Rule:** Never use pandas to read all CSVs at once. All multi-day analysis must go through DuckDB.

## Data Flow

```
CSV files (data_files/YYYY/MM/YYYYMMDD.csv)
    │
    ├─── DuckDB solar_raw view (build_view)
    │       │
    │       ├── query_daily_yield()      → Yield Tracking tab
    │       ├── query_hourly_power()     → Heating Optimizer (model training + past validation)
    │       ├── query_hourly_tank_temp() → Heating Optimizer (actual T2 overlay)
    │       ├── query_latest_tank_temp() → Heating Optimizer (current temp)
    │       ├── query_flow_rate_trend()  → Degradation tab
    │       ├── query_heat_exchanger_trend() → Degradation tab
    │       └── query_collector_yoy()    → Degradation tab
    │
    └─── pandas load_day()
            │
            └── compute_kpis() + charts  → Daily View tab

Open-Meteo API
    │
    ├── fetch_weather()              → Yield Tracking (irradiation overlay)
    │                                → Degradation (clear day classification)
    │                                → Heating Optimizer (model training + forecast)
    └── fetch_irradiance_for_day()   → Daily View (irradiance chart)

EKZ API → ekz_data/consumption.csv
    │
    └── load_ekz_consumption()       → Energy & Grid tab

Heating Optimizer pipeline:
    build_hourly_yield_model()       historical P[kW] + irradiance → linear regression
    forecast_hourly_yield()          forecast irradiance → predicted kW per hour
    simulate_tank_hourly()           two-node model: T_top (heater) + T_bottom (solar/T2)
    recommend_setpoint()             sweep setpoints → minimum safe setpoint
```

## CSV Format — Steca TR A503 TTR

Two file formats coexist:

| Property | Old format (.csv) | New format (.CSV) |
|----------|-------------------|-------------------|
| Delimiter | `,` (comma) | `;` (semicolon) |
| Timestamp | `YYYY-MM-DD HH:MM:SS` | `YYYY-MM-DD HH:MM` |
| Extension | lowercase `.csv` | uppercase `.CSV` |
| Source | Manual export | Direct from device |

Both formats share the same columns. The `build_view()` function creates a UNION ALL of two `read_csv()` calls split by file extension.

**Special handling:**
- `P[kW]` and `p[bar]` use European comma decimal in quoted fields: `"1,6"` → 1.6. DuckDB: `decimal_separator=','`. pandas: `str.replace(",", ".")`.
- `Err` strings in sensor columns → NULL. DuckDB: `nullstr='Err'`. pandas: `na_values=["Err"]`.
- `Qday[kWh]` resets at midnight. Daily yield = `MAX(Qday) - MIN(Qday)`. Resolution is 1 kWh; sub-1kWh days use P[kW] integration fallback.

## Column Rename Map

| CSV Header | Internal Name | Description |
|------------|---------------|-------------|
| `DATE & TIME` | `timestamp` | Measurement timestamp |
| `T1[C]` | `T_collector` | Collector temperature |
| `T2[C]` | `T_tank` | Tank temperature (bottom, T2 sensor) |
| `T4[C]` | `T_flow` | Flow temperature |
| `T5[C]` | `T_return` | Return temperature |
| `V'[l/min]` | `flow_rate` | Flow rate |
| `P[kW]` | `power_kw` | Thermal power |
| `Qday[kWh]` | `Qday` | Cumulative daily energy (resets at midnight) |
| `Qsum[kWh]` | `Qsum` | Lifetime cumulative energy |
| `R1 PWM[%]` | `pump_speed` | Pump speed (0–100%) |

## DuckDB View: `solar_raw`

Created by `build_view(con, data_dir)` with `CREATE OR REPLACE VIEW`. Union of old and new CSV formats. Column names retain original CSV headers (double-quoted in SQL: `"P[kW]"`, `"DATE & TIME"`).

Key DuckDB SQL notes:
- `DATE_TRUNC('day', ...)` — single quotes around interval unit
- `decimal_separator=','` in `read_csv()` for European number format
- `nullstr='Err'` to handle error values

## Directory Roles

```
data/           Data access layer (DuckDB + pandas)
  db.py           DuckDB connection, view builder, all aggregate queries
  loader.py       pandas single-day CSV loader, file finder

analytics/      Business logic (no Streamlit UI code)
  daily.py        KPI computation from daily DataFrame
  yield_tracking.py  Daily/monthly/yearly yield aggregation
  degradation.py  Three degradation signal metrics
  weather.py      Open-Meteo API client (archive + forecast)
  ekz.py          EKZ electricity data (API sync + local storage)
  heating.py      Yield prediction model, two-node tank simulation, setpoint optimizer

ui/             Streamlit rendering (no raw data access)
  sidebar.py      Sidebar controls, location search, returns state dict
  tab_daily.py    Daily View tab
  tab_yield.py    Yield Tracking tab
  tab_degradation.py  Degradation Signals tab
  tab_ekz.py      Energy & Grid tab
  tab_heating.py  Heating Optimizer tab
```

## Caching Strategy

| Decorator | TTL | Used On |
|-----------|-----|---------|
| `@st.cache_resource` | permanent | DuckDB connection |
| `@st.cache_data(ttl=300)` | 5 min | All DuckDB queries, EKZ data load, single-day pandas load |
| `@st.cache_data(ttl=3600)` | 1 hour | Weather API, heating yield model |
| `@st.cache_data(ttl=86400)` | 24 hours | Geocoding (Nominatim) |

Cache keys include `data_dir`, `start`, `end` as strings to ensure proper invalidation.

## Tank Thermal Model (Two-Node Stratification)

```
┌─────────────┐
│   TOP       │  ← Electric heater (heater_fraction ~33% of volume)
│   node      │  ← Hot water drawn from here
├─────────────┤
│   BOTTOM    │  ← Solar heats this (return enters here)
│   node      │  ← Cold mains refills here
│             │  ← T2 sensor location
└─────────────┘
```

- **Heater** only warms top node (small thermal mass → fast response)
- **Solar** heats bottom node; buoyancy mixing equalizes when bottom > top
- **Consumption** draws from top; bottom water rises to replace; mains enters bottom
- **Stable stratification** (hot on top): near-zero downward mixing (k_conduction = 0.01)
- **Unstable** (bottom > top): instant full mixing
- **T2 sensor** at bottom: actual T2 data validates simulated T_bottom
