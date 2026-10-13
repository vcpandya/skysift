"""Complete BLS search over 0.5 to 200 d on a 4-year Kepler baseline costs ~250 s single-core (astropy BLS passes over every point per trial period); throughput comes from the process pool; `oversample` and `n_refine` are the knobs for a faster, less complete sweep, to be judged by injection-recovery."""
from __future__ import annotations

import itertools

import numpy as np
from astropy.timeseries import BoxLeastSquares

from skysift.config import Thresholds
from skysift.detectors.base import register
from skysift.lc.ops import circular_duration_days
from skysift.models import LightCurve, Signal

MIN_POINTS = 500
DURATIONS_H = np.array([1, 1.5, 2, 3, 4, 6, 9, 12])
ALIAS_RATIOS = (2.0, 3.0, 0.5, 1 / 3)
MIN_BAND_PERIODS = 50


def _band_durations(p_mid: float) -> np.ndarray:
    """Trial durations (days) plausible for a circular orbit at p_mid: 0.3 to 3 times the central-transit duration."""
    all_d = DURATIONS_H / 24.0
    t_circ = circular_duration_days(p_mid)
    keep = all_d[(all_d >= 0.3 * t_circ) & (all_d <= 3.0 * t_circ)]
    if keep.size < 3:
        keep = np.sort(all_d[np.argsort(np.abs(all_d - t_circ))[:3]])
    return keep


def _alias_of(period: float, earlier: list[Signal]) -> int | None:
    for j, s in enumerate(earlier):
        ratio = period / s.period
        if any(abs(ratio / r - 1.0) < 0.01 for r in ALIAS_RATIOS):
            return j
    return None


def _separated(order: np.ndarray, periods: np.ndarray, k: int) -> list[int]:
    """Indices from `order` (best first) whose periods differ by more than 2 percent from every earlier pick."""
    picked: list[int] = []
    for i in order:
        if all(abs(periods[i] - periods[j]) / periods[j] >= 0.02 for j in picked):
            picked.append(int(i))
            if len(picked) >= k:
                break
    return picked


