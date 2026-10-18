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
    """True for every point less than ``edge_mask_days`` after the first point of the series or the first point
    following a step longer than ``edge_gap_days`` (``time`` sorted). The start of the data counts as a gap edge: the
    spacecraft was just as cold before the first exposure (notes/2026-10-17-long-period-sde.md, section 5, shows a
    3157 ppm dip 0.55 d after data start)."""
    if edge_mask_days <= 0 or time.size == 0:
        return np.zeros(time.size, bool)
    starts = np.concatenate([time[:1], time[1:][np.diff(time) > edge_gap_days]])
    j = np.searchsorted(starts, time, side="right") - 1
    return (j >= 0) & (time - starts[np.maximum(j, 0)] < edge_mask_days)


def detrend(lc: LightCurve, window_days: float = 2.0, gap_days: float = 1.0, flux: np.ndarray | None = None,
            edge_mask_days: float = GAP_EDGE_MASK_DAYS, edge_gap_days: float = GAP_EDGE_TRIGGER_DAYS,
            pad_mode: str = MEDIAN_PAD_MODE) -> np.ndarray:
    """Divide by a running median per contiguous segment (split at steps longer than ``gap_days``). Returns normalised
    flux aligned with lc.time, NaN where bad and for the first ``edge_mask_days`` of the data and after every step
    longer than ``edge_gap_days`` between consecutive good points: those points are dropped before the running median,
    so the ramp neither enters the search nor pulls the trend. ``edge_mask_days=0`` turns the mask off.

    The median is padded by mirroring each segment at its edges. Repeating the edge value instead (``"nearest"``) fills
    half the window with one point, so a transit touching a segment edge pulls the median to its own level and is
    flattened; mirroring duplicates only its few in-transit points and the median barely moves. The mask adds a
    segment edge after every downlink gap, so this matters on every real curve. ``pad_mode`` is passed to the running
    median; the dashboard sets it (with the mask arguments) from the settings stored with a signal, so a signal is
    drawn on the flux it was measured on."""
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
        trend = median_filter(y[seg], size=n, mode=pad_mode)
        out[idx[seg]] = y[seg] / trend
    return out


def fold_phase(time: np.ndarray, period: float, t0: float) -> np.ndarray:
    return (time - t0 + 0.5 * period) % period - 0.5 * period


