import json
import math

import numpy as np
from scipy.special import erf

from skysift.lc.ops import season_of
from skysift.lc.pixels import (
    ALL_MISSING,
    BOX_PIXELS_MIN,
    N_MC,
    SEPARATION_SIGMA,
    BoxCoverage,
    Ephemeris,
    QuarterData,
    SeasonResult,
    _axis_sigma,
    box_coverage,
    difference_images,
    edge_allowance,
    leak_depth,
    localise,
    localise_quarters,
    quarter_season,
    season_images,
    snr_box_centroid,
    two_source_fit,
    verdict,
)

SHAPE = (9, 9)                      # rows, cols
PSF_SIGMA = 0.9                     # px
POS_TARGET = (4.0, 4.0)             # (col, row), zero-based, pixel centres at integers
POS_NEIGHBOUR = (6.8, 4.6)
F_TARGET = 1.0e6                    # electrons per long cadence in the target's PSF
BACKGROUND = 2.0e3                  # electrons per pixel per cadence
CADENCE = 29.4 / 1440               # d
T_START = 131.5                     # BKJD, the start of Kepler Q1
QUARTER_DAYS = 93.0
EPH = Ephemeris(10.0, 137.0, 0.125)  # 10 d period, 3 h transit; t0 puts exactly two transits in the first 25 d
DEPTH = 3000e-6
RATIO = 5.0


def _psf(pos, shape=SHAPE):
    """Pixel-integrated circular Gaussian of unit total flux centred on (col, row)."""
    col, row = pos
    s = math.sqrt(2.0) * PSF_SIGMA
    cc, rr = np.arange(shape[1]), np.arange(shape[0])
    px = 0.5 * (erf((cc + 0.5 - col) / s) - erf((cc - 0.5 - col) / s))
    py = 0.5 * (erf((rr + 0.5 - row) / s) - erf((rr - 0.5 - row) / s))
    return np.outer(py, px)


def _in_transit(time, eph):
    ph = (time - eph.t0 + 0.5 * eph.period) % eph.period - 0.5 * eph.period
    return np.abs(ph) < 0.5 * eph.duration


