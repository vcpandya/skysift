"""Difference-image localisation of a transit signal on Kepler target pixel files (spec section 4.4).

Ported from the KIC 6146676 study (``docs/candidates/kic-006146676/localisation/diffimage.py`` and
``context/a2_leak_test.py``). Pure numerics: the caller supplies the pixel cube, its times, quality flags and quarters,
the ephemeris and the pixel positions of the target and of the neighbouring star. Positions are zero-based
``(col, row)`` on the cube's pixel grid with pixel centres at integers; images are indexed ``[row, col]``.

Estimators, as in the study:
- per event, mean out-of-transit image minus mean in-transit image, its variance from the out-of-transit scatter;
- the events of every usable quarter of a season stacked with inverse-variance weights on the season's union frame
  (below);
- the 3x3 flux-weighted centroid boxed on the highest signal-to-noise difference pixel (the brightest-pixel box was
  bimodal on KIC 6146676), its error covariance from an N_MC-draw noise Monte Carlo, and its signed distance from
  the perpendicular bisector of the two stars in units of its error along the star-to-star axis;
- a two-source Gaussian PSF fit (free width, free common shift) on the direct image for each star's flux, the same
  PSFs on the difference image for each star's lost flux, implied depth = lost flux / flux.

Real target pixel files change stamp shape between quarters, and the stars' pixel positions change between seasons
(the spacecraft rolls every quarter). The general entry point is therefore ``localise_quarters`` on one
``QuarterData`` per quarter, each with its own stamp and positions. Within a season every quarter is placed by the
integer offset of its rounded target position relative to the season's first usable quarter; the season frame is
the union of all the shifted stamps (as the study built it from CRVAL1P/CRVAL2P), with invalid padding wherever a
quarter has no pixel. A season votes when both stars' 3x3 boxes lie complete on valid pixels of that frame, or when a
box cut by the frame edge or a dead pixel keeps its centre and at least ``BOX_PIXELS_MIN`` of its 9 pixels. A cut box
pulls the centroid towards the frame interior, so the shift a point source would suffer (``edge_allowance``) is added
in quadrature to the centroid error and the season votes only when it is still decisive; complete boxes are unchanged.
Every season's centroid error also carries ``CENTROID_FLOOR_PIX`` in quadrature, per axis: the quarter-to-quarter
systematic of the 3 x 3 box centroid that the Monte Carlo cannot see, measured on the live pixel files by
``tools/centroid_floor.py`` (``docs/superpowers/notes/2026-10-22-centroid-floor.md``). The quarters' sub-pixel
residuals from the integer co-registration move the stacked stars off the first quarter's positions, so the season's
star positions are the event-weighted mean of the quarters' own positions, the shift stored per season
(``registration_shift_pix``; the same note gives the ruling). ``localise`` is the single-grid convenience wrapper.
"Season" is the Kepler archive SEASON (0 to 3) each ``QuarterData`` carries from its TPF header (Q0 shares Q1's
module and roll, so it shares Q1's season), and the same numbering derived from the quarter when it is not known
(``quarter_season``).

Known limitation: the Gaussian PSF is not the Kepler PRF, so the two-source depth errors are somewhat optimistic
when the model does not match the stars' images (on a pixel-integrated synthetic stamp a dip on the neighbour put
2.7 sigma of depth on the target in one season).

Known limitation, a follow-up: the bisector test takes the stars' WCS positions as they are (averaged over the
season's quarters as above). The two-source fit's
free common shift (0.05 to 0.15 px in the KIC 6146676 study, about the size of the centroid error) is stored per
season as ``shift_pix`` (with the fitted ``psf_sigma_pix``) so the dossier can print a caveat, but it is neither
applied to the positions nor added to the centroid error; and the target's position (a Gaia epoch for a KBonus star)
is not moved to the TPF epoch for proper motion. The study made the same choices.
"""
from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import least_squares

PIX_ARCSEC = 3.98
N_MC = 200
RNG_SEED = 20261020

EVENT_IN_MIN = 3            # good in-transit cadences an event needs
EVENT_OUT_MIN = 6           # good out-of-transit cadences an event needs
EVENT_OUT_SIDE_MIN = 2      # ... of which at least this many on each side (a one-sided baseline follows any ramp)
EVENT_IN_HALF = 0.5         # in transit: within this many durations of mid-transit
EVENT_OUT_LO, EVENT_OUT_HI = 1.0, 3.0   # out of transit: between these many durations of mid-transit
QUARTER_EVENTS_MIN = 2      # a quarter with fewer usable events is unusable
SEASON_EVENTS_MIN = 2       # seasons with fewer events do not vote in the verdict
TRANSITS_MIN = 3            # fewer usable transits in all leaves the rung undetermined
VALID_FINITE_FRACTION = 0.9  # a pixel is valid when finite this often, relative to the best pixel of the quarter
SEPARATION_SIGMA = 3.0      # pass: centroid nearer the target by more than this many sigma
BOX_PIXELS_MIN = 6          # a star's 3x3 box cut by the frame edge votes only with at least this many valid pixels
EDGE_PSF_SIGMA_PIX = 0.6    # point-source width used for the edge allowance when the two-source fit gives none
EDGE_PAD = 2                # pixels of padding around the frame when the cut box is rendered for the allowance
PASS_SEASONS_MIN = 2        # a pass needs at least this many voting seasons; one alone leaves it undetermined
# The systematic floor of the 3 x 3 box centroid, per axis, added in quadrature to every season's error with the Monte
# Carlo error and the edge allowance: the pooled quarter-to-quarter rms of the direct-image box centroid within a
# season, over the complete-box seasons with two quarters or more of the 8 live stars (7 seasons, 24 quarters), the
# larger of the neighbour's (0.0141 px in column, 0.0189 px in row, 0.0167 px their quadratic mean) and the target's
# (0.0106 px). Measured on 22 October 2026 by tools/centroid_floor.py on the live pixel files; method, table and
# validation in docs/superpowers/notes/2026-10-22-centroid-floor.md. Rerun the tool when the pixel set changes and
# update the value and the date.
CENTROID_FLOOR_PIX = 0.0167
CENTROID_FLOOR_MEASURED = "2026-10-22"
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
    quarter's own stamp. ``season`` is the Kepler archive SEASON (0 to 3, the TPF header's SEASON: quarters on the
    same module and roll, Q0 sharing Q1's); None falls back to ``quarter_season``."""
    quarter: int
    cube: np.ndarray
    time: np.ndarray
    quality: np.ndarray
    pos_target: tuple[float, float]
    pos_neighbour: tuple[float, float]
    season: int | None = None


