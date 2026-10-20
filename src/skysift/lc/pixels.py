"""Difference-image localisation of a transit signal on Kepler target pixel files (spec section 4.4).

Ported from the KIC 6146676 study (``docs/candidates/kic-006146676/localisation/diffimage.py`` and
``context/a2_leak_test.py``). Pure numerics: the caller supplies the pixel cube, its times, quality flags and quarters,
the ephemeris and the pixel positions of the target and of the neighbouring star. Positions are zero-based
``(col, row)`` on the cube's pixel grid with pixel centres at integers; images are indexed ``[row, col]``.

Estimators, as in the study:
- per event, mean out-of-transit image minus mean in-transit image, its variance from the out-of-transit scatter;
- the events of every usable quarter of a season stacked with inverse-variance weights (a star returns to the same
  CCD module every fourth quarter, so a season's quarters share one pixel grid);
- the 3x3 flux-weighted centroid boxed on the highest signal-to-noise difference pixel (the brightest-pixel box was
  bimodal on KIC 6146676), its error from an N_MC-draw noise Monte Carlo;
- a two-source Gaussian PSF fit (free width, free common shift) on the direct image for each star's flux, the same
  PSFs on the difference image for each star's lost flux, implied depth = lost flux / flux.

Real target pixel files change stamp shape between quarters and the stars' pixel positions change between seasons,
so the general entry point is ``localise_quarters`` on one ``QuarterData`` per quarter, each with its own stamp and
positions; within a season the quarters are co-registered on the integer pixel offset of the target.
``localise`` is the single-grid convenience wrapper.

Known limitation: the Gaussian PSF is not the Kepler PRF, so the two-source depth errors are somewhat optimistic
when the model does not match the stars' images (on a pixel-integrated synthetic stamp a dip on the neighbour put
2.7 sigma of depth on the target in one season).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import least_squares

from skysift.lc.ops import season_of

PIX_ARCSEC = 3.98
N_MC = 200
RNG_SEED = 20261020

EVENT_IN_MIN = 3            # good in-transit cadences an event needs
EVENT_OUT_MIN = 6           # good out-of-transit cadences an event needs
EVENT_OUT_SIDE_MIN = 2      # ... of which at least this many on each side (a one-sided baseline follows any ramp)
QUARTER_EVENTS_MIN = 2      # a quarter with fewer usable events is unusable
SEASON_EVENTS_MIN = 2       # seasons with fewer events do not vote in the verdict
TRANSITS_MIN = 3            # fewer usable transits in all leaves the rung undetermined
VALID_FINITE_FRACTION = 0.9  # a pixel is valid when finite this often, relative to the best pixel of the quarter
SEPARATION_SIGMA = 3.0      # pass: centroid nearer the target by more than this many sigma
NEIGHBOUR_DEPTH_SIGMA = 3.0  # pass: |two-source depth on the neighbour| below this many sigma; fail: above it
TARGET_DEPTH_SIGMA = 1.0    # fail (two-source route): depth on the target below this many sigma
DIRECT_WEIGHT_FLOOR = 100.0  # direct-image fit weights 1 / sqrt(|direct| + floor), photon-like (study choice)
LEAK_WINDOW_DAYS = 2.0      # running-median window of the leak-test detrend
LEAK_N_CONTROL = 100        # control phases that calibrate the leak-test error
LEAK_IN_MIN, LEAK_OUT_MIN = 5, 10


@dataclass(frozen=True)
class Ephemeris:
    period: float       # d
    t0: float           # BKJD
    duration: float     # d


@dataclass
class QuarterData:
    """One quarter's target pixel file: the cube (n_cad, rows, cols) and the stars' positions, (col, row) in this
    quarter's own stamp."""
    quarter: int
    cube: np.ndarray
    time: np.ndarray
    quality: np.ndarray
    pos_target: tuple[float, float]
    pos_neighbour: tuple[float, float]


_NAN2 = (math.nan, math.nan)


