# PRD: Physics-Based Solar Thermal Simulation

**Status**: Draft
**Date**: 2026-03-30
**Author**: Auto-generated from research + user input

---

## 1. Problem Statement

The current simulation in `analytics/heating.py` has two fundamental flaws:

1. **Solar prediction ignores tank temperature** — uses `P[kW] = slope x irradiance + intercept` (a black-box regression). A hot tank (T_bottom=70C) gets the same predicted yield as a cold one (30C). In reality, collector efficiency drops significantly at higher temperatures because thermal losses from the collector to the environment increase with temperature.

2. **Two-node tank model is too coarse** — only top/bottom split. Consumption draws and solar heat input interact unrealistically. The real tank has continuous stratification; a 2-node model causes artificial mixing and oversimplified dynamics.

### Goal

Replace the monolithic simulation with **three clean physics components** grounded in established thermal engineering standards:

| Component | Model basis | Key improvement |
|-----------|------------|-----------------|
| Solar Collector | EN 12975 / Hottel-Whillier | Temperature-dependent efficiency |
| Tank (Boiler) | Multi-node stratified (TRNSYS-style) | 4-node stratification with proper conduction/mixing |
| Hot Water Usage | Preset profiles + T2 estimation | Cleaner separation, interactive debugging |

Each component should be independently testable and tunable via an interactive playground in the UI.

---

## 2. System Description

### Tank: Weishaupt WASol 510-2

Bivalent solar hot water tank with two smooth-tube heat exchangers (only lower one used for solar; upper HX port has electric heater installed instead).

**Key dimensions** (all heights relative to 15mm foot screws):

| # | Description | Height (mm) | Height % |
|---|------------|-------------|----------|
| 12 | Warmwasser G1 (hot water outlet) | 1827 | 94% |
| 11 | Vorlauf Warmeerzeuger G1 (upper HX flow) -- UNUSED | 1401 | 72% |
| 10 | Zirkulation G3/4 | 1204 | 62% |
| 5 | Fuhlerhulse oben (upper sensor) -- not connected | 1071 | 55% |
| 9 | Rucklauf Warmeerzeuger G1 (upper HX return) -- ELECTRIC HEATER | 1001 | 52% |
| 8 | Vorlauf Solar G1 (solar flow, hot glycol IN) | 854 | 44% |
| 4 | Fuhlerhulse unten (T2 sensor) | 479 | 25% |
| 7 | Rucklauf Solar G1 (solar return, cold glycol OUT) | 216 | 11% |
| 6 | Trinkwasser G1 (cold mains inlet) | 115 | 6% |
| 13 | Total height | 1935 | 100% |
| 14 | Outer diameter (Deckel) | 733 | -- |

**Specs**: Volume = **444 L**, Lower HX (solar) content: 15.3 L, Upper HX: 11.2 L (unused).

### System diagram

```
                          Irradiance (G) [W/m2]
                          |
                    +-----v-----+
                    | COLLECTOR  |  T1 sensor (absorber)
                    | flat-plate |  T_amb from weather API
                    +--+-----+--+
                  T4   |     |  T5
                (flow) |     | (return)
                  hot  |     |  cold
                       v     ^
    1935mm +--------------------+
           |  Node 1 (TOP)      |  <-- Hot water outlet (1827mm)
           |  168 L (38%)       |
    1204mm |--------------------|
           |  Node 2 (UPPER)    |  <-- Electric heater (~1001mm port)
           |   80 L (18%)       |
     854mm |--------------------|
           |  Node 3 (MID-LOW)  |  <-- Solar coil top (854mm, hot glycol in)
           |   86 L (19%)       |  <-- T2 sensor (479mm) at boundary
     479mm |--------------------|
           |  Node 4 (BOTTOM)   |  <-- Solar coil bottom (216mm, cold glycol out)
           |  110 L (25%)       |  <-- Cold mains inlet (115mm)
       0mm +--------------------+
                       ^
                  Cold mains (12C)
```

### System parameters

| Parameter | Value | Source |
|-----------|-------|--------|
| System type | Indirect (glycol loop + coil HX in tank) | User confirmed |
| Tank model | Weishaupt WASol 510-2 | Manual |
| Tank volume | 444 L | Manual (Trinkwasser) |
| Collector type | Flat-plate, specs unknown | Calibrate from data |
| Electric heater | 3 kW, node 1 (top) | config.toml |
| Heater window | 01:00-05:00 | config.toml |
| Household | 2 persons | User confirmed |
| Daily hot water | ~100 L at 40C, ~3-4 kWh/day | EN 16147 "M" cycle |
| Mains temperature | 12C (seasonal: 5-18C) | config.toml |
| Tank ambient | 20C (indoor) | Assumed |

