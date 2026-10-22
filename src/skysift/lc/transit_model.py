"""Uniform-disc transit model on a circular orbit and its least-squares fit to a folded light curve.

The fit reads the transit's shape through three numbers: the radius ratio ``k``, the impact parameter ``b`` and the
orbit size ``a_over_r``. The Kepler Robovetter's V-shape rule fails a signal when ``k + b`` exceeds 1.04, which is
the grazing-eclipse geometry (an eclipsing binary seen at the edge of the star), and a planet-occultation exception
needs the same fit. The occulted fraction is Mandel and Agol (2002), equation 1, without limb darkening: a uniform
source, so the depth of a central transit is exactly ``k**2``.

Two families are fitted. The free fit spans every geometry the bounds allow, grazing (``b > 1 - k``) and deep
(``k >= 1``) included. The non-grazing fit keeps the disc wholly inside the star (``b <= 1 - k``, ``k < 1``), a box- or
U-shaped transit. The non-grazing family is a subset of the free one, so a lower non-grazing chi-square means the free
optimiser stopped in a local minimum (on a shallow, cadence-smeared fold the uniform-disc model is degenerate: a large
``k`` with ``b`` near ``1 + k`` and ``a_over_r`` near ``b`` mimics a box), and the better of the two solutions is
returned as the fit. The non-grazing minimum is kept as ``chi2_nongrazing``, so ``chi2_nongrazing - chi2 >= 0``
measures how much a grazing or deep geometry is preferred: a grazing verdict needs it to be large.

Rung 2 keeps ``fit_transit``, the binned uniform-disc fit, for the V-shape rule and its tests. The dossier quotes
``fit_shape`` instead (referee pack spec 11.3): a quadratic limb-darkened star (``quadratic_ld_flux``, the Kepler band
coefficients of Claret and Bloemen 2011 interpolated in Teff by ``limb_darkening``), the model averaged over the
exposure (``exposure_flux``) and fitted to the unbinned fold, once free and once with a Gaussian prior on the stellar
density (``stellar_density``); ``compare_fits`` says which of the two is quoted."""
from __future__ import annotations

import math
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
# the non-grazing family: b = u x (1 - k), u in [0, 1], so b <= 1 - k always holds
K_BOUNDS_NONGRAZING = (1e-3, 0.99)
U_BOUNDS = (0.0, 1.0)
# Both fits start at the a_over_r of transits 1, 2/3 and 3/2 times the signal's duration (the detector's duration
# grid steps by up to 1.5). For a small k the binned model is nearly a staircase in a_over_r (ingress is far shorter
# than a sub-bin), the solver does not move a_over_r far from its start, and a start set by a grid duration that
# misses the true one leaves the minimum unfound
START_DURATION_FACTORS = (1.0, 2.0 / 3.0, 1.5)
BIN_SUBSAMPLES = 9
# twice the shape-contrast resolution: at 4 per duration only ~4 bins fall in transit, as many as the free parameters,
# and the grazing, central and V solutions tie in chi2
FIT_BINS_PER_DURATION = 2 * SHAPE_BINS_PER_DURATION


@dataclass
class TransitFit:
    # k, b, a_over_r, depth_ppm, t14_days, chi2 and ok describe the better (lower chi2) of the free and the
    # non-grazing solutions
    k: float            # R_p / R_star
    b: float            # impact parameter
    a_over_r: float     # semi-major axis over stellar radius
    depth_ppm: float    # model depth at mid-transit (k^2 x 1e6 when b + k <= 1; the overlap fraction otherwise)
    t14_days: float     # first-to-fourth contact duration of the fitted model
    chi2: float         # sum of squared weighted bin residuals (floored bin errors); not divided by degrees of freedom
    n_bins: int         # fold bins with at least 5 points within +-3 durations (counted also when ok is False)
    ok: bool            # False: under 20 usable bins, none in transit, or the solver did not converge
    # chi2 of the best non-grazing fit (b <= 1 - k, k < 1) on the same bins and weights; NaN when that fit fails (no
    # start converged) or the free fit had no fold to fit. chi2_nongrazing - chi2 (>= 0 whenever finite) is the
    # preference for a grazing or deep geometry
    chi2_nongrazing: float


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
    return TransitFit(nan, nan, nan, nan, nan, nan, int(n_bins), False, nan)