def quarter_season(qd: QuarterData) -> int:
    """The season a quarter is stacked in: its archive SEASON when known, else the archive's numbering from the
    quarter, ``(max(quarter, 1) + 2) % 4`` (Q1 and Q5 are season 3, Q2 is 0, Q3 is 1, Q4 is 2; Q0 has Q1's module
    and roll, so it never joins Q4's season)."""
    if qd.season is not None:
        return int(qd.season)
    return (max(int(qd.quarter), 1) + 2) % 4


_NAN2 = (math.nan, math.nan)


def _finite2(p) -> bool:
    return p is not None and all(math.isfinite(float(x)) for x in p)


_BOX_OFFSETS = tuple((dc, dr) for dr in (-1, 0, 1) for dc in (-1, 0, 1))   # (dcol, drow), row-major


@dataclass(frozen=True)
class BoxCoverage:
    """How much of a star's 3x3 box lies on valid pixels: the count, whether the centre pixel is one of them, and
    the (dcol, drow) offsets of the missing pixels in row-major order."""
    valid: int
    centre: bool
    missing: tuple[tuple[int, int], ...]

    @property
    def complete(self) -> bool:
        return self.valid == 9

    @property
    def partial(self) -> bool:
        return self.centre and BOX_PIXELS_MIN <= self.valid < 9

    @property
    def eligible(self) -> bool:
        return self.complete or self.partial

    def metrics(self) -> dict:
        return {"pixels": self.valid, "missing": [[dc, dr] for dc, dr in self.missing]}


ALL_MISSING = BoxCoverage(0, False, _BOX_OFFSETS)      # the coverage of a star with no position


@dataclass
class SeasonImage:
    """A season's stacked images on its union frame. ``offsets_pix`` is each quarter's integer (col, row) offset
    from the season's first usable quarter (round of the target-position difference), ``residual_offsets_pix`` the
    target's leftover fractional (col, row) offset (at most 0.5 px by construction), ``quarter_events`` each
    quarter's usable events and ``quarter_shifts`` where each quarter sits on the frame (frame pixel (r, c) is that
    quarter's pixel (r + sr, c + sc), ``_place_quarter``). The star positions are the event-weighted mean of the
    quarters' own positions on the frame; ``registration_shift_pix`` is how far that moved the target from the first
    quarter's. ``box_target`` and
    ``box_neighbour`` measure how much of that star's 3x3 box lies on valid frame pixels; ``target_on_frame`` and
    ``neighbour_on_frame`` say whether the box is eligible to vote (complete, or cut with its centre and at least
    BOX_PIXELS_MIN pixels)."""
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
    box_target: BoxCoverage = ALL_MISSING
    box_neighbour: BoxCoverage = ALL_MISSING
    target_on_frame: bool = False
    neighbour_on_frame: bool = False
    quarter_events: list[int] = field(default_factory=list)
    quarter_shifts: list[tuple[int, int]] = field(default_factory=list)
    registration_shift_pix: tuple[float, float] = (0.0, 0.0)


@dataclass
class SeasonResult:
    season: int
    n_events: int
    centroid_colrow: tuple[float, float]
    centroid_sigma_pix: float                   # per-axis centroid error
    dist_target_pix: float
    dist_neighbour_pix: float
    nearer: str                                 # "target" | "neighbour" | "unknown" (a season that does not vote)
    separation_sigma: float                     # (d_n^2 - d_t^2) / (2 S) / sigma_axis: signed distance from the
    #                                             bisector over the centroid error along the star-to-star axis
    depth_target_ppm: tuple[float, float]       # two-source implied depth, error
    depth_neighbour_ppm: tuple[float, float]
    max_snr: float
    quarters: list[int] = field(default_factory=list)
    two_source: bool = False                    # the two-source fit ran (both stars' boxes eligible: ``on_frame``)
    voting: bool = False                        # both boxes eligible, a finite centroid, >= SEASON_EVENTS_MIN, and
    #                                             a cut box decisive
    on_frame: bool = False                      # both stars' 3x3 boxes eligible (complete, or cut: BOX_PIXELS_MIN)
    pos_target: tuple[float, float] = _NAN2     # season-frame (col, row)
    pos_neighbour: tuple[float, float] = _NAN2
    centroid_sigma_axis_pix: float = math.nan   # centroid error along the neighbour-to-target axis
    non_voting_reason: str | None = None        # "box incomplete: target" | "box incomplete: neighbour" |
    #                                             "box cut: ..." (a cut box that was not decisive) |
    #                                             "centroid not measured"; None for a voting season
    shift_pix: tuple[float, float] = _NAN2      # the two-source fit's common (col, row) shift of both stars
    psf_sigma_pix: float = math.nan             # the two-source fit's Gaussian PSF width
    box_target: BoxCoverage = ALL_MISSING
    box_neighbour: BoxCoverage = ALL_MISSING
    box_partial: bool = False                   # on the frame with at least one box cut: the allowance is in the error
    edge_bias_pix: float = 0.0                  # the larger allowance of the cut boxes, in the centroid error
    edge_bias_axis_pix: float = 0.0             # ... and its part along the star-to-star axis, in the axis error
    separation_sigma_raw: float = math.nan      # ``separation_sigma`` over the Monte Carlo axis error alone
    centroid_sigma_mc_pix: float = math.nan     # the per-axis Monte Carlo error, before the allowance and the floor
    floor_pix: float = 0.0                      # the systematic floor in the error (CENTROID_FLOOR_PIX)
    registration_shift_pix: tuple[float, float] = (0.0, 0.0)    # the season positions' move to the quarters' mean
    direct_centroid_colrow: tuple[float, float] = _NAN2     # the target's direct-image centroid (``direct_centroid``)


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

    def neighbour_votes(self) -> tuple[float, int, int]:
        """(k sigma, n seasons on the neighbour, m voting seasons), as the fail verdict counts them."""
        return neighbour_votes(self.seasons)

    def metrics(self) -> dict:
        """Strict-JSON metrics (no NaN or inf, no arrays) for the test row, the dashboard and the dossier."""
        seasons = [{
            "season": int(s.season), "quarters": [int(q) for q in s.quarters], "n_events": int(s.n_events),
            "centroid_colrow": [_f(x) for x in s.centroid_colrow], "centroid_sigma_pix": _f(s.centroid_sigma_pix),
            "centroid_sigma_axis_pix": _f(s.centroid_sigma_axis_pix),
            "dist_target_pix": _f(s.dist_target_pix), "dist_neighbour_pix": _f(s.dist_neighbour_pix),
            "nearer": s.nearer, "separation_sigma": _f(s.separation_sigma),
            "depth_target_ppm": [_f(x) for x in s.depth_target_ppm],
            "depth_neighbour_ppm": [_f(x) for x in s.depth_neighbour_ppm], "max_snr": _f(s.max_snr),
            "two_source": bool(s.two_source), "voting": bool(s.voting), "non_voting_reason": s.non_voting_reason,
            "pos_target": [_f(x) for x in s.pos_target], "pos_neighbour": [_f(x) for x in s.pos_neighbour],
            "shift_pix": [_f(x) for x in s.shift_pix], "psf_sigma_pix": _f(s.psf_sigma_pix),
            "box_pixels_target": int(s.box_target.valid), "box_pixels_neighbour": int(s.box_neighbour.valid),
            "box_missing_target": s.box_target.metrics()["missing"],
            "box_missing_neighbour": s.box_neighbour.metrics()["missing"],
            "box_partial": bool(s.box_partial), "edge_bias_pix": _f(s.edge_bias_pix),
            "edge_bias_axis_pix": _f(s.edge_bias_axis_pix), "separation_sigma_raw": _f(s.separation_sigma_raw),
            "centroid_sigma_mc_pix": _f(s.centroid_sigma_mc_pix), "floor_pix": _f(s.floor_pix),
            "registration_shift_pix": [_f(x) for x in s.registration_shift_pix],
            "direct_centroid_colrow": [_f(x) for x in s.direct_centroid_colrow],
        } for s in self.seasons]
        # the mean centroid error of the voting seasons (of all seasons with one when none votes)
        sig = [s["centroid_sigma_pix"] for s in seasons if s["centroid_sigma_pix"] is not None and s["voting"]]
        sig = sig or [s["centroid_sigma_pix"] for s in seasons if s["centroid_sigma_pix"] is not None]
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
    with warnings.catch_warnings():                 # an all-NaN (invalid) pixel has no mean
        warnings.simplefilter("ignore", RuntimeWarning)
        direct = np.where(valid, np.nanmean(np.where(valid, fl[outmask], np.nan), axis=0), np.nan)
    return events, valid, direct