def apply_mask(time: np.ndarray, flux: np.ndarray, mask: list[dict] | None) -> np.ndarray:
    """``flux`` with NaN at every point the masked second pass of BoxDip dropped: within ``half_primary_days`` of
    ``t0 + k period`` or within ``half_secondary_days`` of ``t0 + (k + 0.5) period``, for each entry of ``mask`` (the
    signal's ``extra["mask"]``). The windows are those of the detector (``box_dip._in_window``, strict inequality).
    An empty or missing mask returns ``flux`` itself; otherwise a new array, the input untouched."""
    if not mask:
        return flux
    time = np.asarray(time)
    hit = np.zeros(time.size, bool)
    for m in mask:
        period, t0 = float(m["period"]), float(m["t0"])
        hit |= np.abs(fold_phase(time, period, t0)) < float(m["half_primary_days"])
        hit |= np.abs(fold_phase(time, period, t0 + 0.5 * period)) < float(m["half_secondary_days"])
    out = np.array(flux, dtype=float)
    out[hit] = np.nan
    return out


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
EPOCH_CLASSES = {"odd": (2, 1), "even": (2, 0)}     # the string forms of stacked_depth's residue-class selector


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
    ``epochs`` keeps all events (``"all"``), only the odd or the even ones (``"odd"``, ``"even"``), or one residue
    class ``(n, r)``: the epochs whose number ``round((t - t0) / period)`` is ``r`` modulo ``n`` (``"odd"`` is
    ``(2, 1)``, ``"even"`` is ``(2, 0)``). The formal
    error is the out-of-window scatter over the square root of the in-window count; it is multiplied by the ratio of
    the robust spread (1.4826 times the median absolute deviation) of the same window's depth at nine control phases
    (0.1 to 0.9, at least 1.5 durations from the primary and from the window) to that formal error whenever the ratio
    exceeds one, which carries the red noise the formal error misses; a real secondary in one control does not count.
    Fewer than three controls leave the error as is. Returns NaN depths and ``n_in = 0`` when the window holds fewer
    than 10 points or the baseline fewer than 50."""
    good = np.isfinite(fnorm) & np.isfinite(time)
    if epochs != "all":
        n, r = EPOCH_CLASSES[epochs] if isinstance(epochs, str) else epochs
        good &= np.round((time - t0) / period).astype(int) % int(n) == int(r)
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


EVENT_COVERAGE_MIN = 0.75       # an event is covered when it holds at least this fraction of the points expected
CHASES_MAX_EVENTS = 5           # Chases is computed only for signals with at most this many events
CHASES_FRACTION = 0.8           # a slid box counts as a competing dip above this fraction of the event's own SES
CHASES_MIN = 0.01               # C_i below this marks the event bad (applied by the rung)
# an epoch is an expected transit only when an observed cadence lies this close to its mid-transit (days): Kepler's
# inter-quarter and monthly gaps plus the 1.5 d gap-edge mask span about 3 d, so a transit inside such a gap is still
# expected (and uncovered), while one in a quarter the target was never observed in is not
EXPECTED_EPOCH_MAX_GAP_DAYS = 3.0
SWEET_FACTORS = (0.5, 1.0, 2.0)
PHASE_MIN_BOX, PHASE_MIN_BASELINE = 5, 50


@dataclass
class PhaseSignificance:
    phases: np.ndarray          # cycle fraction in [-0.5, 0.5)
    sigma: np.ndarray           # depth significance of the box centred at each phase (positive = dip)
    sigma_pri: float
    sigma_sec: float
    phase_sec: float
    sigma_ter: float
    sigma_pos: float
    fa2: float
    ms2: float
    ms3: float


def _nanmax_or_nan(x: np.ndarray) -> float:
    return float(np.nanmax(x)) if x.size and np.isfinite(x).any() else float("nan")


def _slid_box_sigma(cycles: np.ndarray, fn: np.ndarray, phases: np.ndarray, width: float) -> np.ndarray:
    """Depth significance of the box of ``width`` cycles centred at each of ``phases`` on the fold ``cycles`` (in
    [-0.5, 0.5)), against the median and 1.4826 x MAD of the points at least ``PRIMARY_MASK_DURATIONS`` widths from
    the box centre and from phase 0; NaN where the box holds fewer than ``PHASE_MIN_BOX`` points, the baseline fewer
    than ``PHASE_MIN_BASELINE``, or the baseline scatter is zero.

    The same numbers as a pass over the whole fold per phase, without the pass: they agree to about one ulp of the
    baseline level (2e-16 for normalised flux; 5e-11 on sigma at 600 ppm scatter, measured). The fold is sorted once;
    box counts and sums are differences of cumulative sums at ``searchsorted`` positions. The baseline of a phase is
    the away-from-primary set minus a contiguous window of it. Its median is the global order statistic shifted past
    the removed points: with ``s_j`` the sorted global ranks of the removed points, the k-th kept element sits at
    global index ``k + #{j : s_j - j <= k}``. Its MAD is found by bisecting the half-width h in
    ``[0, max(g[-1] - level, level - g[0])]`` (64 steps, so the interval ends at its range / 2**64) for the smallest h
    holding half of the baseline, the removed points counted by their ranks; when ``h = 0`` already holds half of
    it (more than half of the baseline sits exactly at the median) the MAD is set to 0 and sigma is NaN, as the
    definition gives. Total work is about twelve times the number of points, whatever the number of phases. The
    box sums are running sums of the residuals about the fold median, so gross outliers (flux far from the median)
    cost precision in every box mean; the flux is expected normalised and clipped upstream."""
    out = np.full(phases.size, np.nan)
    order = np.argsort(cycles, kind="stable")
    xs, ys = cycles[order], fn[order]
    if xs.size == 0:
        return out
    half, guard = 0.5 * width, PRIMARY_MASK_DURATIONS * width
    ref = float(np.median(ys))
    xt = np.concatenate([xs - 1.0, xs, xs + 1.0])
    csum = np.concatenate([[0.0], np.cumsum(np.tile(ys - ref, 3))])
    lo = np.searchsorted(xt, phases - half, side="right")
    hi = np.searchsorted(xt, phases + half, side="left")
    n_in = hi - lo
    away = np.abs(xs) >= guard
    a_x, a_y = xs[away], ys[away]
    n_a = a_x.size
    if n_a < PHASE_MIN_BASELINE:
        return out
    at = np.concatenate([a_x - 1.0, a_x, a_x + 1.0])
    e_lo = np.searchsorted(at, phases - guard, side="right")
    e_hi = np.searchsorted(at, phases + guard, side="left")
    removed = e_hi - e_lo                                           # baseline points inside the exclusion window
    n_base = n_a - removed
    ok = np.flatnonzero((n_in >= PHASE_MIN_BOX) & (n_base >= PHASE_MIN_BASELINE))
    if ok.size == 0:
        return out
    g_order = np.argsort(a_y, kind="stable")
    g = a_y[g_order]
    rank = np.empty(n_a, np.int64); rank[g_order] = np.arange(n_a)
    m = removed[ok]
    start = np.concatenate([[0], np.cumsum(m)[:-1]])
    kk = np.repeat(np.arange(ok.size), m)
    within = np.arange(kk.size) - start[kk]
    keys = kk * n_a + rank[(e_lo[ok][kk] + within) % n_a]
    keys.sort()                                                     # removed ranks, ascending within each phase
    shift_key = kk * (n_a + 1) + (keys - kk * n_a - within)         # removed rank minus its position: kept before it
    r_lo, r_hi = (n_base[ok] - 1) // 2, n_base[ok] // 2
    ids = np.arange(ok.size)
    j_lo = np.searchsorted(shift_key, ids * (n_a + 1) + r_lo, side="right") - start
    j_hi = np.searchsorted(shift_key, ids * (n_a + 1) + r_hi, side="right") - start
    level = 0.5 * (g[r_lo + j_lo] + g[r_hi + j_hi])

    def count_within(h):
        a, b = np.searchsorted(g, level - h, side="left"), np.searchsorted(g, level + h, side="right")
        gone = np.searchsorted(keys, ids * n_a + b, side="left") - np.searchsorted(keys, ids * n_a + a, side="left")
        return (b - a) - gone

    dev = []
    for need in (r_lo + 1, r_hi + 1):
        low, high = np.zeros(ok.size), np.maximum(g[-1] - level, level - g[0])
        for _ in range(64):
            mid = 0.5 * (low + high)
            enough = count_within(mid) >= need
            high, low = np.where(enough, mid, high), np.where(enough, low, mid)
        dev.append(np.where(count_within(0.0) >= need, 0.0, high))          # a true MAD of 0 is not 2^-64 of the range
    scat = 1.4826 * 0.5 * (dev[0] + dev[1])
    n = n_in[ok]
    mean = (csum[hi[ok]] - csum[lo[ok]]) / n + ref
    with np.errstate(divide="ignore", invalid="ignore"):
        sig = (1 - mean / level) / (scat / np.sqrt(n))
    out[ok] = np.where(scat > 0, sig, np.nan)
    return out


def phase_significance(time, fnorm, period, t0, duration_days) -> PhaseSignificance:
    """Model-shift statistics (Thompson et al. 2018, Appendix A): the box of the detected duration slid around the
    fold in steps of ``duration / SHAPE_BINS_PER_DURATION``, the depth significance at each step against the median
    and robust scatter (1.4826 times the median absolute deviation) of the points at least ``PRIMARY_MASK_DURATIONS``
    durations from the box centre and from the primary (phase 0), so neither the box nor the primary sits in the
    baseline. A step with fewer than 5 points in the box or 50 in the baseline is NaN.

    ``sigma_pri`` is the step at phase 0; ``sigma_sec`` the deepest step at least 1.5 durations from it (at
    ``phase_sec``); ``sigma_ter`` the deepest at least 1.5 durations from both; ``sigma_pos`` the strongest
    brightening (most negative sigma, as a positive number) away from the primary. ``fa2 = sqrt(2) erfcinv(duration
    / period)`` is the significance a single trial of the fold exceeds by chance; ``ms2 = fa2 - (sigma_pri -
    sigma_ter)`` and ``ms3 = fa2 - (sigma_pri - sigma_pos)`` fail the signal when large (the rung applies the
    thresholds). The primary is not compared with the secondary: two equal dips leave ``ms2`` and ``ms3`` low, and the
    secondary rules of the rung handle them."""
    from scipy.special import erfcinv
    time, fnorm = np.asarray(time, float), np.asarray(fnorm, float)
    good = np.isfinite(time) & np.isfinite(fnorm)
    cycles = fold_phase(time[good], period, t0) / period                    # [-0.5, 0.5)
    fn = fnorm[good]
    width = duration_days / period
    step = width / SHAPE_BINS_PER_DURATION
    n_half = int(np.floor(0.5 / step))
    phases = step * np.arange(-n_half, n_half + 1)
    phases = phases[(phases >= -0.5) & (phases < 0.5)]
    sigma = _slid_box_sigma(cycles, fn, phases, width)
    sigma_pri = float(sigma[np.argmin(np.abs(phases))]) if phases.size else float("nan")
    off = np.abs(phases) >= PRIMARY_MASK_DURATIONS * width
    sec = np.where(off, sigma, np.nan)
    if np.isfinite(sec).any():
        k = int(np.nanargmax(sec)); sigma_sec, phase_sec = float(sec[k]), float(phases[k])
        ter = np.where(off & (_circular(phases - phase_sec, 1.0) >= PRIMARY_MASK_DURATIONS * width), sigma, np.nan)
        sigma_ter = _nanmax_or_nan(ter)
    else:
        sigma_sec = phase_sec = sigma_ter = float("nan")
    sigma_pos = _nanmax_or_nan(np.where(off, -sigma, np.nan))
    fa2 = float(np.sqrt(2.0) * erfcinv(width))
    return PhaseSignificance(phases, sigma, sigma_pri, sigma_sec, phase_sec, sigma_ter, sigma_pos, fa2,
                             fa2 - (sigma_pri - sigma_ter), fa2 - (sigma_pri - sigma_pos))


@dataclass
class SweetResult:
    factor: float               # the period multiple (0.5, 1 or 2) of the strongest sine
    amplitude_ppm: float
    snr: float                  # amplitude over its least-squares error


def sweet(time, flux, period, depth_ppm) -> SweetResult:
    """Sine-wave evaluation of the signal (SWEET, Thompson et al. 2018, Appendix A): a linear fit of an offset, a
    cosine and a sine at ``f x period`` for ``f`` in ``SWEET_FACTORS`` to the finite points, keeping the multiple with
    the largest amplitude signal to noise. The error is the least-squares propagation of the amplitude from the
    residual variance and ``inv(X^T X)``. ``depth_ppm`` is the caller's to compare with the amplitude: the rule sits
    in the rung, and no field of the result carries it. ``time`` and ``period`` are in days and ``flux`` is normalised to
    1, so ``amplitude_ppm`` is the relative amplitude times 1e6. Fewer than 4 finite points return a NaN amplitude and snr."""
    time, flux = np.asarray(time, float), np.asarray(flux, float)
    good = np.isfinite(time) & np.isfinite(flux)
    if good.sum() < 4:
        return SweetResult(1.0, float("nan"), float("nan"))
    t, y = time[good] - time[good].min(), flux[good]
    best = SweetResult(1.0, float("nan"), float("-inf"))
    for f in SWEET_FACTORS:
        w = 2 * np.pi * t / (f * period)
        x = np.column_stack([np.ones(t.size), np.cos(w), np.sin(w)])
        coef, *_ = np.linalg.lstsq(x, y, rcond=None)
        resid = y - x @ coef
        try:
            cov = np.linalg.inv(x.T @ x) * float(resid @ resid) / (t.size - 3)
        except np.linalg.LinAlgError:
            continue
        a, b = float(coef[1]), float(coef[2])
        amp = float(np.hypot(a, b))
        if amp <= 0:
            continue
        var = (a * a * cov[1, 1] + b * b * cov[2, 2] + 2 * a * b * cov[1, 2]) / amp ** 2
        if not np.isfinite(var) or var <= 0:
            var = (a * a * cov[1, 1] + b * b * cov[2, 2]) / amp ** 2
        snr = amp / float(np.sqrt(var)) if var > 0 else float("nan")
        if np.isfinite(snr) and snr > best.snr:
            best = SweetResult(float(f), amp * 1e6, snr)
    return best if np.isfinite(best.snr) else SweetResult(1.0, float("nan"), float("nan"))


@dataclass
class EventQuality:
    """Per-event quality of a signal (``event_quality``). Coverage is SkySift's adaptation of Rubble (T18 A.3.7.1):
    T18 compares an event's cadences with the regular 29.42 min cadence, because the Kepler pipeline's flags leave
    about 92 percent of the cadences; SkySift's quality mask (quality == 0) leaves 64 to 93 percent per star, so an
    event is compared with the star's own duty cycle ``duty``."""
    epochs: np.ndarray          # every expected epoch (mid-transit within EXPECTED_EPOCH_MAX_GAP_DAYS of an observed time)
    coverage: np.ndarray        # observed cadences within +-1 duration / (2 duration / median cadence x duty); NaN at cadence 0
    ses: np.ndarray             # depth / error per epoch from _box_depth; NaN with fewer than 3 box points or no data
    chases: np.ndarray | None   # C_i per epoch when len(epochs) <= CHASES_MAX_EVENTS, else None
    duty: float                 # observed / regular cadences inside the observed spans (_duty_cycle); NaN at cadence 0