@dataclass
class SeasonImage:
    """A season's stacked images on the grid of its reference quarter (the first usable quarter whose stamp holds
    the neighbour, else the first usable quarter). ``offsets_pix`` is the integer (col, row) shift that took each
    quarter onto that grid, ``residual_offsets_pix`` the target's leftover fractional (col, row) offset (below
    0.5 px by construction)."""
    season: int
    quarters: list[int]
    n_events: int
    diff: np.ndarray        # out-of-transit minus in-transit flux per pixel (lost light is positive)
    var: np.ndarray         # its variance; inf on invalid pixels
    direct: np.ndarray      # mean out-of-transit image; NaN on invalid pixels
    valid: np.ndarray       # pixels valid in at least one co-registered quarter of the season
    pos_target: tuple[float, float] = _NAN2         # season-frame (col, row)
    pos_neighbour: tuple[float, float] = _NAN2
    offsets_pix: list[tuple[int, int]] = field(default_factory=list)
    residual_offsets_pix: list[tuple[float, float]] = field(default_factory=list)
    neighbour_in_frame: bool = False                # the neighbour's pixel lies on a valid pixel of the frame


@dataclass
class SeasonResult:
    season: int
    n_events: int
    centroid_colrow: tuple[float, float]
    centroid_sigma_pix: float
    dist_target_pix: float
    dist_neighbour_pix: float
    nearer: str                                 # "target" | "neighbour"
    separation_sigma: float                     # (dist_neighbour - dist_target) / sigma
    depth_target_ppm: tuple[float, float]       # two-source implied depth, error
    depth_neighbour_ppm: tuple[float, float]
    max_snr: float
    quarters: list[int] = field(default_factory=list)
    two_source: bool = True         # False: no quarter of the season holds the neighbour, the fit is unavailable


def _f(x) -> float | None:
    """A Python float for JSON, None for NaN, inf and missing values."""
    if x is None:
        return None
    x = float(x)
    return x if math.isfinite(x) else None


@dataclass
class Localisation:
    seasons: list[SeasonResult]
    n_events_used: int
    n_quarters_usable: int
    unusable_quarters: list[int]
    verdict: str            # "pass" | "fail" | "undetermined"
    reason: str
    images: list[SeasonImage] = field(default_factory=list, repr=False)

    def metrics(self) -> dict:
        """Strict-JSON metrics (no NaN or inf, no arrays) for the test row, the dashboard and the dossier."""
        seasons = [{
            "season": int(s.season), "quarters": [int(q) for q in s.quarters], "n_events": int(s.n_events),
            "centroid_colrow": [_f(x) for x in s.centroid_colrow], "centroid_sigma_pix": _f(s.centroid_sigma_pix),
            "dist_target_pix": _f(s.dist_target_pix), "dist_neighbour_pix": _f(s.dist_neighbour_pix),
            "nearer": s.nearer, "separation_sigma": _f(s.separation_sigma),
            "depth_target_ppm": [_f(x) for x in s.depth_target_ppm],
            "depth_neighbour_ppm": [_f(x) for x in s.depth_neighbour_ppm], "max_snr": _f(s.max_snr),
        } for s in self.seasons]
        sig = [s["centroid_sigma_pix"] for s in seasons if s["centroid_sigma_pix"] is not None]
        return {"seasons": seasons, "n_events_used": int(self.n_events_used),
                "n_quarters_usable": int(self.n_quarters_usable),
                "unusable_quarters": [int(q) for q in self.unusable_quarters],
                "verdict": self.verdict, "reason": self.reason,
                "centroid_sigma_pix_mean": _f(np.mean(sig)) if sig else None}


def _epoch_dt(time: np.ndarray, eph: Ephemeris) -> tuple[np.ndarray, np.ndarray]:
    n = np.round((time - eph.t0) / eph.period)
    return n.astype(np.int64), time - (eph.t0 + n * eph.period)