def _best_of(residuals, starts, lower, upper):
    """``least_squares`` from each start within the bounds; the converged start with the lowest cost wins (a converged
    start beats an unconverged one). None when no start produces a result. Each start is (parameters, x_scale)."""
    best = None
    for start, x_scale in starts:
        try:
            trial = least_squares(residuals, start, bounds=(lower, upper), x_scale=x_scale)
        except ValueError:      # non-finite residuals at the start (a NaN period or error): this start is unusable
            continue
        if best is None or (trial.success, -trial.cost) > (best.success, -best.cost):
            best = trial
    return best


def fit_transit(time, fnorm, period, t0, duration_days) -> TransitFit:
    """Weighted least-squares fit of the uniform-disc model (plus a free baseline level) to the binned fold within
    +-3 durations of mid-transit. ``time``, ``period``, ``t0`` and ``duration_days`` are in days; ``fnorm`` is the
    normalised flux. Bins are ``1 / FIT_BINS_PER_DURATION`` of a duration wide (8 per duration), each weighted by the
    standard error of its own points (never below half the robust point scatter over sqrt(count), as
    ``shape_contrast``); a bin needs 5 points. The solver is started from a central and two grazing geometries, each
    with ``a_over_r`` set from 1, 2/3 and 3/2 times ``duration_days`` (``START_DURATION_FACTORS``), and the converged
    start with the lowest chi-square is kept (a converged start beats an unconverged one).

    The non-grazing family is fitted on the same bins, weights and baseline parameter, parameterised as
    ``b = u (1 - k)`` with ``u`` in [0, 1] and ``k`` in [1e-3, 0.99], from the same nine starts (``k`` clipped to its
    bound, ``u = b / (1 - k)`` clipped to 1). Its chi-square is ``chi2_nongrazing``, NaN unless a start converged: an
    unconverged chi-square overstates the family's minimum and so the preference for grazing. The returned parameters,
    ``chi2`` and ``ok`` are the non-grazing solution's when it converged and either has the lower chi-square or the
    free fit did not converge (the same rule as between starts); the free solution's otherwise. ``chi2_nongrazing -
    chi2`` is then never negative.

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

    def residuals_nongrazing(p):
        k, u, a, c = p
        return residuals((k, u * (1.0 - k), a, c))

    inside = np.abs(centre) < 0.5 * duration_days
    if not inside.any():
        return _failed(n_bins)
    box_depth = float(np.mean(1.0 - mean[inside]))
    lower = [K_BOUNDS[0], B_BOUNDS[0], A_BOUNDS[0], C_BOUNDS[0]]
    upper = [K_BOUNDS[1], B_BOUNDS[1], A_BOUNDS[1], C_BOUNDS[1]]
    k_box = float(np.clip(np.sqrt(max(box_depth, 1e-6)), *K_BOUNDS))
    # The grazing and the central solutions are separate basins that a local solver does not cross, so start in
    # each and keep the lower chi-square: central (b 0.3), then grazing starts that the box depth cannot reach.
    starts, starts_ng = [], []
    for factor in START_DURATION_FACTORS:
        for k0, b0 in ((k_box, 0.3), (max(k_box, 0.3), 0.9), (max(2 * k_box, 0.5), 1.2)):
            a0 = period * np.sqrt(max((1.0 + k0) ** 2 - b0 * b0, 0.05)) / (np.pi * factor * duration_days)
            a0 = float(np.clip(a0, *A_BOUNDS))
            x_scale = [0.05, 0.3, max(a0, 2.0), 0.01]
            starts.append(([k0, b0, a0, 1.0], x_scale))
            k_ng = float(np.clip(k0, *K_BOUNDS_NONGRAZING))
            starts_ng.append(([k_ng, min(b0 / (1.0 - k_ng), U_BOUNDS[1]), a0, 1.0], x_scale))
    res = _best_of(residuals, starts, lower, upper)
    if res is None:
        return _failed(n_bins)
    ng = _best_of(residuals_nongrazing, starts_ng, [K_BOUNDS_NONGRAZING[0], U_BOUNDS[0], A_BOUNDS[0], C_BOUNDS[0]],
                  [K_BOUNDS_NONGRAZING[1], U_BOUNDS[1], A_BOUNDS[1], C_BOUNDS[1]])
    chi2_ng = float(np.sum(ng.fun ** 2)) if ng is not None and ng.success else float("nan")
    chi2 = float(np.sum(res.fun ** 2))
    k, b, a, _ = (float(v) for v in res.x)
    ok = bool(res.success)
    # the non-grazing family lies inside the free one: a better non-grazing solution is the free fit's true minimum
    if np.isfinite(chi2_ng) and (not ok or chi2_ng < chi2):
        k, u, a, _ = (float(v) for v in ng.x)
        b, chi2, ok = u * (1.0 - k), chi2_ng, True
    depth = float((1.0 - uniform_disc_flux(np.array([0.0]), period, k, b, a)[0]) * 1e6)
    return TransitFit(k, b, a, depth, _t14(period, k, b, a), chi2, n_bins, ok, chi2_ng)


# ---------------------------------------------------------------------------------------------------- the shape fit
#
# The dossier's shape fit (referee pack spec 11.3): a quadratic limb-darkened star (Kepler band coefficients of
# Claret and Bloemen 2011), the model averaged over the exposure, fitted to every point of the fold within
# FIT_WINDOW_DURATIONS of mid-transit, once free and once with a Gaussian prior on the stellar density.

EXPOSURE_RESOLUTION_MIN = 0.1        # minutes, the sampling of a model before it is averaged over the exposure
LD_LOGG = 4.5
LD_SOURCE = ("Claret and Bloemen 2011, Kepler band, quadratic law, ATLAS models (method L, log g 4.5, [M/H] 0, "
             "microturbulence 2 km/s), linear in Teff between the rows, held at the table's edges")
LD_TABLE = ((3500, 0.4158, 0.3203), (3750, 0.4224, 0.3037), (4000, 0.5516, 0.1880), (4250, 0.6834, 0.0718),
            (4500, 0.6811, 0.0672), (4750, 0.6270, 0.1082), (5000, 0.5684, 0.1530), (5250, 0.5086, 0.1959),
            (5500, 0.4526, 0.2327), (5750, 0.4035, 0.2622), (6000, 0.3588, 0.2868), (6250, 0.3239, 0.3029),
            (6500, 0.2971, 0.3119), (6750, 0.2753, 0.3195), (7000, 0.2567, 0.3263))   # (Teff K, u1, u2)
LD_NO_TEFF = 5750                    # the row used when the star has no Teff
LD_NODES = 32                        # Gauss-Legendre nodes of the annulus integral (cosine substitution)
LD_TABLE_NODES = 256                 # separations per side of the contact |1 - k| in the exposure grid's table
CONSTRAINED_DELTA_CHI2 = 2.0         # spec 11.3: within 2, the shape does not constrain b and a/R*
G_CGS = 6.674e-8
UNCONSTRAINED = "unconstrained at this cadence (the quoted values follow the host's density)"
A_LABEL_STELLAR, A_LABEL_FITTED = "from the stellar parameters", "fitted"
NO_DENSITY = "no stellar density, so no density prior"
SHAPE_MIN_POINTS = 20                # fewer points in the window: no shape fit
SHAPE_MAD_TO_SIGMA = 1.4826
SCATTER_MIN_POINTS = 10        # fewer window points beyond one duration: the scatter of the whole window

_GL_X, _GL_W = np.polynomial.legendre.leggauss(LD_NODES)
_THETA = 0.5 * np.pi * (_GL_X + 1.0)          # the nodes on [0, pi]
_THETA_W = 0.5 * np.pi * _GL_W
_COS, _SIN = np.cos(_THETA), np.sin(_THETA)


def limb_darkening(teff: float | None) -> dict:
    """The quadratic coefficients of ``LD_TABLE`` at ``teff``, linear in Teff between the rows and held at the
    table's edges (``np.interp``); the ``LD_NO_TEFF`` row, with ``teff`` stored as None, when the star has none."""
    known = teff is not None and math.isfinite(float(teff)) and float(teff) > 0
    t = float(teff) if known else float(LD_NO_TEFF)
    rows = np.array(LD_TABLE, dtype=float)
    return {"law": "quadratic", "u1": float(np.interp(t, rows[:, 0], rows[:, 1])),
            "u2": float(np.interp(t, rows[:, 0], rows[:, 2])), "teff": t if known else None, "logg": LD_LOGG,
            "source": LD_SOURCE}


