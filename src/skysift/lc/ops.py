from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import median_filter

from skysift.models import LightCurve


def _segments(time: np.ndarray, gap_days: float):
    breaks = np.where(np.diff(time) > gap_days)[0] + 1
    return np.split(np.arange(time.size), breaks)


# Kepler data resume after every downlink and safe mode with a thermal ramp, up to 3 percent deep, whose tail stays
# near 1000 ppm past the first day; the 2 d running median cannot follow it. The downlink gaps are 0.7 to 1.0 d long,
# so the mask triggers on any gap longer than GAP_EDGE_TRIGGER_DAYS, not on the 1.0 d segment split
# (notes/2026-10-17-long-period-sde.md, section 8).
GAP_EDGE_MASK_DAYS = 1.5        # points this close after a gap are dropped before detrending (NaN in the output)
GAP_EDGE_TRIGGER_DAYS = 0.5     # a time step longer than this between consecutive points is a gap for the mask
MEDIAN_PAD_MODE = "mirror"      # how the running median extends a segment past its edges
DETREND_VERSION = 3             # 1: running median only; 2: 1.0 d gap-edge mask first; 3: 1.5 d mask, mirror padding


def _gap_edge_mask(time: np.ndarray, edge_mask_days: float, edge_gap_days: float) -> np.ndarray:
    """True for every point less than ``edge_mask_days`` after the first point following a step longer than
    ``edge_gap_days`` (``time`` sorted). The first point of the series is not a gap edge."""
    starts = time[1:][np.diff(time) > edge_gap_days]
    if edge_mask_days <= 0 or starts.size == 0:
        return np.zeros(time.size, bool)
    j = np.searchsorted(starts, time, side="right") - 1
    return (j >= 0) & (time - starts[np.maximum(j, 0)] < edge_mask_days)


def detrend(lc: LightCurve, window_days: float = 2.0, gap_days: float = 1.0, flux: np.ndarray | None = None,
            edge_mask_days: float = GAP_EDGE_MASK_DAYS, edge_gap_days: float = GAP_EDGE_TRIGGER_DAYS) -> np.ndarray:
    """Divide by a running median per contiguous segment (split at steps longer than ``gap_days``). Returns normalised
    flux aligned with lc.time, NaN where bad and for the first ``edge_mask_days`` after every step longer than
    ``edge_gap_days`` between consecutive good points: those points are dropped before the running median, so the
    ramp neither enters the search nor pulls the trend. ``edge_mask_days=0`` turns the mask off.

    The median is padded by mirroring each segment at its edges. Repeating the edge value instead (``"nearest"``) fills
    half the window with one point, so a transit touching a segment edge pulls the median to its own level and is
    flattened; mirroring duplicates only its few in-transit points and the median barely moves. The mask adds a
    segment edge after every downlink gap, so this matters on every real curve."""
    m = lc.good_mask()
    f = (lc.flux if flux is None else flux)
    m = m & np.isfinite(f)
    good = np.where(m)[0]
    m[good[_gap_edge_mask(lc.time[good], edge_mask_days, edge_gap_days)]] = False
    out = np.full(lc.time.size, np.nan)
    t, y, idx = lc.time[m], f[m], np.where(m)[0]
    for seg in _segments(t, gap_days):
        if seg.size < 10:
            continue
        dt = np.nanmedian(np.diff(t[seg]))
        n = max(5, round(window_days / dt) | 1)
        trend = median_filter(y[seg], size=n, mode=MEDIAN_PAD_MODE)
        out[idx[seg]] = y[seg] / trend
    return out


def fold_phase(time: np.ndarray, period: float, t0: float) -> np.ndarray:
    return (time - t0 + 0.5 * period) % period - 0.5 * period


def n_window_epochs(time, fnorm, period, t0, duration_days) -> int:
    """Number of distinct events (epoch ``round((t - t0) / period)``) with a finite point within half a duration of
    mid-transit."""
    time, fnorm = np.asarray(time), np.asarray(fnorm)
    inside = np.isfinite(time) & np.isfinite(fnorm) & (np.abs(fold_phase(time, period, t0)) < 0.5 * duration_days)
    return int(np.unique(np.round((time[inside] - t0) / period)).size)