def box_coverage(pos, valid: np.ndarray) -> BoxCoverage:
    """Coverage of the 3x3 box around the pixel of the (col, row) position: a pixel is missing when it is off the
    frame or not valid. A position that is not finite has ``ALL_MISSING``."""
    if not _finite2(pos):
        return ALL_MISSING
    c, r = round(float(pos[0])), round(float(pos[1]))
    missing = tuple((dc, dr) for dc, dr in _BOX_OFFSETS
                    if not (0 <= r + dr < valid.shape[0] and 0 <= c + dc < valid.shape[1] and valid[r + dr, c + dc]))
    return BoxCoverage(9 - len(missing), (0, 0) not in missing, missing)


def _allowance_width(psf: float) -> float:
    """The PSF width (px) the edge allowance uses: the two-source fit's width when it lies strictly inside the fit's
    bounds (0.3, 3.0), else EDGE_PSF_SIGMA_PIX. A fit pinned on a bound has collapsed, and nan means no fit."""
    return psf if math.isfinite(psf) and 0.3 < psf < 3.0 else EDGE_PSF_SIGMA_PIX


def edge_allowance(pos, valid: np.ndarray, psf_sigma: float, pos_other) -> tuple[float, float]:
    """``(bias, bias_axis)``: how far, in pixels, a box cut by the frame edge or by invalid pixels moves the 3x3
    centroid of a point source at ``pos`` (and the part of that along the axis from ``pos_other`` to ``pos``).

    A Gaussian of width ``psf_sigma`` is rendered at ``pos`` on a frame padded by EDGE_PAD pixels, and its 3x3
    centroid around the brightest valid pixel is taken twice: on the valid pixels and with every pixel valid. The
    bias is the length of the difference. A complete box has none; a position that is not finite, or that coincides
    with ``pos_other``, has no allowance (nan)."""
    if not (_finite2(pos) and _finite2(pos_other)):
        return math.nan, math.nan
    d = np.array([float(pos[0]) - float(pos_other[0]), float(pos[1]) - float(pos_other[1])])
    n = float(np.hypot(*d))
    if not n > 0:
        return math.nan, math.nan
    if box_coverage(pos, valid).complete:
        return 0.0, 0.0
    shape = (valid.shape[0] + 2 * EDGE_PAD, valid.shape[1] + 2 * EDGE_PAD)
    vpad = np.zeros(shape, bool)
    vpad[EDGE_PAD:EDGE_PAD + valid.shape[0], EDGE_PAD:EDGE_PAD + valid.shape[1]] = valid
    if not vpad.any():
        return math.nan, math.nan
    img = gauss_psf(shape, float(pos[0]) + EDGE_PAD, float(pos[1]) + EDGE_PAD, psf_sigma)
    r0, c0 = np.unravel_index(int(np.argmax(np.where(vpad, img, -np.inf))), shape)
    cut = _box_centroid(img, vpad, int(r0), int(c0))
    full = _box_centroid(img, np.ones(shape, bool), int(r0), int(c0))
    shift = np.array(cut) - np.array(full)
    if not np.all(np.isfinite(shift)):
        return math.nan, math.nan
    return float(np.hypot(*shift)), float(abs(shift @ (d / n)))


