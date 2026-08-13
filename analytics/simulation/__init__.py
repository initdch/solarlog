"""Physics-based solar thermal simulation package.

Three components: collector, tank, consumption — each independently testable.
"""
from analytics.simulation.collector import CollectorModel
from analytics.simulation.tank import TankModel
from analytics.simulation.consumption import (
    PRESETS,
    SHOWER_KWH,
    BATH_KWH,
    BASELINE_KWH_PER_DAY,
    build_consumption_profile,
    estimate_consumption_from_t2,
)

__all__ = [
    "CollectorModel",
    "TankModel",
    "PRESETS",
    "SHOWER_KWH",
    "BATH_KWH",
    "BASELINE_KWH_PER_DAY",
    "build_consumption_profile",
    "estimate_consumption_from_t2",
]