def season_of(quarter: np.ndarray) -> np.ndarray:
    """Kepler season of each quarter (``quarter % 4``): the spacecraft rolls every quarter, so a star returns to the
    same CCD module every fourth quarter."""
    return np.asarray(quarter) % 4


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


@dataclass
class StackedDepth:
    depth_ppm: float
    err_ppm: float
    n_in: int
    n_out: int
    inflation: float


STACK_MIN_IN, STACK_MIN_BASELINE, STACK_MIN_CONTROLS = 10, 50, 3


def _circular(x: np.ndarray, period: float) -> np.ndarray:
    return np.abs((x + 0.5 * period) % period - 0.5 * period)


def _window_depth(ph, fn, period, duration_days, centre):
    """Depth and formal error of the box of width ``duration_days`` centred ``centre`` days from the primary on a fold
    whose phases are ``ph``. The baseline is the median of the points at least 1.5 durations from both the primary and
    the box. Returns (depth, err, n_in, n_out) as fractions, or None when the box or its baseline is too sparse."""
    d_win = _circular(ph - centre, period)
    inside = d_win < 0.5 * duration_days
    base = (np.abs(ph) >= PRIMARY_MASK_DURATIONS * duration_days) & (d_win >= PRIMARY_MASK_DURATIONS * duration_days)
    if inside.sum() < STACK_MIN_IN or base.sum() < STACK_MIN_BASELINE:
        return None
    level = np.median(fn[base])
    scat = 1.4826 * np.median(np.abs(fn[base] - level))
    return float(1 - np.mean(fn[inside]) / level), float(scat / level / np.sqrt(inside.sum())), int(inside.sum()), int(base.sum())


def stacked_depth(time, fnorm, period, t0, duration_days, *, epochs="all", phase_offset=0.0) -> StackedDepth:
    """Box depth on the stacked fold, for signals whose single events hold too few points to measure.

    The window is ``duration_days`` wide, centred ``phase_offset`` of a period from mid-transit (0.5 for the secondary).
    ``epochs`` keeps all events or only the odd or the even ones (epoch number ``round((t - t0) / period)``). The formal
    error is the out-of-window scatter over the square root of the in-window count; it is multiplied by the ratio of
    the robust spread (1.4826 times the median absolute deviation) of the same window's depth at nine control phases
    (0.1 to 0.9, at least 1.5 durations from the primary and from the window) to that formal error whenever the ratio
    exceeds one, which carries the red noise the formal error misses; a real secondary in one control does not count.
    Fewer than three controls leave the error as is. Returns NaN depths and ``n_in = 0`` when the window holds fewer
    than 10 points or the baseline fewer than 50."""
    good = np.isfinite(fnorm) & np.isfinite(time)
    if epochs != "all":
        parity = np.round((time - t0) / period).astype(int) % 2
        good &= parity == (1 if epochs == "odd" else 0)
    ph, fn = fold_phase(time[good], period, t0), fnorm[good]
    r = _window_depth(ph, fn, period, duration_days, phase_offset * period)
    if r is None:
        return StackedDepth(float("nan"), float("nan"), 0, 0, 1.0)
    depth, err, n_in, n_out = r
    away = PRIMARY_MASK_DURATIONS * duration_days
    ctrl = []
    for c in np.linspace(0.1, 0.9, 9):
        if min(c, 1 - c) * period < away or _circular(np.array([(c - phase_offset) * period]), period)[0] < away:
            continue
        rc = _window_depth(ph, fn, period, duration_days, c * period)
        if rc:
            ctrl.append(rc[0])
    # robust spread (1.4826 MAD about the median): one real secondary among the controls must not inflate the error
    spread = 1.4826 * float(np.median(np.abs(np.asarray(ctrl) - np.median(ctrl)))) if ctrl else 0.0
    inflation = max(1.0, spread / err) if len(ctrl) >= STACK_MIN_CONTROLS and err > 0 else 1.0
    return StackedDepth(depth * 1e6, err * inflation * 1e6, n_in, n_out, inflation)


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