def _quarter_events(qd: QuarterData, eph: Ephemeris, in_half: float, out_lo: float, out_hi: float):
    """The usable events of one quarter as (diff, var) image pairs, its valid-pixel mask and its mean
    out-of-transit image; None when the quarter is unusable (fewer than QUARTER_EVENTS_MIN events)."""
    fl = np.asarray(qd.cube, float); time = np.asarray(qd.time, float); quality = np.asarray(qd.quality)
    if fl.ndim != 3 or fl.shape[0] != time.size or quality.size != time.size:
        raise ValueError(f"quarter {qd.quarter}: cube {fl.shape} does not match {time.size} cadences")
    if time.size == 0:
        return None
    ep, d = _epoch_dt(time, eph)
    half, lo, hi = in_half * eph.duration, out_lo * eph.duration, out_hi * eph.duration
    fin = np.isfinite(fl).mean(axis=0)
    valid = (fin > VALID_FINITE_FRACTION * fin.max()) if fin.max() > 0 else np.zeros(fin.shape, bool)
    if not valid.any():
        return None
    good = (quality == 0) & np.isfinite(time) & np.all(np.isfinite(fl[:, valid]), axis=1)
    events, outmask = [], np.zeros(fl.shape[0], bool)
    for e in np.unique(ep[good & (np.abs(d) < half)]):
        sel = good & (ep == e)
        ins = sel & (np.abs(d) < half)
        outs = sel & (np.abs(d) >= lo) & (np.abs(d) <= hi)
        n_in, n_out = int(ins.sum()), int(outs.sum())
        n_before, n_after = int((outs & (d < 0)).sum()), int((outs & (d > 0)).sum())
        if n_in < EVENT_IN_MIN or n_out < EVENT_OUT_MIN or min(n_before, n_after) < EVENT_OUT_SIDE_MIN:
            continue
        fo, fi = fl[outs], fl[ins]
        with np.errstate(invalid="ignore"):
            img = fo.mean(axis=0) - fi.mean(axis=0)
            var = fo.var(axis=0, ddof=1) * (1.0 / n_in + 1.0 / n_out)
        ok = valid & np.isfinite(img) & np.isfinite(var) & (var > 0)
        events.append((np.where(ok, img, 0.0), np.where(ok, var, np.inf)))
        outmask |= outs
    if len(events) < QUARTER_EVENTS_MIN:
        return None
    with np.errstate(invalid="ignore"):
        direct = np.where(valid, np.nanmean(np.where(valid, fl[outmask], np.nan), axis=0), np.nan)
    return events, valid, direct


def _finite2(p) -> bool:
    return p is not None and all(math.isfinite(float(x)) for x in p)


def _holds(pos, valid: np.ndarray) -> bool:
    """True when the (col, row) position falls on a valid pixel of the grid."""
    if not _finite2(pos):
        return False
    c, r = round(float(pos[0])), round(float(pos[1]))
    return 0 <= r < valid.shape[0] and 0 <= c < valid.shape[1] and bool(valid[r, c])


def _shift_into(img: np.ndarray, shape: tuple[int, int], dc: int, dr: int, fill) -> np.ndarray:
    """``img`` on a ``shape`` frame where frame pixel (r, c) is image pixel (r + dr, c + dc); ``fill`` elsewhere."""
    out = np.full(shape, fill, dtype=img.dtype)
    r0, r1 = max(0, -dr), min(shape[0], img.shape[0] - dr)
    c0, c1 = max(0, -dc), min(shape[1], img.shape[1] - dc)
    if r1 > r0 and c1 > c0:
        out[r0:r1, c0:c1] = img[r0 + dr:r1 + dr, c0 + dc:c1 + dc]
    return out