def _separation(dt: np.ndarray, period: float, b: float, a_over_r: float) -> np.ndarray:
    """The projected separation in stellar radii, ``a_over_r sqrt(sin(phi)^2 + (b / a_over_r cos(phi))^2)``,
    infinite behind the star (``cos(phi) <= 0``)."""
    phi = 2.0 * np.pi * dt / period
    z = a_over_r * np.sqrt(np.sin(phi) ** 2 + (b / a_over_r * np.cos(phi)) ** 2)
    return np.where(np.cos(phi) > 0.0, z, np.inf)


def _inner_disc(s: np.ndarray, u1: float, u2: float) -> np.ndarray:
    """``integral of I(r) 2 r dr`` from 0 to ``sqrt(s)`` over pi, in closed form (``s = r^2``, ``mu = sqrt(1 - s)``):
    the light of the stellar disc of radius ``sqrt(s)``, which the planet covers whole when ``sqrt(s) <= k - z``."""
    rim = 1.0 - (1.0 - s) ** 1.5
    return s - u1 * (s - 2.0 / 3.0 * rim) - u2 * (2.0 * s - 0.5 * s * s - 4.0 / 3.0 * rim)


def _blocked(z: np.ndarray, k: float, u1: float, u2: float) -> np.ndarray:
    """The fraction of a quadratic limb-darkened star's light that a dark disc of radius ``k`` blocks at the
    separations ``z`` (each below ``1 + k``): the intensity-weighted arc ``2 r alpha(r)`` of every annulus the disc
    crosses, integrated from ``max(0, z - k)`` to ``min(1, z + k)``, over the star's total ``pi (1 - u1/3 - u2/6)``.
    The radius runs as ``mid - half cos(theta)``, which takes the square-root behaviour at the ends of the range out
    of the integrand, on ``LD_NODES`` Gauss-Legendre nodes in theta. When the disc covers the star's centre (``z <
    k``) the annuli inside ``k - z`` are covered whole (``alpha = pi``), and the arc has a square-root kink there, so
    that part is taken in closed form (``_inner_disc``) and the nodes span ``k - z`` to ``min(1, z + k)`` only."""
    zz = np.maximum(np.asarray(z, dtype=float), 1e-12)
    inner = np.clip(k - zz, 0.0, 1.0)                 # the radius out to which every annulus is covered
    lo, hi = np.maximum(inner, zz - k)[:, None], np.minimum(1.0, zz + k)[:, None]
    hi = np.maximum(hi, lo)
    mid, half = 0.5 * (lo + hi), 0.5 * (hi - lo)
    r = mid - half * _COS[None, :]
    mu = np.sqrt(np.clip(1.0 - r * r, 0.0, 1.0))
    intensity = 1.0 - u1 * (1.0 - mu) - u2 * (1.0 - mu) ** 2
    zc = zz[:, None]
    with np.errstate(divide="ignore", invalid="ignore"):
        cos_alpha = np.clip((r * r + zc * zc - k * k) / (2.0 * r * zc), -1.0, 1.0)
    alpha = np.arccos(np.nan_to_num(cos_alpha, nan=1.0))
    integrand = intensity * 2.0 * r * alpha * half * _SIN[None, :]
    covered = np.pi * _inner_disc(inner * inner, u1, u2)
    return (covered + integrand @ _THETA_W) / (np.pi * (1.0 - u1 / 3.0 - u2 / 6.0))


