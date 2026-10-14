from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_YAML = Path(__file__).resolve().parents[2] / "config" / "default.yaml"


class Thresholds(BaseModel):
    sde_min: float = 9.0
    snr_min: float = 7.0
    min_events: int = 3
    duration_ratio_min: float = 0.3
    duration_ratio_max: float = 3.0
    parity_sigma: float = 3.0
    secondary_sigma: float = 3.0
    free_secondary_sigma: float = 5.0
    season_sigma: float = 3.0
    min_period_days: float = 0.5
    max_period_days: float = 200.0

    @model_validator(mode="after")
    def _period_window(self) -> Thresholds:
        if not 0 < self.min_period_days < self.max_period_days:
            raise ValueError(f"period window must satisfy 0 < min_period_days < max_period_days, got "
                             f"{self.min_period_days} to {self.max_period_days}")
        return self


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SKYSIFT_", env_nested_delimiter="__", extra="ignore")

    data_dir: Path = Path("./data")
    blob_url: str = "file://./data/blobs"
    database_url: str = "sqlite:///./data/engine.db"
    workers_io: int = 8
    workers_cpu: int = 2
    retention: str = "stream"
    thresholds: Thresholds = Thresholds()


def load_settings(yaml_path: Path | None = None) -> Settings:
    """YAML gives defaults; environment variables (SKYSIFT_*) override them."""
    path = yaml_path or DEFAULT_YAML
    base = yaml.safe_load(path.read_text()) if path.exists() else {}
    env_only = Settings()  # reads environment
    merged = {**base, **env_only.model_dump(exclude_unset=True)}
    if "thresholds" in base and "thresholds" in env_only.model_dump(exclude_unset=True):
        merged["thresholds"] = {**base["thresholds"], **env_only.model_dump(exclude_unset=True)["thresholds"]}
    return Settings(**merged)
