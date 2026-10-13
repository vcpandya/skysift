from __future__ import annotations

import numpy as np
from astropy.timeseries import BoxLeastSquares

from skysift.config import Thresholds
from skysift.detectors.base import register
from skysift.models import LightCurve, Signal

MIN_POINTS = 500
DURATIONS_H = np.array([1, 1.5, 2, 3, 4, 6, 9, 12])


class BoxDip:
    name = "box_dip"
    version = "1"
    cost_class = "cpu_medium"

    def __init__(self, top_k: int = 3, oversample: int = 5):
        self.top_k, self.oversample = top_k, oversample

    def detect(self, lc: LightCurve, fnorm: np.ndarray, th: Thresholds) -> list[Signal]:
        m = np.isfinite(fnorm) & np.isfinite(lc.time)
        if m.sum() < MIN_POINTS:
            return []
        t, y = lc.time[m], fnorm[m]
        med = np.median(y); mad = 1.4826 * np.median(np.abs(y - med))
        k = np.abs(y - med) < 5 * mad
        t, y = t[k], y[k]
        baseline = float(t.max() - t.min())
        pmax = min(th.max_period_days, baseline / 3.0)
        if pmax <= th.min_period_days:
            return []
        durations = DURATIONS_H / 24.0
        # frequency grid fine enough that a transit drifts < duration/oversample across the baseline
        q = durations.min() / baseline / self.oversample
        freqs = np.arange(1 / pmax, 1 / th.min_period_days, q)
        periods = 1 / freqs[::-1]
        # constant per-point uncertainty from the robust scatter, so depth_err (and snr) are in real units
        sigma = max(1.4826 * float(np.median(np.abs(y - np.median(y)))), 1e-12)
        model = BoxLeastSquares(t, y, dy=np.full(t.size, sigma))
        res = model.power(periods, durations, objective="snr")
        power = np.asarray(res.power)
        pmed = np.median(power); pstd = 1.4826 * np.median(np.abs(power - pmed)) or 1e-12
        order = np.argsort(power)[::-1]
        out: list[Signal] = []
        for i in order:
            p = float(periods[i])
            if any(abs(p - s.period) / s.period < 0.02 for s in out):
                continue
            dur = float(res.duration[i]); t0 = float(res.transit_time[i]); depth = float(res.depth[i])
            ph = (t - t0 + 0.5 * p) % p - 0.5 * p
            n_events = int(np.unique(np.round((t[np.abs(ph) < 0.5 * dur] - t0) / p)).size)
            snr = float(depth / max(res.depth_err[i], 1e-12))
            out.append(Signal(target_id=str(lc.meta.get("target_id", "")), detector=self.name, detector_version=self.version,
                              period=p, t0=t0, duration=dur, depth_ppm=depth * 1e6, snr=snr,
                              sde=float((power[i] - pmed) / pstd), n_events=n_events,
                              extra={"baseline_days": baseline, "sde_spectrum_std": float(pstd), "alias_of": None}))
            if len(out) >= self.top_k:
                break
        return out


register(BoxDip())