def quadratic_ld_flux(dt_days, period: float, k: float, b: float, a_over_r: float, u1: float,
                      u2: float) -> np.ndarray:
    """Relative flux of a star with the quadratic limb darkening ``I(mu) = 1 - u1 (1 - mu) - u2 (1 - mu)^2``
    transited by a dark disc of radius ``k`` on a circular orbit, at offsets ``dt_days`` from mid-transit (``1 -
    _blocked``). The separation is that of ``uniform_disc_flux``; the flux is 1 behind the star (``cos(phi) <= 0``)
    and wherever the disc does not touch it. With ``u1 = u2 = 0`` it is the uniform disc."""
    dt = np.atleast_1d(np.asarray(dt_days, dtype=float))
    out = np.ones_like(dt)
    if not (k > 0 and a_over_r > 0):
        return out
    z = _separation(dt, period, b, a_over_r)
    hit = z < 1.0 + k
    if hit.any():
        out[hit] = 1.0 - _blocked(z[hit], k, u1, u2)
    return out


def _cosine_nodes(lo: float, hi: float, n: int) -> np.ndarray:
    """``n`` nodes from ``lo`` to ``hi``, both included, closest together at the two ends."""
    return lo + (hi - lo) * 0.5 * (1.0 - np.cos(np.linspace(0.0, np.pi, n)))