def _shift_into(img: np.ndarray, shape: tuple[int, int], dc: int, dr: int, fill) -> np.ndarray:
    """``img`` on a ``shape`` frame where frame pixel (r, c) is image pixel (r + dr, c + dc); ``fill`` elsewhere."""
    out = np.full(shape, fill, dtype=img.dtype)
    r0, r1 = max(0, -dr), min(shape[0], img.shape[0] - dr)
    c0, c1 = max(0, -dc), min(shape[1], img.shape[1] - dc)
    if r1 > r0 and c1 > c0:
        out[r0:r1, c0:c1] = img[r0 + dr:r1 + dr, c0 + dc:c1 + dc]
    return out


def _place_quarter(events, valid: np.ndarray, direct: np.ndarray, shape: tuple[int, int], sc: int, sr: int,
                   num: np.ndarray, wsum: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """One quarter's events (``_quarter_events``) placed on a frame of ``shape`` where frame pixel (r, c) is quarter
    pixel (r + sr, c + sc): each event's difference image is added into ``num`` with its per-pixel inverse-variance
    weight, the weights into ``wsum`` (both in place, event after event). Returns the quarter's valid mask on the frame
    and its direct image there (0 off its valid pixels). The season stack and ``tools/centroid_floor.py`` both place
    quarters through this."""
    v = _shift_into(valid, shape, sc, sr, False)
    for img, var in events:
        vv = _shift_into(var, shape, sc, sr, np.inf)
        w_pix = np.where(v & np.isfinite(vv), 1.0 / np.where(np.isfinite(vv), vv, 1.0), 0.0)
        num += w_pix * _shift_into(img, shape, sc, sr, 0.0); wsum += w_pix
    return v, np.where(v, _shift_into(np.nan_to_num(direct), shape, sc, sr, 0.0), 0.0)


def season_images(quarters: list[QuarterData], eph: Ephemeris, *, in_half: float = EVENT_IN_HALF,
                  out_lo: float = EVENT_OUT_LO, out_hi: float = EVENT_OUT_HI) -> list[SeasonImage]:
    """Per-season difference images from per-quarter stamps, sorted by season. Per quarter the events as in
    ``difference_images``; quarters with non-finite star positions are dropped. Within a season (``quarter_season``)
    each quarter is offset from the season's first usable quarter by round(pos_target - first pos_target) per axis;
    the frame spans the minimum to maximum extent of all the shifted stamps, a quarter's missing pixels are invalid,
    and the events are stacked with per-pixel inverse-variance weights. Each star's season position is the mean of
    the quarters' own positions carried into the frame (each less its quarter's integer offset), weighted by the
    quarters' events, as the stack holds them; ``registration_shift_pix`` is the target's move from the first
    quarter's position (zero for a season of one quarter). The stack itself weights each pixel of each event by its
    inverse variance while the position is weighted by events; the two agree when the per-event noise is similar
    across the season's quarters."""
    usable: dict[int, tuple] = {}
    for qd in sorted(quarters, key=lambda x: int(x.quarter)):
        if not (_finite2(qd.pos_target) and _finite2(qd.pos_neighbour)):
            continue
        res = _quarter_events(qd, eph, in_half, out_lo, out_hi)
        if res is not None:
            usable[int(qd.quarter)] = (qd, *res)
    by_season: dict[int, list[int]] = {}
    for q in sorted(usable):
        by_season.setdefault(quarter_season(usable[q][0]), []).append(q)
    images = []
    for s in sorted(by_season):
        qs = by_season[s]
        first = usable[qs[0]][0]
        p_t = (float(first.pos_target[0]), float(first.pos_target[1]))
        p_n = (float(first.pos_neighbour[0]), float(first.pos_neighbour[1]))
        offsets, residuals, res_n = [], [], []
        for q in qs:
            pt, pn = usable[q][0].pos_target, usable[q][0].pos_neighbour
            dc, dr = round(float(pt[0]) - p_t[0]), round(float(pt[1]) - p_t[1])
            offsets.append((dc, dr)); residuals.append((float(pt[0]) - dc - p_t[0], float(pt[1]) - dr - p_t[1]))
            res_n.append((float(pn[0]) - dc - p_n[0], float(pn[1]) - dr - p_n[1]))
        n_ev = [len(usable[q][1]) for q in qs]
        # the stack holds each quarter's stars at its own positions less its integer offset: their event-weighted mean
        w_events = np.array(n_ev, float) / sum(n_ev)
        shift_t = tuple(float(x) for x in w_events @ np.array(residuals))
        shift_n = tuple(float(x) for x in w_events @ np.array(res_n))
        # quarter q covers first-quarter columns [-dc, -dc + width) and rows [-dr, -dr + height)
        c_min = min(-dc for dc, _ in offsets); r_min = min(-dr for _, dr in offsets)
        c_max = max(-dc + usable[q][2].shape[1] for q, (dc, _) in zip(qs, offsets))
        r_max = max(-dr + usable[q][2].shape[0] for q, (_, dr) in zip(qs, offsets))
        shape = (r_max - r_min, c_max - c_min)
        num, wsum = np.zeros(shape), np.zeros(shape)
        dsum, dn = np.zeros(shape), np.zeros(shape)
        n_events = 0
        shifts = [(dc + c_min, dr + r_min) for dc, dr in offsets]    # frame pixel (r, c) is quarter (r + sr, c + sc)
        for q, (sc, sr) in zip(qs, shifts, strict=True):
            _qd, events, valid, direct = usable[q]
            v, direct_frame = _place_quarter(events, valid, direct, shape, sc, sr, num, wsum)
            dsum += direct_frame; dn += v
            n_events += len(events)
        valid = wsum > 0
        safe = np.where(valid, wsum, 1.0)
        f_t = (p_t[0] + shift_t[0] - c_min, p_t[1] + shift_t[1] - r_min)
        f_n = (p_n[0] + shift_n[0] - c_min, p_n[1] + shift_n[1] - r_min)
        box_t, box_n = box_coverage(f_t, valid), box_coverage(f_n, valid)
        images.append(SeasonImage(
            season=s, quarters=qs, n_events=n_events, diff=np.where(valid, num / safe, 0.0),
            var=np.where(valid, 1.0 / safe, np.inf),
            direct=np.where(valid & (dn > 0), dsum / np.maximum(dn, 1), np.nan), valid=valid,
            pos_target=f_t, pos_neighbour=f_n, offsets_pix=offsets, residual_offsets_pix=residuals,
            box_target=box_t, box_neighbour=box_n, target_on_frame=box_t.eligible, neighbour_on_frame=box_n.eligible,
            quarter_events=n_ev, quarter_shifts=shifts, registration_shift_pix=shift_t))
    return images


def _split_quarters(cube, time, quality, quarter, pos_target, pos_neighbour) -> list[QuarterData]:
    cube = np.asarray(cube, float); time = np.asarray(time, float)
    quality = np.asarray(quality); quarter = np.asarray(quarter).astype(int)
    if cube.ndim != 3 or cube.shape[0] != time.size or quality.size != time.size or quarter.size != time.size:
        raise ValueError(f"cube {cube.shape} does not match {time.size} cadences")
    return [QuarterData(quarter=int(q), cube=cube[quarter == q], time=time[quarter == q],
                        quality=quality[quarter == q], pos_target=pos_target, pos_neighbour=pos_neighbour)
            for q in np.unique(quarter)]


def difference_images(cube, time, quality, quarter, eph: Ephemeris, *, in_half: float = EVENT_IN_HALF,
                      out_lo: float = EVENT_OUT_LO, out_hi: float = EVENT_OUT_HI, pos_target=None,
                      pos_neighbour=None) -> list[SeasonImage]:
    """Per-season difference images of one cube on one pixel grid, sorted by season. Per quarter, every event with
    at least EVENT_IN_MIN good cadences within ``in_half`` durations of mid-transit and EVENT_OUT_MIN good cadences
    between ``out_lo`` and ``out_hi`` durations (EVENT_OUT_SIDE_MIN on each side) gives mean(out) - mean(in) per
    pixel, with variance std(out)^2 (1/n_in + 1/n_out). Good means quality 0 and finite flux in every valid pixel.
    Quarters with fewer than QUARTER_EVENTS_MIN events are unusable; the events of a season's usable quarters are
    stacked with inverse-variance weights. Without positions the images carry NaN positions and no frame flags."""
    known = _finite2(pos_target) and _finite2(pos_neighbour)
    pt, pn = (pos_target, pos_neighbour) if known else ((0.0, 0.0), (0.0, 0.0))
    images = season_images(_split_quarters(cube, time, quality, quarter, pt, pn), eph,
                           in_half=in_half, out_lo=out_lo, out_hi=out_hi)
    if not known:
        for im in images:
            im.pos_target = im.pos_neighbour = _NAN2
            im.box_target = im.box_neighbour = ALL_MISSING
            im.target_on_frame = im.neighbour_on_frame = False
    return images


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


_NAN_COV = np.full((2, 2), np.nan)


def snr_box_centroid(diff, var, valid, rng) -> tuple[tuple[float, float], float, float, np.ndarray]:
    """3x3 flux-weighted centroid ``(col, row)`` around the highest-S/N difference pixel, its error, that S/N and
    the 2x2 (col, row) covariance of the N_MC centroids of ``diff + N(0, sqrt(var))``, each redrawn around its own
    highest-S/N pixel. The error is sqrt of the mean per-axis variance (the trace of that covariance over 2); the
    errors can be anisotropic, so a test along one direction should project the covariance (``_axis_sigma``)."""
    diff = np.asarray(diff, float); var = np.asarray(var, float); valid = np.asarray(valid, bool)
    snr = _snr(diff, var, valid)
    if not np.isfinite(snr).any():
        return (math.nan, math.nan), math.nan, math.nan, _NAN_COV.copy()
    r0, c0 = np.unravel_index(int(np.argmax(snr)), snr.shape)
    centroid = _box_centroid(diff, valid, int(r0), int(c0))
    sd = np.sqrt(np.where(valid & np.isfinite(var), var, 0.0))
    draws = np.empty((N_MC, 2))
    for i in range(N_MC):
        dm = diff + rng.normal(size=diff.shape) * sd
        a, b = np.unravel_index(int(np.argmax(_snr(dm, var, valid))), dm.shape)
        draws[i] = _box_centroid(dm, valid, int(a), int(b))
    draws = draws[np.all(np.isfinite(draws), axis=1)]
    if len(draws) < 3:
        return centroid, math.nan, float(snr[r0, c0]), _NAN_COV.copy()
    cov = np.cov(draws.T, ddof=1)
    return centroid, float(np.sqrt(0.5 * np.trace(cov))), float(snr[r0, c0]), cov


def _axis_sigma(cov: np.ndarray, pos_target, pos_neighbour) -> float:
    """Centroid error along the unit vector from the neighbour to the target: sqrt(u^T C u)."""
    d = np.array([pos_target[0] - pos_neighbour[0], pos_target[1] - pos_neighbour[1]], float)
    n = float(np.hypot(*d))
    if not (n > 0 and np.all(np.isfinite(cov))):
        return math.nan
    u = d / n
    return float(math.sqrt(max(float(u @ cov @ u), 0.0)))


def gauss_psf(shape: tuple[int, int], col: float, row: float, sig: float) -> np.ndarray:
    """Circular Gaussian of unit integral sampled at the pixel centres of a ``shape`` (rows, cols) grid."""
    rr, cc = np.mgrid[0:shape[0], 0:shape[1]]
    return np.exp(-0.5 * ((cc - col) ** 2 + (rr - row) ** 2) / sig ** 2) / (2 * np.pi * sig ** 2)


def _two_psfs(shape, pos_target, pos_neighbour, psf_sigma_pix: float, shift_pix) -> tuple[np.ndarray, np.ndarray]:
    """The two stars' ``gauss_psf`` at their positions plus the common shift, as the two-source fit places them."""
    dx, dy = (float(x) for x in shift_pix)
    sig = float(psf_sigma_pix)
    return (gauss_psf(shape, float(pos_target[0]) + dx, float(pos_target[1]) + dy, sig),
            gauss_psf(shape, float(pos_neighbour[0]) + dx, float(pos_neighbour[1]) + dy, sig))


def psf_fluxes(image, valid, pos_target, pos_neighbour, psf_sigma_pix: float,
               shift_pix=(0.0, 0.0)) -> tuple[float, float] | None:
    """The two stars' fluxes in ``image`` under the two-PSF model of ``two_source_fit`` at a fixed width and shift:
    linear least squares of image = F_t P_t + F_n P_n + c on the valid finite pixels (spec 11.5, the predicted leak).
    None when the width, a position or the shift is not finite, or the system is singular (fewer than three pixels,
    or two PSFs the pixels cannot tell apart)."""
    img = np.asarray(image, float)
    if not (math.isfinite(float(psf_sigma_pix)) and float(psf_sigma_pix) > 0 and _finite2(pos_target)
            and _finite2(pos_neighbour) and _finite2(shift_pix)):
        return None
    v = np.asarray(valid, bool) & np.isfinite(img)
    if v.sum() < 3:
        return None
    pt, pn = _two_psfs(img.shape, pos_target, pos_neighbour, psf_sigma_pix, shift_pix)
    a = np.vstack([pt[v], pn[v], np.ones(int(v.sum()))]).T
    sol, _res, rank, _sv = np.linalg.lstsq(a, img[v], rcond=None)
    if rank < 3:
        return None
    return float(sol[0]), float(sol[1])


def aperture_share(mask, pos_target, pos_neighbour, psf_sigma_pix: float, shift_pix, flux_target: float,
                   flux_neighbour: float) -> float | None:
    """The target's share of the two stars' light inside ``mask`` under the two-PSF model:
    F_t sum(P_t[mask]) / (F_t sum(P_t[mask]) + F_n sum(P_n[mask])), other stars and the background left out. A
    target flux that is not positive gives 0, never a negative share (Task 12 review M-7). None for an empty mask, a
    non-finite input, a negative neighbour flux or a total that is not positive."""
    m = np.asarray(mask, bool)
    if not m.any() or not (math.isfinite(float(flux_target)) and math.isfinite(float(flux_neighbour))
                           and math.isfinite(float(psf_sigma_pix)) and float(psf_sigma_pix) > 0
                           and _finite2(pos_target) and _finite2(pos_neighbour) and _finite2(shift_pix)):
        return None
    if float(flux_neighbour) < 0:
        return None
    pt, pn = _two_psfs(m.shape, pos_target, pos_neighbour, psf_sigma_pix, shift_pix)
    t, n = max(float(flux_target), 0.0) * float(pt[m].sum()), float(flux_neighbour) * float(pn[m].sum())
    return t / (t + n) if t + n > 0 else None


def _nan_fit() -> dict:
    return {"psf_sigma_pix": math.nan, "shift_pix": _NAN2, "direct_flux_target": math.nan,
            "direct_flux_target_err": math.nan, "direct_flux_neighbour": math.nan,
            "direct_flux_neighbour_err": math.nan, "diff_amp_target": math.nan, "diff_amp_target_err": math.nan,
            "diff_amp_neighbour": math.nan, "diff_amp_neighbour_err": math.nan, "depth_target_ppm": _NAN2,
            "depth_neighbour_ppm": _NAN2, "chi2_two_source": math.nan, "npix": 0, "background_direct": math.nan}


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
            "chi2_two_source": chi2, "npix": int(v.sum()), "background_direct": float(_b)}