class BoxDip:
    name = "box_dip"
    version = "1"
    cost_class = "cpu_medium"

    def __init__(self, top_k: int = 3, oversample: float = 1.5, n_bands: int = 24, refine_oversample: float = 6.0,
                 n_refine: int = 40, two_stage: bool = True):
        self.top_k, self.oversample, self.n_bands = top_k, oversample, n_bands
        self.refine_oversample, self.n_refine, self.two_stage = refine_oversample, n_refine, two_stage

    def _coarse_search(self, model: BoxLeastSquares, baseline: float, thresholds: Thresholds, pmax: float) -> dict:
        """Coarse stage. Log-spaced period bands; in each, durations follow the circular-orbit transit duration and the
        frequency step keeps the transit drift over the baseline (df * P * baseline) below duration/oversample, using the
        band's longest period (Ofir 2014). Returns the concatenated spectrum plus per-band durations and SDE norms."""
        edges = np.geomspace(thresholds.min_period_days, pmax, self.n_bands + 1)
        band_durs = []
        keys = ("period", "power", "duration", "t0", "depth", "err", "df", "band")
        parts: dict[str, list[np.ndarray]] = {key: [] for key in keys}
        for b, (p_lo, p_hi) in enumerate(itertools.pairwise(edges)):
            durs = _band_durations(float(np.sqrt(p_lo * p_hi)))
            band_durs.append(durs)
            df = durs.min() / (self.oversample * baseline * p_hi)
            periods = 1 / np.arange(1 / p_hi, 1 / p_lo, df)[::-1]
            res = model.power(periods, durs, objective="snr")
            parts["period"].append(periods)
            parts["power"].append(np.asarray(res.power))
            parts["duration"].append(np.asarray(res.duration))
            parts["t0"].append(np.asarray(res.transit_time))
            parts["depth"].append(np.asarray(res.depth))
            parts["err"].append(np.asarray(res.depth_err))
            parts["df"].append(np.full(periods.size, df))
            parts["band"].append(np.full(periods.size, b))
        spec = {key: np.concatenate(parts[key]) for key in keys}
        # SDE normalisation per band: the noise level of the BLS spectrum rises with period, so each band gets its own
        # median and MAD-sigma; bands with fewer than MIN_BAND_PERIODS trial periods fall back to the global values.
        power = spec["power"]
        gmed = float(np.median(power)); gstd = 1.4826 * float(np.median(np.abs(power - gmed))) or 1e-12
        band_med, band_std = np.full(self.n_bands, gmed), np.full(self.n_bands, gstd)
        for b, pw in enumerate(parts["power"]):
            if pw.size >= MIN_BAND_PERIODS:
                band_med[b] = np.median(pw)
                band_std[b] = 1.4826 * float(np.median(np.abs(pw - band_med[b]))) or 1e-12
        spec.update(band_durs=band_durs, band_med=band_med, band_std=band_std)
        return spec

    def _refine(self, model: BoxLeastSquares, spec: dict, i: int) -> tuple:
        """Re-search +-2 coarse steps around coarse peak i on a symmetric grid that contains the coarse frequency."""
        f0, df, b = 1 / spec["period"][i], spec["df"][i], int(spec["band"][i])
        n = round(2 * self.refine_oversample / self.oversample)
        freqs = f0 + df * np.linspace(-2, 2, 2 * n + 1)
        rp = np.sort(1 / freqs[freqs > 0])
        res = model.power(rp, spec["band_durs"][b], objective="snr")
        j = int(np.argmax(res.power))
        return (float(rp[j]), float(res.power[j]), float(res.duration[j]), float(res.transit_time[j]),
                float(res.depth[j]), float(res.depth_err[j]), b)

    def detect(self, lc: LightCurve, fnorm: np.ndarray, thresholds: Thresholds) -> list[Signal]:
        th = thresholds
        m = np.isfinite(fnorm) & np.isfinite(lc.time)
        if m.sum() < MIN_POINTS:
            return []
        t, y = lc.time[m], fnorm[m]
        med = np.median(y); mad = 1.4826 * np.median(np.abs(y - med))
        if mad == 0:
            return []
        # clip upward outliers only: transits are downward and must survive however deep they are
        k = (y - med) < 5 * mad
        if k.sum() < MIN_POINTS:
            return []
        t, y = t[k], y[k]
        baseline = float(t.max() - t.min())
        pmax = min(th.max_period_days, baseline / 3.0)
        if pmax <= th.min_period_days:
            return []
        # constant per-point uncertainty from the robust scatter, so depth_err (and snr) are in real units
        sigma = max(1.4826 * float(np.median(np.abs(y - np.median(y)))), 1e-12)
        model = BoxLeastSquares(t, y, dy=np.full(t.size, sigma))
        spec = self._coarse_search(model, baseline, th, pmax)
        periods, power = spec["period"], spec["power"]
        order = np.argsort(power)[::-1]
        # candidates: (period, power, duration, t0, depth, depth_err, band)
        if self.two_stage:
            cands = [self._refine(model, spec, i) for i in _separated(order, periods, self.n_refine)]
            n_refined = len(cands)
        else:
            cands = [(float(periods[i]), float(power[i]), float(spec["duration"][i]), float(spec["t0"][i]),
                      float(spec["depth"][i]), float(spec["err"][i]), int(spec["band"][i]))
                     for i in _separated(order, periods, self.top_k)]
            n_refined = 0
        cp = np.array([c[0] for c in cands])
        keep = _separated(np.argsort([c[1] for c in cands])[::-1], cp, self.top_k)
        out: list[Signal] = []
        for ci in keep:
            p, pw, dur, t0, depth, err, b = cands[ci]
            bmed, bstd = float(spec["band_med"][b]), float(spec["band_std"][b])
            ph = (t - t0 + 0.5 * p) % p - 0.5 * p
            n_events = int(np.unique(np.round((t[np.abs(ph) < 0.5 * dur] - t0) / p)).size)
            out.append(Signal(target_id=str(lc.meta.get("target_id", "")), detector=self.name, detector_version=self.version,
                              period=p, t0=t0, duration=dur, depth_ppm=depth * 1e6, snr=float(depth / max(err, 1e-12)),
                              sde=(pw - bmed) / bstd, n_events=n_events,
                              extra={"baseline_days": baseline, "sde_spectrum_std": bstd, "band": b,
                                     "alias_of": _alias_of(p, out), "n_periods": int(periods.size),
                                     "n_bands": self.n_bands, "two_stage": self.two_stage, "n_refined": n_refined}))
        return out


register(BoxDip())