def _blocked_table(k: float, u1: float, u2: float) -> tuple[np.ndarray, np.ndarray]:
    """``_blocked`` on ``LD_TABLE_NODES`` separations from 0 to ``|1 - k|`` and as many from there to ``1 + k`` (the
    contact points, where it has its kinks, are nodes), for linear interpolation in z: an exposure grid holds a
    thousand or more samples inside the transit, and between the contacts the blocked light is smooth in z."""
    edge = abs(1.0 - k)
    z = np.concatenate([_cosine_nodes(0.0, edge, LD_TABLE_NODES), _cosine_nodes(edge, 1.0 + k, LD_TABLE_NODES)[1:]])
    blocked = _blocked(z, k, u1, u2)
    blocked[-1] = 0.0                                  # last contact
    return z, blocked


class _ExposureGrid:
    """The grid that averages a model over one exposure at the offsets ``dt``: ``EXPOSURE_RESOLUTION_MIN`` samples
    spanning the offsets plus one exposure (symmetric about mid-transit, where the model is, so only one half is
    computed), a running mean over ``round(exposure / resolution)`` of them and linear interpolation back to ``dt``.
    Built once per fit, since the offsets do not change between the solver's calls. On the grid the blocked light is
    read from ``_blocked_table``, linear in z: within 1e-7 of the integral itself for a radius ratio up to 0.1 and
    within 1e-5 of it at any (``test_transit_model``), which keeps a fit's thousand solver calls at about a
    millisecond each. An exposure at or below the resolution keeps the plain flux of ``quadratic_ld_flux``."""

    def __init__(self, dt: np.ndarray, period: float, exposure_days: float):
        self.dt, self.period = dt, period
        step = EXPOSURE_RESOLUTION_MIN / 1440.0
        self.n_avg = round(exposure_days / step) if exposure_days > step else 1
        self.half = None
        if self.n_avg > 1 and dt.size:
            m = math.ceil((float(np.max(np.abs(dt))) + exposure_days) / step)
            self.half = step * np.arange(m + 1)
            # the running mean of samples i to i + n_avg - 1 sits at the centre of the window; the centres are evenly
            # spaced, so the linear interpolation at each offset is fixed once: an index and a weight
            first = -m * step + 0.5 * (self.n_avg - 1) * step
            n_centres = 2 * m + 1 - self.n_avg + 1
            pos = np.clip((dt - first) / step, 0.0, n_centres - 1.0)
            self.index = np.minimum(np.floor(pos).astype(np.int64), n_centres - 2)
            self.weight = pos - self.index

    def flux(self, k: float, b: float, a_over_r: float, u1: float, u2: float) -> np.ndarray:
        if self.half is None:
            return quadratic_ld_flux(self.dt, self.period, k, b, a_over_r, u1, u2)
        dip = np.zeros(self.half.size)
        if k > 0 and a_over_r > 0:
            z = _separation(self.half, self.period, b, a_over_r)
            hit = z < 1.0 + k
            if hit.any():
                dip[hit] = np.interp(z[hit], *_blocked_table(k, u1, u2))
        csum = np.concatenate([[0.0], np.cumsum(np.concatenate([dip[:0:-1], dip]))])
        avg = (csum[self.n_avg:] - csum[:-self.n_avg]) / self.n_avg
        return 1.0 - (avg[self.index] * (1.0 - self.weight) + avg[self.index + 1] * self.weight)