def direct_centroid(direct, valid, fit: dict, pos_target, pos_neighbour) -> tuple[float, float]:
    """The target's own out-of-transit (direct-image) centroid, measured as the difference-image centroid is: the 3x3
    flux-weighted centroid around the brightest valid pixel of the target's 3x3 box, on the direct image less the
    two-source fit's background and its neighbour PSF (so the neighbour's light does not pull it). The difference
    centroid less this one is the offset Bryson et al. 2013 quote, free of the WCS zero point; NaN without a two-source
    fit. It never enters a vote."""
    sig, (dx, dy) = float(fit["psf_sigma_pix"]), fit["shift_pix"]
    if not (_finite2(pos_target) and _finite2(pos_neighbour) and math.isfinite(sig) and _finite2((dx, dy))
            and math.isfinite(float(fit["background_direct"])) and math.isfinite(float(fit["direct_flux_neighbour"]))):
        return _NAN2
    d = np.asarray(direct, float)
    ok = np.asarray(valid, bool) & np.isfinite(d)
    neighbour = gauss_psf(d.shape, float(pos_neighbour[0]) + dx, float(pos_neighbour[1]) + dy, sig)
    clean = np.where(ok, d - float(fit["background_direct"]) - float(fit["direct_flux_neighbour"]) * neighbour, 0.0)
    c, r = round(float(pos_target[0])), round(float(pos_target[1]))
    box = [(r + dr, c + dc) for dc, dr in _BOX_OFFSETS
           if 0 <= r + dr < d.shape[0] and 0 <= c + dc < d.shape[1] and ok[r + dr, c + dc]]
    if not box:
        return _NAN2
    r0, c0 = max(box, key=lambda rc: clean[rc])
    return _box_centroid(clean, ok, int(r0), int(c0))


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


