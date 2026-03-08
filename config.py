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
class Config:
    data: DataConfig = field(default_factory=DataConfig)
    location: LocationConfig = field(default_factory=LocationConfig)
    app: AppConfig = field(default_factory=AppConfig)


def load_config(path: str = "config.toml") -> Config:
    p = Path(path)
    if not p.exists():
        return Config()
    with open(p, "rb") as f:
        raw = tomllib.load(f)

    data = DataConfig(**{k: v for k, v in raw.get("data", {}).items() if k in DataConfig.__dataclass_fields__})
    location = LocationConfig(**{k: v for k, v in raw.get("location", {}).items() if k in LocationConfig.__dataclass_fields__})
    app = AppConfig(**{k: v for k, v in raw.get("app", {}).items() if k in AppConfig.__dataclass_fields__})
    return Config(data=data, location=location, app=app)