def season_images(quarters: list[QuarterData], eph: Ephemeris, *, in_half: float = 0.5, out_lo: float = 1.0,
                  out_hi: float = 3.0) -> list[SeasonImage]:
    """Per-season difference images from per-quarter stamps, sorted by season. Per quarter the events as in
    ``difference_images``; within a season (``season_of``) every usable quarter is moved onto the grid of the
    reference quarter (the first usable one whose stamp holds the neighbour, else the first usable one) by the
    integer pixel offset round(pos_target - reference pos_target), with invalid padding where the stamps do not
    overlap, and the events are stacked with per-pixel inverse-variance weights. The season's positions are the
    reference quarter's."""
    usable: dict[int, tuple] = {}
    for qd in sorted(quarters, key=lambda x: int(x.quarter)):
        res = _quarter_events(qd, eph, in_half, out_lo, out_hi)
        if res is not None:
            usable[int(qd.quarter)] = (qd, *res)
    by_season: dict[int, list[int]] = {}
    for q in sorted(usable):
        by_season.setdefault(int(season_of(np.array([q]))[0]), []).append(q)
    images = []
    for s in sorted(by_season):
        qs = by_season[s]
        ref = next((q for q in qs if _holds(usable[q][0].pos_neighbour, usable[q][2])), qs[0])
        rqd = usable[ref][0]
        shape = usable[ref][2].shape
        p_t = tuple(float(x) for x in rqd.pos_target) if _finite2(rqd.pos_target) else _NAN2
        p_n = tuple(float(x) for x in rqd.pos_neighbour) if _finite2(rqd.pos_neighbour) else _NAN2
        num, wsum = np.zeros(shape), np.zeros(shape)
        dsum, dn = np.zeros(shape), np.zeros(shape)
        offsets, residuals, n_events = [], [], 0
        for q in qs:
            qd, events, valid, direct = usable[q]
            if _finite2(qd.pos_target) and _finite2(p_t):
                dc = round(float(qd.pos_target[0]) - p_t[0]); dr = round(float(qd.pos_target[1]) - p_t[1])
                residuals.append((float(qd.pos_target[0]) - dc - p_t[0], float(qd.pos_target[1]) - dr - p_t[1]))
            else:
                dc = dr = 0; residuals.append(_NAN2)
            offsets.append((dc, dr))
            v = _shift_into(valid, shape, dc, dr, False)
            for img, var in events:
                vv = _shift_into(var, shape, dc, dr, np.inf)
                w = np.where(v & np.isfinite(vv), 1.0 / np.where(np.isfinite(vv), vv, 1.0), 0.0)
                num += w * _shift_into(img, shape, dc, dr, 0.0); wsum += w
            dsum += np.where(v, _shift_into(np.nan_to_num(direct), shape, dc, dr, 0.0), 0.0); dn += v
            n_events += len(events)
        valid = wsum > 0
        safe = np.where(valid, wsum, 1.0)
        images.append(SeasonImage(
            season=s, quarters=qs, n_events=n_events, diff=np.where(valid, num / safe, 0.0),
            var=np.where(valid, 1.0 / safe, np.inf),
            direct=np.where(valid & (dn > 0), dsum / np.maximum(dn, 1), np.nan), valid=valid,
            pos_target=p_t, pos_neighbour=p_n, offsets_pix=offsets, residual_offsets_pix=residuals,
            neighbour_in_frame=_holds(p_n, valid)))
    return images


def _split_quarters(cube, time, quality, quarter, pos_target, pos_neighbour) -> list[QuarterData]:
    cube = np.asarray(cube, float); time = np.asarray(time, float)
    quality = np.asarray(quality); quarter = np.asarray(quarter).astype(int)
    if cube.ndim != 3 or cube.shape[0] != time.size or quality.size != time.size or quarter.size != time.size:
        raise ValueError(f"cube {cube.shape} does not match {time.size} cadences")
    return [QuarterData(quarter=int(q), cube=cube[quarter == q], time=time[quarter == q],
                        quality=quality[quarter == q], pos_target=pos_target, pos_neighbour=pos_neighbour)
            for q in np.unique(quarter)]


def difference_images(cube, time, quality, quarter, eph: Ephemeris, *, in_half: float = 0.5, out_lo: float = 1.0,
                      out_hi: float = 3.0, pos_target=_NAN2, pos_neighbour=_NAN2) -> list[SeasonImage]:
    """Per-season difference images of one cube on one pixel grid, sorted by season. Per quarter, every event with
    at least EVENT_IN_MIN good cadences within ``in_half`` durations of mid-transit and EVENT_OUT_MIN good cadences
    between ``out_lo`` and ``out_hi`` durations (EVENT_OUT_SIDE_MIN on each side) gives mean(out) - mean(in) per
    pixel, with variance std(out)^2 (1/n_in + 1/n_out). Good means quality 0 and finite flux in every valid pixel.
    Quarters with fewer than QUARTER_EVENTS_MIN events are unusable; the events of a season's usable quarters are
    stacked with inverse-variance weights. The positions, when given, are carried into the season images."""
    return season_images(_split_quarters(cube, time, quality, quarter, pos_target, pos_neighbour), eph,
                         in_half=in_half, out_lo=out_lo, out_hi=out_hi)


