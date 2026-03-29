# Module & Function Index — Solar Thermal Analyzer

## `app.py` — Entry Point
| Component | Description |
|-----------|-------------|
| Module-level | Configures Streamlit page, loads config, renders sidebar, creates 5 tabs |

## `config.py` — Configuration
| Function/Class | Description |
|----------------|-------------|
| `DataConfig` | Dataclass: `directory` (CSV root path) |
| `LocationConfig` | Dataclass: `latitude`, `longitude`, `timezone` |
| `AppConfig` | Dataclass: `clear_day_cloud_cover_max` |
| `EkzConfig` | Dataclass: `installation_id`, `cookie`, `csrf_token`, `data_start`, `data_dir` |
| `TankConfig` | Dataclass: `volume_liters`, `target_temp`, `heater_power_kw`, `daily_consumption_kwh`, `heater_start_hour`, `heater_end_hour`, `heater_fraction`, `mains_temp` |
| `Config` | Aggregates all sub-configs |
| `_load_toml(path)` | Reads TOML file, returns empty dict if missing |
| `load_config(path)` | Loads base config + `config.local.toml` overrides, returns `Config` |

## `data/db.py` — DuckDB Queries
| Function | Decorator | Description |
|----------|-----------|-------------|
| `get_connection()` | `@st.cache_resource` | Returns singleton in-memory DuckDB connection |
| `build_view(con, data_dir)` | — | Creates `solar_raw` view via UNION ALL of .csv and .CSV formats |
| `query_daily_yield(data_dir, start, end)` | `@st.cache_data(ttl=300)` | Daily yield via MAX-MIN Qday with P[kW] integration fallback |
| `query_hourly_power(data_dir, start, end)` | `@st.cache_data(ttl=300)` | Hourly average P[kW], filters hours with <30 records |
| `query_hourly_tank_temp(data_dir, start, end)` | `@st.cache_data(ttl=300)` | Hourly average T2[C] for simulation overlay |
| `query_latest_tank_temp(data_dir)` | `@st.cache_data(ttl=300)` | Latest T2 reading + timestamp |
| `query_flow_rate_trend(data_dir, start, end)` | `@st.cache_data(ttl=300)` | Monthly p95 flow rate at full pump speed |
| `query_heat_exchanger_trend(data_dir, start, end)` | `@st.cache_data(ttl=300)` | Monthly median thermal resistance proxy |
| `query_collector_yoy(data_dir, start, end, clear_day_dates)` | `@st.cache_data(ttl=300)` | Peak midday power on clear days by year/month |

## `data/loader.py` — pandas Single-Day Loader
| Function | Decorator | Description |
|----------|-----------|-------------|
| `find_csv_for_date(data_dir, d)` | — | Locates CSV for a date, tries .csv and .CSV in YYYY/MM/ paths |
| `load_day(data_dir, d)` | `@st.cache_data(ttl=300)` | Loads single-day CSV, renames columns, converts types |
| `count_available_files(data_dir, start, end)` | — | Counts CSV files in date range (sampled, capped at 400) |

## `analytics/daily.py` — Daily KPIs
| Function | Description |
|----------|-------------|
| `compute_kpis(df)` | Returns dict: peak_collector_temp, peak_power_kw, daily_yield_kwh, pump_runtime_hours, max_flow_rate |
| `get_active_periods(df)` | Filters DataFrame to rows where pump is running |

## `analytics/yield_tracking.py` — Yield Aggregation
| Function | Description |
|----------|-------------|
| `get_daily_yield(data_dir, start, end)` | Daily yield with partial_day flag (span < 23h) |
| `get_monthly_yield(data_dir, start, end)` | Monthly sum excluding partial days |
| `get_yearly_yield(data_dir, start, end)` | Yearly sum excluding partial days |
| `get_lifetime_total(data_dir)` | Returns max Qsum (lifetime kWh) |

## `analytics/degradation.py` — Degradation Signals
| Function | Description |
|----------|-------------|
| `flow_rate_trend(data_dir, start, end)` | Monthly p95 flow rate (declining → pump/blockage issue) |
| `heat_exchanger_trend(data_dir, start, end)` | Monthly median thermal resistance (increasing → scaling) |
| `collector_yoy(data_dir, start, end, clear_day_dates)` | Peak midday power on clear days (declining → soiling) |

## `analytics/weather.py` — Open-Meteo API
| Function | Decorator | Description |
|----------|-----------|-------------|
| `_fetch_archive(lat, lon, start, end, tz)` | — | Fetches hourly weather from archive API (>5 days ago) |
| `_fetch_forecast(lat, lon, start, end, tz)` | — | Fetches hourly weather from forecast API (recent + future) |
| `fetch_weather(lat, lon, start, end, tz)` | `@st.cache_data(ttl=3600)` | Splits range at archive/forecast boundary, chunks by year. Returns (hourly_df, daily_df) |
| `fetch_irradiance_for_day(lat, lon, d, tz)` | `@st.cache_data(ttl=3600)` | Hourly shortwave_radiation for a single day |
| `classify_clear_days(daily_df, max_cloud_pct)` | — | Returns dates where mean daytime cloud cover <= threshold |

