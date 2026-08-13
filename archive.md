# Solar Thermal Analyzer — Completed Tasks

## 2026-03-30: Physics-Based Simulation (PRD Implementation)

- Created `analytics/simulation/` package (collector.py, tank.py, consumption.py)
- Implemented 4-node stratified tank model (TRNSYS-style, WASol 510-2 geometry)
- Implemented EN 12975 lumped collector model with auto-calibration
- Extracted consumption module from heating.py
- Updated config.py with CollectorConfig and expanded TankConfig
- Updated config.toml with actual WASol 510-2 specs (444L, port positions)
- Added query_hourly_collector_data() to data/db.py
- Converted heating.py to facade delegating to simulation package
- Created 4 sub-tabs: Full Simulation, Collector, Tank, Consumption playgrounds
- Updated PRD with actual tank dimensions from manual