def _running_median(t: np.ndarray, f: np.ndarray, keep: np.ndarray) -> np.ndarray:
    """LEAK_WINDOW_DAYS running median of ``f`` (``t`` sorted) over the ``keep`` points; NaN with fewer than 10."""
    tk, fk = t[keep], f[keep]
    lo = np.searchsorted(tk, t - 0.5 * LEAK_WINDOW_DAYS); hi = np.searchsorted(tk, t + 0.5 * LEAK_WINDOW_DAYS)
    return np.array([np.median(fk[a:b]) if b - a >= 10 else np.nan for a, b in zip(lo, hi)])


def leak_depth(time, flux, quality, eph: Ephemeris, quarter=None) -> tuple[float, float]:
    """Box depth and error (ppm) at the ephemeris in a light curve (the a2 ``measure`` method). The flux is divided
    by a LEAK_WINDOW_DAYS running median with the transits (|dt| < one duration) excluded, per quarter when
    ``quarter`` is given (as a2 ``detrend`` did: the flux level jumps between quarters), so raw counts are fine;
    quality-flagged and non-finite cadences are dropped. The formal error is inflated by the scatter of the depths at
    LEAK_N_CONTROL control phases (each at least four durations from the transit, the transits masked) when that
    scatter is larger. Returns (NaN, NaN) when there are too few points in or around the transits; callers turn NaN
    into None before it goes into JSON metrics."""
    t = np.asarray(time, float); f = np.asarray(flux, float); q = np.asarray(quality)
    qq = np.zeros(t.size, int) if quarter is None else np.asarray(quarter).astype(int)
    if not (f.size == q.size == qq.size == t.size):
        raise ValueError("time, flux, quality and quarter differ in length")
    ok = (q == 0) & np.isfinite(t) & np.isfinite(f)
    t, f, qq = t[ok], f[ok], qq[ok]
    order = np.argsort(t, kind="stable"); t, f, qq = t[order], f[order], qq[order]
    if t.size == 0:
        return math.nan, math.nan
    keep = np.abs(_phase_dt(t, eph.t0, eph.period)) >= eph.duration
    med = np.full(t.size, np.nan)
    for g in np.unique(qq):
        m = qq == g
        med[m] = _running_median(t[m], f[m], keep[m])
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
    """Centroid more than SEPARATION_SIGMA on the target's side of the bisector and a two-source depth on the
    neighbour consistent with zero."""
    d_n, e_n = s.depth_neighbour_ppm
    return bool(s.separation_sigma > SEPARATION_SIGMA and abs(d_n) < NEIGHBOUR_DEPTH_SIGMA * e_n)


