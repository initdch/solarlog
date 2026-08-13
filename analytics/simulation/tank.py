"""4-node stratified tank model — TRNSYS-style energy balance.

Node layout for Weishaupt WASol 510-2 (444L):
  Node 1 (top):     1204-1935mm, 168L — hot water outlet
  Node 2 (upper):    854-1204mm,  80L — electric heater (~1001mm port)
  Node 3 (mid-low):  479-854mm,   86L — solar coil top (hot glycol in)
  Node 4 (bottom):     0-479mm,  110L — solar coil bottom, mains inlet, T2 sensor at boundary
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class TankModel:
    """Parameterized N-node tank model."""

    volume_liters: float = 444.0
    node_boundaries: list[float] = field(default_factory=lambda: [0, 479, 854, 1204, 1935])
    heater_node: int = 2          # 1-indexed: node with electric heater
    heater_power_kw: float = 3.0
    heater_start_hour: int = 1
    heater_end_hour: int = 5
    coil_nodes: list[int] = field(default_factory=lambda: [3, 4])
    coil_split: list[float] = field(default_factory=lambda: [0.4, 0.6])
    mains_temp: float = 12.0
    target_temp: float = 45.0
    k_standby: float = 0.0085    # K/h/K — calibrated from Aug 14-15 cooling
    k_eff: float = 1.0           # W/(m·K) — effective thermal conductivity
    T_amb_tank: float = 20.0     # indoor ambient

    def __post_init__(self):
        # node_boundaries is bottom-up: [0, 479, 854, 1204, 1935]
        # We build internal arrays TOP-DOWN: index 0 = top, index N-1 = bottom
        # This way T[0] = Node 1 = top (hot water outlet), T[N-1] = Node N = bottom (mains inlet)
        bounds = self.node_boundaries  # bottom-up
        self.n_nodes = len(bounds) - 1
        total_height = bounds[-1] - bounds[0]

        # Build segments bottom-up, then reverse to get top-down
        heights_bottom_up = [bounds[i + 1] - bounds[i] for i in range(self.n_nodes)]
        self.node_heights = list(reversed(heights_bottom_up))
        self.node_volumes = [self.volume_liters * (h / total_height) for h in self.node_heights]
        self.node_C = [v * 4.186 / 3600 for v in self.node_volumes]  # kWh/K

        # Height ranges for display (top-down): Node 1 = highest segment
        self._height_ranges = []
        for i in range(self.n_nodes):
            # Reversed: internal index 0 = top segment
            orig_idx = self.n_nodes - 1 - i
            lo = bounds[orig_idx]
            hi = bounds[orig_idx + 1]
            self._height_ranges.append((lo, hi))

        # Effective cross-section from total volume / height
        self.A_cross = self.volume_liters * 1e-3 / (total_height * 1e-3)  # m²

        # UA per node (proportional to surface area — simplified)
        UA_total = self.k_standby * self.volume_liters * 4.186 / 3600  # kW/K
        weights = []
        for i in range(self.n_nodes):
            wall = self.node_heights[i]
            cap = 0.5 if i == 0 or i == self.n_nodes - 1 else 0.0
            weights.append(wall + cap * (total_height / self.n_nodes))
        total_w = sum(weights)
        self.node_UA = [UA_total * w / total_w for w in weights]

        # Inter-node conductance [kW/K] — between adjacent nodes (top-down order)
        # Conductance between node i (higher) and node i+1 (lower)
        centers_bottom_up = [(bounds[i] + bounds[i + 1]) / 2 for i in range(self.n_nodes)]
        centers = list(reversed(centers_bottom_up))  # top-down
        self.node_conductance = []
        for i in range(self.n_nodes - 1):
            dx = abs(centers[i] - centers[i + 1]) * 1e-3  # m
            conductance = self.k_eff * self.A_cross / dx / 1000  # kW/K
            self.node_conductance.append(conductance)

    @classmethod
    def from_config(cls, tank_cfg) -> TankModel:
        """Create a TankModel from a TankConfig dataclass."""
        kwargs = {}
        for attr in ("volume_liters", "node_boundaries", "heater_node",
                      "heater_power_kw", "heater_start_hour", "heater_end_hour",
                      "coil_nodes", "coil_split", "mains_temp", "target_temp"):
            if hasattr(tank_cfg, attr):
                kwargs[attr] = getattr(tank_cfg, attr)
        return cls(**kwargs)


def _heater_active(h: int, start: int, end: int) -> bool:
    """Check if hour h falls within the heater window, handling wrap-around."""
    if start <= end:
        return start <= h < end
    return h >= start or h < end


def simulate_tank(
    forecast_df: pd.DataFrame,
    consumption_profile_24h: list[float],
    tank: TankModel,
    initial_temps: list[float] | float,
    heater_setpoint: float,
    per_hour_consumption: list[float] | None = None,
    collector_model=None,
    weather_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Simulate tank temperature hour-by-hour with N-node stratified model.

    Args:
        forecast_df: DataFrame with 'datetime' and 'predicted_kw' columns.
        consumption_profile_24h: 24-element list of kWh per hour (default pattern).
        tank: TankModel instance.
        initial_temps: Starting temperature(s). Float = all nodes same, list = per-node.
        heater_setpoint: Temperature setpoint for electric heater.
        per_hour_consumption: If provided, overrides consumption_profile_24h per row.
        collector_model: If provided, recomputes solar dynamically from weather + tank temp.
        weather_df: Required if collector_model is provided. Has 'shortwave_radiation', 'temperature_2m'.
    """
    n = tank.n_nodes
    if isinstance(initial_temps, (int, float)):
        T = [float(initial_temps)] * n
    else:
        T = [float(t) for t in initial_temps]

    heater_idx = tank.heater_node - 1  # 0-indexed
    rows = []

    for idx, (_, hour_row) in enumerate(forecast_df.iterrows()):
        dt = hour_row["datetime"]
        h = dt.hour if hasattr(dt, "hour") else pd.Timestamp(dt).hour

        # 1. Standby loss — Newton's cooling per node
        for i in range(n):
            T[i] -= tank.node_UA[i] / tank.node_C[i] * max(T[i] - tank.T_amb_tank, 0.0)

        # 2. Electric heater
        heater_kwh = 0.0
        if _heater_active(h, tank.heater_start_hour, tank.heater_end_hour):
            if T[heater_idx] < heater_setpoint:
                energy_needed = (heater_setpoint - T[heater_idx]) * tank.node_C[heater_idx]
                heater_kwh = min(energy_needed, tank.heater_power_kw)
                T[heater_idx] += heater_kwh / tank.node_C[heater_idx]

        # 3. Solar heat input
        if collector_model is not None and collector_model.is_valid and weather_df is not None:
            # Dynamic: recompute solar based on current tank bottom temperature
            w_row = weather_df.iloc[idx] if idx < len(weather_df) else None
            if w_row is not None:
                G = w_row.get("shortwave_radiation", 0.0)
                T_amb = w_row.get("temperature_2m", tank.T_amb_tank)
                T_in = T[n - 1]  # bottom node temperature as collector inlet proxy
                solar_kw = collector_model.predict(G, T_in, T_amb)
            else:
                solar_kw = hour_row.get("predicted_kw", 0.0)
        else:
            solar_kw = hour_row.get("predicted_kw", 0.0)

        # Distribute solar to coil nodes
        for coil_idx, split in zip(tank.coil_nodes, tank.coil_split):
            node_i = coil_idx - 1  # 0-indexed
            if 0 <= node_i < n:
                T[node_i] += (solar_kw * split) / tank.node_C[node_i]

        # 4. Inter-node conduction
        for i in range(n - 1):
            delta = T[i] - T[i + 1]
            transfer_kw = tank.node_conductance[i] * delta
            # Limit transfer to prevent overshoot
            max_transfer = abs(delta) * min(tank.node_C[i], tank.node_C[i + 1]) / 2
            transfer_kw = np.clip(transfer_kw, -max_transfer, max_transfer)
            T[i] -= transfer_kw / tank.node_C[i]
            T[i + 1] += transfer_kw / tank.node_C[i + 1]

        # 5. Buoyancy mixing — resolve temperature inversions
        for _pass in range(n * 2):  # limited passes to prevent infinite loop
            mixed_any = False
            for i in range(n - 1, 0, -1):  # bottom to top
                if T[i] > T[i - 1] + 0.001:  # lower node hotter than upper (with tolerance)
                    T_mixed = (
                        T[i] * tank.node_C[i] + T[i - 1] * tank.node_C[i - 1]
                    ) / (tank.node_C[i] + tank.node_C[i - 1])
                    T[i] = T_mixed
                    T[i - 1] = T_mixed
                    mixed_any = True
            if not mixed_any:
                break

        # 6. Consumption — plug-flow displacement
        if per_hour_consumption is not None and idx < len(per_hour_consumption):
            consumption_kwh = per_hour_consumption[idx]
        else:
            consumption_kwh = consumption_profile_24h[h]

        if consumption_kwh > 0 and T[0] > tank.mains_temp:
            liters_drawn = consumption_kwh / (4.186 / 3600 * max(T[0] - tank.mains_temp, 1.0))
            # Cascade: top water leaves, each node gets water from below, mains enters bottom
            for i in range(n):
                frac = min(liters_drawn / tank.node_volumes[i], 1.0)
                if i < n - 1:
                    T[i] = T[i] * (1 - frac) + T[i + 1] * frac
                else:
                    T[i] = T[i] * (1 - frac) + tank.mains_temp * frac

        # Clamp
        for i in range(n):
            T[i] = max(T[i], 5.0)

        row = {
            "datetime": dt,
            "heater_kw": round(heater_kwh, 2),
            "solar_kw": round(solar_kw, 2),
            "consumption_kw": round(consumption_kwh, 2),
        }
        for i in range(n):
            row[f"T_{i+1}"] = round(T[i], 1)
        rows.append(row)

    return pd.DataFrame(rows)


def recommend_setpoint(
    forecast_df: pd.DataFrame,
    consumption_profile_24h: list[float],
    tank: TankModel,
    initial_temps: list[float] | float,
    sp_min: int = 30,
    sp_max: int = 75,
    collector_model=None,
    weather_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Sweep setpoints over tomorrow (first 24h) and compute heater energy + min temp.

    min_temp uses T_1 — that's where hot water is drawn from.
    """
    tomorrow = forecast_df.head(24)
    if tomorrow.empty:
        return pd.DataFrame()

    w_tomorrow = weather_df.head(24) if weather_df is not None else None

    rows = []
    for sp in range(sp_min, sp_max + 1):
        sim = simulate_tank(
            tomorrow, consumption_profile_24h, tank, initial_temps,
            float(sp), collector_model=collector_model, weather_df=w_tomorrow,
        )
        if sim.empty:
            continue
        rows.append({
            "setpoint": sp,
            "total_heater_kwh": round(sim["heater_kw"].sum(), 1),
            "min_temp": round(sim["T_1"].min(), 1),
        })
    return pd.DataFrame(rows)
