from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import median_filter

from skysift.models import LightCurve


def _segments(time: np.ndarray, gap_days: float):
    breaks = np.where(np.diff(time) > gap_days)[0] + 1
    return np.split(np.arange(time.size), breaks)


def detrend(lc: LightCurve, window_days: float = 2.0, gap_days: float = 1.0, flux: np.ndarray | None = None) -> np.ndarray:
    """Divide by a running median per contiguous segment. Returns normalised flux aligned with lc.time (NaN where bad)."""
    m = lc.good_mask()
    f = (lc.flux if flux is None else flux)
    m = m & np.isfinite(f)
    out = np.full(lc.time.size, np.nan)
    t, y, idx = lc.time[m], f[m], np.where(m)[0]
    for seg in _segments(t, gap_days):
        if seg.size < 10:
            continue
        dt = np.nanmedian(np.diff(t[seg]))
        n = max(5, round(window_days / dt) | 1)
        trend = median_filter(y[seg], size=n, mode="nearest")
        out[idx[seg]] = y[seg] / trend
    return out


def fold_phase(time: np.ndarray, period: float, t0: float) -> np.ndarray:
    return (time - t0 + 0.5 * period) % period - 0.5 * period


@dataclass
class EventDepths:
    epochs: np.ndarray
    quarters: np.ndarray
    depths: np.ndarray
    errors: np.ndarray
    mean: float
    err_formal: float
    err_scaled: float
    control_rms: float
    n: int


PRIMARY_MASK_DURATIONS = 1.5    # off-primary searches ignore points this many transit durations from mid-transit


def _box_depth(ph, fn, half, baseline="linear"):
    i = np.abs(ph) < 0.8 * half
    o = (np.abs(ph) > 1.5 * half) & (np.abs(ph) < max(0.5, 4 * half))
    if i.sum() < 3 or o.sum() < 20:
        return None
    if baseline == "linear":
        c = np.polyfit(ph[o], fn[o], 1); base_i = np.polyval(c, ph[i]); base_o = np.polyval(c, ph[o])
        resid = fn[o] - base_o
    else:
        base_i = np.full(i.sum(), np.median(fn[o])); resid = fn[o] - np.median(fn[o])
    scat = 1.4826 * np.median(np.abs(resid))
    d = float(np.mean(1 - fn[i] / base_i))
    return d, scat / np.sqrt(i.sum())


def event_depths(time, fnorm, quarter, period, t0, duration_days, local_baseline="linear") -> EventDepths:
    good = np.isfinite(fnorm); time, fnorm, quarter = time[good], fnorm[good], quarter[good]
    half = duration_days / 2.0
    ph = fold_phase(time, period, t0); ep = np.round((time - t0) / period).astype(int)
    rows = []
    for e in np.unique(ep):
        s = ep == e
        r = _box_depth(ph[s], fnorm[s], half, local_baseline)
        if r: rows.append((e, int(np.median(quarter[s])), r[0], r[1]))
    if not rows:
        return EventDepths(np.array([]), np.array([]), np.array([]), np.array([]), np.nan, np.nan, np.nan, np.nan, 0)
    a = np.array(rows, dtype=float)
    w = 1 / a[:, 3] ** 2; mean = float(np.sum(w * a[:, 2]) / np.sum(w)); err = float(1 / np.sqrt(np.sum(w)))
    chi2 = float(np.sum(w * (a[:, 2] - mean) ** 2)); scaled = err * max(1.0, np.sqrt(chi2 / max(1, len(rows) - 1)))
    # control boxes away from the primary: on short periods the 0.1/0.9 boxes and their baseline windows would
    # otherwise overlap the transit itself, inflating control_rms
    off_primary = np.abs(ph) >= PRIMARY_MASK_DURATIONS * duration_days
    ctrl = []
    for off in np.linspace(0.1, 0.9, 9):
        p2 = fold_phase(time[off_primary], period, t0 + off * period)
        r = _box_depth(p2, fnorm[off_primary], half, local_baseline)
        if r: ctrl.append(r[0])
    return EventDepths(a[:, 0].astype(int), a[:, 1].astype(int), a[:, 2] * 1e6, a[:, 3] * 1e6, mean * 1e6, err * 1e6,
                       scaled * 1e6, float(np.std(ctrl) * 1e6) if ctrl else np.nan, len(rows))


