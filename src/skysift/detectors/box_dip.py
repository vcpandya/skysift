"""Complete BLS search over 0.5 to 200 d on a 4-year Kepler baseline costs ~250 s single-core (astropy BLS passes over every point per trial period); throughput comes from the process pool; `oversample` and `n_refine` are the knobs for a faster, less complete sweep, to be judged by injection-recovery."""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import numpy as np
from astropy.timeseries import BoxLeastSquares

from skysift.config import Thresholds
from skysift.detectors.base import register
from skysift.detectors.periodogram import PeriodogramSummary
from skysift.lc.ops import circular_duration_days, n_window_epochs, stacked_depth
from skysift.models import LightCurve, Signal

MIN_POINTS = 500
DURATIONS_H = np.array([1, 1.5, 2, 3, 4, 6, 9, 12])
MIN_BAND_PERIODS = 50
MIN_BAND_SIGMA_FRACTION = 0.25  # a band whose sigma is under this fraction of the median band sigma takes that median sigma
MAX_DURATION_FRACTION = 0.9     # trial durations must stay below this fraction of the band's shortest period
ALIAS_PARITY_SIGMA = 5.0        # the deepest epoch class of a sub-harmonic alias beats the others by this many combined errors
ALIAS_PARITY_RATIO = 0.5        # and every other class (and their mean) is under this fraction of the deepest one
MAX_SUBHARMONIC = 16            # sub-harmonics P/2 .. P/16 are tested (n = 7 caught K06356.01 ranked at 24.09/7 d; 8 left
                                # the 24.0/11 and 24.0/13 d residues of a deep V binary as alias rows that passed every rung)


def _distinct_sigma(pw: np.ndarray) -> float:
    """MAD-sigma of the distinct powers of a band (values equal to 1e-9 of the band's largest power are one value):
    a tied band's repeated power is one box outcome counted many times, so its distinct values are its effective
    trials. NaN when fewer than MIN_BAND_PERIODS distinct values remain."""
    scale = float(np.max(np.abs(pw))) or 1.0
    distinct = np.unique(np.round(pw / scale, 9)) * scale
    if distinct.size < MIN_BAND_PERIODS:
        return np.nan
    return 1.4826 * float(np.median(np.abs(distinct - np.median(distinct))))


def _band_norms(power: np.ndarray, band_powers: list[np.ndarray], n_bands: int) -> tuple[np.ndarray, np.ndarray]:
    """Per-band SDE normalisation: the noise level of the BLS spectrum changes with period, so each band gets its own
    median and sigma. The sigma is the MAD-sigma of the band's DISTINCT powers (``_distinct_sigma``): on a sparse curve
    the long period bands tie, as the best box of most trial periods lands on the same one or two events and the power
    repeats exactly, and the MAD of the raw powers shrinks with the tie fraction (to 0 or 1e-14 past half), which
    turned 2 sigma bumps into SDEs of 1e13 in the pilot of 19 October 2026 (a 2001 point curve over 1054 d had raw
    MAD-sigmas of 0, 4e-14 and 0.08 in its three longest bands) and inflated SDEs 1.5 times in bands with 12 percent
    ties. The reference noise level is the median of the band sigmas (the global MAD of the concatenated spectrum also
    measures how far the band medians drift apart, 6.6 to 57 on a star with a deep binary, and would make healthy
    bands fall back): a band with fewer than MIN_BAND_PERIODS distinct powers, or a sigma below MIN_BAND_SIGMA_FRACTION
    of the reference, keeps its own median (the tied level is the right baseline) and takes the reference sigma; a band
    with fewer than MIN_BAND_PERIODS trial periods takes the global median too. With no band sigma at all the reference
    is the MAD-sigma of the distinct powers of the whole spectrum, then its standard deviation, then inf (a constant
    spectrum: every SDE 0, nothing detected). An empty spectrum gets median 0 and sigma inf."""
    band_med, band_std = np.zeros(n_bands), np.full(n_bands, np.inf)
    if power.size == 0:
        return band_med, band_std
    band_med[:] = float(np.median(power))
    own = np.full(n_bands, np.nan)
    for b, pw in enumerate(band_powers):
        if pw.size < MIN_BAND_PERIODS: continue
        band_med[b] = float(np.median(pw))
        own[b] = _distinct_sigma(pw)
    finite = own[np.isfinite(own)]
    if finite.size:
        reference = float(np.median(finite))
    else:
        reference = _distinct_sigma(power)
        if not np.isfinite(reference) or reference == 0:
            reference = float(np.std(power)) or np.inf
    for b in range(n_bands):
        keep = np.isfinite(own[b]) and own[b] >= MIN_BAND_SIGMA_FRACTION * reference
        band_std[b] = own[b] if keep else reference
    return band_med, band_std


