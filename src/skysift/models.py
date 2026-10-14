from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np
from pydantic import BaseModel, Field


class Target(BaseModel):
    source: str
    target_id: str
    ra: float
    dec: float
    gaia_id: str | None = None
    kic: int | None = None
    gmag: float | None = None
    meta: dict = Field(default_factory=dict)


@dataclass
class LightCurve:
    time: np.ndarray          # BKJD or BTJD days
    flux: np.ndarray          # primary flux (PSF for KBonus)
    flux_err: np.ndarray
    quality: np.ndarray       # 0 = good
    quarter: np.ndarray       # quarter / sector id per cadence
    flux_alt: np.ndarray | None = None   # secondary flux (SAP for KBonus)
    meta: dict = field(default_factory=dict)

    def good_mask(self) -> np.ndarray:
        m = np.isfinite(self.time) & np.isfinite(self.flux) & np.isfinite(self.flux_err)
        m &= self.flux_err > 0
        m &= self.quality == 0
        return m

    def cleaned(self) -> LightCurve:
        m = self.good_mask()
        return LightCurve(self.time[m], self.flux[m], self.flux_err[m], self.quality[m], self.quarter[m],
                          None if self.flux_alt is None else self.flux_alt[m], dict(self.meta))

    @property
    def n_good(self) -> int:
        return int(self.good_mask().sum())


class Signal(BaseModel):
    target_id: str
    detector: str
    detector_version: str
    period: float
    t0: float
    duration: float          # days
    depth_ppm: float
    snr: float
    sde: float
    n_events: int
    extra: dict = Field(default_factory=dict)


class TestStatus(StrEnum):
    __test__ = False  # not a pytest test class

    PASS = "pass"
    FAIL = "fail"
    UNDETERMINED = "undetermined"


class TestResult(BaseModel):
    __test__ = False  # not a pytest test class

    rung: str
    status: TestStatus
    metrics: dict = Field(default_factory=dict)
    artifacts: list[str] = Field(default_factory=list)
    reason: str = ""
