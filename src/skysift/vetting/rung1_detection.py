from __future__ import annotations

from skysift.lc.ops import circular_duration_days
from skysift.models import Signal, TestResult, TestStatus
from skysift.vetting.base import DataBundle

SYSTEMATIC_PERIODS = (93.0, 46.5, 372.5, 186.25)


class Rung1Detection:
    name = "rung1_detection"
    cost = 0.01

    def run(self, s: Signal, b: DataBundle) -> TestResult:
        th = b.thresholds
        if any(abs(s.period - p) / p < 0.01 for p in SYSTEMATIC_PERIODS):
            return TestResult(rung=self.name, status=TestStatus.UNDETERMINED, reason="systematic_alias",
                              metrics={"period": s.period})
        mass = b.target.meta.get("mass", 0.5); radius = b.target.meta.get("radius", 0.5)
        t_circ = circular_duration_days(s.period, mass, radius)
        ratio = s.duration / t_circ
        m = {"sde": s.sde, "snr": s.snr, "n_events": s.n_events, "duration_ratio": ratio, "t_circ_days": t_circ}
        if s.sde < th.sde_min: return TestResult(rung=self.name, status=TestStatus.FAIL, metrics=m, reason=f"sde {s.sde:.1f} < {th.sde_min}")
        if s.snr < th.snr_min: return TestResult(rung=self.name, status=TestStatus.FAIL, metrics=m, reason=f"snr {s.snr:.1f} < {th.snr_min}")
        if s.n_events < th.min_events: return TestResult(rung=self.name, status=TestStatus.FAIL, metrics=m, reason=f"n_events {s.n_events} < {th.min_events}")
        if not (th.duration_ratio_min <= ratio <= th.duration_ratio_max):
            return TestResult(rung=self.name, status=TestStatus.FAIL, metrics=m, reason=f"duration ratio {ratio:.2f} outside range")
        return TestResult(rung=self.name, status=TestStatus.PASS, metrics=m)
