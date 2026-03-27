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
class Config:
    data: DataConfig = field(default_factory=DataConfig)
    location: LocationConfig = field(default_factory=LocationConfig)
    app: AppConfig = field(default_factory=AppConfig)
    ekz: EkzConfig = field(default_factory=EkzConfig)


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
    for section in ("data", "location", "app", "ekz"):
        raw.setdefault(section, {}).update(local.get(section, {}))

    data = DataConfig(**{k: v for k, v in raw.get("data", {}).items() if k in DataConfig.__dataclass_fields__})
    location = LocationConfig(**{k: v for k, v in raw.get("location", {}).items() if k in LocationConfig.__dataclass_fields__})
    app = AppConfig(**{k: v for k, v in raw.get("app", {}).items() if k in AppConfig.__dataclass_fields__})
    ekz = EkzConfig(**{k: v for k, v in raw.get("ekz", {}).items() if k in EkzConfig.__dataclass_fields__})
    return Config(data=data, location=location, app=app, ekz=ekz)
