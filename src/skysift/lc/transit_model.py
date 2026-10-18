"""Uniform-disc transit model on a circular orbit and its least-squares fit to a folded light curve.

The fit reads the transit's shape through three numbers: the radius ratio ``k``, the impact parameter ``b`` and the
orbit size ``a_over_r``. The Kepler Robovetter's V-shape rule fails a signal when ``k + b`` exceeds 1.04, which is
the grazing-eclipse geometry (an eclipsing binary seen at the edge of the star), and a planet-occultation exception
needs the same fit. The occulted fraction is Mandel and Agol (2002), equation 1, without limb darkening: a uniform
source, so the depth of a central transit is exactly ``k**2``."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import least_squares

from skysift.lc.ops import SHAPE_BINS_PER_DURATION, fold_phase

FIT_WINDOW_DURATIONS = 3.0      # the fold is kept within this many durations of mid-transit
FIT_MIN_PER_BIN = 5
FIT_MIN_BINS = 20
K_BOUNDS = (1e-3, 2.0)
B_BOUNDS = (0.0, 3.0)
A_BOUNDS = (1.5, 1e4)
C_BOUNDS = (0.9, 1.1)
BIN_SUBSAMPLES = 9
# twice the shape-contrast resolution: at 4 per duration only ~4 bins fall in transit, as many as the free parameters,
# and the grazing, central and V solutions tie in chi2
FIT_BINS_PER_DURATION = 2 * SHAPE_BINS_PER_DURATION


@dataclass
class TransitFit:
    k: float            # R_p / R_star
    b: float            # impact parameter
    a_over_r: float     # semi-major axis over stellar radius
    depth_ppm: float    # model depth at mid-transit (k^2 x 1e6 when b + k <= 1; the overlap fraction otherwise)
    t14_days: float     # first-to-fourth contact duration of the fitted model
    chi2: float         # sum of squared weighted bin residuals (floored bin errors); not divided by degrees of freedom
    n_bins: int         # fold bins with at least 5 points within +-3 durations (counted also when ok is False)
    ok: bool            # False: under 20 usable bins, none in transit, or the solver did not converge


def _occulted_fraction(z: np.ndarray, k: float) -> np.ndarray:
    z = np.asarray(z, dtype=float)
    out = np.zeros_like(z)
    if k < 1.0:
        out[z <= 1.0 - k] = k * k
    else:
        out[z <= k - 1.0] = 1.0
    part = (z > abs(1.0 - k)) & (z < 1.0 + k)
    if part.any():
        zp = z[part]
        kappa1 = np.arccos(np.clip((1.0 - k * k + zp * zp) / (2.0 * zp), -1.0, 1.0))
        kappa0 = np.arccos(np.clip((k * k + zp * zp - 1.0) / (2.0 * k * zp), -1.0, 1.0))
        root = np.sqrt(np.maximum(4.0 * zp * zp - (1.0 + zp * zp - k * k) ** 2, 0.0) / 4.0)
        out[part] = (k * k * kappa0 + kappa1 - root) / np.pi
    return out


def uniform_disc_flux(dt_days: np.ndarray, period: float, k: float, b: float, a_over_r: float) -> np.ndarray:
    """Relative flux of a uniform star transited by a dark disc of radius ``k`` on a circular orbit, at time offsets
    ``dt_days`` from mid-transit (days, as ``period``). Flux is 1 everywhere when ``b >= 1 + k`` (the disc never
    touches the star), and 1 where ``|2 pi dt / period| > pi / 2``: the planet is then behind the star, and only the
    near-side half of the orbit can transit."""
    dt = np.atleast_1d(np.asarray(dt_days, dtype=float))
    if b >= 1.0 + k:
        return np.ones_like(dt)
    ang = 2.0 * np.pi * dt / period
    z = np.hypot(a_over_r * np.sin(ang), b * np.cos(ang))
    # the planet is behind the star for |ang| > pi/2 on a circular orbit: no transit there
    z = np.where(np.cos(ang) > 0.0, z, np.inf)
    return 1.0 - _occulted_fraction(z, k)


def _t14(period: float, k: float, b: float, a_over_r: float) -> float:
    inner = (1.0 + k) ** 2 - b * b
    if inner <= 0.0:
        return float("nan")
    arg = np.sqrt(inner) / a_over_r
    if arg > 1.0:
        return float("nan")
    return float(period / np.pi * np.arcsin(arg))


def _failed(n_bins: int) -> TransitFit:
    nan = float("nan")
    return TransitFit(nan, nan, nan, nan, nan, nan, int(n_bins), False)


def fit_transit(time, fnorm, period, t0, duration_days) -> TransitFit:
    """Weighted least-squares fit of the uniform-disc model (plus a free baseline level) to the binned fold within
    +-3 durations of mid-transit. ``time``, ``period``, ``t0`` and ``duration_days`` are in days; ``fnorm`` is the
    normalised flux. Bins are ``1 / FIT_BINS_PER_DURATION`` of a duration wide (8 per duration), each weighted by the
    standard error of its own points (never below half the robust point scatter over sqrt(count), as
    ``shape_contrast``); a bin needs 5 points. The solver is started from a central and two grazing geometries and the
    converged start with the lowest chi-square is returned (a converged start beats an unconverged one).

    Returns ``ok=False`` with NaN parameters (``n_bins`` as counted) when fewer than 20 bins are usable, when no usable
    bin lies within ``duration_days / 2`` of mid-transit (the fold holds no transit to fit, so k + b would be
    meaningless), or when no start produces a result; ``ok=False`` with finite parameters means the solver did not
    converge."""
    time = np.asarray(time, dtype=float)
    fnorm = np.asarray(fnorm, dtype=float)
    good = np.isfinite(time) & np.isfinite(fnorm)
    dt_all = fold_phase(time[good], period, t0)
    y_all = fnorm[good]
    if y_all.size == 0 or not duration_days > 0:
        return _failed(0)
    near = np.abs(dt_all) < FIT_WINDOW_DURATIONS * duration_days
    dt, y = dt_all[near], y_all[near]
    n_total = round(2 * FIT_WINDOW_DURATIONS * FIT_BINS_PER_DURATION)
    width = 2 * FIT_WINDOW_DURATIONS * duration_days / n_total
    idx = np.minimum(((dt + FIT_WINDOW_DURATIONS * duration_days) / width).astype(int), n_total - 1)
    counts = np.bincount(idx, minlength=n_total)
    sums = np.bincount(idx, weights=y, minlength=n_total)
    sq = np.bincount(idx, weights=y * y, minlength=n_total)
    usable = counts >= FIT_MIN_PER_BIN
    n_bins = int(usable.sum())
    if n_bins < FIT_MIN_BINS:
        return _failed(n_bins)
    mean = sums[usable] / counts[usable]
    var = np.maximum(sq[usable] / counts[usable] - mean ** 2, 0.0)
    # the model is averaged over each bin's width (a bin is an eighth of a duration wide, too coarse to sample at one
    # point near the contacts); the points are dense in phase, so a uniform sub-grid stands for them
    centre = -FIT_WINDOW_DURATIONS * duration_days + (np.arange(n_total)[usable] + 0.5) * width
    sub = centre[:, None] + ((np.arange(BIN_SUBSAMPLES) + 0.5) / BIN_SUBSAMPLES - 0.5) * width
    floor = 1.4826 * float(np.median(np.abs(y_all - np.median(y_all)))) or 1e-12
    err = np.sqrt(np.maximum(var, (0.5 * floor) ** 2) / counts[usable])
    wts = 1.0 / err

    def residuals(p):
        k, b, a, c = p
        return (mean - c * uniform_disc_flux(sub.ravel(), period, k, b, a).reshape(sub.shape).mean(axis=1)) * wts

    inside = np.abs(centre) < 0.5 * duration_days
    if not inside.any():
        return _failed(n_bins)
    box_depth = float(np.mean(1.0 - mean[inside]))
    lower = [K_BOUNDS[0], B_BOUNDS[0], A_BOUNDS[0], C_BOUNDS[0]]
    upper = [K_BOUNDS[1], B_BOUNDS[1], A_BOUNDS[1], C_BOUNDS[1]]
    k_box = float(np.clip(np.sqrt(max(box_depth, 1e-6)), *K_BOUNDS))
    best = None
    # The grazing and the central solutions are separate basins that a local solver does not cross, so start in
    # each and keep the lower chi-square: central (b 0.3), then grazing starts that the box depth cannot reach.
    for k0, b0 in ((k_box, 0.3), (max(k_box, 0.3), 0.9), (max(2 * k_box, 0.5), 1.2)):
        a0 = period * np.sqrt(max((1.0 + k0) ** 2 - b0 * b0, 0.05)) / (np.pi * duration_days)
        start = [k0, b0, float(np.clip(a0, *A_BOUNDS)), 1.0]
        try:
            trial = least_squares(residuals, start, bounds=(lower, upper),
                                  x_scale=[0.05, 0.3, max(start[2], 2.0), 0.01])
        except ValueError:      # non-finite residuals at the start (a NaN period or error): this start is unusable
            continue
        if best is None or (trial.success, -trial.cost) > (best.success, -best.cost):
            best = trial
    if best is None:
        return _failed(n_bins)
    res = best
    k, b, a, _ = (float(v) for v in res.x)
    depth = float((1.0 - uniform_disc_flux(np.array([0.0]), period, k, b, a)[0]) * 1e6)
    chi2 = float(np.sum(res.fun ** 2))
    return TransitFit(k, b, a, depth, _t14(period, k, b, a), chi2, n_bins, bool(res.success))