## `analytics/ekz.py` — EKZ Electricity
| Function | Decorator | Description |
|----------|-----------|-------------|
| `_csv_path(data_dir)` | — | Path to local consumption.csv |
| `_read_local(data_dir)` | — | Reads local CSV or returns empty DataFrame |
| `_write_local(data_dir, df)` | — | Writes consumption DataFrame to CSV |
| `_parse_series(values)` | — | Parses API series values into DataFrame |
| `_fetch_from_api(installation_id, cookie, csrf_token, start, end)` | — | Fetches date range from EKZ API, merges HT/NT |
| `sync_ekz(data_dir, installation_id, cookie, csrf_token, data_start)` | — | Syncs missing + re-fetches last 7 days |
| `load_ekz_consumption(data_dir, start, end)` | `@st.cache_data(ttl=300)` | Loads local consumption filtered by date range |
| `ekz_data_status(data_dir)` | — | Returns dict with row count, date range, staleness |

## `analytics/heating.py` — Heating Optimizer
| Function | Decorator | Description |
|----------|-----------|-------------|
| `build_hourly_yield_model(data_dir, lat, lon, tz, lookback_days)` | `@st.cache_data(ttl=3600)` | Fits power_kw ~ shortwave_radiation over historical hours |
| `forecast_hourly_yield(model, lat, lon, tz, days)` | — | Predicts hourly solar power from weather forecast |
| `actual_hourly_data(data_dir, target_date, days)` | — | Loads actual P[kW] and T2 for past dates |
| `estimate_consumption_from_t2(power_df, temp_df, tank_cfg)` | — | Estimates consumption from T2 drops during non-solar hours |
| `build_consumption_profile(showers_morning, showers_evening, baths_evening, ...)` | — | Builds 24h consumption profile from shower/bath counts |
| `_heater_active(h, start, end)` | — | Checks if hour is in heater window (handles wrap-around) |
| `simulate_tank_hourly(forecast_df, consumption_profile, tank_cfg, current_temp, setpoint)` | — | Two-node simulation: T_top (heater/draw) + T_bottom (solar/T2) |
| `recommend_setpoint(forecast_df, consumption_profile, tank_cfg, current_temp, ...)` | — | Sweeps setpoints over first 24h, returns energy + min temp |

## `ui/sidebar.py` — Sidebar
| Function | Decorator | Description |
|----------|-----------|-------------|
| `_geocode(query)` | `@st.cache_data(ttl=86400)` | Nominatim geocoding, returns up to 5 results |
| `render_sidebar(cfg)` | — | Renders sidebar inputs, returns state dict |

## `ui/tab_daily.py` — Daily View Tab
| Function | Description |
|----------|-------------|
| `render_tab_daily(state)` | Main render: KPI cards, date picker, 3 charts |
| `_render_temperature_chart(df)` | Plotly: collector/tank/flow/return temps + pump speed |
| `_render_irradiance_chart(irr)` | Plotly: hourly shortwave radiation |
| `_render_power_flow_chart(df)` | Plotly: power (primary) + flow rate (secondary) |

## `ui/tab_yield.py` — Yield Tracking Tab
| Function | Description |
|----------|-------------|
| `render_tab_yield(state)` | Main render: lifetime metric, granularity selector, chart |
| `_fetch_daily_irradiation(state, start, end)` | Daily irradiation from weather API (Wh/m²) |
| `_render_yield_chart(df, x_col, label, has_partial, irr)` | Plotly bar chart with color coding + optional irradiation line |

## `ui/tab_degradation.py` — Degradation Signals Tab
| Function | Description |
|----------|-------------|
| `render_tab_degradation(state)` | Main render: 3 sub-tabs |
| `_render_flow_rate(data_dir, start, end)` | Bubble chart + trendline |
| `_render_heat_exchanger(data_dir, start, end)` | Line chart + trendline |
| `_render_collector_yoy(data_dir, start, end, lat, lon, tz, threshold)` | YoY peak power on clear days |
| `_add_trendline(fig, x_series, y_series)` | Adds OLS trendline to Plotly figure |

## `ui/tab_ekz.py` — Energy & Grid Tab
| Function | Description |
|----------|-------------|
| `render_tab_ekz(state, cfg)` | Main render: sync, metrics, consumption vs solar, correlation, heater estimate |
| `_merge_solar_ekz(solar_df, ekz_df)` | Merges complete solar days with EKZ consumption |
| `_correlation_and_heater_estimate(merged)` | Pearson r + heater contribution estimate |

## `ui/tab_heating.py` — Heating Optimizer Tab
| Function | Description |
|----------|-------------|
| `render_tab_heating(state, cfg)` | Main render: model metrics, controls, consumption, forecast/simulation, recommendation |
| `_render_hourly_chart(sim, forecast, tank_cfg, setpoint, actual_temp, is_past)` | Plotly subplot: T_top + T_bottom + actual T2, solar, consumption, heater window |
| `_render_extended_chart(sim, tank_cfg, setpoint, actual_temp)` | Continuous multi-day temperature chart |