@dataclass
class SearchResult:
    signals: list[Signal] = field(default_factory=list)
    periodogram: PeriodogramSummary | None = None
    spectrum: dict | None = None            # full arrays (period, power, sde, duration), only on request


def _band_durations(p_mid: float) -> np.ndarray:
    """Trial durations (days) plausible for a circular orbit at p_mid: 0.3 to 3 times the central-transit duration."""
    all_d = DURATIONS_H / 24.0
    t_circ = circular_duration_days(p_mid)
    keep = all_d[(all_d >= 0.3 * t_circ) & (all_d <= 3.0 * t_circ)]
    if keep.size < 3:
        keep = np.sort(all_d[np.argsort(np.abs(all_d - t_circ))[:3]])
    return keep


# every ratio p/q with 1 <= p, q <= MASK_ALIAS_ORDER: the harmonics and sub-harmonics a masked periodic signal (and the
# window function its mask leaves behind) can still raise in the second-pass spectrum; 8 like MAX_SUBHARMONIC (the
# 24.09/7 d residue of K06356.01 passed the filter at order 4)
MASK_ALIAS_ORDER = 8
MASK_ALIAS_RATIOS = tuple(sorted({p / q for p in range(1, MASK_ALIAS_ORDER + 1) for q in range(1, MASK_ALIAS_ORDER + 1)}))
MASK_WINDOW_DURATIONS = 0.75    # floor of the mask half-width in durations (a 1.5-duration window) around the primary
                                # and around phase 0.5; wider fixed windows swallow a short-period curve entirely
MASK_EXTENT_BINS = 400          # phase bins of the fold on which the extent of the masked event is measured
MASK_EXTENT_SIGMA = 3.0         # a bin belongs to the event while its mean is this many binned scatters below the rest
MASK_EXTENT_MIN_POINTS = 3      # bins with fewer points are not used
MASK_EXTENT_MAX_PHASE = 0.25    # the walk stops this far in phase from the deepest bin on either side
MASK_EXTENT_MAX_HALF = 0.15     # no mask half-width exceeds this fraction of the masked period


def _event_extent(t: np.ndarray, y: np.ndarray, period: float, t0: float, duration: float,
                  phase: float) -> float | None:
    """Total width in days of the event around ``phase`` (0 for the primary, 0.5 for the secondary) on the fold at
    ``period`` from ``t0``, or None when no event is seen there. The fold is binned in MASK_EXTENT_BINS phase bins
    (bins with at least MASK_EXTENT_MIN_POINTS points); the out-of-event level and scatter are the median and 1.4826 x
    MAD of the bin means more than two durations from phase 0 and from phase 0.5. From the deepest bin within one
    duration of ``phase`` the run walks outwards on each side while the bin mean stays more than MASK_EXTENT_SIGMA
    scatters below that level, letting one bin break the run on each side; the extent is the phase span of the run.
    The box BLS fits to a V-shaped eclipse is narrower than the eclipse (a 6 h box on the 11 h eclipses of
    K06356.01), so a mask sized from the box alone leaves the wings in the curve."""
    nb = MASK_EXTENT_BINS
    ph = ((t - t0) / period - phase + 0.5) % 1.0 - 0.5                 # phase from the event centre, in [-0.5, 0.5)
    idx = np.minimum(((ph + 0.5) * nb).astype(int), nb - 1)
    count = np.bincount(idx, minlength=nb)
    total = np.bincount(idx, weights=y, minlength=nb)
    valid = count >= MASK_EXTENT_MIN_POINTS
    mean = np.where(valid, total / np.maximum(count, 1), np.nan)
    centre = (np.arange(nb) + 0.5) / nb - 0.5                          # bin centres, phase from the event centre
    absolute = (centre + phase) % 1.0                                  # bin centres, phase from t0
    d0 = np.minimum(absolute, 1.0 - absolute)
    d5 = np.abs(absolute - 0.5)
    w = 2.0 * duration / period
    out = valid & (d0 > w) & (d5 > w)
    if out.sum() < 10:
        return None
    level = float(np.median(mean[out]))
    scatter = 1.4826 * float(np.median(np.abs(mean[out] - level)))
    if not scatter > 0:
        return None
    low = valid & (mean - level < -MASK_EXTENT_SIGMA * scatter)
    near = np.where(valid & (np.abs(centre) <= duration / period))[0]
    if near.size == 0:
        return None
    deep = int(near[np.argmin(mean[near])])
    if not low[deep]:
        return None
    reach = int(MASK_EXTENT_MAX_PHASE * nb)
    ends = []
    for step in (-1, 1):
        end, broken = deep, False
        for k in range(1, reach + 1):
            j = (deep + step * k) % nb
            if low[j]:
                end = deep + step * k
            elif broken:
                break
            else:
                broken = True
        ends.append(end)
    return (ends[1] - ends[0] + 1) * period / nb


