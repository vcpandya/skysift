"""Errors on the ephemeris and on the transit shape, the numbers a referee asks for before anything else.

The period and the epoch come from the times of the single transits. Each event is timed by sliding a fixed template
across a window of plus and minus three durations and taking the shift that minimises the chi-square of the whitened
flux: the rung 2 uniform-disc model when its fit is ok, else the search box of the stored depth and duration. The
template is averaged over each cadence's exposure (the median time step, 29.4 minutes for Kepler long cadence), as
Kipping 2010 asks of any model compared with integrated photometry; a box sampled at single instants makes the
chi-square a staircase in the shift, with no curvature to read an error from. The shift runs over a one minute grid,
the minimum is refined by a parabola through the lowest grid point and its two neighbours, and the error is the
half-width of that parabola at a chi-square one above the minimum, inflated by the square root of the reduced
chi-square when that exceeds one. The per-point error is the robust scatter of the window's points beyond one duration.
A weighted straight line through the mid-times gives the period, the reference time and their covariance, with the
reference epoch at the error-weighted centre of the series, where its error is smallest, and the error of a prediction
grows with the distance from that centre (Deeg 2015). Events more than five median absolute deviations from a first
line are dropped and the line is fitted again (Holczer et al. 2016).

The radius ratio, impact parameter, a/R*, duration and model depth carry errors from a bootstrap over whole transits:
the events are drawn with replacement, their points are concatenated and the quadratic limb-darkened,
exposure-integrated fit (``transit_model.fit_shape``) is run on each resample (Schmitt et al. 2014, the Planet Hunters
practice), free and, when the star has a density, with the density prior, each resample started at the full-data
solution. The quoted centre is the resample median (spec 11.3), the errors the 50th minus the 16th and the 84th minus
the 50th percentile, and the full-data value is kept beside them as ``point``, their offset a bias indicator.
Without a fit the box depth keeps its per-event error and the box duration gets percentiles from the width that
maximises the depth over its error on each resample.

Per-transit timing keeps only the transits whose single-event statistic reaches ``SES_MIN`` (Holczer et al. 2016;
``drop_low_ses``), and the dossier's depth is ``fold_depth`` on the fold it quotes.

Odd and even depths are the weighted means of the per-event box depths of each parity, with the stacked fold of
each parity beside them (``lc.ops.stacked_depth``), and the secondary block turns
rung 2's phase 0.5 depth into a 3-sigma limit and a geometric-albedo limit. Pure numerics; no I/O."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from skysift.lc.ops import event_depths, measure_at, stacked_depth
from skysift.lc.transit_model import EXPOSURE_RESOLUTION_MIN, ShapeFit, _t14, fit_shape, uniform_disc_flux

BKJD_OFFSET = 2454833.0
EVENT_IN_MIN, EVENT_OUT_MIN, EVENT_OUT_SIDE_MIN = 3, 6, 2     # the pixel rung's event rules
GRID_STEP_MIN = 1.0            # minutes, the shift grid of the per-transit chi-square
GRID_HALF_DURATIONS = 1.0      # the grid spans plus and minus one duration
OUTLIER_MAD = 5.0              # Holczer et al. 2016
MIN_EVENTS = 3                 # fewer used events: no ephemeris error
N_BOOT, BOOT_MIN_CONVERGED, BOOT_SEED = 200, 20, 20261022
BOX_DURATION_FACTORS = (0.5, 2.0)      # the box-duration grid, times the search duration, 40 steps in log
EPHEMERIS_METHOD = ("weighted linear fit of per-transit mid-times, reference epoch at the centre of the series "
                    "(Deeg 2015; Holczer et al. 2016)")
SHAPE_METHOD = "bootstrap over transits of the quadratic limb-darkened, exposure-integrated fit (Schmitt et al. 2014)"
# the density-prior fit's bootstrap (fix round 1 of Task 10, I-1): a fixed prior would pin the flat k-b-a/R* valley at
# its centre on every resample, so the resamples leave out the prior's own width
SHAPE_PRIOR_DRAW = "with the density prior's centre drawn from its Gaussian on each resample"
SHAPE_PRIOR_METHOD = SHAPE_METHOD.replace(" (Schmitt", f", {SHAPE_PRIOR_DRAW} (Schmitt")
PRIOR_DRAW_FLOOR = 0.05        # a drawn density centre below this fraction of the mean is held there (never <= 0)
CADENCE_MIN = 29.4             # Kepler long cadence, minutes: a shorter ingress leaves b and a/R* degenerate with k
DEGENERACY_REASON = "ingress shorter than the cadence: b and a/R* are degenerate with k"
GRAZING_REASON = "grazing point solution: b and a/R* are degenerate with k"
ALBEDO_NO_CONSTRAINT = "no constraint"

WINDOW_DURATIONS = 3.0         # an event's window: the points within this many durations of the predicted mid-time
BOX_DURATION_STEPS = 40
MAD_TO_SIGMA = 1.4826
SES_MIN = 2.5                  # a transit below this single-event S/N is not timed (Holczer et al. 2016)
LOW_SES_REASON = "single-event S/N below 2.5 (Holczer et al. 2016)"
FOLD_DEPTH_MIN_EVENTS = 4      # fewer usable per-event depths: the stacked fold's box depth, as rung 3
EVENT_DEPTH_SOURCE = "per-event weighted mean of the box depth"
STACKED_DEPTH_SOURCE = "box depth of the stacked fold"


def _f(x) -> float | None:
    """A Python float for JSON, None for NaN, inf and missing values."""
    if x is None:
        return None
    x = float(x)
    return x if math.isfinite(x) else None


def _finite(*xs) -> bool:
    return all(x is not None and math.isfinite(float(x)) for x in xs)


@dataclass(frozen=True)
class Template:
    """The fixed dip an event is timed with. ``depth`` is a fraction and is not fitted (the per-event depths are a
    separate measurement): the box is ``1 - depth`` within half a duration, the "fit" kind is the uniform-disc model
    of ``k``, ``b`` and ``a_over_r`` with its dip scaled to ``depth`` at mid-transit. A "fit" template whose geometry
    has no dip (``b >= 1 + k``: the disc never touches the star) has nothing to scale and raises ValueError."""
    kind: str                  # "fit" | "box"
    depth: float
    duration_days: float
    k: float | None = None
    b: float | None = None
    a_over_r: float | None = None

    def __post_init__(self):
        if self.kind not in ("fit", "box"):
            raise ValueError(f"template kind must be 'fit' or 'box', got {self.kind!r}")
        if self.kind == "fit" and not (_finite(self.k, self.b, self.a_over_r) and self.k > 0 and self.a_over_r > 0
                                       and self.b < 1.0 + self.k):
            raise ValueError(f"a fit template needs a dipping geometry, got k={self.k}, b={self.b}, "
                             f"a_over_r={self.a_over_r}")

    def flux(self, dt_days: np.ndarray, period: float) -> np.ndarray:
        dt = np.atleast_1d(np.asarray(dt_days, dtype=float))
        if self.kind == "box":
            return np.where(np.abs(dt) < 0.5 * self.duration_days, 1.0 - self.depth, 1.0)
        model = uniform_disc_flux(dt, period, self.k, self.b, self.a_over_r)
        own = 1.0 - float(uniform_disc_flux(np.array([0.0]), period, self.k, self.b, self.a_over_r)[0])
        return 1.0 - (1.0 - model) * (self.depth / own)


def template_from(shape_metrics: dict, depth_ppm: float, duration_days: float) -> Template:
    """The rung 2 uniform-disc model when ``fit_ok`` is true and ``fit_k``, ``fit_b`` and ``fit_a_over_r`` are
    finite, else the search box; either way of depth ``depth_ppm`` and duration ``duration_days``."""
    k, b, a = (shape_metrics.get(key) for key in ("fit_k", "fit_b", "fit_a_over_r"))
    if shape_metrics.get("fit_ok") is True and _finite(k, b, a):
        return Template("fit", depth_ppm * 1e-6, duration_days, float(k), float(b), float(a))
    return Template("box", depth_ppm * 1e-6, duration_days)


@dataclass(frozen=True)
class TransitTime:
    epoch: int
    t_bkjd: float | None       # mid-time; None when the event is not timed
    err_days: float | None
    chi2_red: float | None     # chi-square minimum over (window points - 1)
    n_in: int                  # points within half a duration of the predicted mid-time
    n_out: int                 # points one to three durations from it
    used: bool
    reason: str | None         # why the event is not used; None when it is

    def as_dict(self) -> dict:
        t = _f(self.t_bkjd)
        return {"epoch": int(self.epoch), "t_bkjd": t, "t_bjd": None if t is None else t + BKJD_OFFSET,
                "err_days": _f(self.err_days), "chi2_red": _f(self.chi2_red), "n_in": int(self.n_in),
                "n_out": int(self.n_out), "used": bool(self.used), "reason": self.reason}


def _exposure_averaged(template: Template, period: float, exposure: float, span: float):
    """``template.flux`` averaged over an exposure of ``exposure`` days centred on each offset, as a function valid
    for offsets within ``span`` days (a running mean of a finely sampled template, then linear interpolation)."""
    step = EXPOSURE_RESOLUTION_MIN / 1440.0
    n_avg = max(1, round(exposure / step))
    half = span + 0.5 * n_avg * step
    x = np.arange(-half, half + step, step)
    fine = template.flux(x, period)
    csum = np.concatenate([[0.0], np.cumsum(fine)])
    avg = (csum[n_avg:] - csum[:-n_avg]) / n_avg
    centres = x[: avg.size] + 0.5 * (n_avg - 1) * step
    return lambda d: np.interp(d, centres, avg)


def _time_one(d, y, shifts, model, n_in, n_out, epoch, mid, duration_days) -> TransitTime:
    out = np.abs(d) >= duration_days
    level = float(np.median(y[out]))
    sigma = MAD_TO_SIGMA * float(np.median(np.abs(y[out] - level)))
    if not sigma > 0:           # half the baseline points or more share one value: no per-point error
        return TransitTime(epoch, None, None, None, n_in, n_out, False, "flat baseline")
    chi2 = np.sum(((y[None, :] - level * model(d[None, :] - shifts[:, None])) / sigma) ** 2, axis=1)
    j = int(np.argmin(chi2))
    red = float(chi2[j]) / max(1, d.size - 1)
    if j == 0 or j == shifts.size - 1:
        return TransitTime(epoch, None, None, red, n_in, n_out, False, "no minimum inside the grid")
    h = shifts[1] - shifts[0]
    curv = chi2[j - 1] - 2.0 * chi2[j] + chi2[j + 1]
    if not curv > 0:
        return TransitTime(epoch, None, None, red, n_in, n_out, False, "no minimum inside the grid")
    vertex = shifts[j] + 0.5 * h * (chi2[j - 1] - chi2[j + 1]) / curv
    err = h * math.sqrt(2.0 / curv) * max(1.0, math.sqrt(red))     # chi2 = (curv / 2h^2) (s - vertex)^2 + c
    if err > 0.5 * duration_days:
        return TransitTime(epoch, None, None, red, n_in, n_out, False, "shift error above half a duration")
    return TransitTime(epoch, float(mid + vertex), float(err), red, n_in, n_out, True, None)


def transit_times(time, fnorm, period: float, t0: float, duration_days: float, template: Template) -> list[TransitTime]:
    """The mid-time of each event with a point within three durations of its predicted time ``t0 + epoch period``,
    in epoch order. An event needs ``EVENT_IN_MIN`` points within half a duration and ``EVENT_OUT_MIN`` from one to
    three durations, ``EVENT_OUT_SIDE_MIN`` on each side (the pixel rung's rules); otherwise, or when the baseline is
    flat (a zero median absolute deviation), the chi-square has no minimum inside the grid or the shift error exceeds
    half a duration, it is kept with ``used`` false and the reason. The template is ``template`` averaged over the
    median time step of the data (module docstring)."""
    time, fnorm = np.asarray(time, float), np.asarray(fnorm, float)
    good = np.isfinite(time) & np.isfinite(fnorm)
    t, f = time[good], fnorm[good]
    if t.size == 0:
        return []
    order = np.argsort(t)
    t, f = t[order], f[order]
    epoch = np.round((t - t0) / period).astype(np.int64)
    dt = t - (t0 + epoch * period)
    near = np.abs(dt) <= WINDOW_DURATIONS * duration_days
    exposure = float(np.median(np.diff(t))) if t.size > 1 else 0.0
    n_half = round(GRID_HALF_DURATIONS * duration_days * 1440.0 / GRID_STEP_MIN)
    shifts = np.arange(-n_half, n_half + 1) * GRID_STEP_MIN / 1440.0
    model = _exposure_averaged(template, period, exposure,
                               (WINDOW_DURATIONS + GRID_HALF_DURATIONS) * duration_days)
    rows = []
    for e in (int(x) for x in np.unique(epoch[near])):
        w = near & (epoch == e)
        d, y = dt[w], f[w]
        a = np.abs(d)
        n_in = int((a < 0.5 * duration_days).sum())
        out = a >= duration_days
        n_out = int(out.sum())
        sides = min(int((out & (d < 0)).sum()), int((out & (d > 0)).sum()))
        if n_in < EVENT_IN_MIN:
            rows.append(TransitTime(e, None, None, None, n_in, n_out, False, "too few in-transit points"))
        elif n_out < EVENT_OUT_MIN or sides < EVENT_OUT_SIDE_MIN:
            rows.append(TransitTime(e, None, None, None, n_in, n_out, False, "too few out-of-transit points"))
        else:
            rows.append(_time_one(d, y, shifts, model, n_in, n_out, e, t0 + e * period, duration_days))
    return rows


@dataclass(frozen=True)
class LinearEphemeris:
    ok: bool
    reason: str | None
    period: float
    period_err: float | None
    t_ref: float | None
    t_ref_err: float | None
    epoch_ref: int | None
    cov: float | None          # covariance of (t_ref, period), inflated as the square
    t0: float                  # at epoch 0
    t0_err: float | None
    chi2: float | None
    dof: int | None
    inflation: float | None    # max(1, sqrt(chi2 / dof)), applied to every error
    rms_min: float | None      # rms of the used residuals, minutes
    n_used: int
    n_measured: int
    n_outliers: int
    times: tuple[TransitTime, ...]     # every event passed in; outliers with used False and reason "outlier (x MAD)"

    def predict(self, epoch: int) -> tuple[float, float | None]:
        """Mid-time at ``epoch`` (BKJD) and its error in days: sigma squared is t_ref_err squared plus
        (epoch - epoch_ref) squared period_err squared (Deeg 2015). No error without an ok fit."""
        # Deeg's form drops the covariance, which vanishes only at the unrounded weighted centre; t0_err keeps it, so
        # predict(0)[1] and t0_err differ slightly when epoch_ref is rounded
        if not self.ok:
            return self.t0 + epoch * self.period, None
        de = epoch - self.epoch_ref
        return self.t_ref + de * self.period, math.hypot(self.t_ref_err, de * self.period_err)

    def next_transits(self, after_bkjd: float, n: int = 3) -> list[dict]:
        """The first ``n`` predicted transits after ``after_bkjd``, each {"epoch", "t_bkjd", "t_bjd", "err_days"}."""
        e = math.floor((after_bkjd - self.t0) / self.period)
        while self.predict(e)[0] <= after_bkjd:
            e += 1
        rows = []
        for epoch in range(e, e + n):
            t, err = self.predict(epoch)
            rows.append({"epoch": epoch, "t_bkjd": _f(t), "t_bjd": _f(t + BKJD_OFFSET), "err_days": _f(err)})
        return rows

    def as_dict(self) -> dict:
        return {"ok": self.ok, "reason": self.reason, "method": EPHEMERIS_METHOD, "period": _f(self.period),
                "period_err": _f(self.period_err), "t_ref": _f(self.t_ref),
                "t_ref_bjd": None if _f(self.t_ref) is None else self.t_ref + BKJD_OFFSET,
                "t_ref_err": _f(self.t_ref_err), "epoch_ref": self.epoch_ref, "cov": _f(self.cov),
                "t0": _f(self.t0), "t0_bjd": None if _f(self.t0) is None else self.t0 + BKJD_OFFSET,
                "t0_err": _f(self.t0_err), "chi2": _f(self.chi2), "dof": self.dof, "inflation": _f(self.inflation),
                "rms_min": _f(self.rms_min), "n_used": self.n_used, "n_measured": self.n_measured,
                "n_outliers": self.n_outliers, "times": [t.as_dict() for t in self.times]}


def _wls(epochs: np.ndarray, t: np.ndarray, err: np.ndarray):
    """Weighted straight line t = t_ref + period (epoch - epoch_ref), epoch_ref the rounded weighted mean epoch:
    (t_ref, period, var_t_ref, var_period, cov, epoch_ref, residuals)."""
    w = 1.0 / err ** 2
    e_ref = round(float(np.sum(w * epochs) / np.sum(w)))
    x = epochs - e_ref
    s, sx, sxx, sy, sxy = np.sum(w), np.sum(w * x), np.sum(w * x * x), np.sum(w * t), np.sum(w * x * t)
    det = s * sxx - sx * sx
    period = (s * sxy - sx * sy) / det
    t_ref = (sxx * sy - sx * sxy) / det
    return float(t_ref), float(period), float(sxx / det), float(s / det), float(-sx / det), e_ref, \
        t - (t_ref + period * x)


def _no_ephemeris(reason, period, t0, times, n_measured, n_outliers) -> LinearEphemeris:
    n_used = sum(1 for t in times if t.used)
    return LinearEphemeris(False, reason, float(period), None, None, None, None, None, float(t0), None, None, None,
                           None, None, n_used, n_measured, n_outliers, tuple(times))


def linear_fit(times: list[TransitTime], period: float, t0: float) -> LinearEphemeris:
    """The linear ephemeris of the used events of ``times`` (epochs counted from ``t0`` at ``period``): a first
    weighted fit, the Holczer et al. 2016 rule (events more than ``OUTLIER_MAD`` median absolute deviations of the
    residuals from the line are dropped), and the fit again. Errors are inflated by ``max(1, sqrt(chi2 / dof))``.
    With fewer than ``MIN_EVENTS`` used events it is not ok and carries the input period and t0 with no errors."""
    times = list(times)
    idx = [i for i, t in enumerate(times) if t.used and _finite(t.t_bkjd, t.err_days) and t.err_days > 0]
    n_measured = len(idx)
    if n_measured < MIN_EVENTS:
        return _no_ephemeris(f"fewer than {MIN_EVENTS} timed transits ({n_measured})", period, t0, times,
                             n_measured, 0)
    ep = np.array([times[i].epoch for i in idx], dtype=float)
    tt = np.array([times[i].t_bkjd for i in idx])
    er = np.array([times[i].err_days for i in idx])
    resid = _wls(ep, tt, er)[-1]
    mad = float(np.median(np.abs(resid - np.median(resid))))
    keep = np.ones(ep.size, bool)
    if mad > 0:
        dev = np.abs(resid) / mad
        keep = dev <= OUTLIER_MAD
        for j in np.flatnonzero(~keep):
            i = idx[j]
            times[i] = TransitTime(times[i].epoch, times[i].t_bkjd, times[i].err_days, times[i].chi2_red,
                                   times[i].n_in, times[i].n_out, False, f"outlier ({dev[j]:.1f} MAD)")
    n_outliers = int((~keep).sum())
    if keep.sum() < MIN_EVENTS:
        return _no_ephemeris(f"fewer than {MIN_EVENTS} timed transits after the outlier cut ({int(keep.sum())})",
                             period, t0, times, n_measured, n_outliers)
    ep, tt, er = ep[keep], tt[keep], er[keep]
    t_ref, p, var_t, var_p, cov, e_ref, resid = _wls(ep, tt, er)
    chi2 = float(np.sum((resid / er) ** 2))
    dof = int(ep.size - 2)
    infl = max(1.0, math.sqrt(chi2 / dof)) if dof > 0 else 1.0
    var_t, var_p, cov = var_t * infl ** 2, var_p * infl ** 2, cov * infl ** 2
    t0_fit = t_ref - p * e_ref
    # t0_err carries the covariance term; predict (Deeg's form) does not, so the two differ slightly at epoch 0
    var_t0 = max(var_t + e_ref ** 2 * var_p - 2.0 * e_ref * cov, 0.0)
    return LinearEphemeris(True, None, p, math.sqrt(var_p), t_ref, math.sqrt(var_t), e_ref, cov, t0_fit,
                           math.sqrt(var_t0), chi2, dof, infl, float(np.sqrt(np.mean(resid ** 2)) * 1440.0),
                           int(ep.size), n_measured, n_outliers, tuple(times))


def drop_low_ses(times: list[TransitTime], ses_by_epoch: dict[int, float],
                 min_ses: float | None = None) -> list[TransitTime]:
    """``times`` with every event whose single-event S/N is finite and below ``min_ses`` (``SES_MIN``, read at call
    time, when None) marked not used, with no mid-time, error or reduced chi-square and the reason
    ``LOW_SES_REASON`` (Holczer et al. 2016 time only the transits a single-event statistic can see). An epoch
    without an SES, or with a non-finite one, is kept as it is."""
    limit = SES_MIN if min_ses is None else float(min_ses)
    out = []
    for t in times:
        ses = ses_by_epoch.get(int(t.epoch))
        if ses is not None and math.isfinite(float(ses)) and float(ses) < limit:
            t = TransitTime(t.epoch, None, None, None, t.n_in, t.n_out, False, LOW_SES_REASON)
        out.append(t)
    return out


def fold_depth(time, fnorm, quarter, period, t0, duration_days) -> dict:
    """The transit depth on a fold, {"depth_ppm", "err_ppm", "source"}: the weighted mean of the per-event box depths
    with its scaled error (``event_depths``), or, below ``FOLD_DEPTH_MIN_EVENTS`` usable events (a finite depth and a
    positive error, as rung 3 counts them), the box depth of the stacked fold (``stacked_depth``). None values and
    the source None when neither measures."""
    time, fnorm = np.asarray(time, float), np.asarray(fnorm, float)
    ev = event_depths(time, fnorm, np.asarray(quarter), period, t0, duration_days)
    usable = np.isfinite(ev.depths) & np.isfinite(ev.errors) & (ev.errors > 0)
    if int(usable.sum()) >= FOLD_DEPTH_MIN_EVENTS and _finite(ev.mean, ev.err_scaled):
        return {"depth_ppm": float(ev.mean), "err_ppm": float(ev.err_scaled), "source": EVENT_DEPTH_SOURCE}
    st = stacked_depth(time, fnorm, period, t0, duration_days)
    if st.n_in and _finite(st.depth_ppm, st.err_ppm):
        return {"depth_ppm": float(st.depth_ppm), "err_ppm": float(st.err_ppm), "source": STACKED_DEPTH_SOURCE}
    return {"depth_ppm": None, "err_ppm": None, "source": None}


def predict_from(timing: dict, epoch: int) -> tuple[float, float | None]:
    """``LinearEphemeris.predict`` from a stored ``as_dict()`` block."""
    if not timing.get("ok"):
        return float(timing["t0"]) + epoch * float(timing["period"]), None
    de = epoch - int(timing["epoch_ref"])
    return (float(timing["t_ref"]) + de * float(timing["period"]),
            math.hypot(float(timing["t_ref_err"]), de * float(timing["period_err"])))


@dataclass(frozen=True)
class ShapeErrors:
    ok: bool
    reason: str | None
    method: str
    n_boot: int
    n_converged: int
    seed: int
    # each {"median": the resample p50, "minus": p50 - p16, "plus": p84 - p50, "point": the full-data value};
    # None without a fit or when not ok
    k: dict | None
    b: dict | None
    a_over_r: dict | None
    t14_h: dict | None
    model_depth_ppm: dict | None
    box_depth_ppm: dict | None          # {"median": depth_ppm, "minus": err, "plus": err} from event_depths
    box_duration_h: dict | None         # only without a fit: {"median", "minus", "plus", "point"} over the resamples
    ingress_min: float | None = None    # (T14 - T23) / 2 of the point geometry, minutes; None without a fit
    degenerate: bool = False            # ingress shorter than CADENCE_MIN: a quoted free b and a/R* carry no errors
    degeneracy_reason: str | None = None

    def as_dict(self) -> dict:
        def block(d):
            return None if d is None else {key: _f(v) for key, v in d.items()}
        return {"ok": self.ok, "reason": self.reason, "method": self.method, "n_boot": self.n_boot,
                "n_converged": self.n_converged, "seed": self.seed, "k": block(self.k), "b": block(self.b),
                "a_over_r": block(self.a_over_r), "t14_h": block(self.t14_h),
                "model_depth_ppm": block(self.model_depth_ppm), "box_depth_ppm": block(self.box_depth_ppm),
                "box_duration_h": block(self.box_duration_h), "ingress_min": _f(self.ingress_min),
                "degenerate": self.degenerate, "degeneracy_reason": self.degeneracy_reason}


def ingress_minutes(period: float, k: float, b: float, a_over_r: float) -> float | None:
    """The ingress duration (T14 - T23) / 2 of a uniform-disc transit on a circular orbit, minutes. T23 uses
    ``sqrt((1 - k)^2 - b^2)`` in the form of ``transit_model._t14`` and is 0 when ``b >= 1 - k`` (a grazing transit
    has no flat bottom, so the ingress is T14 / 2). None when T14 is undefined."""
    t14 = _t14(period, k, b, a_over_r)
    if not math.isfinite(t14):
        return None
    inner = (1.0 - k) ** 2 - b * b
    t23 = 0.0 if b >= 1.0 - k or inner <= 0.0 else period / math.pi * math.asin(min(1.0, math.sqrt(inner) / a_over_r))
    return 0.5 * (t14 - t23) * 1440.0


def _spread(samples, point: float | None) -> dict:
    """The resample median ``p50`` as the quoted value (spec 11.3), the spread ``p50 - p16`` and ``p84 - p50`` (never
    negative) and ``point``, the full-data value, beside them."""
    p16, p50, p84 = np.percentile(np.asarray(samples, float), [16, 50, 84])
    return {"median": float(p50), "minus": float(p50 - p16), "plus": float(p84 - p50),
            "point": None if point is None else float(point)}


def _fit_row(fit: ShapeFit) -> tuple:
    """(k, b, a/R*, T14 in hours, model depth in ppm) of a shape fit."""
    return fit.k, fit.b, fit.a_over_r, fit.t14_days * 24.0, fit.depth_ppm


def _point_fit(point: dict) -> ShapeFit:
    """The start a resample's free fit takes without a model: the point geometry as an ok ``ShapeFit``."""
    nan = float("nan")
    return ShapeFit(True, None, "free", float(point["k"]), float(point["b"]), float(point["a_over_r"]), nan, nan, nan,
                    0.0, nan, 0, 0.0, 0.0, CADENCE_MIN, nan, None)


def _best_box_duration(t, f, period, t0, durations) -> float | None:
    best, best_snr = None, -np.inf
    for dur in durations:
        snr = measure_at(t, f, period, t0, dur).snr
        if np.isfinite(snr) and snr > best_snr:
            best, best_snr = float(dur), float(snr)
    return best


def bootstrap_shape(time, fnorm, period, t0, duration_days, point: dict | None, *, n_boot: int = N_BOOT,
                    seed: int = BOOT_SEED, model: dict | None = None) -> ShapeErrors:
    """Errors on the transit shape from ``n_boot`` resamples of the events, drawn with replacement among every epoch
    with a point within three durations (six without a fit): every transit the full-data fit sees. The single-event
    S/N cut applies to the timing alone; resampling only the timed transits would keep the ones noise made deeper
    and bias the medians deep (round-2 final review, C-1). With
    ``point`` ({"k", "b", "a_over_r", "t14_h", "fit_depth_ppm"} of the quoted full-data fit) each resample is fitted
    with ``transit_model.fit_shape``; without it, the box duration that maximises the depth over its error on a grid
    from half to twice ``duration_days`` is kept per resample. The box depth always comes from ``event_depths`` over
    the same epochs, with its scaled error. Fewer than ``BOOT_MIN_CONVERGED`` converged resamples give no shape
    errors. Every block quotes the resample median beside the full-data value (``_spread``).

    ``model`` is {"u1", "u2", "exposure_days", "density": (mean, sigma) | None, "free": ShapeFit, "prior": ShapeFit |
    None, "quoted": "free" | "prior"}: only the quoted variant is refitted, with the model's limb darkening and
    exposure and started at its full-data solution, and all five values and their ``point`` come from it (spec 11.3 as
    amended in fix round 1: the radius ratio from the quoted fit). The prior variant draws the prior's centre on each
    resample from N(mean, sigma) with its own generator seeded from ``seed``, sigma kept in the prior term, so a
    parameter the data leave to the prior carries the prior's width (the perturbed-prior bootstrap; the method says
    so, ``SHAPE_PRIOR_METHOD``). Without ``model`` (the module's own tests): no limb darkening, the ``CADENCE_MIN``
    exposure, the free variant only, started at ``point`` and with ``point``'s values as the full-data ones.

    When the point geometry is degenerate between k, b and a/R* (``point_degeneracy``: an ingress shorter than
    ``CADENCE_MIN``, spec 3.4, ruling of 22 October 2026, or a grazing point solution) the block is marked
    ``degenerate`` with the reason. A quoted free b and a/R* then keep their full-data values with no errors, while k,
    T14 and the model depth, which the depth and the box width constrain, keep theirs; a quoted prior b and a/R* keep
    their errors, since the density prior removes the degeneracy."""
    time, fnorm = np.asarray(time, float), np.asarray(fnorm, float)
    good = np.isfinite(time) & np.isfinite(fnorm)
    t, f = time[good], fnorm[good]
    epoch = np.round((t - t0) / period).astype(np.int64)
    dt = t - (t0 + epoch * period)
    reach = WINDOW_DURATIONS * duration_days * (1.0 if point is not None else BOX_DURATION_FACTORS[1])
    near = np.abs(dt) <= reach
    pool = np.unique(epoch[near])
    in_pool = np.isin(epoch, pool)
    ev = event_depths(t[in_pool], f[in_pool], np.zeros(int(in_pool.sum()), int), period, t0, duration_days)
    box_depth = ({"median": float(ev.mean), "minus": float(ev.err_scaled), "plus": float(ev.err_scaled)}
                 if ev.n and _finite(ev.mean, ev.err_scaled) else None)
    members = {int(e): np.flatnonzero(near & (epoch == e)) for e in pool}
    ingress = None
    if point is not None and _finite(point.get("k"), point.get("b"), point.get("a_over_r")):
        ingress = ingress_minutes(period, float(point["k"]), float(point["b"]), float(point["a_over_r"]))
    reason = point_degeneracy(point, ingress)
    degenerate = reason is not None
    flags = (ingress, degenerate, reason)
    rng = np.random.default_rng(seed)
    durations = np.geomspace(BOX_DURATION_FACTORS[0] * duration_days, BOX_DURATION_FACTORS[1] * duration_days,
                             BOX_DURATION_STEPS)
    quoted, density = "free", None
    if point is not None:
        if model is None:
            u1 = u2 = 0.0
            exposure, density, free_start, prior_start = CADENCE_MIN / 1440.0, None, _point_fit(point), None
            full = tuple(float(point[key]) for key in ("k", "b", "a_over_r", "t14_h", "fit_depth_ppm"))
        else:
            u1, u2, exposure = float(model["u1"]), float(model["u2"]), float(model["exposure_days"])
            density, free_start, prior_start = model.get("density"), model["free"], model.get("prior")
            if density is None or prior_start is None or not prior_start.ok:
                density, prior_start = None, None
            quoted = "prior" if model.get("quoted") == "prior" and prior_start is not None else "free"
            full = _fit_row(prior_start if quoted == "prior" else free_start)
        start = prior_start if quoted == "prior" else free_start
        if quoted == "free":
            density = None
    draw = np.random.default_rng([seed, 1])          # the prior centres, apart from the resample draws
    samples = []
    for _ in range(n_boot if pool.size else 0):
        sel = np.concatenate([members[int(e)] for e in rng.choice(pool, size=pool.size, replace=True)])
        if point is not None:
            kw = {"u1": u1, "u2": u2, "exposure_days": exposure}
            if density is not None:
                centre = max(float(draw.normal(density[0], density[1])), PRIOR_DRAW_FLOOR * density[0])
                kw["density"] = (centre, density[1])
            fit = fit_shape(t[sel], f[sel], period, t0, duration_days, **kw, start=start)
            row = _fit_row(fit)
            if fit.ok and _finite(*row):
                samples.append(row)
        else:
            best = _best_box_duration(t[sel], f[sel], period, t0, durations)
            if best is not None:
                samples.append((best * 24.0,))
    n_conv = len(samples)
    if n_conv < BOOT_MIN_CONVERGED:
        return ShapeErrors(False, f"{n_conv} of {n_boot} resamples converged (fewer than {BOOT_MIN_CONVERGED})",
                           SHAPE_METHOD, n_boot, n_conv, seed, None, None, None, None, None, box_depth, None, *flags)
    s = np.array(samples)
    if point is None:
        whole = _best_box_duration(t[near & in_pool], f[near & in_pool], period, t0, durations)
        return ShapeErrors(True, None, SHAPE_METHOD, n_boot, n_conv, seed, None, None, None, None, None, box_depth,
                           _spread(s[:, 0], None if whole is None else whole * 24.0))
    k, b, a, t14, depth = (_spread(s[:, i], full[i]) for i in range(5))
    if degenerate and quoted == "free":     # the resamples scatter around one solution in the k-b-a/R* valley
        b, a = ({"median": d["point"], "minus": None, "plus": None, "point": d["point"]} for d in (b, a))
    method = SHAPE_PRIOR_METHOD if density is not None else SHAPE_METHOD
    return ShapeErrors(True, None, method, n_boot, n_conv, seed, k, b, a, t14, depth, box_depth, None, *flags)


@dataclass(frozen=True)
class ParityDepths:
    odd: dict                  # {"depth_ppm", "err_ppm", "n", "inflation"}
    even: dict
    difference_sigma: float | None      # |odd - even| over the two errors in quadrature
    # the stacked fold of each parity (lc.ops.stacked_depth): {"odd": {"depth_ppm", "err_ppm"}, "even": {...},
    # "difference_sigma"}; None without the fold arguments
    stacked: dict | None = None

    def as_dict(self) -> dict:
        def block(d):
            return {"depth_ppm": _f(d["depth_ppm"]), "err_ppm": _f(d["err_ppm"]), "n": int(d["n"]),
                    "inflation": _f(d["inflation"])}
        return {"odd": block(self.odd), "even": block(self.even), "difference_sigma": _f(self.difference_sigma),
                "stacked": self.stacked}


def _parity(depths: np.ndarray, errors: np.ndarray) -> dict:
    ok = np.isfinite(depths) & np.isfinite(errors) & (errors > 0)
    d, e = depths[ok], errors[ok]
    if d.size == 0:
        return {"depth_ppm": None, "err_ppm": None, "n": 0, "inflation": None}
    w = 1.0 / e ** 2
    mean = float(np.sum(w * d) / np.sum(w))
    chi2 = float(np.sum(w * (d - mean) ** 2))
    infl = max(1.0, math.sqrt(chi2 / (d.size - 1))) if d.size > 1 else 1.0
    return {"depth_ppm": mean, "err_ppm": infl / math.sqrt(float(np.sum(w))), "n": int(d.size), "inflation": infl}


def _stacked_parity(time, fnorm, period, t0, duration_days) -> dict:
    """The odd and even box depths of the stacked fold (``stacked_depth`` with ``epochs="odd"`` and ``"even"``) and
    their difference over the two errors in quadrature; None values where a parity's window is too sparse."""
    time, fnorm = np.asarray(time, float), np.asarray(fnorm, float)
    sides = {}
    for name in ("odd", "even"):
        st = stacked_depth(time, fnorm, period, t0, duration_days, epochs=name)
        sides[name] = {"depth_ppm": _f(st.depth_ppm), "err_ppm": _f(st.err_ppm)}
    o, e = sides["odd"], sides["even"]
    sigma = None
    if None not in (o["depth_ppm"], o["err_ppm"], e["depth_ppm"], e["err_ppm"]):
        err = math.hypot(o["err_ppm"], e["err_ppm"])
        sigma = abs(o["depth_ppm"] - e["depth_ppm"]) / err if err > 0 else None
    return {**sides, "difference_sigma": sigma}


def parity_depths(events, time=None, fnorm=None, period=None, t0=None, duration_days=None) -> ParityDepths:
    """Odd and even depths of ``events`` (``lc.ops.EventDepths``): the weighted mean of the per-event box depths of
    each parity, its error inflated by the square root of the parity's reduced chi-square when above one. With the
    fold arguments the stacked fold of each parity is measured beside them (``stacked``, spec 11.5); without them
    ``stacked`` is None."""
    epochs = np.asarray(events.epochs, dtype=np.int64)
    depths, errors = np.asarray(events.depths, float), np.asarray(events.errors, float)
    odd = _parity(depths[epochs % 2 == 1], errors[epochs % 2 == 1])
    even = _parity(depths[epochs % 2 == 0], errors[epochs % 2 == 0])
    sigma = None
    if odd["n"] and even["n"]:
        err = math.hypot(odd["err_ppm"], even["err_ppm"])
        sigma = abs(odd["depth_ppm"] - even["depth_ppm"]) / err if err > 0 else None
    stacked = None
    if all(x is not None for x in (time, fnorm, period, t0, duration_days)):
        stacked = _stacked_parity(time, fnorm, float(period), float(t0), float(duration_days))
    return ParityDepths(odd, even, sigma, stacked)


DEPTH_TREND_SIGMA = 2.0         # spec 11.5: a depth trend against time is printed above this significance
DEPTH_TREND_MIN_EVENTS = 3


def depth_trend(epochs, depths, errors, period: float) -> dict:
    """A linear trend of the per-event depths against time (spec 11.5): the weighted least-squares slope of the depths
    against years from the weighted mean epoch (epoch x period / 365.25), weights 1 / error squared, its error scaled
    by sqrt(max(1, chi2_red)) so that scatter beyond the errors (red noise) widens it, and the slope over its error.
    {"slope_ppm_per_year", "err_ppm_per_year", "sigma", "chi2_red", "n", "printed"}: ``printed`` when ``sigma``
    exceeds ``DEPTH_TREND_SIGMA``; None values, and ``printed`` False, below ``DEPTH_TREND_MIN_EVENTS`` usable events
    (a finite depth and a positive error) or without a spread in time."""
    e = np.asarray(epochs, float)
    d, s = np.asarray(depths, float), np.asarray(errors, float)
    ok = np.isfinite(e) & np.isfinite(d) & np.isfinite(s) & (s > 0)
    n = int(ok.sum())
    out = {"slope_ppm_per_year": None, "err_ppm_per_year": None, "sigma": None, "chi2_red": None, "n": n,
           "printed": False}
    if n < DEPTH_TREND_MIN_EVENTS or not (math.isfinite(float(period)) and float(period) > 0):
        return out
    years = e[ok] * float(period) / 365.25
    w = 1.0 / s[ok] ** 2
    x = years - float(np.sum(w * years) / np.sum(w))
    sxx = float(np.sum(w * x * x))
    if not sxx > 0:
        return out
    y = d[ok]
    mean = float(np.sum(w * y) / np.sum(w))
    slope = float(np.sum(w * x * (y - mean)) / sxx)
    chi2_red = float(np.sum(w * (y - mean - slope * x) ** 2)) / (n - 2) if n > 2 else 0.0
    err = math.sqrt(max(1.0, chi2_red) / sxx)
    sigma = abs(slope) / err
    return {"slope_ppm_per_year": slope, "err_ppm_per_year": err, "sigma": sigma, "chi2_red": chi2_red, "n": n,
            "printed": bool(sigma > DEPTH_TREND_SIGMA)}


def point_degeneracy(point: dict | None, ingress: float | None) -> str | None:
    """Why the rung 2 point geometry leaves b and a/R* degenerate with k, None when it does not: a grazing solution
    (b at or above 1 - k, where the ingress is long but k and b trade against each other most), ``GRAZING_REASON``,
    else an ingress shorter than the cadence, ``DEGENERACY_REASON``. None without a point."""
    if point is None or not _finite(point.get("k"), point.get("b")):
        return None
    if float(point["b"]) >= 1.0 - float(point["k"]):
        return GRAZING_REASON
    return DEGENERACY_REASON if ingress is not None and ingress < CADENCE_MIN else None


@dataclass(frozen=True)
class SecondaryLimit:
    depth_ppm: float | None             # rung 2's depth at phase 0.5
    err_ppm: float | None               # rung 2's control rms
    limit_3sigma_ppm: float | None      # max(depth, 0) + 3 err
    strongest_ppm: float | None         # the strongest secondary at any phase
    strongest_phase: float | None
    strongest_sigma: float | None
    albedo_limit: float | None          # limit x (a/R* / k)^2, an occultation of A_g (Rp / a)^2; None without a fit
    #                                     and None (with ``albedo_note``) when it constrains nothing
    albedo_note: str | None = None      # ALBEDO_NO_CONSTRAINT for a degenerate fit or a limit above 1
    albedo_reason: str | None = None    # why there is no constraint

    def as_dict(self) -> dict:
        return {"depth_ppm": _f(self.depth_ppm), "err_ppm": _f(self.err_ppm),
                "limit_3sigma_ppm": _f(self.limit_3sigma_ppm), "strongest_ppm": _f(self.strongest_ppm),
                "strongest_phase": _f(self.strongest_phase), "strongest_sigma": _f(self.strongest_sigma),
                "albedo_limit": _f(self.albedo_limit), "albedo_note": self.albedo_note,
                "albedo_reason": self.albedo_reason}


ALBEDO_DEGENERATE = "degenerate shape"


def albedo_above_one(albedo: float) -> str:
    """Why a geometric-albedo limit above 1 constrains nothing, with the limit at two significant figures, or with
    as many decimals as keep it visibly above 1 ("A_g < 1.04", never "A_g < 1.0")."""
    if not math.isfinite(albedo) or albedo <= 1:
        return "the limit exceeds 1"
    if albedo >= 10:
        digits = 1 - math.floor(math.log10(albedo))
        return f"the limit exceeds 1 (A_g < {round(albedo, digits):,.0f})"
    decimals = 1
    while round(albedo, decimals) <= 1 and decimals < 6:
        decimals += 1
    if round(albedo, decimals) <= 1:
        return "the limit exceeds 1"
    return f"the limit exceeds 1 (A_g < {albedo:.{decimals}f})"


def secondary_limit(shape_metrics: dict, degenerate: bool = False) -> SecondaryLimit | None:
    """The secondary block from the rung 2 metrics; None when ``secondary_ppm`` or ``control_rms_ppm`` is missing.
    The albedo limit scales with (a/R*)^2, so a ``degenerate`` fit (the shape block's flag) or a limit above 1 gives
    no limit but ``albedo_note`` ``ALBEDO_NO_CONSTRAINT`` with the reason (referee pack ruling k)."""
    depth, err = _f(shape_metrics.get("secondary_ppm")), _f(shape_metrics.get("control_rms_ppm"))
    if depth is None or err is None:
        return None
    limit = max(depth, 0.0) + 3.0 * err
    k, a = _f(shape_metrics.get("fit_k")), _f(shape_metrics.get("fit_a_over_r"))
    albedo, note, why = None, None, None
    if shape_metrics.get("fit_ok") is True and k is not None and a is not None and k > 0 and a > 0:
        albedo = limit * 1e-6 * (a / k) ** 2
        if degenerate:
            albedo, note, why = None, ALBEDO_NO_CONSTRAINT, ALBEDO_DEGENERATE
        elif albedo > 1:
            albedo, note, why = None, ALBEDO_NO_CONSTRAINT, albedo_above_one(albedo)
    return SecondaryLimit(depth, err, limit, _f(shape_metrics.get("free_secondary_ppm")),
                          _f(shape_metrics.get("free_secondary_phase")), _f(shape_metrics.get("free_secondary_sigma")),
                          albedo, note, why)
