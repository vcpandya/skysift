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
    ctrl = []
    for off in np.linspace(0.1, 0.9, 9):
        p2 = fold_phase(time, period, t0 + off * period); r = _box_depth(p2, fnorm, half, local_baseline)
        if r: ctrl.append(r[0])
    return EventDepths(a[:, 0].astype(int), a[:, 1].astype(int), a[:, 2] * 1e6, a[:, 3] * 1e6, mean * 1e6, err * 1e6,
                       scaled * 1e6, float(np.std(ctrl) * 1e6) if ctrl else np.nan, len(rows))


def secondary_search(time, fnorm, period, t0, duration_days, phases=None):
    """Box depth at each trial phase; returns (best_phase, depth_ppm, sigma) excluding +-1.5 durations of the primary."""
    if phases is None:
        step = min(0.01, 0.5 * duration_days / period)
        phases = np.arange(0.1, 0.9 + 1e-9, step)
    good = np.isfinite(fnorm); time, fnorm = time[good], fnorm[good]
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