ALIAS_COARSE_STEPS = 10         # alias window of the first pass, in coarse frequency steps at the peak's period


def _alias_of(period: float, earlier: list[Signal], spec: dict | None = None) -> int | None:
    """Index of the first signal of ``earlier`` whose frequency times a ratio p/q (p and q up to MASK_ALIAS_ORDER, 1
    included) lies within ALIAS_COARSE_STEPS coarse frequency steps (``spec["df"]`` at the trial period nearest
    ``period``) of ``1 / period``, or None; without a spectrum the window is 1 percent of the period. The window
    follows the search's own resolution, so a real planet a percent away from a small-integer ratio of a stronger
    signal (a near-resonant pair, 2.02 x 0.61 d sits 580 steps from the 2:1 alias) is not an alias. It is wider than
    the DUPLICATE_COARSE_STEPS of ``_duplicates``: an alias peak lands a few steps off the exact ratio and a resolved
    one carries that offset (3.2002 d, 4 steps from 24.0 x 2/15 d, resolved to 9.6007 d, missed the 3-step window of
    24.0 x 2/5 d on the 6000 ppm V binary). Ten steps cover 0.05 percent of the 0.5 to 30 d frequency axis for a 24 d
    signal and 0.4 percent for a 0.6 d one, against 2.7 and 24 percent for the 1 percent windows."""
    if spec is not None and spec["period"].size:
        tol = ALIAS_COARSE_STEPS * float(spec["df"][int(np.argmin(np.abs(spec["period"] - period)))])
        f = 1.0 / period
        for j, s in enumerate(earlier):
            if any(abs(f - r / s.period) < tol for r in MASK_ALIAS_RATIOS):
                return j
        return None
    for j, s in enumerate(earlier):
        ratio = period / s.period
        if any(abs(ratio / r - 1.0) < 0.01 for r in MASK_ALIAS_RATIOS):
            return j
    return None


def _reveals_subharmonic(sig: Signal, kept: Signal) -> bool:
    """True when ``sig`` sits within 1 percent of n times the period of ``kept`` (n at least 2) and is at least half
    of n times as deep: then ``kept`` is the P/n fold of ``sig``, which stacks each event with n - 1 empty windows,
    and not a signal of its own (a true period folded at nP keeps its depth: 0.61 d against 3 x 0.61 d)."""
    n = round(sig.period / kept.period)
    return (n >= 2 and abs(sig.period / (n * kept.period) - 1.0) < 0.01
            and sig.depth_ppm >= 0.5 * n * kept.depth_ppm)


REFILL_FACTOR = 4               # without the refine stage the ranking offers this many times top_k separated peaks


COMBINATION_ORDER = 2           # m f_i - n f_j with m, n up to this, plus f_i + f_j
COMBINATION_RESOLUTION = 2.0    # frequency tolerance for a combination match, in units of 1/baseline


def _is_alias(period: float, earlier_periods: list[float], baseline: float | None = None) -> bool:
    """True when ``period`` sits within 1 percent of a small-integer ratio (p/q, p and q up to MASK_ALIAS_ORDER) of an
    earlier period, or, given the baseline, within COMBINATION_RESOLUTION/baseline in frequency of a combination of
    two earlier frequencies (m f_i - n f_j with m, n up to COMBINATION_ORDER, or f_i + f_j). A multi-periodic variable
    (pulsation, differential rotation) raises sidelobes at exactly those combination frequencies, equally spaced in
    frequency with its main peaks, and masking the strongest peak only promotes the next sidelobe
    (gaia-dr3-2075988698983795072: 1.574, 1.386 and 1.762 per day, spaced by 0.188)."""
    if any(abs(period / e / r - 1.0) < 0.01 for e in earlier_periods for r in MASK_ALIAS_RATIOS):
        return True
    if baseline is None or len(earlier_periods) < 2:
        return False
    f, tol = 1.0 / period, COMBINATION_RESOLUTION / baseline
    freqs = [1.0 / e for e in earlier_periods]
    combos = [fi + fj for i, fi in enumerate(freqs) for fj in freqs[i + 1:]]
    combos += [m * fi - n * fj for fi in freqs for fj in freqs if fi != fj
               for m in range(1, COMBINATION_ORDER + 1) for n in range(1, COMBINATION_ORDER + 1)]
    return any(c > 0 and abs(f - c) < tol for c in combos)