### Available sensor data (per minute, from Steca TR A503 TTR CSV files)

| Column | Description | Used for |
|--------|-------------|----------|
| `T1[C]` | Collector absorber temperature | Pump control, stagnation detection |
| `T2[C]` | Tank bottom temperature | **Primary validation sensor**, consumption estimation |
| `T4[C]` | Flow temperature (hot from collector) | Collector mean temp calibration |
| `T5[C]` | Return temperature (cold to collector) | Collector inlet temp, calibration |
| `V'[l/min]` | Flow rate through collector loop | Pump status proxy |
| `P[kW]` | Solar power (controller-computed) | Calibration target |
| `R1 PWM[%]` | Pump speed (0-100%) | Pump on/off detection |
| `Qday[kWh]` | Cumulative daily yield | Cross-validation |

### Available weather data (from Open-Meteo API, hourly)

| Field | Description | Used for |
|-------|-------------|----------|
| `shortwave_radiation` | Global horizontal irradiance [W/m2] | Collector model input (G) |
| `temperature_2m` | Ambient air temperature [C] | Collector model input (T_amb) |
| `cloud_cover` | Cloud cover [%] | Display only |

---

## 3. Component 1: Solar Collector

### 3.1 Physics model

**Reference**: EN 12975 / ISO 9806 (European solar collector test standard), Hottel-Whillier-Bliss equation (Duffie & Beckman, "Solar Engineering of Thermal Processes").

The collector's useful heat output depends on irradiance AND the temperature difference between the fluid and ambient air:

```
Q_useful = A x [eta_0 x G  -  a1 x (T_m - T_amb)  -  a2 x (T_m - T_amb)^2]
```

Where:
- `A` = collector aperture area [m2]
- `eta_0` = optical efficiency (zero-loss, at normal incidence) [-]
- `a1` = first-order heat loss coefficient [W/m2K]
- `a2` = second-order heat loss coefficient [W/m2K2]
- `G` = global irradiance on collector plane [W/m2]
- `T_m` = mean fluid temperature = (T_flow + T_return) / 2 = (T4 + T5) / 2 [C]
- `T_amb` = ambient air temperature [C]

**Typical flat-plate collector values** (source: SPF Rapperswil database):

| Parameter | Budget | Good (selective) | High-end |
|-----------|--------|-----------------|----------|
| eta_0 | 0.70-0.75 | 0.78-0.82 | 0.80-0.85 |
| a1 [W/m2K] | 5.0-7.0 | 3.5-4.5 | 2.5-3.5 |
| a2 [W/m2K2] | 0.01-0.02 | 0.01-0.02 | 0.005-0.015 |
| Area per panel [m2] | 2.0-2.5 | 2.0-2.5 | 2.0-2.5 |

### 3.2 Lumped calibration approach

Since the system is indirect (heat exchanger coil), and the collector datasheet is unavailable, we calibrate **lumped parameters** that absorb collector area + optical efficiency + HX losses into two coefficients:

```
Q_to_tank [kW] = max(0,  c1 x G  -  c2 x (T_in - T_amb))
```

- `c1 = A x eta_0 x F_hx / 1000` -- lumped optical gain [kW per W/m2]
- `c2 = A x a1 x F_hx / 1000` -- lumped thermal loss [kW/K]
- `T_in` = collector inlet temperature (T5 from real data, or T_bottom in simulation)
- `F_hx` = heat exchanger penalty factor (~0.90-0.95), absorbed into c1/c2

We drop the a2 quadratic term -- it's small for flat plates and we have limited data for a 3-parameter fit.

### 3.3 Calibration method

**Training data**: hourly records where pump is running (R1 PWM > 0), from DuckDB:

```sql
SELECT DATE_TRUNC('hour', "DATE & TIME") AS hour,
       AVG("T2[C]") AS avg_T2,
       AVG("T5[C]") AS avg_T5_return,
       AVG("P[kW]") AS avg_power_kw,
       AVG("R1 PWM[%]") AS avg_pump,
       COUNT(*) AS record_count
FROM solar_raw
WHERE "R1 PWM[%]" > 0 AND "P[kW]" IS NOT NULL
GROUP BY 1 HAVING COUNT(*) >= 30
```