def _duty_cycle(t_obs: np.ndarray, cadence: float) -> float:
    """The star's duty cycle: the number of observed cadences over the number of regular cadences inside the observed
    spans. The spans are ``t_obs`` (sorted) split at steps longer than ``EXPECTED_EPOCH_MAX_GAP_DAYS``, the gaps across
    which no transit is expected; a span holds ``round(length / cadence) + 1`` regular cadences. The quarter and
    monthly gaps shorter than that stay inside the spans and count against the duty cycle, as they count against the
    events that fall in them. NaN when ``cadence`` is not positive."""
    if not cadence > 0 or t_obs.size == 0:
        return float("nan")
    spans = _segments(t_obs, EXPECTED_EPOCH_MAX_GAP_DAYS)
    regular = sum(round((t_obs[s[-1]] - t_obs[s[0]]) / cadence) + 1 for s in spans)
    return float(t_obs.size / regular)


def _chases_one(t, y, t_event, ses, period, duration_days) -> float:
    """C_i of one event: ``min(dt, P/10) / (P/10)``, ``dt`` the distance from mid-transit to the nearest centre of a
    box of the transit duration, slid over the points of ``t, y`` within P/10, whose significance exceeds
    ``CHASES_FRACTION x ses``. Centres within 1.5 durations of mid-transit are ignored, and so is any centre whose box
    holds fewer than 3 points or whose baseline holds fewer than 10."""
    reach = period / 10.0
    near = np.abs(t - t_event) <= reach
    tw, yw = t[near], y[near]
    dt = reach
    for c in tw[np.abs(tw - t_event) >= PRIMARY_MASK_DURATIONS * duration_days]:
        d = np.abs(tw - c)
        inside, base = d < 0.5 * duration_days, d >= PRIMARY_MASK_DURATIONS * duration_days
        if inside.sum() < 3 or base.sum() < 10:
            continue
        level = np.median(yw[base])
        scat = 1.4826 * np.median(np.abs(yw[base] - level))
        if scat <= 0:
            continue
        sig = (level - np.mean(yw[inside])) / (scat / np.sqrt(inside.sum()))
        if abs(sig) > CHASES_FRACTION * ses:
            dt = min(dt, abs(c - t_event))
    return float(min(dt, reach) / reach)


