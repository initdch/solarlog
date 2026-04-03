from dataclasses import dataclass, field
from pathlib import Path
import sys

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


@dataclass
class DataConfig:
    directory: str = "./data_files"


@dataclass
class LocationConfig:
    latitude: float = 48.14
    longitude: float = 11.58
    timezone: str = "Europe/Berlin"


@dataclass
class AppConfig:
    clear_day_cloud_cover_max: int = 20


@dataclass
class EkzConfig:
    installation_id: str = ""
    cookie: str = ""
    csrf_token: str = ""
    data_start: str = "2025-09-23"
    data_dir: str = "./ekz_data"


@dataclass
class CollectorConfig:
    eta0_area: float = 0.0    # A * eta_0 * F_hx / 1000 [kW per W/m2] — 0 = auto-calibrate
    a1_area: float = 0.0      # A * a1 * F_hx / 1000 [kW/K] — 0 = auto-calibrate


@dataclass
class TankConfig:
    volume_liters: int = 444
    height_mm: int = 1935
    diameter_mm: int = 733
    n_nodes: int = 4
    node_boundaries: list = field(default_factory=lambda: [0, 479, 854, 1204, 1935])
    target_temp: float = 45.0
    heater_power_kw: float = 3.0
    daily_consumption_kwh: float = 5.0
    heater_start_hour: int = 1
    heater_end_hour: int = 5
    heater_node: int = 2
    coil_nodes: list = field(default_factory=lambda: [3, 4])
    coil_split: list = field(default_factory=lambda: [0.4, 0.6])
    mains_temp: float = 12.0
    t2_sensor_mm: int = 479
    # Legacy field for backward compatibility
    heater_fraction: float = 0.33


@dataclass
class Config:
    data: DataConfig = field(default_factory=DataConfig)
    location: LocationConfig = field(default_factory=LocationConfig)
    app: AppConfig = field(default_factory=AppConfig)
    ekz: EkzConfig = field(default_factory=EkzConfig)
    collector: CollectorConfig = field(default_factory=CollectorConfig)
    tank: TankConfig = field(default_factory=TankConfig)


def _load_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def load_config(path: str = "config.toml") -> Config:
    raw = _load_toml(Path(path))
    p = Path(path)
    local = _load_toml(p.parent / (p.stem + ".local.toml"))

    # local overrides base
    for section in ("data", "location", "app", "ekz", "collector", "tank"):
        raw.setdefault(section, {}).update(local.get(section, {}))

    data = DataConfig(**{k: v for k, v in raw.get("data", {}).items() if k in DataConfig.__dataclass_fields__})
    location = LocationConfig(**{k: v for k, v in raw.get("location", {}).items() if k in LocationConfig.__dataclass_fields__})
    app = AppConfig(**{k: v for k, v in raw.get("app", {}).items() if k in AppConfig.__dataclass_fields__})
    ekz = EkzConfig(**{k: v for k, v in raw.get("ekz", {}).items() if k in EkzConfig.__dataclass_fields__})
    collector = CollectorConfig(**{k: v for k, v in raw.get("collector", {}).items() if k in CollectorConfig.__dataclass_fields__})
    tank = TankConfig(**{k: v for k, v in raw.get("tank", {}).items() if k in TankConfig.__dataclass_fields__})
    return Config(data=data, location=location, app=app, ekz=ekz, collector=collector, tank=tank)
