from __future__ import annotations

import math
from numbers import Real

from skysift.lc.ops import circular_duration_days
from skysift.models import Signal, TestResult, TestStatus
from skysift.vetting.base import DataBundle

SYSTEMATIC_PERIODS = (93.0, 46.5, 372.5, 186.25)
FALLBACK_STAR = 0.5   # solar masses and radii when the catalogue value is missing or unusable


def _stellar_param(meta: dict, key: str) -> tuple[float, bool]:
    """Return (value, from_meta): the meta value only when it is a finite number > 0, else the 0.5 fallback."""
    v = meta.get(key)
    if isinstance(v, Real) and not isinstance(v, bool) and math.isfinite(v) and v > 0:
        return float(v), True
    return FALLBACK_STAR, False


class Rung1Detection:
    name = "rung1_detection"
    cost = 0.01

    def run(self, s: Signal, b: DataBundle) -> TestResult:
        th = b.thresholds
        if any(abs(s.period - p) / p < 0.01 for p in SYSTEMATIC_PERIODS):
            return TestResult(rung=self.name, status=TestStatus.UNDETERMINED, reason="systematic_alias",
                              metrics={"period": s.period})
        mass, mass_ok = _stellar_param(b.target.meta, "mass")
        radius, radius_ok = _stellar_param(b.target.meta, "radius")
        t_circ = circular_duration_days(s.period, mass, radius)
        ratio = s.duration / t_circ
        m = {"sde": s.sde, "snr": s.snr, "n_events": s.n_events, "duration_ratio": ratio, "t_circ_days": t_circ,
             "stellar_mass": mass, "stellar_radius": radius,
             "stellar_source": "meta" if (mass_ok and radius_ok) else "fallback"}
        if s.sde < th.sde_min: return TestResult(rung=self.name, status=TestStatus.FAIL, metrics=m, reason=f"sde {s.sde:.1f} < {th.sde_min}")
        if s.snr < th.snr_min: return TestResult(rung=self.name, status=TestStatus.FAIL, metrics=m, reason=f"snr {s.snr:.1f} < {th.snr_min}")
        if s.n_events < th.min_events: return TestResult(rung=self.name, status=TestStatus.FAIL, metrics=m, reason=f"n_events {s.n_events} < {th.min_events}")
        if not (th.duration_ratio_min <= ratio <= th.duration_ratio_max):
            return TestResult(rung=self.name, status=TestStatus.FAIL, metrics=m, reason=f"duration ratio {ratio:.2f} outside range")
        return TestResult(rung=self.name, status=TestStatus.PASS, metrics=m)
