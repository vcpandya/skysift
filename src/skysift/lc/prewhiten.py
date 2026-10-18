"""Harmonic pre-whitening: iterative removal of significant sinusoidal components before the box search.

A pulsator (delta Scuti, gamma Doradus) or a spotted rotator fills the box-search spectrum with its harmonics,
sub-harmonics and beat sidelobes: gaia-dr3-2052626821369997696 (a 15.05 per day delta Scuti) produced four
"transits" at exactly 8, 9, 10 and 11 times its pulsation period, and gaia-dr3-2075988698983795072 hid everything
under a comb of frequencies between 1.3 and 1.6 per day. Kepler's own pipeline whitens before it searches; this is the
classical asteroseismology version: find the strongest Lomb-Scargle peak up to the Nyquist frequency, fit a sinusoid
with two harmonics at that frequency, subtract it, repeat while the peak is significant.

Two guards keep transits and eclipses out of the model. A component is only removed when its false-alarm probability
is below ``fap_max`` (so noise peaks stop the loop), and only when the fold of the residual at that period is better
described by a wave than by a box (the same box-versus-sine contrast rung 2 uses): an eclipsing binary is periodic
but box-shaped, so it is left for the detector and the EB branch. For short dips the guard also folds on a grid
around the peak, since a short transit's peak sits off its true frequency and folding there smears the dip away
(K04802.01, a 1 h, 130 ppm transit at 0.6056 d). The removed model is returned as plain
coefficients, so the dashboard can show the same whitened flux without refitting.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from astropy.timeseries import LombScargle

from skysift.lc.ops import fold_phase, shape_contrast

PREWHITEN_VERSION = 3               # 2: the shape guard scans around the peak for short dips; 3: only boxes at least a
                                    # cadence wide (2 searched the 30 sample targets once, run 7, and is stale)
HARMONICS = 2                       # fit a1 cos + b1 sin at f and at 2f
SAMPLES_PER_PEAK = 5
# trial box widths for the shape guard, as a fraction of the period: from a one-hour transit at 30 days (0.14 percent)
# up to a contact binary's half cycle, so a transit's own harmonics are recognised as box-like and kept
GUARD_BOX_FRACTIONS = tuple(float(x) for x in np.geomspace(0.0015, 0.5, 18))
AMP_FLOOR_FRACTION = 0.06           # stop once a wave's amplitude is below this fraction of the point scatter
GUARD_HARMONICS = 8                 # harmonics and sub-harmonics of a refused frequency are refused too
GUARD_EXCLUSION = 4.0               # in resolution elements (1/baseline): covers the leakage sidelobes of a refused peak
# the scan of the shape guard: short box widths are also tried at f0 + k * GUARD_SCAN_STEP * resolution for k in
# -GUARD_SCAN_STEPS .. GUARD_SCAN_STEPS (plus or minus 2 resolution elements), the box on the fold's deepest bin;
# trials at or below half a resolution element are skipped
GUARD_SCAN_MAX_FRACTION = 0.12
GUARD_SCAN_STEP = 0.1
GUARD_SCAN_STEPS = 20
GUARD_SCAN_BINS = 100
GUARD_SCAN_MIN_SNR = 7.0            # a scanned box must be a dip this many of its own errors deep to count
GUARD_SCAN_MIN_POINTS = 10
GUARD_SCAN_MIN_WIDTH = 1.0          # in cadences: a box narrower than one sample is not a box the data can show (a
                                    # 95.7 min delta Scuti wave at 30 min cadence was refused as box-like without it)
MIN_POINTS = 200


@dataclass
class Component:
    frequency: float                # per day
    amplitude_ppm: float            # of the fundamental
    snr: float                      # Lomb-Scargle peak over the robust spectrum scatter
    coef: list[float]               # [c0, a1, b1, a2, b2] in normalised flux

    def as_dict(self) -> dict:
        return {"frequency": self.frequency, "amplitude_ppm": self.amplitude_ppm, "snr": self.snr, "coef": list(self.coef)}

    @classmethod
    def from_dict(cls, d: dict) -> Component:
        return cls(float(d["frequency"]), float(d["amplitude_ppm"]), float(d["snr"]), [float(x) for x in d["coef"]])


@dataclass
class PrewhitenResult:
    residual: np.ndarray            # aligned with the input flux, NaN where the input was NaN
    components: list[Component] = field(default_factory=list)
    skipped: list[float] = field(default_factory=list)      # frequencies the shape guard refused to remove


def _design(time: np.ndarray, frequency: float) -> np.ndarray:
    w = 2 * np.pi * frequency * time
    return np.column_stack([np.ones(time.size), np.cos(w), np.sin(w), np.cos(2 * w), np.sin(2 * w)])


def prewhiten_model(time: np.ndarray, components: list[Component | dict]) -> np.ndarray:
    """The flux removed by ``prewhiten`` (zero-mean waves), so that ``fnorm - prewhiten_model(...)`` is the residual."""
    model = np.zeros(np.asarray(time).size)
    for c in components:
        if isinstance(c, dict):
            c = Component.from_dict(c)
        model += _design(np.asarray(time, float), c.frequency)[:, 1:] @ np.asarray(c.coef[1:], float)
    return model


def _wave_minimum_time(frequency: float, coef: list[float]) -> float:
    """Time (near zero) at which the fundamental a1 cos(w) + b1 sin(w) is lowest."""
    phase = np.arctan2(-coef[2], -coef[1])          # w at the minimum of a1 cos w + b1 sin w
    return float(phase / (2 * np.pi * frequency))


def _related(f: float, skipped: float, resolution: float) -> bool:
    """f belongs to the harmonic family of a refused frequency: m times skipped/k for small k and m (an eclipsing
    binary refused at 2 f_orb must keep 3 f_orb and f_orb too), to the spectral resolution."""
    for k in range(1, GUARD_HARMONICS + 1):
        base = skipped / k
        m = round(f / base)
        if 1 <= m <= 2 * GUARD_HARMONICS and abs(f - m * base) < GUARD_EXCLUSION * resolution:
            return True
    return False


def _deepest_t0(t: np.ndarray, y: np.ndarray, period: float) -> float:
    """Mid-time of the deepest bin of a ``GUARD_SCAN_BINS``-bin fold at ``period``: where a short dip sits."""
    phase = ((t - t[0]) / period) % 1.0
    idx = np.minimum((phase * GUARD_SCAN_BINS).astype(int), GUARD_SCAN_BINS - 1)
    counts = np.bincount(idx, minlength=GUARD_SCAN_BINS)
    sums = np.bincount(idx, weights=y, minlength=GUARD_SCAN_BINS)
    means = np.where(counts > 0, sums / np.maximum(counts, 1), np.inf)
    return float(t[0] + (int(np.argmin(means)) + 0.5) / GUARD_SCAN_BINS * period)


def _box_like(t: np.ndarray, y: np.ndarray, f0: float, coef: list[float], resolution: float, *,
              scan: bool = True) -> bool:
    """The shape guard: when any box beats the two-harmonic sine the periodic signal is an eclipse or transit, not a
    wave. First the fold at 1/f0 with the box centred on the wave's minimum, every box width (``scan=False`` stops
    here). Then the short widths (up to ``GUARD_SCAN_MAX_FRACTION`` of the period) on a grid of trial frequencies
    within ``GUARD_SCAN_STEPS * GUARD_SCAN_STEP`` resolution elements of f0, the box centred on the fold's deepest
    bin: the Lomb-Scargle peak of a short dip sits up to 1.5 resolution elements off its true frequency, and folded
    there a 1 h dip drifts over every phase. A scanned trial only counts when its box is a dip at least
    ``GUARD_SCAN_MIN_SNR`` of its own errors deep: among a few hundred trials on a fold with no dip (a wave smeared
    at a neighbouring frequency) some box beats the sine by chance, which would refuse every real wave."""
    period = 1.0 / f0
    t0 = t[0] + (_wave_minimum_time(f0, coef) - t[0]) % period
    for frac in GUARD_BOX_FRACTIONS:
        box_chi2, sine_chi2, n = shape_contrast(t, y, period, t0, frac * period)
        if n and box_chi2 < sine_chi2:
            return True
    if not scan:
        return False
    # only box widths of at least GUARD_SCAN_MIN_WIDTH cadences: narrower boxes hold one or two points per cycle and
    # the box-versus-sine contrast on them is noise (every short width of a 95.7 min wave at 30 min cadence is such)
    cadence = float(np.median(np.diff(t))) if t.size > 1 else 0.0
    short = [frac for frac in GUARD_BOX_FRACTIONS
             if frac <= GUARD_SCAN_MAX_FRACTION and frac / f0 >= GUARD_SCAN_MIN_WIDTH * cadence]
    if not short:
        return False
    sigma = 1.4826 * float(np.median(np.abs(y - np.median(y)))) or 1e-12
    for k in range(-GUARD_SCAN_STEPS, GUARD_SCAN_STEPS + 1):
        trial = f0 + k * GUARD_SCAN_STEP * resolution
        if trial <= 0.5 * resolution:       # a peak at the first grid frequency (2 / baseline) reaches 0 at k = -20:
            continue                        # nothing periodic is defined below half a resolution element
        period = 1.0 / trial
        t0 = _deepest_t0(t, y, period)
        offset = np.abs(fold_phase(t, period, t0))
        for frac in short:
            inside = offset < 0.5 * frac * period
            n_in = int(inside.sum())
            if n_in < GUARD_SCAN_MIN_POINTS or n_in == y.size:
                continue
            depth = float(y[~inside].mean() - y[inside].mean())
            if depth < GUARD_SCAN_MIN_SNR * sigma * np.sqrt(1.0 / n_in + 1.0 / (y.size - n_in)):
                continue
            box_chi2, sine_chi2, n = shape_contrast(t, y, period, t0, frac * period)
            if n and box_chi2 < sine_chi2:
                return True
    return False


def prewhiten(time: np.ndarray, fnorm: np.ndarray, *, max_components: int = 50, fap_max: float = 1e-6,
              fmin: float | None = None, fmax: float | None = None, shape_guard: bool = True) -> PrewhitenResult:
    """Remove up to ``max_components`` significant sinusoids from ``fnorm`` (normalised, detrended flux).

    ``fmax`` defaults to the Nyquist frequency of the median cadence and ``fmin`` to two cycles per baseline. The
    loop stops at the first peak whose false-alarm probability exceeds ``fap_max``; a peak the shape guard refuses
    (its fold is box-like) is recorded in ``skipped`` and the next distinct peak is tried instead."""
    time = np.asarray(time, float); fnorm = np.asarray(fnorm, float)
    residual = fnorm.copy()
    good = np.isfinite(fnorm) & np.isfinite(time)
    out = PrewhitenResult(residual=residual)
    if max_components <= 0 or good.sum() < MIN_POINTS:
        return out
    t, y = time[good], residual[good]
    cadence = float(np.median(np.diff(t)))
    baseline = float(t.max() - t.min())
    if fmax is None:
        fmax = 0.5 / cadence
    if fmin is None:
        fmin = 2.0 / baseline
    if fmax <= fmin:
        return out
    resolution = 1.0 / baseline
    # a wave this small changes nothing the box search can see: its variance is below 0.2 percent of the noise
    amp_floor = AMP_FLOOR_FRACTION * max(1.4826 * float(np.median(np.abs(y - np.median(y)))), 1e-12)
    for _ in range(max_components):
        ls = LombScargle(t, y)
        freq, power = ls.autopower(minimum_frequency=fmin, maximum_frequency=fmax, samples_per_peak=SAMPLES_PER_PEAK)
        if freq.size == 0:
            break
        med = float(np.median(power)); mad = 1.4826 * float(np.median(np.abs(power - med))) or 1e-12
        order = np.argsort(power)[::-1]
        chosen = None
        for i in order[:200]:
            f0 = float(freq[i])
            if any(_related(f0, s, resolution) for s in out.skipped):
                continue
            chosen = (i, f0)
            break
        if chosen is None:
            break
        i, f0 = chosen
        if ls.false_alarm_probability(power[i], minimum_frequency=fmin, maximum_frequency=fmax) > fap_max:
            break
        design = _design(t, f0)
        coef, *_ = np.linalg.lstsq(design, y, rcond=None)
        coef = [float(c) for c in coef]
        amp = float(np.hypot(coef[1], coef[2]))
        if amp < amp_floor:
            break
        if shape_guard and _box_like(t, y, f0, coef, resolution):
            out.skipped.append(f0)
            continue
        y = y - (design @ np.asarray(coef)) + coef[0]
        out.components.append(Component(f0, amp * 1e6, float((power[i] - med) / mad), coef))
    residual[good] = y
    return out