def _on_neighbour(s: SeasonResult) -> bool:
    """Centroid more than SEPARATION_SIGMA on the neighbour's side, or a significant two-source depth on the
    neighbour with none on the target."""
    (d_t, e_t), (d_n, e_n) = s.depth_target_ppm, s.depth_neighbour_ppm
    return bool(s.separation_sigma < -SEPARATION_SIGMA
                or (d_n > NEIGHBOUR_DEPTH_SIGMA * e_n and d_t < TARGET_DEPTH_SIGMA * e_t))


def partial_abstention(s: SeasonResult) -> str | None:
    """Why a season with a cut box abstains, None when it is decisive and votes: a cut box votes only when it puts
    the dip on the target (``_on_target``) or on the neighbour (``_on_neighbour``) with the allowance in its error.
    Otherwise it is ambiguous (within SEPARATION_SIGMA of the bisector) or the neighbour depth disagrees."""
    if _on_target(s) or _on_neighbour(s):
        return None
    if abs(s.separation_sigma) <= SEPARATION_SIGMA:
        return f"box cut: ambiguous ({s.separation_sigma:.1f} sigma)"
    return f"box cut: {_disagreement(s)}"


def _neighbour_strength(s: SeasonResult) -> float:
    """The stronger of the two neighbour indicators, in sigma."""
    d_n, e_n = s.depth_neighbour_ppm
    by_depth = d_n / e_n if e_n > 0 else -math.inf
    return max(-s.separation_sigma, by_depth if math.isfinite(by_depth) else -math.inf)


def _voting(seasons: list[SeasonResult]) -> list[SeasonResult]:
    """The seasons that vote, by season: both stars' boxes on the frame (a cut box only when decisive), a finite
    separation, enough events."""
    return sorted((s for s in seasons if s.voting and s.n_events >= SEASON_EVENTS_MIN
                   and math.isfinite(s.separation_sigma)), key=lambda s: s.season)


def neighbour_votes(seasons: list[SeasonResult]) -> tuple[float, int, int]:
    """(k, n, m): of the m voting seasons, the n that put the dip on the neighbour (``_on_neighbour``) and the
    median of their neighbour strength in sigma (NaN when n is 0); the numbers of the fail verdict."""
    voting = _voting(seasons)
    neighbour = [s for s in voting if _on_neighbour(s)]
    k = float(np.median([_neighbour_strength(s) for s in neighbour])) if neighbour else math.nan
    return k, len(neighbour), len(voting)


def _why_not_voting(s: SeasonResult) -> str:
    if s.non_voting_reason:
        return s.non_voting_reason
    return "too few events" if s.n_events < SEASON_EVENTS_MIN else "centroid not measured"


def _disagreement(s: SeasonResult) -> str:
    """Why a voting season that does not pass disagrees: the centroid on the neighbour's side, or (the centroid on
    the target's side) the two-source depth on the neighbour that vetoed it."""
    if s.separation_sigma < -SEPARATION_SIGMA:
        return f"centroid nearer neighbour, {abs(s.separation_sigma):.1f} sigma"
    d_n, e_n = s.depth_neighbour_ppm
    if math.isfinite(d_n) and math.isfinite(e_n):
        return f"neighbour depth {d_n:.0f} +/- {e_n:.0f} ppm"
    return "neighbour depth not measured"


def verdict(seasons: list[SeasonResult], n_events_used: int) -> tuple[str, str]:
    """Spec 4.4 over the voting seasons (both stars' 3x3 boxes on the frame, complete or cut but decisive, a finite
    centroid and error, at least SEASON_EVENTS_MIN events): undetermined with fewer than TRANSITS_MIN usable transits
    or no voting season; pass when every voting season, and at least PASS_SEASONS_MIN of them, is more than
    SEPARATION_SIGMA on the target's side of the bisector with a neighbour depth consistent with zero (one voting
    season alone is undetermined, naming why the others did not vote); fail when a majority is more than
    SEPARATION_SIGMA on the neighbour's side or puts the depth on the neighbour; otherwise
    undetermined, naming the first voting season that does not pass and why ("ambiguous" when it lies within
    SEPARATION_SIGMA of the bisector, else the centroid on the neighbour's side or the two-source depth on the
    neighbour)."""
    if n_events_used < TRANSITS_MIN:
        return "undetermined", f"pixels: {n_events_used} usable transits < {TRANSITS_MIN}"
    voting = _voting(seasons)
    if not voting:
        if seasons and all(not s.on_frame for s in seasons):
            return "undetermined", "pixels: no season holds both stars on the frame"
        return "undetermined", "pixels: no season localizes the dip"
    m = len(voting)
    if all(_on_target(s) for s in voting):
        if m < PASS_SEASONS_MIN:
            voters = {id(s) for s in voting}
            others = [s for s in sorted(seasons, key=lambda s: s.season) if id(s) not in voters]
            why = "; ".join(f"season {s.season}: {_why_not_voting(s)}" for s in others) or "no other season has data"
            return "undetermined", f"pixels: only one season could vote ({why})"
        k = min(s.separation_sigma for s in voting)
        return "pass", f"pixels: the dip is on the target in {m}/{m} seasons (centroid >= {k:.1f} sigma nearer)"
    k, n, _m = neighbour_votes(seasons)
    if n > m / 2:
        return "fail", f"pixels: the dip is on the neighbour in {n}/{m} seasons ({k:.1f} sigma)"
    s = next(s for s in voting if not _on_target(s))
    if abs(s.separation_sigma) <= SEPARATION_SIGMA:
        return "undetermined", f"pixels: season {s.season} ambiguous ({s.separation_sigma:.1f} sigma)"
    return "undetermined", f"pixels: season {s.season} disagrees ({_disagreement(s)})"


