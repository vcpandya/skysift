from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

import yaml
from pydantic import BaseModel

from skysift.models import LightCurve, Target

REGISTRY_YAML = Path(__file__).resolve().parents[3] / "config" / "sources.yaml"


class SourceInfo(BaseModel):
    name: str
    agency: str
    status: str
    n_lightcurves: int
    baseline_days: float
    cadence_min: float
    depth_limit_ppm: float
    fraction_searched: float
    known_candidate_density: float
    est_yield_per_10k: float
    bytes_per_star: int
    seconds_per_star: float
    notes: str = ""

    def cpu_hours_per_10k(self) -> float:
        return self.seconds_per_star * 1e4 / 3600


def load_registry(path: Path = REGISTRY_YAML) -> dict[str, SourceInfo]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {k: SourceInfo(name=k, **v) for k, v in raw.items()}


class Source(Protocol):
    name: str

    def list_targets(self, filter: dict) -> Iterator[Target]: ...
    def fetch_lightcurve(self, target: Target) -> tuple[LightCurve, str, str]: ...
