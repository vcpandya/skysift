from __future__ import annotations

import numpy as np

from skysift.lc.ops import event_depths, secondary_search, shape_contrast
from skysift.models import Signal, TestResult, TestStatus
from skysift.vetting.base import DataBundle


class Rung2Shape:
    name = "rung2_shape"
    cost = 0.05

    def run(self, s: Signal, b: DataBundle) -> TestResult:
        th = b.thresholds
        lc, fn = b.lc, b.fnorm
        ep = np.round((lc.time - s.t0) / s.period).astype(int)
        odd = ep % 2 == 1; even = ~odd
        d_odd = event_depths(lc.time[odd], fn[odd], lc.quarter[odd], s.period, s.t0, s.duration)
        d_even = event_depths(lc.time[even], fn[even], lc.quarter[even], s.period, s.t0, s.duration)
        d_sec = event_depths(lc.time, fn, lc.quarter, s.period, s.t0 + 0.5 * s.period, s.duration)
        all_d = event_depths(lc.time, fn, lc.quarter, s.period, s.t0, s.duration)
        free_phase, free_depth, free_sigma = secondary_search(lc.time, fn, s.period, s.t0, s.duration)
        parity_sigma = abs(d_odd.mean - d_even.mean) / np.hypot(d_odd.err_scaled, d_even.err_scaled)
        sec_sigma = d_sec.mean / d_sec.err_scaled if d_sec.n else 0.0
        timing_rms_min = float(np.nan)
        box_chi2, sine_chi2, shape_n = shape_contrast(lc.time, fn, s.period, s.t0, s.duration)
        m = {"depth_ppm": all_d.mean, "depth_err_ppm": all_d.err_scaled, "n_events": all_d.n,
             "odd_ppm": d_odd.mean, "even_ppm": d_even.mean,
             "parity_sigma": float(parity_sigma), "secondary_ppm": d_sec.mean, "secondary_sigma": float(sec_sigma),
             "free_secondary_phase": free_phase, "free_secondary_ppm": free_depth, "free_secondary_sigma": free_sigma,
             "control_rms_ppm": all_d.control_rms, "timing_rms_min": timing_rms_min,
             "box_chi2": box_chi2, "sine_chi2": sine_chi2, "shape_n": shape_n}
        # the stacked fold has every point, so this verdict holds even when the per-event tests below are undetermined
        # (about two points per event for sub-day periods at 30-minute cadence)
        if np.isfinite(sine_chi2) and sine_chi2 <= box_chi2:
            return TestResult(rung=self.name, status=TestStatus.FAIL, metrics=m, reason="sinusoidal")
        if not np.isfinite(parity_sigma) or d_odd.n < 2 or d_even.n < 2:
            return TestResult(rung=self.name, status=TestStatus.UNDETERMINED, metrics=m, reason="too few odd/even events")
        if parity_sigma > th.parity_sigma:
            return TestResult(rung=self.name, status=TestStatus.FAIL, metrics=m, reason=f"odd_even {parity_sigma:.1f} sigma")
        if sec_sigma > th.secondary_sigma:
            return TestResult(rung=self.name, status=TestStatus.FAIL, metrics=m, reason=f"secondary_at_0.5 {sec_sigma:.1f} sigma")
        if not np.isfinite(free_sigma) or not np.isfinite(free_phase):
            return TestResult(rung=self.name, status=TestStatus.UNDETERMINED, metrics=m, reason="free_secondary_undetermined")
        if free_sigma > th.free_secondary_sigma:
            # A secondary within one transit duration of phase 0.5 is a circular-orbit eclipse the phase-0.5 box
            # (+-0.8 half-durations) can narrowly miss; anything further out routes to the EB branch.
            if abs(free_phase - 0.5) * s.period <= s.duration:
                return TestResult(rung=self.name, status=TestStatus.FAIL, metrics=m,
                                  reason=f"secondary_at_0.5 {free_sigma:.1f} sigma (free-phase search, phase {free_phase:.3f})")
            return TestResult(rung=self.name, status=TestStatus.FAIL, metrics=m, reason="eccentric_secondary")
        return TestResult(rung=self.name, status=TestStatus.PASS, metrics=m)