def _scene(n_days, dip_on, depth, neighbour_ratio, n_quarters):
    """Times, quarters (93 d each from BKJD 131.5) and the two stars' fluxes per cadence, with a 3 h box transit of
    ``depth`` every 10 d on the chosen star."""
    time = np.arange(T_START, T_START + n_days, CADENCE)
    quarter = np.minimum(1 + ((time - T_START) // QUARTER_DAYS).astype(int), n_quarters)
    dip = np.where(_in_transit(time, EPH), depth, 0.0)
    f_t = F_TARGET * (1.0 - (dip if dip_on == "target" else 0.0 * dip))
    f_n = neighbour_ratio * F_TARGET * (1.0 - (dip if dip_on == "neighbour" else 0.0 * dip))
    return time, quarter, f_t, f_n


def _render(rng, f_t, f_n, pt, pn, shape=SHAPE):
    """Photon-noise stamps of the two stars plus background."""
    model = f_t[:, None, None] * _psf(pt, shape) + f_n[:, None, None] * _psf(pn, shape) + BACKGROUND
    return rng.normal(model, np.sqrt(model))


def make_cube(dip_on="target", n_days=372, depth=DEPTH, seed=0, neighbour_ratio=RATIO, n_quarters=4):
    """A 9 x 9 pixel stamp holding a target and a brighter neighbour 2.9 px away (Gaussian PSFs, sigma 0.9 px), long
    cadence, photon noise on every pixel, quarters of 93 d (so the first four quarters cover seasons 1, 2, 3 and 0)
    and a 3 h box transit of ``depth`` every 10 d on the chosen star."""
    time, quarter, f_t, f_n = _scene(n_days, dip_on, depth, neighbour_ratio, n_quarters)
    cube = _render(np.random.default_rng(seed), f_t, f_n, POS_TARGET, POS_NEIGHBOUR)
    quality = np.zeros(time.size, int)
    return cube, time, quality, quarter, EPH, POS_TARGET, POS_NEIGHBOUR


def make_quarters(dip_on="target", layout=None, n_quarters=5, seed=0, neighbour_ratio=RATIO):
    """Per-quarter stamps of the make_cube scene: ``layout`` maps a quarter to (shape, stamp origin (col, row) on the
    grid of the default stamp); positions are given in each stamp's own pixels. Quarters 1 and 5 share season 1."""
    layout = layout or {}
    rng = np.random.default_rng(seed)
    time, quarter, f_t, f_n = _scene(QUARTER_DAYS * n_quarters, dip_on, DEPTH, neighbour_ratio, n_quarters)
    out = []
    for q in range(1, n_quarters + 1):
        m = quarter == q
        shape, (oc, orow) = layout.get(q, (SHAPE, (0, 0)))
        pt = (POS_TARGET[0] - oc, POS_TARGET[1] - orow); pn = (POS_NEIGHBOUR[0] - oc, POS_NEIGHBOUR[1] - orow)
        out.append(QuarterData(quarter=q, cube=_render(rng, f_t[m], f_n[m], pt, pn, shape), time=time[m],
                               quality=np.zeros(m.sum(), int), pos_target=pt, pos_neighbour=pn))
    return out


SHIFTED = ((10, 10), (-1, 0))       # one extra column on the left (stars one column further right), one extra row
SUBPIX = ((9, 9), (-0.3, 0))        # stamp 0.3 px off: target at col 4.3, rounds to the same pixel
NO_NEIGHBOUR = ((9, 6), (0, 0))     # columns 0 to 5 only: the neighbour (col 6.8) is off the stamp
NO_TARGET = ((9, 5), (4, 0))        # columns 4 to 8 only: the target sits on the stamp's first column
CORNER_TARGET = ((5, 5), (4, 4))    # columns 4 to 8 and rows 4 to 8: the target on the stamp's corner pixel


def _swap_season(cube, rest, season):
    """The same cube with the dip moved from the target to the neighbour in one season only."""
    time, _quality, quarter, eph, pt, pn = rest
    m = (season_of(quarter) == season) & _in_transit(time, eph)
    out = cube.copy()
    out[m] += DEPTH * F_TARGET * _psf(pt) - DEPTH * RATIO * F_TARGET * _psf(pn)
    return out


def _by_season(loc):
    return {s.season: s for s in loc.seasons}


def test_difference_images_recover_the_dip_pixels():
    cube, time, quality, quarter, eph, pt, _pn = make_cube("target")
    seasons = difference_images(cube, time, quality, quarter, eph)
    assert len(seasons) == 4 and all(s.n_events >= 2 for s in seasons)
    s = seasons[0]; r, c = np.unravel_index(np.argmax(s.diff / np.sqrt(s.var)), s.diff.shape)
    assert abs(c - pt[0]) < 1.1 and abs(r - pt[1]) < 1.1
    # archive seasons: Q2 is 0, Q3 is 1, Q4 is 2, Q1 is 3
    assert [im.season for im in seasons] == [0, 1, 2, 3] and [im.quarters for im in seasons] == [[2], [3], [4], [1]]
    assert all(im.diff.shape == im.var.shape == im.direct.shape == im.valid.shape == SHAPE for im in seasons)


def test_difference_images_treat_nan_positions_as_unknown():
    cube, time, quality, quarter, eph, *_ = make_cube("target")
    assert len(difference_images(cube, time, quality, quarter, eph, pos_target=(math.nan, math.nan),
                                 pos_neighbour=(math.nan, math.nan))) == 4


def test_difference_images_drop_quarters_with_fewer_than_two_events():
    cube, time, quality, quarter, eph, *_ = make_cube("target", n_days=40)
    quarter = np.where(time > 145.0, 2, 1)          # quarter 1 holds one transit (137), quarter 2 the other three
    seasons = difference_images(cube, time, quality, quarter, eph)
    assert [im.quarters for im in seasons] == [[2]] and seasons[0].n_events == 3


def test_flagged_cadences_and_a_dead_pixel_are_left_out():
    cube, time, quality, quarter, eph, pt, pn = make_cube("target")
    cube = cube.copy(); quality = quality.copy()
    quality[::13] = 1024; cube[::13] = 1e9                     # flagged cadences hold garbage
    cube[:, 0, 8] = np.nan                                     # a dead pixel in a corner
    loc = localise(cube, time, quality, quarter, eph, pt, pn)
    assert loc.verdict == "pass" and all(not im.valid[0, 8] and im.valid.sum() == 80 for im in loc.images)
    assert all(s.voting and abs(s.depth_target_ppm[0] - 3000) < 4 * s.depth_target_ppm[1] for s in loc.seasons)


def test_snr_box_centroid_finds_a_point_source_and_its_error():
    diff = 100.0 * _psf((3.3, 5.0)); var = np.full(SHAPE, 1.0); valid = np.ones(SHAPE, bool)
    (col, row), sigma, snr, cov = snr_box_centroid(diff, var, valid, np.random.default_rng(3))
    assert abs(col - 3.3) < 0.2 and abs(row - 5.0) < 0.05
    assert 0 < sigma < 0.1 and snr > 15 and N_MC == 200 and cov.shape == (2, 2)
    assert math.isclose(sigma, math.sqrt(0.5 * np.trace(cov)))


def test_two_source_fit_recovers_the_injected_depth():
    cube, time, quality, quarter, eph, pt, pn = make_cube("target")
    im = difference_images(cube, time, quality, quarter, eph)[1]
    fit = two_source_fit(im.direct, im.diff, im.var, im.valid, pt, pn)
    d_t, e_t = fit["depth_target_ppm"]; d_n, e_n = fit["depth_neighbour_ppm"]
    assert abs(d_t - 3000) < 4 * e_t and e_t < 300 and abs(d_n) < 3 * e_n
    assert abs(fit["direct_flux_neighbour"] / fit["direct_flux_target"] - RATIO) < 0.25


def test_localise_passes_a_dip_on_the_target():
    loc = localise(*make_cube("target"))
    assert loc.verdict == "pass" and all(s.nearer == "target" and s.separation_sigma > 3 for s in loc.seasons)
    assert abs(loc.seasons[0].depth_neighbour_ppm[0]) < 3 * loc.seasons[0].depth_neighbour_ppm[1]
    assert loc.n_events_used >= 30 and loc.n_quarters_usable == 4 and loc.unusable_quarters == []
    assert len(loc.images) == 4 and loc.reason.startswith("pixels: ")
    assert all(s.voting and s.two_source for s in loc.seasons)


def test_localise_passes_a_target_dip_beside_a_neighbour_23_times_brighter():
    loc = localise(*make_cube("target", neighbour_ratio=23.0))
    assert loc.verdict == "pass" and all(s.separation_sigma > 3 for s in loc.seasons)


def test_localise_fails_a_dip_on_the_neighbour():
    loc = localise(*make_cube("neighbour"))
    assert loc.verdict == "fail" and "neighbour" in loc.reason
    assert all(s.separation_sigma < -3 for s in loc.seasons)


def test_localise_needs_three_usable_transits():
    loc = localise(*make_cube("target", n_days=25))        # two transits only
    assert loc.verdict == "undetermined" and loc.reason.startswith("pixels: 2 usable transits < 3")


def test_localise_is_undetermined_when_a_season_disagrees():
    cube, *rest = make_cube("target")
    # move the dip to the neighbour in Q2 only (quarter % 4 == 2), archive season 0
    loc = localise(_swap_season(cube, rest, season=2), *rest)
    assert loc.verdict == "undetermined" and "season 0" in loc.reason
    assert loc.reason.startswith("pixels: season 0 disagrees (centroid nearer neighbour, ")


def test_localise_is_deterministic():
    a = localise(*make_cube("target")).metrics(); b = localise(*make_cube("target")).metrics()
    assert a == b


def test_separation_is_the_signed_distance_from_the_bisector():
    loc = localise(*make_cube("target"))
    for s, im in zip(loc.seasons, loc.images):
        (ct, rt), (cn, rn) = im.pos_target, im.pos_neighbour
        u = np.array([ct - cn, rt - rn]) / math.hypot(ct - cn, rt - rn)      # unit vector towards the target
        mid = np.array([(ct + cn) / 2, (rt + rn) / 2])
        along = float((np.array(s.centroid_colrow) - mid) @ u)
        assert math.isclose(s.separation_sigma, along / s.centroid_sigma_axis_pix, rel_tol=1e-9)


def test_separation_uses_the_centroid_error_along_the_star_axis():
    # an elongated error: noisy pixels left and right of a bright point source jitter the column centroid only
    diff = 2000.0 * _psf((4.0, 4.0)); var = np.ones(SHAPE); var[4, 3] = var[4, 5] = 400.0
    (_col, _row), sigma, _snr, cov = snr_box_centroid(diff, var, np.ones(SHAPE, bool), np.random.default_rng(5))
    along = _axis_sigma(cov, (4.0, 4.0), (6.8, 4.0))               # stars side by side: the axis is the column
    assert cov[0, 0] > 25 * cov[1, 1] and along > 1.2 * sigma and math.isclose(along, math.sqrt(cov[0, 0]))
    # on the cube the reported separation divides by the axis error, which differs from the isotropic mean
    loc = localise(*make_cube("target"))
    assert any(abs(s.centroid_sigma_axis_pix / s.centroid_sigma_pix - 1) > 0.2 for s in loc.seasons)
    for s in loc.seasons:
        (ct, rt), (cn, rn) = s.pos_target, s.pos_neighbour
        bisector = (s.dist_neighbour_pix ** 2 - s.dist_target_pix ** 2) / (2 * math.hypot(ct - cn, rt - rn))
        assert math.isclose(s.separation_sigma, bisector / s.centroid_sigma_axis_pix, rel_tol=1e-9)


def test_axis_sigma_projects_the_covariance():
    cov = np.array([[0.04, 0.0], [0.0, 0.0025]])                  # 0.2 px in col, 0.05 px in row
    assert math.isclose(_axis_sigma(cov, (4.0, 4.0), (7.0, 4.0)), 0.2)
    assert math.isclose(_axis_sigma(cov, (4.0, 4.0), (4.0, 1.0)), 0.05)
    assert math.isnan(_axis_sigma(cov, (4.0, 4.0), (4.0, 4.0)))


def _season(season, sep=10.0, d_t=(3000.0, 100.0), d_n=(0.0, 50.0), n=5, voting=True, on_frame=True):
    return SeasonResult(season=season, n_events=n, centroid_colrow=(4.0, 4.0), centroid_sigma_pix=0.1,
                        dist_target_pix=0.1, dist_neighbour_pix=2.9,
                        nearer=("target" if sep > 0 else "neighbour") if voting else "unknown",
                        separation_sigma=sep, depth_target_ppm=d_t, depth_neighbour_ppm=d_n, max_snr=12.0,
                        two_source=on_frame, voting=voting, on_frame=on_frame)


def test_verdict_rules():
    ok = [_season(s) for s in range(4)]
    assert verdict(ok, 20) == ("pass",
                               "pixels: the dip is on the target in 4/4 seasons (centroid >= 10.0 sigma nearer)")
    assert verdict(ok, 2) == ("undetermined", "pixels: 2 usable transits < 3")
    weak = ok[:1] + [_season(1, sep=2.0)] + ok[2:]
    assert verdict(weak, 20) == ("undetermined", "pixels: season 1 ambiguous (2.0 sigma)")
    weak_nb = ok[:1] + [_season(1, sep=-2.5)] + ok[2:]
    assert verdict(weak_nb, 20) == ("undetermined", "pixels: season 1 ambiguous (-2.5 sigma)")
    leak = ok[:3] + [_season(3, d_n=(400.0, 50.0))]
    # the centroid is on the target's side; the two-source depth on the neighbour vetoes the season, and says so
    assert verdict(leak, 20) == ("undetermined", "pixels: season 3 disagrees (neighbour depth 400 +/- 50 ppm)")
    unmeasured = ok[:3] + [_season(3, d_n=(math.nan, math.nan))]
    assert verdict(unmeasured, 20) == ("undetermined", "pixels: season 3 disagrees (neighbour depth not measured)")
    one_nb = [_season(0, sep=-8.0)] + ok[1:]
    assert verdict(one_nb, 20) == ("undetermined",
                                   "pixels: season 0 disagrees (centroid nearer neighbour, 8.0 sigma)")
    nb = [_season(s, sep=-8.0) for s in range(3)] + ok[3:]
    assert verdict(nb, 20) == ("fail", "pixels: the dip is on the neighbour in 3/4 seasons (8.0 sigma)")
    half = [_season(s, sep=-8.0) for s in range(2)] + ok[2:]                  # 2/4 is not a majority
    assert verdict(half, 20)[0] == "undetermined"
    ambiguous_nb = [_season(s, sep=-2.0) for s in range(3)] + ok[3:]           # nearer, not significant: no fail
    assert verdict(ambiguous_nb, 20) == ("undetermined", "pixels: season 0 ambiguous (-2.0 sigma)")
    two_source = [_season(s, d_t=(50.0, 100.0), d_n=(900.0, 60.0)) for s in range(3)] + ok[3:]
    assert verdict(two_source, 20) == ("fail", "pixels: the dip is on the neighbour in 3/4 seasons (15.0 sigma)")
    one_event = ok[:3] + [_season(3, sep=-8.0, n=1)]                          # ignored: fewer than two events
    assert verdict(one_event, 20)[0] == "pass"
    off = ok[:3] + [_season(3, sep=-8.0, voting=False, on_frame=False)]        # ignored: a star off the frame
    assert verdict(off, 20) == ("pass",
                                "pixels: the dip is on the target in 3/3 seasons (centroid >= 10.0 sigma nearer)")
    none = [_season(s, voting=False, on_frame=False) for s in range(4)]
    assert verdict(none, 20) == ("undetermined", "pixels: no season holds both stars on the frame")
    lost = [_season(s, sep=math.nan, voting=False) for s in range(4)]
    assert verdict(lost, 20) == ("undetermined", "pixels: no season localizes the dip")
    mixed = lost[:2] + [_season(s, voting=False, on_frame=False) for s in (2, 3)]   # some on the frame, none voting
    assert verdict(mixed, 20) == ("undetermined", "pixels: no season localizes the dip")


def test_leak_depth_sees_an_injected_dip_and_nothing_elsewhere():
    t = np.arange(131.5, 131.5 + 400, 29.4 / 1440); f = 1 + np.random.default_rng(1).normal(0, 3e-4, t.size)
    eph = Ephemeris(10.0, 140.0, 0.125); ph = (t - eph.t0 + 5) % 10 - 5
    d, e = leak_depth(t, f * np.where(np.abs(ph) < 0.0625, 1 - 1e-3, 1), np.zeros(t.size, int), eph)
    assert abs(d - 1000) < 5 * e
    d0, e0 = leak_depth(t, f, np.zeros(t.size, int), eph); assert abs(d0) < 4 * e0


def test_leak_depth_normalises_raw_counts_and_skips_flagged_cadences():
    t = np.arange(131.5, 131.5 + 400, 29.4 / 1440); rng = np.random.default_rng(2)
    eph = Ephemeris(10.0, 140.0, 0.125); ph = (t - eph.t0 + 5) % 10 - 5
    f = 8.0e4 * (1 + 2e-3 * np.sin(t / 7.0)) * (1 + rng.normal(0, 3e-4, t.size))
    f = f * np.where(np.abs(ph) < 0.0625, 1 - 1e-3, 1)
    q = np.zeros(t.size, int); q[::17] = 128; f[::17] = 0.0          # flagged cadences hold garbage
    d, e = leak_depth(t, f, q, eph)
    assert abs(d - 1000) < 5 * e and e < 100


def test_leak_depth_detrends_per_quarter():
    t = np.arange(131.5, 131.5 + 372, 29.4 / 1440); rng = np.random.default_rng(4)
    quarter = 1 + ((t - 131.5) // 93).astype(int)
    eph = Ephemeris(10.0, 140.0, 0.125); ph = (t - eph.t0 + 5) % 10 - 5
    level = np.array([1.0, 1.4, 0.7, 1.2])[quarter - 1]            # the flux level jumps at every quarter boundary
    f = 8.0e4 * level * (1 + rng.normal(0, 3e-4, t.size)) * np.where(np.abs(ph) < 0.0625, 1 - 1e-3, 1)
    d, e = leak_depth(t, f, np.zeros(t.size, int), eph, quarter=quarter)
    assert abs(d - 1000) < 5 * e and e < 100
    assert leak_depth(t, f, np.zeros(t.size, int), eph, quarter=quarter) == (d, e)


def test_metrics_are_json_ready():
    json.dumps(localise(*make_cube("target")).metrics())


def test_metrics_carry_the_dashboard_keys_and_strict_json():
    m = localise(*make_cube("target", n_days=25)).metrics()
    json.dumps(m, allow_nan=False)
    m = localise(*make_cube("target")).metrics()
    json.dumps(m, allow_nan=False)
    assert {"n_events_used", "n_quarters_usable", "unusable_quarters", "verdict", "reason",
            "centroid_sigma_pix_mean", "seasons"} <= set(m)
    keys = {"season", "quarters", "n_events", "centroid_colrow", "centroid_sigma_pix", "dist_target_pix",
            "dist_neighbour_pix", "nearer", "separation_sigma", "depth_target_ppm", "depth_neighbour_ppm", "max_snr",
            "two_source", "pos_target", "pos_neighbour", "voting", "non_voting_reason", "centroid_sigma_axis_pix",
            "shift_pix", "psf_sigma_pix", "box_pixels_target", "box_pixels_neighbour", "box_missing_target",
            "box_missing_neighbour", "box_partial", "edge_bias_pix", "edge_bias_axis_pix", "separation_sigma_raw"}
    assert all(set(s) == keys for s in m["seasons"]) and len(m["seasons"]) == 4
    assert isinstance(m["seasons"][0]["centroid_colrow"][0], float) and isinstance(m["n_events_used"], int)
    assert m["centroid_sigma_pix_mean"] == np.mean([s["centroid_sigma_pix"] for s in m["seasons"]])
    assert all(isinstance(s["centroid_sigma_axis_pix"], float) and s["centroid_sigma_axis_pix"] > 0
               for s in m["seasons"])
    assert all(s["pos_target"] == list(POS_TARGET) and s["voting"] is True and s["non_voting_reason"] is None
               for s in m["seasons"])


def test_localise_quarters_co_registers_a_shifted_reshaped_stamp():
    aligned = localise_quarters(make_quarters(), EPH)
    shifted = localise_quarters(make_quarters(layout={5: SHIFTED}), EPH)
    assert aligned.verdict == shifted.verdict == "pass"
    im = next(i for i in shifted.images if i.season == 3)
    # the union frame spans Q1's 9 x 9 and Q5's 10 x 10 stamp, which reaches one column further left
    assert im.quarters == [1, 5] and im.diff.shape == (10, 10) and im.n_events == 18
    assert im.pos_target == (POS_TARGET[0] + 1, POS_TARGET[1]) and im.pos_neighbour == (POS_NEIGHBOUR[0] + 1, 4.6)
    assert im.valid.all()                                          # Q5 fills column 0 and row 9
    assert im.offsets_pix == [(0, 0), (1, 0)] and im.residual_offsets_pix == [(0.0, 0.0), (0.0, 0.0)]
    s = _by_season(shifted)[3]
    assert s.voting and s.nearer == "target" and s.separation_sigma > 3
    assert abs(s.depth_neighbour_ppm[0]) < 3 * s.depth_neighbour_ppm[1]
    assert abs(s.depth_target_ppm[0] - 3000) < 4 * s.depth_target_ppm[1]
    assert math.hypot(s.centroid_colrow[0] - im.pos_target[0], s.centroid_colrow[1] - im.pos_target[1]) < 0.2


def test_a_sub_pixel_offset_is_recorded_and_changes_nothing():
    loc = localise_quarters(make_quarters(layout={5: SUBPIX}), EPH)
    im = next(i for i in loc.images if i.season == 3)
    assert im.offsets_pix == [(0, 0), (0, 0)] and im.diff.shape == SHAPE
    assert math.isclose(im.residual_offsets_pix[1][0], 0.3) and im.residual_offsets_pix[1][1] == 0.0
    assert loc.verdict == "pass" and _by_season(loc)[3].separation_sigma > 3


def test_localise_quarters_dip_on_the_neighbour_fails_across_shifted_stamps():
    loc = localise_quarters(make_quarters("neighbour", layout={5: SHIFTED}), EPH)
    assert loc.verdict == "fail" and "neighbour" in loc.reason


def test_one_quarter_without_the_neighbour_is_covered_by_the_other():
    for layout in ({5: NO_NEIGHBOUR}, {1: NO_NEIGHBOUR}):
        loc = localise_quarters(make_quarters(layout=layout), EPH)
        im = next(i for i in loc.images if i.season == 3); s1 = _by_season(loc)[3]
        assert im.diff.shape == SHAPE and im.neighbour_on_frame and s1.voting and s1.two_source
        assert loc.verdict == "pass" and s1.n_events == 18


def test_a_season_without_the_neighbour_does_not_vote():
    for dip_on, expected in (("target", "pass"), ("neighbour", "fail")):
        loc = localise_quarters(make_quarters(dip_on, layout={1: NO_NEIGHBOUR, 5: NO_NEIGHBOUR}), EPH)
        m = loc.metrics(); json.dumps(m, allow_nan=False)
        by = {s["season"]: s for s in m["seasons"]}
        assert by[3]["voting"] is False and by[3]["nearer"] == "unknown" and by[3]["two_source"] is False
        assert by[3]["non_voting_reason"] == "box incomplete: neighbour"
        assert by[3]["depth_neighbour_ppm"] == [None, None]
        assert all(by[s]["voting"] and by[s]["depth_neighbour_ppm"][0] is not None for s in (0, 1, 2))
        assert loc.verdict == expected and "3/3 seasons" in loc.reason


def test_complete_boxes_vote_exactly_as_before():
    """Numbers of the committed code, taken before localise_quarters changed: complete boxes must not move."""
    loc = localise(*make_cube("target"))
    by = _by_season(loc)
    assert all(not s.box_partial and s.edge_bias_pix == 0.0 and s.separation_sigma == s.separation_sigma_raw
               for s in loc.seasons)
    assert all(s.box_target.complete and s.box_neighbour.complete for s in loc.seasons)
    assert [round(by[s].separation_sigma, 6) for s in (0, 1, 2, 3)] == [12.236112, 4.360287, 16.856287, 13.182444]
    assert [round(by[s].centroid_sigma_pix, 6) for s in (0, 1, 2, 3)] == [0.136073, 0.253986, 0.21437, 0.17134]


def test_a_season_with_the_target_on_the_stamp_edge_votes_with_an_allowance():
    for dip_on, expected in (("target", "pass"), ("neighbour", "fail")):
        loc = localise_quarters(make_quarters(dip_on, layout={1: NO_TARGET, 5: NO_TARGET}), EPH)
        s1 = _by_season(loc)[3]; im = next(i for i in loc.images if i.season == 3)
        assert im.pos_target == (0.0, 4.0) and im.box_target.partial and im.box_neighbour.complete
        assert im.target_on_frame and s1.on_frame and s1.voting and s1.two_source and s1.box_partial
        assert s1.box_target.metrics() == {"pixels": 6, "missing": [[-1, -1], [-1, 0], [-1, 1]]}
        assert 0.0 < s1.edge_bias_pix < 0.6 and s1.edge_bias_axis_pix <= s1.edge_bias_pix + 1e-12
        assert s1.centroid_sigma_pix > s1.edge_bias_pix and abs(s1.separation_sigma) < abs(s1.separation_sigma_raw)
        assert s1.non_voting_reason is None
        assert loc.verdict == expected and "4/4 seasons" in loc.reason
        if dip_on == "target":
            assert s1.nearer == "target" and s1.separation_sigma > SEPARATION_SIGMA
        else:
            assert s1.nearer == "neighbour" and s1.separation_sigma < -SEPARATION_SIGMA
        m = loc.metrics(); json.dumps(m, allow_nan=False)
        row = next(x for x in m["seasons"] if x["season"] == 3)
        assert row["box_partial"] is True and row["box_pixels_target"] == 6 and row["box_pixels_neighbour"] == 9
        assert row["box_missing_target"] == [[-1, -1], [-1, 0], [-1, 1]] and row["box_missing_neighbour"] == []
        assert row["edge_bias_pix"] > 0 and row["separation_sigma_raw"] is not None


def test_every_season_with_the_target_on_the_edge_is_eligible_and_carries_the_allowance():
    """The target on the first column with a 0.9 px PSF puts 0.36 px of allowance in the axis error, so the 2.9 px
    pair is only just decisive (2.8 to 3.0 sigma in a single-quarter season): each season either votes or says why
    it did not with a stored "box cut" reason, and none is dropped as an incomplete box."""
    loc = localise_quarters(make_quarters(layout={q: NO_TARGET for q in range(1, 6)}), EPH)
    assert all(s.on_frame and s.two_source and s.box_partial and s.box_target.metrics()["pixels"] == 6
               for s in loc.seasons)
    assert all(0.3 < s.edge_bias_pix < 0.4 and abs(s.separation_sigma) < abs(s.separation_sigma_raw)
               for s in loc.seasons)
    assert all(s.voting if s.non_voting_reason is None else s.non_voting_reason.startswith("box cut: ambiguous")
               for s in loc.seasons)
    assert loc.reason != "pixels: no season holds both stars on the frame"


def test_a_corner_box_does_not_vote():
    loc = localise_quarters(make_quarters(layout={1: CORNER_TARGET, 5: CORNER_TARGET}), EPH)
    s1 = _by_season(loc)[3]
    assert s1.box_target.valid == 4 and not s1.on_frame and not s1.voting and not s1.two_source
    assert s1.non_voting_reason == "box incomplete: target" and s1.nearer == "unknown"
    assert loc.verdict == "pass" and "3/3 seasons" in loc.reason


def test_a_dead_pixel_in_the_box_votes_with_a_small_allowance():
    qs = make_quarters()
    for qd in qs:
        if qd.quarter in (1, 5):
            qd.cube[:, 3, 4] = math.nan                  # row 3, col 4: offset (0, -1) from the target's pixel
    s1 = _by_season(localise_quarters(qs, EPH))[3]
    assert s1.voting and s1.box_partial and s1.box_target.metrics() == {"pixels": 8, "missing": [[0, -1]]}
    assert 0.0 < s1.edge_bias_pix < 0.15


def test_a_cut_box_votes_only_when_decisive():
    from dataclasses import replace

    from skysift.lc.pixels import partial_abstention
    partial = replace(_season(0), box_partial=True, edge_bias_pix=0.1)
    assert partial_abstention(partial) is None                                     # 10 sigma on the target
    assert partial_abstention(replace(_season(0, sep=-8.0), box_partial=True)) is None          # 8 sigma on the neighbour
    assert partial_abstention(replace(_season(0, sep=1.7), box_partial=True)) == "box cut: ambiguous (1.7 sigma)"
    assert partial_abstention(replace(_season(0, sep=-2.9), box_partial=True)) == "box cut: ambiguous (-2.9 sigma)"
    assert (partial_abstention(replace(_season(0, d_n=(200.0, 60.0)), box_partial=True))
            == "box cut: neighbour depth 200 +/- 60 ppm")
    assert (partial_abstention(replace(_season(0, d_n=(math.nan, math.nan)), box_partial=True))
            == "box cut: neighbour depth not measured")
    # the verdict: an abstaining cut season is silent, the complete seasons decide
    silent = replace(_season(3, sep=1.7, voting=False), box_partial=True,
                     non_voting_reason="box cut: ambiguous (1.7 sigma)")
    assert verdict([_season(0), _season(1), _season(2), silent], 20)[0] == "pass"
    assert verdict([_season(0), silent], 20) == (
        "undetermined", "pixels: only one season could vote (season 3: box cut: ambiguous (1.7 sigma))")


def test_the_allowance_falls_back_to_the_default_width(monkeypatch):
    import skysift.lc.pixels as px
    monkeypatch.setattr(px, "two_source_fit", lambda *a, **k: px._nan_fit())
    loc = localise_quarters(make_quarters(layout={1: NO_TARGET, 5: NO_TARGET}), EPH)
    s1 = _by_season(loc)[3]; im = next(i for i in loc.images if i.season == 3)
    assert s1.box_partial and math.isfinite(s1.separation_sigma) and math.isnan(s1.psf_sigma_pix)
    want, want_axis = edge_allowance(im.pos_target, im.valid, px.EDGE_PSF_SIGMA_PIX, im.pos_neighbour)
    assert s1.edge_bias_pix == want > 0 and s1.edge_bias_axis_pix == want_axis
    # no fit, no neighbour depth: a cut box is not decisive on the target's side without it, so it abstains
    assert not s1.voting and s1.non_voting_reason == "box cut: neighbour depth not measured"


def test_no_season_with_both_stars_on_the_frame_is_undetermined():
    layout = {q: CORNER_TARGET for q in range(1, 6)}
    loc = localise_quarters(make_quarters(layout=layout), EPH)
    assert loc.verdict == "undetermined" and loc.reason == "pixels: no season holds both stars on the frame"
    assert all(s.nearer == "unknown" for s in loc.seasons) and "nan" not in loc.reason


def test_a_quarter_without_positions_is_unusable():
    qs = make_quarters()
    qs[4].pos_target = (math.nan, 4.0)
    loc = localise_quarters(qs, EPH)
    assert loc.unusable_quarters == [5] and loc.n_quarters_usable == 4 and _by_season(loc)[3].quarters == [1]


def test_localise_wrapper_carries_the_positions_into_the_season_images():
    loc = localise(*make_cube("target"))
    assert all(im.pos_target == POS_TARGET and im.pos_neighbour == POS_NEIGHBOUR and im.offsets_pix == [(0, 0)]
               for im in loc.images)
    assert all(s.two_source for s in loc.seasons)


def test_quarters_are_grouped_by_their_archive_season():
    """A quarter's ``season`` (the TPF header's archive SEASON) decides its stack; without one, ``quarter % 4`` with
    Q0 counted as Q1 (Q0 has Q1's module and roll, never Q4's)."""
    qs = make_quarters("target")
    for qd in qs:
        qd.season = 3 if qd.quarter in (1, 5) else (qd.quarter + 2) % 4
    images = season_images(qs, EPH)
    assert {im.season: im.quarters for im in images} == {3: [1, 5], 0: [2], 1: [3], 2: [4]}
    loc = localise_quarters(qs, EPH)
    assert loc.verdict == "pass" and sorted(s.season for s in loc.seasons) == [0, 1, 2, 3]
    q0 = QuarterData(quarter=0, cube=np.zeros((1, 2, 2)), time=np.zeros(1), quality=np.zeros(1, int),
                     pos_target=(0.0, 0.0), pos_neighbour=(1.0, 1.0))
    assert quarter_season(q0) == quarter_season(QuarterData(**{**q0.__dict__, "quarter": 1})) == 3
    assert [quarter_season(QuarterData(**{**q0.__dict__, "quarter": q})) for q in (2, 3, 4, 5)] == [0, 1, 2, 3]
    assert quarter_season(QuarterData(**{**q0.__dict__, "season": 3})) == 3


def test_neighbour_votes_are_the_fail_verdicts_numbers():
    cube, time, quality, quarter, eph, pt, pn = make_cube("neighbour")
    loc = localise(cube, time, quality, quarter, eph, pt, pn)
    k, n, m = loc.neighbour_votes()
    assert loc.verdict == "fail" and (n, m) == (4, 4) and f"{n}/{m} seasons ({k:.1f} sigma)" in loc.reason
    k, n, m = localise(*make_cube("target")).neighbour_votes()
    assert (n, m) == (0, 4) and math.isnan(k)


def test_a_pass_needs_two_voting_seasons():
    """One voting season on the target is not enough for a pass: the reason names why each other season did not
    vote. Two are enough; a fail is not held back."""
    silenced = []
    for season, why in ((1, "box incomplete: target"), (2, "box incomplete: neighbour")):
        s = _season(season, voting=False, on_frame=False); s.non_voting_reason = why; silenced.append(s)
    few = _season(3, sep=math.nan, voting=False, n=1)
    one = [_season(0)] + silenced + [few]
    why = ("pixels: only one season could vote (season 1: box incomplete: target; season 2: box incomplete: "
           "neighbour; season 3: too few events)")
    assert verdict(one, 20) == ("undetermined", why)
    alone = "pixels: only one season could vote (no other season has data)"
    assert verdict([_season(0)], 20) == ("undetermined", alone)
    assert verdict([_season(0), _season(2)] + silenced[:1], 20)[0] == "pass"
    assert verdict([_season(0, sep=-8.0)] + silenced, 20)[0] == "fail"


def test_the_two_source_shift_is_stored_per_season():
    loc = localise(*make_cube("target"))
    m = loc.metrics(); json.dumps(m, allow_nan=False)
    for s in m["seasons"]:
        assert len(s["shift_pix"]) == 2 and all(x is not None and abs(x) <= 1.0 for x in s["shift_pix"])
        assert 0.3 <= s["psf_sigma_pix"] <= 3.0


def test_box_coverage_counts_the_pixels_on_the_frame():
    valid = np.ones(SHAPE, bool)
    assert BOX_PIXELS_MIN == 6
    assert box_coverage((4.0, 4.0), valid) == BoxCoverage(9, True, ())
    edge = box_coverage((4.0, 0.1), valid)
    assert (edge.valid, edge.centre, edge.missing) == (6, True, ((-1, -1), (0, -1), (1, -1)))
    assert edge.partial and edge.eligible and not edge.complete
    corner = box_coverage((0.2, -0.3), valid)
    assert corner.valid == 4 and corner.centre and not corner.partial and not corner.eligible
    valid[3, 4] = False                                   # row 3, col 4: offset (0, -1) from (4, 4)
    hole = box_coverage((4.0, 4.0), valid)
    assert hole == BoxCoverage(8, True, ((0, -1),)) and hole.partial
    valid[4, 4] = False
    assert not box_coverage((4.0, 4.0), valid).centre and not box_coverage((4.0, 4.0), valid).eligible
    assert box_coverage((math.nan, 4.0), valid) == ALL_MISSING and ALL_MISSING.valid == 0
    assert box_coverage((4.0, 0.1), np.ones(SHAPE, bool)).metrics() == {
        "pixels": 6, "missing": [[-1, -1], [0, -1], [1, -1]]}


def test_edge_allowance_moves_a_point_source_towards_the_interior():
    valid = np.ones(SHAPE, bool)
    bias, along = edge_allowance((4.0, 0.1), valid, 0.55, (4.0, 3.0))
    assert 0.05 < bias < 0.2 and abs(along - bias) < 1e-9          # the other star is straight up the column
    _, across = edge_allowance((4.0, 0.1), valid, 0.55, (7.0, 0.1))
    assert across < 1e-9                                          # the cut is perpendicular to the star axis
    assert edge_allowance((4.0, 4.0), valid, 0.55, (7.0, 4.0)) == (0.0, 0.0)
    valid[3, 4] = False
    hole, _ = edge_allowance((4.0, 4.0), valid, 0.55, (7.0, 4.0))
    assert 0.0 < hole < 0.15
    assert all(math.isnan(x) for x in edge_allowance((math.nan, 0.1), valid, 0.55, (4.0, 3.0)))