def event_quality(time, fnorm, period, t0, duration_days, *, observed=None) -> EventQuality:
    """Per-event coverage, single-event statistic and Chases (Thompson et al. 2018, Appendix A).

    ``observed`` marks the cadences that were observed (default: the finite points of ``fnorm``). A caller whose
    ``fnorm`` carries a detector mask (NaN at the first-pass eclipses of a second-pass signal) passes the finite points
    of the unmasked flux: a masked cadence was observed, so it counts towards coverage and the epoch expectation, while
    ses and Chases read only the finite ``fnorm``.

    The events are the expected epochs: every epoch ``round((t - t0) / period)`` whose mid-transit lies within the span
    of the observed times and within ``EXPECTED_EPOCH_MAX_GAP_DAYS`` of an observed cadence. A transit in an
    inter-quarter or monthly gap (with the gap-edge mask about 3 d) is still expected and counts as uncovered; a transit
    in a quarter the target was never observed in is not expected. An expected epoch with no point near mid-transit has
    coverage 0.0 and a NaN ses.
    ``coverage`` counts the observed cadences within one duration of mid-transit over the number expected,
    ``(2 x duration / median observed cadence) x duty``, with ``duty`` the star's duty cycle (``_duty_cycle``: observed
    over regular cadences inside the observed spans, the spans split at steps longer than
    ``EXPECTED_EPOCH_MAX_GAP_DAYS``). This is SkySift's adaptation of Rubble (T18 A.3.7.1): T18 compares with the
    regular 29.42 min cadence because the Kepler pipeline's flags leave about 92 percent of the cadences, while
    SkySift's quality mask leaves 64 to 93 percent per star, so on a 64 percent star every event would read about 0.6
    against the regular cadence and the whole signal would fail as gapped. Against the duty cycle a typical event
    reads about 1; there is no cap, and a window better covered than the star's average exceeds 1. An event emptied by
    a gap still reads 0. ``coverage`` is NaN for every epoch (and ``duty`` NaN) when the median cadence is 0 (more than
    half the time stamps duplicated), which the rung treats as not covered.
    ``ses`` is the depth over the error of ``_box_depth`` on the epoch's points, as ``event_depths`` takes it, NaN when
    the box holds fewer than 3 points or the baseline fewer than 20. ``chases`` is computed only when the number of
    events is at most ``CHASES_MAX_EVENTS`` (otherwise None), and is NaN for an event without a positive finite ses: a box of the
    duration slid across +-P/10 of mid-transit must not find a dip as significant as 0.8 x the event's own ses closer
    than P/10 (``_chases_one``); 1 means none nearer than the window, 0 a competing dip on top of the event."""
    time, fnorm = np.asarray(time, float), np.asarray(fnorm, float)
    obs_mask = np.isfinite(fnorm) if observed is None else np.asarray(observed, bool)
    t_obs = np.sort(time[np.isfinite(time) & obs_mask])
    good = np.isfinite(time) & np.isfinite(fnorm)
    time, fn = time[good], fnorm[good]
    if t_obs.size < 2:
        return EventQuality(np.array([], int), np.array([]), np.array([]), np.array([]), float("nan"))
    cadence = float(np.median(np.diff(t_obs)))
    duty = _duty_cycle(t_obs, cadence)
    ph = fold_phase(time, period, t0)
    ep = np.round((time - t0) / period).astype(int)
    epochs = np.arange(int(np.ceil((t_obs[0] - t0) / period)), int(np.floor((t_obs[-1] - t0) / period)) + 1)
    mids = t0 + epochs * period
    k = np.clip(np.searchsorted(t_obs, mids), 1, t_obs.size - 1)          # the observed times either side of each
    gap = np.minimum(np.abs(t_obs[k] - mids), np.abs(mids - t_obs[k - 1]))
    epochs = epochs[gap <= EXPECTED_EPOCH_MAX_GAP_DAYS]
    # cadences expected in an event's +-1 duration window on this star (duplicated stamps: coverage undefined)
    expected = 2.0 * duration_days / cadence * duty if cadence > 0 else np.nan
    half = duration_days / 2.0
    ep_obs = np.round((t_obs - t0) / period).astype(int)
    near_obs = np.abs(fold_phase(t_obs, period, t0)) < duration_days
    coverage = np.array([np.count_nonzero(near_obs & (ep_obs == e)) / expected for e in epochs])
    ses = np.full(epochs.size, np.nan)
    for i, e in enumerate(epochs):
        s = ep == e
        r = _box_depth(ph[s], fn[s], half) if s.any() else None
        if r is not None and r[1] > 0:
            ses[i] = r[0] / r[1]
    chases = None
    if epochs.size <= CHASES_MAX_EVENTS:
        chases = np.full(epochs.size, np.nan)
        for i, e in enumerate(epochs):
            if np.isfinite(ses[i]) and ses[i] > 0:         # a brightening has no dip to compete with
                chases[i] = _chases_one(time, fn, t0 + e * period, ses[i], period, duration_days)
    return EventQuality(epochs, coverage, ses, chases, duty)


def circular_duration_days(period_days: float, mass_msun: float = 0.5, radius_rsun: float = 0.5) -> float:
    """Central-transit duration for a circular orbit: T = P/pi * asin(R*/a), a from Kepler's third law."""
    a_au = (mass_msun * (period_days / 365.25) ** 2) ** (1 / 3)
    a_rsun = a_au * 215.032
    return period_days / np.pi * np.arcsin(min(1.0, radius_rsun / a_rsun))