def localise(cube, time, quality, quarter, eph: Ephemeris, pos_target, pos_neighbour, *,
             rng: np.random.Generator | None = None) -> Localisation:
    """``localise_quarters`` on one cube with one pixel grid and the same (col, row) positions in every quarter."""
    return localise_quarters(_split_quarters(cube, time, quality, quarter, pos_target, pos_neighbour), eph, rng=rng)


def localise_quarters(quarters: list[QuarterData], eph: Ephemeris, *,
                      rng: np.random.Generator | None = None) -> Localisation:
    """Season images on union frames from per-quarter stamps (``season_images``), the S/N-box centroid and the
    two-source fit in each season frame, and the verdict. Every season's centroid errors carry CENTROID_FLOOR_PIX in
    quadrature (per axis, and along the star axis), read at call time. A season votes with both stars' 3x3 boxes
    complete on valid frame pixels, or cut but keeping the centre and BOX_PIXELS_MIN pixels (the box's edge allowance
    added in quadrature to the centroid errors, and the season voting only when decisive: ``partial_abstention``), and a
    finite centroid and error; otherwise it is kept with ``nearer="unknown"`` (and without the two-source fit when a
    star's box is not eligible). Quarters with non-finite star positions are unusable. Every Monte Carlo draw comes
    from ``rng`` (default seeded with RNG_SEED), so the result is deterministic."""
    rng = np.random.default_rng(RNG_SEED) if rng is None else rng
    floor = CENTROID_FLOOR_PIX
    images = season_images(quarters, eph)
    usable = sorted(q for im in images for q in im.quarters)
    unusable = sorted({int(qd.quarter) for qd in quarters} - set(usable))
    seasons = []
    for im in images:
        (ct, rt), (cn, rn) = im.pos_target, im.pos_neighbour
        on_frame = im.target_on_frame and im.neighbour_on_frame
        box_partial = on_frame and not (im.box_target.complete and im.box_neighbour.complete)
        (col, row), sigma_mc, max_snr, cov = snr_box_centroid(im.diff, im.var, im.valid, rng)
        d_t, d_n = math.hypot(col - ct, row - rt), math.hypot(col - cn, row - rn)
        sep_pix = math.hypot(cn - ct, rn - rt)
        sigma_axis_mc = _axis_sigma(cov, (ct, rt), (cn, rn))
        fit = two_source_fit(im.direct, im.diff, im.var, im.valid, (ct, rt), (cn, rn)) if on_frame else _nan_fit()
        # the allowance of each cut box: how far its cut moves a point source's 3x3 centroid (the larger of the two)
        bias = bias_axis = 0.0
        if box_partial:
            psf = _allowance_width(float(fit["psf_sigma_pix"]))
            stars = ((im.box_target, (ct, rt), (cn, rn)), (im.box_neighbour, (cn, rn), (ct, rt)))
            cuts = [edge_allowance(star, im.valid, psf, other) for box, star, other in stars if box.partial]
            if any(math.isnan(b) for b, _ in cuts):         # both are nan together: no allowance can be given
                bias = bias_axis = math.nan
            else:
                bias, bias_axis = max(b for b, _ in cuts), max(a for _, a in cuts)
        # per axis: the Monte Carlo error, the cut box's allowance and the measured systematic floor in quadrature
        sigma, sigma_axis = math.hypot(sigma_mc, bias, floor), math.hypot(sigma_axis_mc, bias_axis, floor)
        # signed distance of the centroid from the bisector, in units of the centroid error along the star axis
        bisector = (d_n ** 2 - d_t ** 2) / (2.0 * sep_pix) if sep_pix > 0 else math.nan
        sep = bisector / sigma_axis if sigma_axis > 0 else math.nan
        sep_raw = bisector / sigma_axis_mc if sigma_axis_mc > 0 else math.nan
        res = SeasonResult(
            season=im.season, n_events=im.n_events, centroid_colrow=(col, row), centroid_sigma_pix=sigma,
            dist_target_pix=d_t, dist_neighbour_pix=d_n, nearer="unknown", separation_sigma=sep,
            depth_target_ppm=fit["depth_target_ppm"], depth_neighbour_ppm=fit["depth_neighbour_ppm"],
            max_snr=max_snr, quarters=list(im.quarters), two_source=on_frame, voting=False, on_frame=on_frame,
            pos_target=(ct, rt), pos_neighbour=(cn, rn), centroid_sigma_axis_pix=sigma_axis,
            shift_pix=tuple(float(x) for x in fit["shift_pix"]), psf_sigma_pix=float(fit["psf_sigma_pix"]),
            box_target=im.box_target, box_neighbour=im.box_neighbour, box_partial=box_partial,
            edge_bias_pix=bias, edge_bias_axis_pix=bias_axis, separation_sigma_raw=sep_raw,
            centroid_sigma_mc_pix=sigma_mc, floor_pix=floor, registration_shift_pix=im.registration_shift_pix,
            direct_centroid_colrow=direct_centroid(im.direct, im.valid, fit, (ct, rt), (cn, rn)) if on_frame else _NAN2)
        measured = bool(on_frame and math.isfinite(sep) and im.n_events >= SEASON_EVENTS_MIN)
        abstention = partial_abstention(res) if measured and box_partial else None
        res.voting = measured and abstention is None
        if res.voting:
            res.nearer = "target" if sep > 0 else "neighbour"
        elif not im.box_target.eligible:
            res.non_voting_reason = "box incomplete: target"
        elif not im.box_neighbour.eligible:
            res.non_voting_reason = "box incomplete: neighbour"
        else:
            res.non_voting_reason = abstention or "centroid not measured"
        seasons.append(res)
    n_used = int(sum(im.n_events for im in images))
    v, reason = verdict(seasons, n_used)
    return Localisation(seasons=seasons, n_events_used=n_used, n_quarters_usable=len(usable),
                        unusable_quarters=unusable, verdict=v, reason=reason, images=images)