def exposure_flux(dt_days, period, k, b, a_over_r, u1, u2, exposure_days: float) -> np.ndarray:
    """``quadratic_ld_flux`` averaged over an exposure of ``exposure_days`` centred on each offset (Kipping 2010):
    the model on an ``EXPOSURE_RESOLUTION_MIN`` grid spanning the offsets plus one exposure, a running mean over
    ``round(exposure / resolution)`` samples, interpolated to ``dt_days``. Only the grid samples where the disc touches
    the star are integrated (through ``_blocked_table``); the rest are 1. An exposure at or below the resolution gives
    the plain flux."""
    dt = np.atleast_1d(np.asarray(dt_days, dtype=float))
    return _ExposureGrid(dt, float(period), float(exposure_days)).flux(k, b, a_over_r, u1, u2)


def stellar_density(a_over_r: float, period_days: float) -> float:
    """The stellar density that a circular orbit of ``a_over_r`` at ``period_days`` implies, ``3 pi (a/R*)^3 / (G
    P^2)`` in g/cm3 (the planet's mass neglected)."""
    p = float(period_days) * 86400.0
    return float(3.0 * np.pi * float(a_over_r) ** 3 / (G_CGS * p * p))


def _jf(x) -> float | None:
    """A Python float for JSON, None for NaN, inf and missing values."""
    if x is None:
        return None
    x = float(x)
    return x if math.isfinite(x) else None


@dataclass(frozen=True)
class ShapeFit:
    ok: bool
    reason: str | None
    variant: str                       # "free" | "prior"
    k: float
    b: float
    a_over_r: float
    t14_days: float
    depth_ppm: float                   # 1 - the limb-darkened model at mid-transit, before the exposure average
    chi2_data: float
    chi2_prior: float                  # the prior term; 0 for "free"
    chi2_total: float                  # chi2_data + chi2_prior
    n_points: int
    u1: float
    u2: float
    exposure_min: float
    density_gcc: float                 # stellar_density(a_over_r, period) of this solution
    prior_gcc: tuple[float, float] | None   # (mean, sigma) of the prior; None for "free"

    def as_dict(self) -> dict:
        """Strict JSON: NaN and infinite values as None, the prior as a list."""
        return {"ok": bool(self.ok), "reason": self.reason, "variant": self.variant, "k": _jf(self.k),
                "b": _jf(self.b), "a_over_r": _jf(self.a_over_r), "t14_days": _jf(self.t14_days),
                "depth_ppm": _jf(self.depth_ppm), "chi2_data": _jf(self.chi2_data),
                "chi2_prior": _jf(self.chi2_prior), "chi2_total": _jf(self.chi2_total),
                "n_points": int(self.n_points), "u1": _jf(self.u1), "u2": _jf(self.u2),
                "exposure_min": _jf(self.exposure_min), "density_gcc": _jf(self.density_gcc),
                "prior_gcc": None if self.prior_gcc is None else [_jf(v) for v in self.prior_gcc]}