def _snr(diff: np.ndarray, var: np.ndarray, valid: np.ndarray) -> np.ndarray:
    ok = valid & np.isfinite(var) & (var > 0) & np.isfinite(diff)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(ok, diff / np.sqrt(np.where(ok, var, 1.0)), -np.inf)


def _box_centroid(img: np.ndarray, valid: np.ndarray, r0: int, c0: int, half: int = 1) -> tuple[float, float]:
    """Flux-weighted (col, row) centroid of the positive valid pixels in the (2 half + 1)^2 box around (r0, c0)."""
    rs = slice(max(r0 - half, 0), min(r0 + half + 1, img.shape[0]))
    cs = slice(max(c0 - half, 0), min(c0 + half + 1, img.shape[1]))
    sub = np.clip(np.where(valid[rs, cs], img[rs, cs], 0.0), 0.0, None)
    rr, cc = np.mgrid[rs, cs]
    tot = sub.sum()
    if not tot > 0:
        return math.nan, math.nan
    return float((sub * cc).sum() / tot), float((sub * rr).sum() / tot)


def snr_box_centroid(diff, var, valid, rng) -> tuple[tuple[float, float], float, float]:
    """3x3 flux-weighted centroid ``(col, row)`` around the highest-S/N difference pixel, its error and that S/N.
    The error is sqrt of the mean per-axis variance of N_MC centroids of ``diff + N(0, sqrt(var))``, each redrawn
    around its own highest-S/N pixel."""
    diff = np.asarray(diff, float); var = np.asarray(var, float); valid = np.asarray(valid, bool)
    snr = _snr(diff, var, valid)
    if not np.isfinite(snr).any():
        return (math.nan, math.nan), math.nan, math.nan
    r0, c0 = np.unravel_index(int(np.argmax(snr)), snr.shape)
    centroid = _box_centroid(diff, valid, int(r0), int(c0))
    sd = np.sqrt(np.where(valid & np.isfinite(var), var, 0.0))
    draws = np.empty((N_MC, 2))
    for i in range(N_MC):
        dm = diff + rng.normal(size=diff.shape) * sd
        a, b = np.unravel_index(int(np.argmax(_snr(dm, var, valid))), dm.shape)
        draws[i] = _box_centroid(dm, valid, int(a), int(b))
    draws = draws[np.all(np.isfinite(draws), axis=1)]
    sigma = float(np.sqrt(np.mean(np.var(draws, axis=0, ddof=1)))) if len(draws) >= 2 else math.nan
    return centroid, sigma, float(snr[r0, c0])


def gauss_psf(shape: tuple[int, int], col: float, row: float, sig: float) -> np.ndarray:
    """Circular Gaussian of unit integral sampled at the pixel centres of a ``shape`` (rows, cols) grid."""
    rr, cc = np.mgrid[0:shape[0], 0:shape[1]]
    return np.exp(-0.5 * ((cc - col) ** 2 + (rr - row) ** 2) / sig ** 2) / (2 * np.pi * sig ** 2)


def _nan_fit() -> dict:
    nan2 = (math.nan, math.nan)
    return {"psf_sigma_pix": math.nan, "shift_pix": nan2, "direct_flux_target": math.nan,
            "direct_flux_target_err": math.nan, "direct_flux_neighbour": math.nan,
            "direct_flux_neighbour_err": math.nan, "diff_amp_target": math.nan, "diff_amp_target_err": math.nan,
            "diff_amp_neighbour": math.nan, "diff_amp_neighbour_err": math.nan, "depth_target_ppm": nan2,
            "depth_neighbour_ppm": nan2, "chi2_two_source": math.nan, "npix": 0}


def _depth(a: float, var_a: float, f: float, var_f: float) -> tuple[float, float]:
    if not (f > 0 and math.isfinite(a)):
        return math.nan, math.nan
    return 1e6 * a / f, 1e6 * math.sqrt(max(var_a, 0.0) / f ** 2 + a ** 2 * max(var_f, 0.0) / f ** 4)