def _in_window(t: np.ndarray, period: float, t0: float, half_width: float) -> np.ndarray:
    ph = (t - t0 + 0.5 * period) % period - 0.5 * period
    return np.abs(ph) < half_width


DUPLICATE_COARSE_STEPS = 3      # after a doubling, a lower-ranked peak this many coarse frequency steps from the new
DUPLICATE_FALLBACK = 0.005      # period is the same signal; without a spectrum, this fraction of the period


def _duplicates(period: float, others: list[Signal], spec: dict | None) -> list[Signal]:
    """The signals of ``others`` that are the signal at ``period`` again: within DUPLICATE_COARSE_STEPS coarse
    frequency steps (``spec["df"]`` at the trial period nearest ``period``) or, without a spectrum, within
    DUPLICATE_FALLBACK of the period. That is the resolution the search itself has there, so an unrelated signal a
    couple of percent away is kept."""
    if spec is not None and spec["period"].size:
        tol = DUPLICATE_COARSE_STEPS * float(spec["df"][int(np.argmin(np.abs(spec["period"] - period)))])
    else:
        tol = DUPLICATE_FALLBACK / period
    return [s for s in others if abs(1.0 / s.period - 1.0 / period) < tol]


HARMONIC_ORDER = 2              # exact harmonics P b/a and P a/b of a kept signal, a up to this and any integer b