**Regression**: two-variable linear regression via `np.linalg.lstsq`:
- Features: `G` (from weather), `T_in - T_amb` (T5 from CSV, temperature_2m from weather)
- Target: `P[kW]` (measured useful heat delivered to tank)
- Filters: G > 50 W/m2, pump on, daytime hours
- Minimum: 50 valid data points
- **Fallback**: if T5 data is sparse or T_amb unavailable, fall back to single-variable regression (current behavior)

### 3.4 Pump control

The Steca TR A503 uses differential hysteresis control:

```
if T_collector - T_tank > dT_on:   pump ON    (dT_on ~ 7-8 K)
if T_collector - T_tank < dT_off:  pump OFF   (dT_off ~ 3-4 K)
```

**In simulation**: we do not model pump speed explicitly. The `max(0, ...)` clamp on Q_useful naturally produces zero output when losses exceed gains (i.e., when the controller would stop the pump).

### 3.5 Key improvement

| Scenario | Current model | New model |
|----------|--------------|-----------|
| Morning, tank at 30C, G=800 | P = 2.1 kW | P = c1 x 800 - c2 x 10 = ~2.0 kW |
| Afternoon, tank at 70C, G=800 | P = 2.1 kW (same!) | P = c1 x 800 - c2 x 50 = ~1.2 kW |

The new model naturally reduces afternoon yield as the tank heats up, matching observed behavior.

---

## 4. Component 2: Tank (4-Node Stratified Model)

### 4.1 Node layout (based on WASol 510-2 port positions)

| Node | Height range | Height % | Volume | Role |
|------|-------------|----------|--------|------|
| 1 | 1204-1935 mm | 62-100% | 168 L (38%) | Hot water outlet (1827mm) |
| 2 | 854-1204 mm | 44-62% | 80 L (18%) | Electric heater (~1001mm port) |
| 3 | 479-854 mm | 25-44% | 86 L (19%) | Solar coil top (hot glycol entry at 854mm) |
| 4 | 0-479 mm | 0-25% | 110 L (25%) | Solar coil bottom (return at 216mm), mains inlet (115mm) |

**T2 sensor** at 479mm sits at the node 3/4 boundary. Validates node 4 top temperature.
Node volumes are unequal. Effective volume per mm: 444L / 1935mm = 0.229 L/mm.

**Reference**: TRNSYS Type 534 (multi-node stratified tank), Duffie & Beckman Ch. 8.

**Why 4 nodes**: We only have T2 (bottom) for validation. More than 4-5 nodes risks over-parameterization with diminishing returns. 4 nodes gives us: heater zone, transition, and two coil zones -- enough to model stratification effects without noise.

### 4.2 Per-node energy balance

For each timestep (dt = 1 hour), for each node i:

```
T_i(t+1) = T_i(t) + dt / C_i x [Q_solar,i + Q_heater,i - Q_loss,i + Q_cond,i - Q_draw,i]
```

Where:
- `C_i = V_node x 4.186 / 3600` [kWh/K] -- thermal capacity of node
- For 112.5 L: `C_i = 112.5 x 4.186 / 3600 = 0.131 kWh/K`

### 4.3 Physics terms

#### (a) Standby losses -- Newton's cooling per node

```
Q_loss,i = UA_i x (T_i - T_amb_tank)
```

- **Calibrated total**: from Aug 14-15 2025 cooling curve (no consumption, no heater):
  - k_standby = 0.0085 K/hour/K of deltaT
  - At T=80C (deltaT=60K): loss = 0.51 K/h for entire tank
  - Q = 0.51 x (450 x 4.186/3600) = 0.27 kW
  - **UA_total = 0.27 kW / 60 K = 0.0045 kW/K = 4.5 W/K**
- Distribute proportionally to surface area (end nodes get slightly more for top/bottom caps)
- `T_amb_tank` = 20C (indoor ambient where tank is located)

#### (b) Inter-node conduction

```
Q_cond,i->i+1 = k_eff x A_cross / dx x (T_i - T_{i+1})
```

- `k_eff` = 1.0 W/(m*K) -- effective thermal conductivity (pure water ~0.6 + wall/edge effects)
- `A_cross` = 0.33 m2 (for D=0.65m cylindrical tank)
- `dx` = 0.375 m (1.5m total height / 4 nodes)
- **Conductance between adjacent nodes: ~0.88 W/K**

#### (c) Buoyancy-driven mixing (inversion algorithm)

After computing new temperatures for all nodes, scan from bottom to top:

```
repeat:
    for i = N down to 2:
        if T_i > T_{i-1}:                    # temperature inversion
            T_mixed = (m_i x T_i + m_{i-1} x T_{i-1}) / (m_i + m_{i-1})
            T_i = T_{i-1} = T_mixed
until no inversions remain
```

This is the standard TRNSYS algorithm. With equal node masses, `T_mixed = (T_i + T_{i-1}) / 2`.

#### (d) Solar heat input -- distributed to coil nodes

The immersed coil spans nodes 3 and 4. Heat from the glycol is distributed:

```
Q_solar,3 = 0.4 x Q_collector    (upper coil -- glycol enters hot here)
Q_solar,4 = 0.6 x Q_collector    (lower coil -- glycol has cooled, less heat transfer)
```

The 40/60 split reflects counterflow heat exchange: the glycol enters the coil at the top of the coil zone (node 3 boundary) and exits at the bottom. More heat is delivered near the entry where the temperature difference is largest.

#### (e) Electric heater -- node 1 only

```
if heater_active(hour) and T_1 < setpoint:
    Q_heater = min(heater_power_kw, (setpoint - T_1) x C_1)
    T_1 += Q_heater / C_1
```

Heater window: configurable (default 01:00-05:00), with wrap-around support.

#### (f) Consumption -- plug-flow displacement model

Hot water is drawn from the top. Each layer shifts upward. Cold mains enters at the bottom.

```
liters_drawn = consumption_kwh / (4.186/3600 x (T_1 - T_mains))
frac = min(liters_drawn / V_node, 1.0)

T_1 = T_1 x (1 - frac) + T_2 x frac    # top gets water from node 2
T_2 = T_2 x (1 - frac) + T_3 x frac    # node 2 gets water from node 3
T_3 = T_3 x (1 - frac) + T_4 x frac    # node 3 gets water from node 4
T_4 = T_4 x (1 - frac) + T_mains x frac # bottom gets cold mains
```

This is a simplified plug-flow model. For large draws (frac > 1.0), the entire tank volume is displaced and all nodes approach mains temperature.

### 4.4 Output per hour

```
datetime, T_1, T_2, T_3, T_4, heater_kw, solar_kw, consumption_kw
```

**Chart mapping**: T_1 = "Top (hot water)", T_4 = "Bottom (T2 sensor)". Actual T2 overlay validates T_4.

---

## 5. Component 3: Hot Water Consumption

### 5.1 Preset profiles

Based on 2-person household (~EN 16147 "M" cycle baseline):

| Preset | Showers AM | Showers PM | Bath | Approx daily kWh |
|--------|-----------|-----------|------|-------------------|
| Away | 0 | 0 | 0 | 0.5 (baseline only) |
| Light | 1 | 0 | 0 | 2.0 |
| Normal | 2 | 1 | 0 | 5.0 |
| Heavy | 2 | 0 | 1 | 7.5 |

**Energy constants**: Shower = 1.5 kWh (~35L at 40C), Bath = 4.0 kWh (~100L at 40C), Baseline = 0.5 kWh/day spread evenly across 24 hours.

### 5.2 T2-based consumption estimation (past dates)

For past dates with actual sensor data, estimate consumption from T2 temperature drops during non-solar hours:

1. Filter: only hours where solar_kw < 0.1 (pump off, no solar interference)
2. Subtract expected standby loss: `standby = 0.0085 x (T_prev - 20.0)` K/hour
3. Remaining drop attributed to cold mains inflow:
   ```
   drop = T_prev - T_curr - standby_loss
   if drop > 0.5 K:
       mix_frac = drop / (T_prev - T_mains)
       liters_drawn = mix_frac x V_bottom
       consumption_kwh = liters_drawn x 4.186/3600 x (T_prev - T_mains)
   ```
4. Returns **per-day profiles** (dict of date -> 24-hour kWh list), not averaged across days

### 5.3 Future: mixing valve model (deferred)

A more physical model would specify draws in liters at a use temperature (e.g., 40C), and compute the hot water fraction needed based on current tank top temperature:

```
hot_fraction = (T_use - T_mains) / (T_top - T_mains)
energy_drawn = liters x hot_fraction x 4.186/3600 x (T_top - T_mains)
```

This means less energy is drawn when the tank is very hot. **Deferred to a future iteration** -- the current kWh-based approach is simpler and sufficient.

---

## 6. User Interface

### 6.1 Tab structure

The Heating Optimizer tab is restructured into 4 sub-tabs:

```
Heating Optimizer
|-- Full Simulation    (current view, upgraded to 4-node + collector model)
|-- Collector          (interactive collector playground)
|-- Tank               (interactive tank playground)
+-- Consumption        (interactive consumption playground)
```

### 6.2 Sub-tab: Full Simulation

The existing simulation view, upgraded:
- **Temperature chart**: 4 node temperature lines (T_1 through T_4) instead of 2. T_4 validated against actual T2 overlay (purple dotted).
- **Collector info**: metric showing "Collector: HW (c1=X, c2=Y, R2=Z)" or "Linear fallback" when insufficient data.
- **Everything else unchanged**: date picker, setpoint slider, consumption presets, recommendation chart, extended multi-day view.

### 6.3 Sub-tab: Collector Playground

**Purpose**: Understand and tune collector behavior in isolation.

**Top section -- Calibration results**:
- Metrics row: c1, c2, R2, number of training data points
- Scatter plot: actual P[kW] vs predicted P[kW], color-coded by (T_in - T_amb)

**Middle section -- Efficiency curve**:
- X-axis: (T_m - T_amb) / G [K*m2/W] (reduced temperature)
- Y-axis: efficiency eta
- Calibrated curve overlaid with actual hourly data points
- Density shading showing where collector operates most hours

**Bottom section -- Interactive tuning**:
- Sliders: `G` (0-1200 W/m2), `T_in` (10-90C), `T_amb` (-10 to 40C)
- Override sliders: `c1`, `c2` (initialized to auto-calibrated values)
- Real-time output: Q_useful [kW] and efficiency [%]
- Chart: family of Q_useful vs G curves at T_in = 20, 40, 60, 80C (current T_in highlighted)

### 6.4 Sub-tab: Tank Playground

**Purpose**: Understand tank stratification by running the tank model with manual inputs.

**Left column -- Inputs**:
- Initial temperature per node: 4 number inputs (T_1 through T_4)
- Solar input: slider 0-5 kW
- Heater: on/off toggle + setpoint slider
- Consumption: slider 0-5 kWh/hour
- Mains temp: number input
- "Step 1 hour" button + "Run 24h" button

**Right column -- Outputs**:
- Node temperature bar chart: vertical bars showing temperature per node (color gradient: blue=cold, red=hot)
- Energy balance breakdown: horizontal stacked bar (Q_solar, Q_heater, Q_loss, Q_cond, Q_draw)
- After "Run 24h": line chart of all 4 node temperatures over 24 hours
- If past date selected: overlay actual T2 on node 4 for validation

### 6.5 Sub-tab: Consumption Playground

**Purpose**: Compare and understand consumption profiles.

**Top section -- Profile builder**:
- Preset selector (Away / Light / Normal / Heavy)
- Manual override: per-hour number inputs or sliders (24 values)
- Shower/bath count controls

**Middle section -- 24h profile chart**:
- Bar chart: kWh per hour for the active profile
- If past date with T2 data available: overlay T2-estimated profile as a second bar series
- Total daily kWh metric

**Bottom section -- Comparison table**:
- Side-by-side: preset profile vs T2-estimated profile (for past dates)
- Difference column highlighting hours where model disagrees with reality

---

## 7. Technical Design

### 7.1 File structure

```
analytics/
  heating.py                  -> slim orchestration facade (~120 lines)
                                 keeps existing function signatures for backward compat
  simulation/
    __init__.py               -> re-exports all public classes/functions
    collector.py              -> CollectorModel + calibration (~100 lines)
    tank.py                   -> TankModel with N-node energy balance (~120 lines)
    consumption.py            -> profiles, presets, T2 estimation (~110 lines)
data/
  db.py                       -> add query_hourly_collector_data()
config.py                     -> add CollectorConfig, update TankConfig
config.toml                   -> add [collector] section, update [tank]
ui/
  tab_heating.py              -> sub-tab layout, full simulation view
  tab_heating_collector.py    -> collector playground (~120 lines)
  tab_heating_tank.py         -> tank playground (~100 lines)
  tab_heating_consumption.py  -> consumption playground (~80 lines)
```

### 7.2 Config changes