def two_source_fit(direct, diff, var, valid, pos_target, pos_neighbour) -> dict:
    """Two Gaussian PSFs at the target and neighbour positions. On the direct image: both fluxes, a constant, a
    common width and a common shift (it absorbs WCS and PSF-centre error), by ``least_squares``; flux errors from its
    Jacobian scaled by the reduced chi-square. On the difference image: the same two PSFs, free amplitudes by
    weighted linear least squares (errors from its design matrix, the linear Jacobian). Implied depth per star =
    difference amplitude / direct flux, in ppm with its error; an approximate check, the PSF is not the Kepler PRF."""
    direct = np.asarray(direct, float); diff = np.asarray(diff, float); var = np.asarray(var, float)
    shape = direct.shape
    v = np.asarray(valid, bool) & np.isfinite(direct) & np.isfinite(var) & (var > 0) & np.isfinite(diff)
    if v.sum() < 8:
        return _nan_fit()
    (ct, rt), (cn, rn) = (float(x) for x in pos_target), (float(x) for x in pos_neighbour)
    wd = 1.0 / np.sqrt(np.abs(direct[v]) + DIRECT_WEIGHT_FLOOR)

    def psfs(dx, dy, sig):
        return gauss_psf(shape, ct + dx, rt + dy, sig)[v], gauss_psf(shape, cn + dx, rn + dy, sig)[v]

    def resid(p):
        dx, dy, sig, ft, fn, b = p
        pt, pn = psfs(dx, dy, sig)
        return (direct[v] - (ft * pt + fn * pn + b)) * wd

    pt0, pn0 = psfs(0.0, 0.0, 1.0)                  # starting fluxes: linear fit at width 1 px, no shift
    a0 = np.linalg.lstsq(np.vstack([pt0, pn0, np.ones(v.sum())]).T * wd[:, None], direct[v] * wd, rcond=None)[0]
    tot = float(np.abs(direct[v]).sum()) + 1.0
    bmax = float(np.abs(direct[v]).max()) + 1.0
    p0 = [0.0, 0.0, 1.0, max(a0[0], 1e-3 * tot), max(a0[1], 1e-3 * tot), float(np.clip(a0[2], -0.9 * bmax, 0.9 * bmax))]
    sol = least_squares(resid, p0, bounds=([-1, -1, 0.3, 0, 0, -bmax], [1, 1, 3, np.inf, np.inf, bmax]))
    dx, dy, sig, ft, fn, _b = (float(x) for x in sol.x)
    dof = max(int(v.sum()) - len(p0), 1)
    cov_direct = np.linalg.pinv(sol.jac.T @ sol.jac) * (2.0 * sol.cost / dof)

    pt, pn = psfs(dx, dy, sig)
    w = 1.0 / var[v]
    A = np.vstack([pt, pn]).T
    cov_diff = np.linalg.pinv(A.T @ (A * w[:, None]))
    amp = cov_diff @ (A.T @ (diff[v] * w))
    chi2 = float((((diff[v] - A @ amp) ** 2) * w).sum())
    return {"psf_sigma_pix": sig, "shift_pix": (dx, dy),
            "direct_flux_target": ft, "direct_flux_target_err": float(math.sqrt(max(cov_direct[3, 3], 0.0))),
            "direct_flux_neighbour": fn, "direct_flux_neighbour_err": float(math.sqrt(max(cov_direct[4, 4], 0.0))),
            "diff_amp_target": float(amp[0]), "diff_amp_target_err": float(math.sqrt(max(cov_diff[0, 0], 0.0))),
            "diff_amp_neighbour": float(amp[1]),
            "diff_amp_neighbour_err": float(math.sqrt(max(cov_diff[1, 1], 0.0))),
            "depth_target_ppm": _depth(float(amp[0]), cov_diff[0, 0], ft, cov_direct[3, 3]),
            "depth_neighbour_ppm": _depth(float(amp[1]), cov_diff[1, 1], fn, cov_direct[4, 4]),
            "chi2_two_source": chi2, "npix": int(v.sum())}


def _phase_dt(time: np.ndarray, t0: float, period: float) -> np.ndarray:
    return (time - t0 + 0.5 * period) % period - 0.5 * period


