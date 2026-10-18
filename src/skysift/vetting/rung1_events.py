from __future__ import annotations

import numpy as np

from skysift.lc.ops import CHASES_MIN, EVENT_COVERAGE_MIN, event_quality
from skysift.models import Signal, TestResult, TestStatus
from skysift.vetting.base import DataBundle

GAPPED_FRACTION_MAX = 0.5       # FAIL when no more than this fraction of the events holds enough data
ZUMA_SES_MIN = -1.0             # an event whose own SES is below this is an excess of flux, not a dip
SES_DOMINANCE_MIN_PERIOD = 90.0 # the single-event dominance test is made only on signals longer than this (days)
SES_DOMINANCE_MAX = 0.8         # max(SES) / sqrt(sum SES^2) above this: one event carries the signal
CHASES_MEDIAN_MIN = 0.8         # the median C_i below this: the events sit among dips as strong as themselves


def _round(values, ndigits: int) -> list:
    return [round(float(v), ndigits) if np.isfinite(v) else None for v in values]


class Rung1Events:
    """Per-event tests of the Kepler Robovetter (Thompson et al. 2018): coverage ("Rubble"), gapped fraction,
    single-event dominance, Chases and Zuma, on top of ``lc.ops.event_quality``."""

    name = "rung1_events"
    cost = 0.02

    def run(self, s: Signal, b: DataBundle) -> TestResult:
        th = b.thresholds
        q = event_quality(b.lc.time, b.fnorm, s.period, s.t0, s.duration)
        n = len(q.epochs)
        covered = q.coverage >= EVENT_COVERAGE_MIN
        has_ses = np.isfinite(q.ses)
        applied = bool(has_ses.sum() >= th.min_events)
        ratio = float("nan")
        chases_median = None
        if applied:
            bad = has_ses & (q.ses < ZUMA_SES_MIN)
            if q.chases is not None:
                bad |= np.isfinite(q.chases) & (q.chases < CHASES_MIN)
            good = covered & ~bad
            ses_good = q.ses[good & has_ses]
            if ses_good.size:
                ratio = float(ses_good.max() / np.sqrt(np.sum(ses_good ** 2)))
            if q.chases is not None and np.isfinite(q.chases).any():
                chases_median = float(np.nanmedian(q.chases))
        else:
            good = covered
        n_good = int(good.sum())
        m = {"n_events_with_data": n, "n_covered": int(covered.sum()), "n_good": n_good,
             "coverage": _round(q.coverage, 3), "ses": _round(q.ses, 2), "ses_rules_applied": applied,
             "ses_max_over_mes": None if np.isnan(ratio) else ratio,
             "chases": None if q.chases is None else _round(q.chases, 3),
             "chases_median": chases_median}

        def fail(reason: str) -> TestResult:
            return TestResult(rung=self.name, status=TestStatus.FAIL, metrics=m, reason=reason)

        frac = float(covered.mean()) if n else 0.0
        if frac <= GAPPED_FRACTION_MAX:
            return fail(f"gapped events ({int(covered.sum())} of {n} covered)")
        if n_good < th.min_events:
            return fail(f"n_good {n_good} < min_events {th.min_events}")
        if applied and s.period > SES_DOMINANCE_MIN_PERIOD and ratio > SES_DOMINANCE_MAX:
            return fail(f"single event dominates ({ratio:.2f})")
        if chases_median is not None and chases_median < CHASES_MEDIAN_MIN:
            return fail(f"chases median {chases_median:.2f} < {CHASES_MEDIAN_MIN}")
        return TestResult(rung=self.name, status=TestStatus.PASS, metrics=m)