def _shape_failed(variant, reason, n_points, u1, u2, exposure_days, prior) -> ShapeFit:
    nan = float("nan")
    return ShapeFit(False, reason, variant, nan, nan, nan, nan, nan, nan, nan, nan, int(n_points), float(u1),
                    float(u2), float(exposure_days) * 1440.0, nan, prior)


def fit_shape(time, fnorm, period, t0, duration_days, *, u1: float, u2: float, exposure_days: float,
              density: tuple[float, float] | None = None, start: ShapeFit | None = None) -> ShapeFit:
    """Least-squares fit of ``exposure_flux`` (times a free baseline level ``c``) to the unbinned fold within
    ``FIT_WINDOW_DURATIONS`` durations of mid-transit, every point weighted by the robust scatter (1.4826 times the
    median absolute deviation) of the window's points beyond one duration, as ``ephemeris.transit_times`` takes it, so
    the transit itself does not inflate it (the whole window below ``SCATTER_MIN_POINTS`` of them). The parameters
    (k, b, a_over_r, c) stay inside ``K_BOUNDS``, ``B_BOUNDS``, ``A_BOUNDS`` and ``C_BOUNDS``. The solver starts from
    the nine geometries ``fit_transit`` uses (a central and two grazing ones, each at ``START_DURATION_FACTORS`` times
    the duration), or from ``start`` alone when it is given and ok (the bootstrap starts each resample at the full-data
    solution); the converged start with the lowest cost wins.

    With ``density`` (mean and sigma in g/cm3) the residuals gain ``(stellar_density(a_over_r) - mean) / sigma``, a
    Gaussian prior on the stellar density, and the variant is "prior"; its square is ``chi2_prior``. Returns ``ok``
    False with NaN values and the reason below ``SHAPE_MIN_POINTS`` points in the window, without a point within half
    a duration of mid-transit, with a prior whose sigma is not positive, or when no start converged."""
    variant = "free" if density is None else "prior"
    prior = None if density is None else (float(density[0]), float(density[1]))
    time, fnorm = np.asarray(time, dtype=float), np.asarray(fnorm, dtype=float)
    good = np.isfinite(time) & np.isfinite(fnorm)
    if not good.any() or not duration_days > 0:
        return _shape_failed(variant, "no points to fit", 0, u1, u2, exposure_days, prior)
    dt_all = fold_phase(time[good], period, t0)
    near = np.abs(dt_all) < FIT_WINDOW_DURATIONS * duration_days
    dt, y = dt_all[near], fnorm[good][near]
    n = int(dt.size)
    if n < SHAPE_MIN_POINTS:
        return _shape_failed(variant, f"{n} points within {FIT_WINDOW_DURATIONS:g} durations of mid-transit "
                             f"(fewer than {SHAPE_MIN_POINTS})", n, u1, u2, exposure_days, prior)
    if prior is not None and not (math.isfinite(prior[0]) and math.isfinite(prior[1]) and prior[1] > 0):
        return _shape_failed(variant, "the density prior has no positive error", n, u1, u2, exposure_days, prior)
    inside, outside = np.abs(dt) < 0.5 * duration_days, np.abs(dt) >= duration_days
    if not inside.any():
        return _shape_failed(variant, "no point within half a duration of mid-transit", n, u1, u2, exposure_days,
                             prior)
    quiet = y[outside] if int(outside.sum()) >= SCATTER_MIN_POINTS else y
    sigma = SHAPE_MAD_TO_SIGMA * float(np.median(np.abs(quiet - np.median(quiet)))) or 1e-12
    grid = _ExposureGrid(dt, float(period), float(exposure_days))

    def residuals(p):
        k, b, a, c = p
        r = (y - c * grid.flux(k, b, a, u1, u2)) / sigma
        if prior is None:
            return r
        return np.append(r, (stellar_density(a, period) - prior[0]) / prior[1])

    base = float(np.median(y[outside])) if outside.any() else 1.0
    c0 = float(np.clip(base, *C_BOUNDS))
    lower = [K_BOUNDS[0], B_BOUNDS[0], A_BOUNDS[0], C_BOUNDS[0]]
    upper = [K_BOUNDS[1], B_BOUNDS[1], A_BOUNDS[1], C_BOUNDS[1]]
    starts = []
    if start is not None and start.ok and all(math.isfinite(v) for v in (start.k, start.b, start.a_over_r)):
        p0 = [float(np.clip(v, lo, hi))
              for v, lo, hi in zip((start.k, start.b, start.a_over_r, c0), lower, upper, strict=True)]
        starts.append((p0, [0.05, 0.3, max(p0[2], 2.0), 0.01]))
    else:
        box_depth = 1.0 - float(np.mean(y[inside])) / base
        k_box = float(np.clip(np.sqrt(max(box_depth, 1e-6)), *K_BOUNDS))
        for factor in START_DURATION_FACTORS:
            for k0, b0 in ((k_box, 0.3), (max(k_box, 0.3), 0.9), (max(2 * k_box, 0.5), 1.2)):
                a0 = period * np.sqrt(max((1.0 + k0) ** 2 - b0 * b0, 0.05)) / (np.pi * factor * duration_days)
                a0 = float(np.clip(a0, *A_BOUNDS))
                starts.append(([k0, b0, a0, c0], [0.05, 0.3, max(a0, 2.0), 0.01]))
    res = _best_of(residuals, starts, lower, upper)
    if res is None or not res.success:
        return _shape_failed(variant, "no start converged", n, u1, u2, exposure_days, prior)
    k, b, a, _c = (float(v) for v in res.x)
    chi2_data = float(np.sum(res.fun[:n] ** 2))
    chi2_prior = float(res.fun[n] ** 2) if prior is not None else 0.0
    depth = float((1.0 - quadratic_ld_flux(np.array([0.0]), period, k, b, a, u1, u2)[0]) * 1e6)
    return ShapeFit(True, None, variant, k, b, a, _t14(period, k, b, a), depth, chi2_data, chi2_prior,
                    chi2_data + chi2_prior, n, float(u1), float(u2), float(exposure_days) * 1440.0,
                    stellar_density(a, period), prior)