def _box(time: np.ndarray, fn: np.ndarray, t0: float, eph: Ephemeris, usable: np.ndarray) -> tuple[float, float]:
    """a2 ``box``: depth = mean(out) - mean(in) in ppm, in |dt| < dur/2, out dur <= |dt| <= 3 dur,
    error std(out) sqrt(1/n_in + 1/n_out)."""
    d = np.abs(_phase_dt(time, t0, eph.period))
    fin = fn[usable & (d < 0.5 * eph.duration)]
    fout = fn[usable & (d >= eph.duration) & (d <= 3.0 * eph.duration)]
    if fin.size < LEAK_IN_MIN or fout.size < LEAK_OUT_MIN:
        return math.nan, math.nan
    s = float(np.std(fout, ddof=1))
    return (float(np.mean(fout)) - float(np.mean(fin))) * 1e6, s * math.sqrt(1 / fin.size + 1 / fout.size) * 1e6


def leak_depth(time, flux, quality, eph: Ephemeris) -> tuple[float, float]:
    """Box depth and error (ppm) at the ephemeris in a light curve (the a2 ``measure`` method). The flux is divided
    by a LEAK_WINDOW_DAYS running median with the transits (|dt| < one duration) excluded, so raw counts are fine;
    quality-flagged and non-finite cadences are dropped. The formal error is inflated by the scatter of the depths at
    LEAK_N_CONTROL control phases (each at least four durations from the transit, the transits masked) when that
    scatter is larger. Returns (NaN, NaN) when there are too few points in or around the transits; callers turn NaN
    into None before it goes into JSON metrics."""
    t = np.asarray(time, float); f = np.asarray(flux, float); q = np.asarray(quality)
    ok = (q == 0) & np.isfinite(t) & np.isfinite(f)
    t, f = t[ok], f[ok]
    order = np.argsort(t); t, f = t[order], f[order]
    if t.size == 0:
        return math.nan, math.nan
    keep = np.abs(_phase_dt(t, eph.t0, eph.period)) >= eph.duration
    tk, fk = t[keep], f[keep]
    lo = np.searchsorted(tk, t - 0.5 * LEAK_WINDOW_DAYS); hi = np.searchsorted(tk, t + 0.5 * LEAK_WINDOW_DAYS)
    med = np.array([np.median(fk[a:b]) if b - a >= 10 else np.nan for a, b in zip(lo, hi)])
    with np.errstate(invalid="ignore", divide="ignore"):
        fn = f / med
    usable = np.isfinite(fn)
    depth, err = _box(t, fn, eph.t0, eph, usable)
    if not math.isfinite(depth):
        return math.nan, math.nan
    gap = 4.0 * eph.duration / eph.period
    ctrl = []
    for ph in np.linspace(0.03, 0.97, LEAK_N_CONTROL):
        if min(ph, 1.0 - ph) < gap:
            continue
        ctrl.append(_box(t, fn, eph.t0 + ph * eph.period, eph, usable & keep))
    ctrl = np.array(ctrl).reshape(-1, 2)
    ctrl = ctrl[np.all(np.isfinite(ctrl), axis=1)]
    if len(ctrl) >= 10:
        inflation = max(1.0, float(np.std(ctrl[:, 0], ddof=1)) / float(np.median(ctrl[:, 1])))
        err *= inflation
    return depth, err


def _on_target(s: SeasonResult) -> bool:
    """Centroid nearer the target by more than SEPARATION_SIGMA and, when the two-source fit is available (some
    quarter of the season holds the neighbour), no significant depth on the neighbour."""
    d_n, e_n = s.depth_neighbour_ppm
    return bool(s.nearer == "target" and s.separation_sigma > SEPARATION_SIGMA
                and (not s.two_source or abs(d_n) < NEIGHBOUR_DEPTH_SIGMA * e_n))


def _on_neighbour(s: SeasonResult) -> bool:
    (d_t, e_t), (d_n, e_n) = s.depth_target_ppm, s.depth_neighbour_ppm
    return bool(s.nearer == "neighbour" or (d_n > NEIGHBOUR_DEPTH_SIGMA * e_n and d_t < TARGET_DEPTH_SIGMA * e_t))