```toml
[collector]
# Leave at 0.0 to auto-calibrate from historical data
eta0_area = 0.0    # A x eta_0 x F_hx / 1000 [kW per W/m2]
a1_area   = 0.0    # A x a1 x F_hx / 1000 [kW/K]

[tank]
# Weishaupt WASol 510-2 -- specs from manual
volume_liters     = 444            # actual tank volume (manual: 444 L Trinkwasser)
height_mm         = 1935           # total tank height
diameter_mm       = 733            # outer diameter (Deckel)
n_nodes           = 4              # stratification layers
node_boundaries   = [0, 479, 854, 1204, 1935]  # [mm] aligned with physical ports
target_temp       = 45.0
heater_power_kw   = 3.0
heater_start_hour = 1
heater_end_hour   = 5
heater_node       = 2              # node with heater (upper HX port at ~1001mm)
coil_nodes        = [3, 4]         # solar coil spans these nodes (216-854mm)
coil_split        = [0.4, 0.6]     # heat distribution: 40% upper coil, 60% lower
mains_temp        = 12.0
t2_sensor_mm      = 479            # Fuhlerhulse unten -- at node 3/4 boundary
```

### 7.3 Backward compatibility

`heating.py` retains all existing function signatures as a facade. Imports from `ui/tab_heating.py` remain unchanged. Internally, it delegates to `simulation/` modules.

---

## 8. Implementation Steps

1. **Create `analytics/simulation/` package** -- extract consumption functions (zero-risk move)
2. **Create `TankModel`** -- 4-node model in `tank.py`, verify behavior against current 2-node
3. **Create `CollectorModel`** -- calibration + prediction in `collector.py`
4. **Wire into `heating.py`** -- facade delegates to new modules; collector model feeds solar output dynamically based on tank temperature
5. **Config updates** -- add CollectorConfig, update TankConfig with node-based parameters
6. **Build UI sub-tabs** -- collector playground, tank playground, consumption playground
7. **Upgrade Full Simulation** -- 4-node temperature chart, collector model indicator

---

## 9. Verification

| Check | Expected behavior |
|-------|-------------------|
| Collector calibration | c1 close to current regression slope; c2 positive and 0.01-0.05 kW/K |
| Past-date T2 match | T_4 (bottom node) tracks actual T2; afternoon plateau matches better |
| Heater isolation | During heater window, T_1 rises; T_4 barely moves |
| Consumption cascade | After shower: T_1 drops sharply, T_2 moderately, T_3/T_4 dip from mains |
| Playgrounds | Each sub-tab renders, sliders update charts in real-time |
| Full app | `streamlit run app.py` -- all 5 top-level tabs + 4 heating sub-tabs functional |

---

## 10. References

- **EN 12975 / ISO 9806:2017** -- European test standard for solar thermal collectors
- **Duffie & Beckman** -- "Solar Engineering of Thermal Processes" (4th ed., Wiley) -- Chapters 6-8
- **TRNSYS Type 534** -- Multi-node stratified tank model documentation (TESS library)
- **SPF Rapperswil** -- Solar collector test database (www.spf.ch)
- **EN 16147** -- Tapping cycles for DHW testing (S/M/L/XL)
- **SIA 385/2** -- Swiss standard for hot water systems in buildings
- **Kalogirou** -- "Solar Energy Engineering" (2nd ed., Academic Press)

---

## Appendix A: Calibrated Constants (Current)

| Constant | Value | Source | Used in |
|----------|-------|--------|---------|
| k_standby | 0.0085 K/h/K | Aug 14-15 2025 cooling curve | Tank standby loss |
| k_conduction | 0.01 kWh/K/h | Estimated (stable stratification) | Tank inter-node conduction |
| T_amb_tank | 20 C | Assumed indoor | Tank standby loss |
| UA_total | 4.5 W/K | Derived from k_standby | Tank standby loss |
| SHOWER_KWH | 1.5 kWh | ~35L at 40C from 12C mains | Consumption profile |
| BATH_KWH | 4.0 kWh | ~100L at 40C from 12C mains | Consumption profile |
| BASELINE_KWH | 0.5 kWh/day | Assumed baseline | Consumption profile |

## Appendix B: Typical Flat-Plate Collector Values (for reference)

| Parameter | Range | Unit | Notes |
|-----------|-------|------|-------|
| eta_0 | 0.75-0.85 | - | Zero-loss efficiency |
| a1 | 3.0-5.0 | W/m2K | First-order heat loss |
| a2 | 0.005-0.020 | W/m2K2 | Second-order heat loss |
| F_R | 0.90-0.97 | - | Heat removal factor |
| F_hx | 0.85-0.95 | - | Heat exchanger penalty |
| Area per panel | 2.0-2.5 | m2 | Aperture area |
| Stagnation temp | 180-220 | C | At G=1000, typical |
| dT_on (pump start) | 7-8 | K | Differential controller |
| dT_off (pump stop) | 3-4 | K | Differential controller |