def compare_fits(free: ShapeFit, prior: ShapeFit | None) -> dict:
    """Which fit the dossier quotes (spec 11.3). ``delta_chi2`` is the prior fit's chi-square, its prior term
    included, less the free fit's: the data term alone moves by under 1 when a wrong prior meets a shape that cannot
    resist it, so the prior term carries the tension. ``density_tension_sigma`` is the free solution's density less
    the prior mean, in the prior's sigma, stored only when ``constrained`` is True (in a flat valley the free fit's
    density means nothing). Without a prior fit, or with one that is not ok, the free fit is quoted with ``constrained``
    None, a/R* "fitted" and the reason ``NO_DENSITY`` (or the prior fit's own). A free fit that is not ok, beside a
    prior fit that is, gives the prior fit with ``constrained`` None and the free fit's reason. Within
    ``CONSTRAINED_DELTA_CHI2`` the shape does not constrain b and a/R*: the prior fit is quoted, a/R* "from the
    stellar parameters", the reason ``UNCONSTRAINED``; beyond it the free fit is quoted and a/R* "fitted"."""
    def out(constrained, quoted, delta, tension, label, reason):
        return {"constrained": constrained, "quoted": quoted, "delta_chi2": _jf(delta),
                "density_tension_sigma": _jf(tension), "a_over_r_label": label, "reason": reason}
    if prior is None or not prior.ok:
        return out(None, "free", None, None, A_LABEL_FITTED, NO_DENSITY if prior is None else prior.reason)
    if not free.ok:
        return out(None, "prior", None, None, A_LABEL_STELLAR, free.reason)
    mean, sigma = prior.prior_gcc
    delta = prior.chi2_total - free.chi2_total
    tension = (free.density_gcc - mean) / sigma
    if delta <= CONSTRAINED_DELTA_CHI2:
        return out(False, "prior", delta, None, A_LABEL_STELLAR, UNCONSTRAINED)
    return out(True, "free", delta, tension, A_LABEL_FITTED, None)