def secondary_search(time, fnorm, period, t0, duration_days, phases=None):
    """Box depth at each trial phase in 0.1..0.9; returns (best_phase, depth_ppm, sigma).

    Points within 1.5 transit durations of the primary (|time folded on t0| < 1.5 * duration_days) are dropped
    before the search, from the in-box and the baseline sets of every trial phase alike: for duration/period above
    ~0.1, or periods under ~1 d where the baseline window spans the orbit, they would otherwise put the primary
    transit itself into a trial box or its baseline. Trial phases whose box falls inside the mask give NaN."""
    if phases is None:
        step = min(0.01, 0.5 * duration_days / period)
        phases = np.arange(0.1, 0.9 + 1e-9, step)
    good = np.isfinite(fnorm); time, fnorm = time[good], fnorm[good]
    keep = np.abs(fold_phase(time, period, t0)) >= PRIMARY_MASK_DURATIONS * duration_days
    time, fnorm = time[keep], fnorm[keep]
    half = duration_days / 2.0
    out = []
    for p in phases:
        ph = fold_phase(time, period, t0 + p * period)
        i = np.abs(ph) < 0.8 * half; o = (np.abs(ph) > 1.5 * half) & (np.abs(ph) < max(0.5, 4 * half))
        if i.sum() < 5 or o.sum() < 50:
            out.append((p, np.nan, np.nan)); continue
        base = np.median(fnorm[o]); scat = 1.4826 * np.median(np.abs(fnorm[o] - base))
        d = 1 - np.mean(fnorm[i]) / base
        out.append((p, d * 1e6, d / (scat / np.sqrt(i.sum()))))
    out = np.array(out)
    if out.size == 0 or not np.isfinite(out[:, 2]).any():
        return float("nan"), float("nan"), float("nan")
    k = np.nanargmax(out[:, 2])
    return float(out[k, 0]), float(out[k, 1]), float(out[k, 2])


SHAPE_BINS_PER_DURATION = 4     # fold bins across one transit duration, so the box edges are resolved
SHAPE_MIN_BINS, SHAPE_MAX_BINS, SHAPE_MIN_PER_BIN = 50, 2000, 5


def shape_contrast(time, fnorm, period, t0, duration_days) -> tuple[float, float, int]:
    """Chi-square of a box (one level inside |phase| < duration/2, another outside) and of a two-harmonic sinusoid,
    both weighted least-squares fits to the binned fold, each bin weighted by the standard error of its own points.

    A transit folds to a flat baseline with a dip, which the box fits and a smooth wave cannot; a pulsator, a spotted
    rotator or a contact binary folds to a wave that the sinusoid explains at least as well as any box, however many
    harmonics or sidelobes of the true period the search landed on. Binning with empirical errors matters: variability
    at other periods is incoherent on this fold and averages out, so the contrast measures the mean fold shape rather
    than drowning in the per-point scatter. Returns ``(box_chi2, sine_chi2, n_bins)``; NaN, NaN, 0 when the fold has
    too few populated bins inside or outside the box to compare."""
    good = np.isfinite(fnorm) & np.isfinite(time)
    cycles = fold_phase(time[good], period, t0) / period          # [-0.5, 0.5)
    y = fnorm[good]
    width = duration_days / period
    n_bins = int(min(SHAPE_MAX_BINS, max(SHAPE_MIN_BINS, round(SHAPE_BINS_PER_DURATION / width))))
    idx = np.minimum(((cycles + 0.5) * n_bins).astype(int), n_bins - 1)
    counts = np.bincount(idx, minlength=n_bins)
    sums = np.bincount(idx, weights=y, minlength=n_bins)
    sq = np.bincount(idx, weights=y * y, minlength=n_bins)
    ok = counts >= SHAPE_MIN_PER_BIN
    mean = sums[ok] / counts[ok]
    var = np.maximum(sq[ok] / counts[ok] - mean ** 2, 0.0)
    centre = (np.arange(n_bins)[ok] + 0.5) / n_bins - 0.5
    inbox = np.abs(centre) < 0.5 * width
    if inbox.sum() < 1 or (~inbox).sum() < 10:
        return float("nan"), float("nan"), 0
    floor = 1.4826 * float(np.median(np.abs(y - np.median(y)))) or 1e-12
    err = np.sqrt(np.maximum(var, (0.5 * floor) ** 2) / counts[ok])     # never below half the robust point scatter
    wts = 1.0 / err
    box = np.where(inbox, np.average(mean[inbox], weights=wts[inbox] ** 2), np.average(mean[~inbox], weights=wts[~inbox] ** 2))
    w = 2 * np.pi * centre
    design = np.column_stack([np.ones(centre.size), np.cos(w), np.sin(w), np.cos(2 * w), np.sin(2 * w)])
    coef, *_ = np.linalg.lstsq(design * wts[:, None], mean * wts, rcond=None)
    box_chi2 = float(np.sum(((mean - box) * wts) ** 2))
    sine_chi2 = float(np.sum(((mean - design @ coef) * wts) ** 2))
    return box_chi2, sine_chi2, int(ok.sum())


def circular_duration_days(period_days: float, mass_msun: float = 0.5, radius_rsun: float = 0.5) -> float:
    """Central-transit duration for a circular orbit: T = P/pi * asin(R*/a), a from Kepler's third law."""
    a_au = (mass_msun * (period_days / 365.25) ** 2) ** (1 / 3)
    a_rsun = a_au * 215.032
    return period_days / np.pi * np.arcsin(min(1.0, radius_rsun / a_rsun))