def verdict(seasons: list[SeasonResult], n_events_used: int) -> tuple[str, str]:
    """Spec 4.4: undetermined with fewer than TRANSITS_MIN usable transits; pass when every season with at least
    SEASON_EVENTS_MIN events has its centroid nearer the target by more than SEPARATION_SIGMA and a two-source depth
    on the neighbour consistent with zero; fail when the centroid or the two-source fit prefers the neighbour in a
    majority of those seasons; undetermined otherwise, naming the first season that disagrees."""
    if n_events_used < TRANSITS_MIN:
        return "undetermined", f"pixels: {n_events_used} usable transits < {TRANSITS_MIN}"
    voting = sorted((s for s in seasons if s.n_events >= SEASON_EVENTS_MIN), key=lambda s: s.season)
    if not voting:
        return "undetermined", f"pixels: no season with {SEASON_EVENTS_MIN} usable transits"
    m = len(voting)
    if all(_on_target(s) for s in voting):
        k = min(s.separation_sigma for s in voting)
        return "pass", f"pixels: the dip is on the target in {m}/{m} seasons (centroid >= {k:.1f} sigma nearer)"
    neighbour = [s for s in voting if _on_neighbour(s)]
    if len(neighbour) > m / 2:
        k = float(np.median([abs(s.separation_sigma) for s in neighbour]))
        return "fail", f"pixels: the dip is on the neighbour in {len(neighbour)}/{m} seasons ({k:.1f} sigma)"
    s = next(s for s in voting if not _on_target(s))
    return "undetermined", (f"pixels: season {s.season} disagrees "
                            f"(centroid nearer {s.nearer}, {abs(s.separation_sigma):.1f} sigma)")


def localise(cube, time, quality, quarter, eph: Ephemeris, pos_target, pos_neighbour, *,
             rng: np.random.Generator | None = None) -> Localisation:
    """``localise_quarters`` on one cube with one pixel grid and the same (col, row) positions in every quarter."""
    return localise_quarters(_split_quarters(cube, time, quality, quarter, pos_target, pos_neighbour), eph, rng=rng)


def localise_quarters(quarters: list[QuarterData], eph: Ephemeris, *,
                      rng: np.random.Generator | None = None) -> Localisation:
    """Season images co-registered from per-quarter stamps (``season_images``), the S/N-box centroid and the
    two-source fit in each season frame, and the verdict. A season with no quarter holding the neighbour still
    gets the centroid test; its two-source fit is unavailable (depths None in the metrics). Every Monte Carlo draw
    comes from ``rng`` (default seeded with RNG_SEED), so the result is deterministic."""
    rng = np.random.default_rng(RNG_SEED) if rng is None else rng
    images = season_images(quarters, eph)
    usable = sorted(q for im in images for q in im.quarters)
    unusable = sorted({int(qd.quarter) for qd in quarters} - set(usable))
    seasons = []
    for im in images:
        (ct, rt), (cn, rn) = im.pos_target, im.pos_neighbour
        (col, row), sigma, max_snr = snr_box_centroid(im.diff, im.var, im.valid, rng)
        d_t, d_n = math.hypot(col - ct, row - rt), math.hypot(col - cn, row - rn)
        sep = (d_n - d_t) / sigma if sigma > 0 else math.nan
        fit = (two_source_fit(im.direct, im.diff, im.var, im.valid, (ct, rt), (cn, rn))
               if im.neighbour_in_frame else _nan_fit())
        seasons.append(SeasonResult(
            season=im.season, n_events=im.n_events, centroid_colrow=(col, row), centroid_sigma_pix=sigma,
            dist_target_pix=d_t, dist_neighbour_pix=d_n, nearer="neighbour" if d_n < d_t else "target",
            separation_sigma=sep, depth_target_ppm=fit["depth_target_ppm"],
            depth_neighbour_ppm=fit["depth_neighbour_ppm"], max_snr=max_snr, quarters=list(im.quarters),
            two_source=im.neighbour_in_frame))
    n_used = int(sum(im.n_events for im in images))
    v, reason = verdict(seasons, n_used)
    return Localisation(seasons=seasons, n_events_used=n_used, n_quarters_usable=len(usable),
                        unusable_quarters=unusable, verdict=v, reason=reason, images=images)