def _harmonic_of(period: float, earlier: list[Signal], spec: dict | None) -> int | None:
    """Index of the first signal of ``earlier`` whose frequency times b/a or a/b (a up to HARMONIC_ORDER, b any
    positive integer) lies within DUPLICATE_COARSE_STEPS coarse frequency steps of ``1 / period``, or None (always
    None without a spectrum). A deep periodic signal raises a peak at every fold that stacks a fraction of its events
    (P/11 and 2P/11 as well as P/2), and the sub-harmonic resolution stops at MAX_SUBHARMONIC: on a 24.0 d V binary
    (1400 d, 6000 ppm) 24.0/11 and 24.0/13 d took the slots a real 2.1 d planet needed. The tolerance is the search's
    own resolution there (as in ``_duplicates``); with a up to 2 the windows cover 0.5 percent of the 0.5 to 30 d
    frequency axis for a 24 d signal, where a up to 8 would cover 5.9 percent and caught the 2.1 d planet at 24.0 x
    7/80 d.
    """
    if spec is None or not spec["period"].size:
        return None
    tol = DUPLICATE_COARSE_STEPS * float(spec["df"][int(np.argmin(np.abs(spec["period"] - period)))])
    f = 1.0 / period
    for j, s in enumerate(earlier):
        fk = 1.0 / s.period
        for a in range(1, HARMONIC_ORDER + 1):
            b = round(a * f / fk)                                   # f = fk b / a: a fold at a/b of the period
            if b >= 1 and abs(f - fk * b / a) < tol:
                return j
            b = round(a * fk / f)                                   # f = fk a / b: b/a times the period
            if b >= 1 and abs(f - fk * a / b) < tol:
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
    version = "5"           # 5: band SDE sigma from distinct powers, fallback to the median band sigma (19 October 2026)
                            # 4: tied bands fell back to the global values (19 October, superseded the same day)
                            # 3: extent-sized mask of every detected signal and alias rows in the first-pass list (18 October)
                            # 2: sub-harmonic resolution up to P/8 on every first-pass signal (18 October 2026)
                            # 3: extent-sized mask, alias filter to order 8, first pass refilled to top_k independent
                            # signals (18 October 2026)
    cost_class = "cpu_medium"

    def __init__(self, top_k: int = 3, oversample: float = 1.5, n_bands: int = 24, refine_oversample: float = 6.0,
                 n_refine: int = 40, two_stage: bool = True, mask_iterations: int = 1, alias_resolution: int = 1):
        self.top_k, self.oversample, self.n_bands = top_k, oversample, n_bands
        self.refine_oversample, self.n_refine, self.two_stage = refine_oversample, n_refine, two_stage
        # after a first pass whose top peak clears the detection threshold, mask that signal (primary and phase 0.5)
        # and search the remaining points once more, so a dominant variable cannot hide an independent signal
        self.mask_iterations = mask_iterations
        # re-issue each first-pass signal at n times its period (n up to MAX_SUBHARMONIC) when one residue class of
        # its epochs modulo n holds the events and the others are empty (0 turns it off)
        self.alias_resolution = alias_resolution

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
            durs = durs[durs < MAX_DURATION_FRACTION * p_lo]     # astropy needs every duration below every period
            band_durs.append(durs)
            if durs.size == 0:                                   # band shorter than any trial duration: nothing to do
                for key in keys: parts[key].append(np.array([]))
                continue
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
        spec["band"] = spec["band"].astype(int)                  # empty skipped bands concatenate as float
        band_med, band_std = _band_norms(spec["power"], parts["power"], self.n_bands)
        spec.update(band_durs=band_durs, band_med=band_med, band_std=band_std,
                    sde=(spec["power"] - band_med[spec["band"]]) / band_std[spec["band"]])
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

    def _rank(self, model: BoxLeastSquares, spec: dict) -> tuple[list[tuple], list[float], int]:
        """Candidates ``(period, power, duration, t0, depth, depth_err, band)`` with their per-band SDE. Selection and
        ranking use the SDE, not raw power, so every band competes on the same scale."""
        periods, power = spec["period"], spec["power"]
        order = np.argsort(spec["sde"])[::-1]
        if self.two_stage:
            cands = [self._refine(model, spec, i) for i in _separated(order, periods, self.n_refine)]
            n_refined = len(cands)
        else:
            cands = [(float(periods[i]), float(power[i]), float(spec["duration"][i]), float(spec["t0"][i]),
                      float(spec["depth"][i]), float(spec["err"][i]), int(spec["band"][i]))
                     for i in _separated(order, periods, REFILL_FACTOR * self.top_k)]
            n_refined = 0
        sdes = [float((c[1] - spec["band_med"][c[6]]) / spec["band_std"][c[6]]) for c in cands]
        return cands, sdes, n_refined

    def _signal(self, lc: LightCurve, t: np.ndarray, y: np.ndarray, spec: dict, cand: tuple, sde: float,
                baseline: float, n_refined: int) -> Signal:
        p, _pw, dur, t0, depth, err, b = cand
        n_events = n_window_epochs(t, y, p, t0, dur)
        return Signal(target_id=str(lc.meta.get("target_id", "")), detector=self.name, detector_version=self.version,
                      period=p, t0=t0, duration=dur, depth_ppm=depth * 1e6, snr=float(depth / max(err, 1e-12)),
                      sde=sde, n_events=n_events,
                      extra={"baseline_days": baseline, "sde_spectrum_std": float(spec["band_std"][b]), "band": b,
                             "alias_of": None, "aliases_skipped": 0, "n_periods": int(spec["period"].size),
                             "n_bands": self.n_bands, "two_stage": self.two_stage, "n_refined": n_refined})

    def _subharmonic(self, t: np.ndarray, y: np.ndarray, p: float, t0: float, duration: float,
                     pmax: float) -> tuple | None:
        """The smallest ``n`` in 2 .. MAX_SUBHARMONIC (with ``n * p`` inside the window) at which the fold at ``p`` is
        a sub-harmonic alias, as ``(n, r_deep, class_depths, deep, contrast_sigma)``, or None. At ``p = P / n`` the
        events of a period-P signal sit in one residue class of the epoch number modulo ``n``, so that class's
        stacked depth stands out and the other classes are empty (or, for a binary, hold a shallower eclipse). A class
        whose depth cannot be measured skips that ``n``."""
        for n in range(2, MAX_SUBHARMONIC + 1):
            if n * p > pmax:
                return None
            cls = [stacked_depth(t, y, p, t0, duration, epochs=(n, r)) for r in range(n)]
            if not all(c.n_in and np.isfinite(c.depth_ppm) and np.isfinite(c.err_ppm) and c.err_ppm > 0 for c in cls):
                continue
            r_deep = int(np.argmax([c.depth_ppm for c in cls]))
            deep = cls[r_deep]
            rest = [c for r, c in enumerate(cls) if r != r_deep]
            w = np.array([1.0 / c.err_ppm ** 2 for c in rest])
            rest_depth = float(np.sum(w * np.array([c.depth_ppm for c in rest])) / w.sum())
            rest_err = float(1.0 / np.sqrt(w.sum()))
            contrast = (deep.depth_ppm - rest_depth) / float(np.hypot(deep.err_ppm, rest_err))
            limit = ALIAS_PARITY_RATIO * deep.depth_ppm
            if (deep.depth_ppm > 0 and contrast > ALIAS_PARITY_SIGMA and rest_depth < limit
                    and all(c.depth_ppm < limit for c in rest)):
                return n, r_deep, [float(c.depth_ppm) for c in cls], deep, float(contrast)
        return None

    def _resolve_alias(self, t: np.ndarray, y: np.ndarray, sig: Signal, pmax: float, spec: dict | None,
                       others: list[Signal] = ()) -> Signal:
        """Per-band SDE ranking can put a deep signal at an integer fraction of its period: the P/n fold stacks every
        event with n - 1 empty (or, for a binary, secondary) windows and can still win its quieter band. KOI K06356.01
        (24.09 d) came out of the live run at 24.09/7, /3, /2 and /35 d. At P/n the events sit in one residue class of
        the epoch number modulo n, so a strong contrast between that class and the others is the signature
        (``_subharmonic``). The smallest triggering n is taken and the test repeated at the new period, so a
        composite factor (P/4 resolves through P/2) reaches the true period; a multiple of the true factor never
        triggers, because it splits the events over several classes. The signal is re-issued at the product of the
        factors, t0 on the deepest class, with that class's depth and snr and events recounted. A correctly ranked
        planet has equal classes and is never touched; the ratio keeps a 5-sigma noise fluke from multiplying a real
        planet, and a binary whose secondary is at least half its primary is left to rung 2.

        The SDE at the new period is the refined SDE of the best signal of ``others`` there (see ``_duplicates``),
        else the per-band SDE of the trial period nearest it when the spectrum is given. The resolved signal keeps the
        larger of that and the original peak's own SDE, which made the detection, with the band of the one kept;
        ``extra["alias_resolved"]`` records the original period, the total factor, the factor of each step, the class
        depths and contrast of the last step, and both SDEs (``sde_original``, ``sde_resolved``, None when neither
        source exists)."""
        if not self.alias_resolution:
            return sig
        p, t0, factors, last = sig.period, sig.t0, [], None
        while (hit := self._subharmonic(t, y, p, t0, sig.duration, pmax)) is not None:
            n, r_deep, depths, deep, contrast = hit
            # stacked_depth numbers epochs round((t - t0) / p): class r holds the events at t0 + p (r + n k)
            t0, p = t0 + r_deep * p, n * p
            factors.append(n)
            last = (depths, deep, contrast)
        if not factors:
            return sig
        depths, deep, contrast = last
        sde_new, band_new = None, None
        dup = _duplicates(p, list(others), spec)
        if dup:
            best = max(dup, key=lambda s: s.sde)
            sde_new, band_new = float(best.sde), (best.extra["band"], best.extra["sde_spectrum_std"])
        elif spec is not None and spec["period"].size:
            i = int(np.argmin(np.abs(spec["period"] - p)))
            b = int(spec["band"][i])
            sde_new, band_new = float(spec["sde"][i]), (b, float(spec["band_std"][b]))
        extra = dict(sig.extra)
        extra["alias_resolved"] = {"from_period": float(sig.period), "factor": int(np.prod(factors)),
                                   "factors": [int(f) for f in factors], "class_depths_ppm": depths,
                                   "contrast_sigma": contrast, "sde_original": float(sig.sde), "sde_resolved": sde_new}
        update = {"period": float(p), "t0": float(t0), "depth_ppm": float(deep.depth_ppm),
                  "snr": float(deep.depth_ppm / deep.err_ppm),
                  "n_events": n_window_epochs(t, y, p, t0, sig.duration), "extra": extra}
        if sde_new is not None and sde_new > sig.sde:
            update["sde"] = sde_new
            extra.update(band=band_new[0], sde_spectrum_std=band_new[1])
        return sig.model_copy(update=update)

    def _masked_pass(self, lc: LightCurve, t: np.ndarray, y: np.ndarray, sigma: float, baseline: float,
                     th: Thresholds, pmax: float, first: list[Signal]) -> list[Signal]:
        """Second pass with every independent first-pass signal (``alias_of`` None) at or above ``sde_min`` masked:
        their points around every primary and every phase-0.5 secondary are dropped, the same grid is searched again,
        and the strongest remaining peak is kept when it clears the detection threshold on the masked spectrum and is
        neither a small-integer alias (``_is_alias``) nor an exact harmonic (``_harmonic_of``) of an independent
        first-pass period. Masking every detected signal, not only the strongest, keeps a companion's own
        sub-harmonics (2.1 x 11/3 d next to a 24.0 d binary) out of the second pass. One iteration, at most one extra
        signal: the cost is one more coarse search.

        Around each masked signal the half-width is max(MASK_WINDOW_DURATIONS x duration, 0.5 x extent + 0.25 x
        duration), with the extent measured on the fold (``_event_extent``) for the primary and for the secondary on
        its own; a secondary without a measurable run takes the primary's half-width. No half-width exceeds
        MASK_EXTENT_MAX_HALF (0.15) of the signal's period, so a strong residual sinusoid cannot mask most of the
        curve. ``extra["masked_period"]`` lists the masked periods and ``extra["mask_half_days"]`` the (primary,
        secondary) half-widths of each; ``extra["mask"]`` holds one ``{"period", "t0", "half_primary_days",
        "half_secondary_days"}`` per masked signal, so the pipeline and the dashboard rebuild the mask
        (``skysift.lc.ops.apply_mask``) and vet and draw the signal on the flux this pass searched."""
        masked = [s for s in first if s.extra.get("alias_of") is None and s.sde >= th.sde_min] or first[:1]
        keep = np.ones(t.size, bool)
        halves = []
        for m in masked:
            floor, cap = MASK_WINDOW_DURATIONS * m.duration, MASK_EXTENT_MAX_HALF * m.period
            ext_p = _event_extent(t, y, m.period, m.t0, m.duration, 0.0)
            ext_s = _event_extent(t, y, m.period, m.t0, m.duration, 0.5)
            half_p = min(cap, floor if ext_p is None else max(floor, 0.5 * ext_p + 0.25 * m.duration))
            half_s = min(cap, half_p if ext_s is None else max(floor, 0.5 * ext_s + 0.25 * m.duration))
            keep &= ~_in_window(t, m.period, m.t0, half_p) & ~_in_window(t, m.period, m.t0 + 0.5 * m.period, half_s)
            halves.append([float(half_p), float(half_s)])
        if keep.sum() < MIN_POINTS:
            return []
        t2, y2 = t[keep], y[keep]
        model = BoxLeastSquares(t2, y2, dy=np.full(t2.size, sigma))
        spec = self._coarse_search(model, baseline, th, pmax)
        if spec["period"].size == 0:
            return []
        cands, sdes, n_refined = self._rank(model, spec)
        # the filters test the independent signals only: an alias row is itself a ratio of one of them, and its own
        # p/q windows would only widen the net (2.1 d sits at 8/7 of the 24.0/13 d row of a deep binary)
        indep = [s for s in first if s.extra.get("alias_of") is None]
        earlier = [s.period for s in indep]
        for ci in np.argsort(sdes)[::-1]:
            if sdes[ci] < th.sde_min:
                return []
            if _is_alias(cands[ci][0], earlier, baseline) or _harmonic_of(cands[ci][0], indep, spec) is not None:
                continue
            sig = self._signal(lc, t2, y2, spec, cands[ci], sdes[ci], baseline, n_refined)
            sig.extra.update({"pass": 2, "masked_period": [float(m.period) for m in masked],
                              "masked_points": int((~keep).sum()), "mask_half_days": halves,
                              "mask": [{"period": float(m.period), "t0": float(m.t0), "half_primary_days": hp,
                                        "half_secondary_days": hs} for m, (hp, hs) in zip(masked, halves)]})
            return [sig]
        return []

    def detect(self, lc: LightCurve, fnorm: np.ndarray, thresholds: Thresholds) -> list[Signal]:
        return self.search(lc, fnorm, thresholds).signals

    def search(self, lc: LightCurve, fnorm: np.ndarray, thresholds: Thresholds, keep_spectrum: bool = False) -> SearchResult:
        """Signals plus the periodogram summary built from the same per-band SDE spectrum the ranking uses; the full
        spectrum arrays come back only with ``keep_spectrum``.

        The first-pass list holds up to ``top_k`` mutually independent signals, then up to ``top_k`` alias rows, filled
        from the SDE ranking (the separated peaks of ``_rank``: n_refine of them, or REFILL_FACTOR x top_k without the
        refine stage). Each peak goes through the sub-harmonic resolution; one that lands on a signal already listed
        (``_duplicates``) is dropped. One within ALIAS_COARSE_STEPS coarse frequency steps of an independent signal
        times p/q (p and q up to MASK_ALIAS_ORDER, ``_alias_of``) or at an exact harmonic of one (``_harmonic_of``)
        becomes an alias row with ``extra["alias_of"]`` set to that signal's index, so the rungs vet it (rung 2 rejects
        a true alias, a near-resonant planet can pass), but only when that signal's SDE is at or above ``sde_min``;
        the alias of a sub-threshold signal is not returned. The others take the independent slots. A peak whose
        resolution lands on n times an independent period with about n times its depth is independent, since the kept
        signal is then its P/n fold. Alias rows follow the independent signals in SDE order. The walk stops once both
        lists are full (the alias list counts as full when no independent signal reaches ``sde_min``) or the peaks run
        out, and no later peak is resolved; each independent signal counts the peaks it absorbed but which were not
        returned (duplicates, aliases past the cap or of a sub-threshold signal) in ``extra["aliases_skipped"]``."""
        th = thresholds
        m = np.isfinite(fnorm) & np.isfinite(lc.time)
        if m.sum() < MIN_POINTS:
            return SearchResult()
        t, y = lc.time[m], fnorm[m]
        med = np.median(y); mad = 1.4826 * np.median(np.abs(y - med))
        if mad == 0:
            return SearchResult()
        # clip upward outliers only: transits are downward and must survive however deep they are
        k = (y - med) < 5 * mad
        if k.sum() < MIN_POINTS:
            return SearchResult()
        t, y = t[k], y[k]
        baseline = float(t.max() - t.min())
        pmax = min(th.max_period_days, baseline / 3.0)
        if pmax <= th.min_period_days:
            return SearchResult()
        # constant per-point uncertainty from the robust scatter, so depth_err (and snr) are in real units
        sigma = max(1.4826 * float(np.median(np.abs(y - np.median(y)))), 1e-12)
        model = BoxLeastSquares(t, y, dy=np.full(t.size, sigma))
        spec = self._coarse_search(model, baseline, th, pmax)
        periods, power = spec["period"], spec["power"]
        if periods.size == 0:
            return SearchResult()
        cands, sdes, n_refined = self._rank(model, spec)
        cp = np.array([c[0] for c in cands])
        ranked = _separated(np.argsort(sdes)[::-1], cp, len(cands))
        raw = [self._signal(lc, t, y, spec, cands[ci], float(sdes[ci]), baseline, n_refined) for ci in ranked]
        # in rank order, each peak is resolved first (lazily: the walk stops once top_k independent signals and top_k
        # alias rows are listed). A resolved period on a listed signal (the sub-harmonics of one object, or a
        # lower-ranked peak at the true period, which lends its refined SDE; three coarse frequency steps) is
        # dropped; one at a p/q alias or an exact harmonic of an independent signal is an alias row. One exception: a
        # peak whose resolution lands on n times an independent period with n times its depth
        # (``_reveals_subharmonic``) shows that the kept signal is itself a sub-harmonic the resolver could not fold
        # back (too few events), and is independent (0.7/2 d resolved to 0.7 d next to an unresolved 0.7/3 d on a
        # 15 d curve)
        # an alias row is kept only for an independent signal at or above sde_min: it is there so the rungs can judge a
        # near-resonant planet next to a real detection, and the alias of a noise peak carries no information (three
        # rows of three noise peaks doubled the stored peaks of a pure-noise curve)
        out: list[Signal] = []
        rows: list[Signal] = []
        for j, raw_sig in enumerate(raw):
            if len(out) >= self.top_k and (len(rows) >= self.top_k or all(s.sde < th.sde_min for s in out)):
                break
            sig = self._resolve_alias(t, y, raw_sig, pmax, spec, others=raw[:j] + raw[j + 1:])
            dup = _duplicates(sig.period, out + rows, spec)
            if dup:
                owner = dup[0] if dup[0].extra["alias_of"] is None else out[dup[0].extra["alias_of"]]
                owner.extra["aliases_skipped"] += 1
                continue
            a = _alias_of(sig.period, out, spec)
            if a is None:
                a = _harmonic_of(sig.period, out, spec)
            if a is not None and sig is not raw_sig and _reveals_subharmonic(sig, out[a]):
                a = None
            if a is None:
                if len(out) < self.top_k:
                    out.append(sig)
            elif len(rows) < self.top_k and out[a].sde >= th.sde_min:
                sig.extra["alias_of"] = a
                rows.append(sig)
            else:
                out[a].extra["aliases_skipped"] += 1
        out += sorted(rows, key=lambda r: -r.sde)
        # the periodogram summary and its peaks describe the first-pass spectrum only; a second-pass signal has its
        # own SDE on the masked spectrum and is not drawn on this envelope
        edges = np.geomspace(th.min_period_days, pmax, self.n_bands + 1)
        peaks = [{"period": s.period, "sde": s.sde, "duration": s.duration, "rank": i} for i, s in enumerate(out)]
        if self.mask_iterations and out and out[0].sde >= th.sde_min:
            out.extend(self._masked_pass(lc, t, y, sigma, baseline, th, pmax, out))
        summary = PeriodogramSummary.from_spectrum(periods, spec["sde"], th.min_period_days, pmax, edges, peaks)
        spectrum = ({"period": periods, "power": power, "sde": spec["sde"], "duration": spec["duration"]}
                    if keep_spectrum else None)
        return SearchResult(signals=out, periodogram=summary, spectrum=spectrum)


register(BoxDip())
