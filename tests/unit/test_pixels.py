import json
import math

import numpy as np
from scipy.special import erf

from skysift.lc.ops import season_of
from skysift.lc.pixels import (
    N_MC,
    Ephemeris,
    SeasonResult,
    difference_images,
    leak_depth,
    localise,
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


def _psf(pos):
    """Pixel-integrated circular Gaussian of unit total flux centred on (col, row)."""
    col, row = pos
    s = math.sqrt(2.0) * PSF_SIGMA
    cc, rr = np.arange(SHAPE[1]), np.arange(SHAPE[0])
    px = 0.5 * (erf((cc + 0.5 - col) / s) - erf((cc - 0.5 - col) / s))
    py = 0.5 * (erf((rr + 0.5 - row) / s) - erf((rr - 0.5 - row) / s))
    return np.outer(py, px)


def _in_transit(time, eph):
    ph = (time - eph.t0 + 0.5 * eph.period) % eph.period - 0.5 * eph.period
    return np.abs(ph) < 0.5 * eph.duration


def make_cube(dip_on="target", n_days=372, depth=DEPTH, seed=0, neighbour_ratio=RATIO, n_quarters=4):
    """A 9 x 9 pixel stamp holding a target and a brighter neighbour 2.9 px away (Gaussian PSFs, sigma 0.9 px), long
    cadence, photon noise on every pixel, quarters of 93 d (so the first four quarters cover seasons 1, 2, 3 and 0)
    and a 3 h box transit of ``depth`` every 10 d on the chosen star."""
    rng = np.random.default_rng(seed)
    time = np.arange(T_START, T_START + n_days, CADENCE)
    quarter = np.minimum(1 + ((time - T_START) // QUARTER_DAYS).astype(int), n_quarters)
    dip = np.where(_in_transit(time, EPH), depth, 0.0)
    f_t = F_TARGET * (1.0 - (dip if dip_on == "target" else 0.0 * dip))
    f_n = neighbour_ratio * F_TARGET * (1.0 - (dip if dip_on == "neighbour" else 0.0 * dip))
    model = f_t[:, None, None] * _psf(POS_TARGET) + f_n[:, None, None] * _psf(POS_NEIGHBOUR) + BACKGROUND
    cube = rng.normal(model, np.sqrt(model))
    quality = np.zeros(time.size, int)
    return cube, time, quality, quarter, EPH, POS_TARGET, POS_NEIGHBOUR


def _swap_season(cube, rest, season):
    """The same cube with the dip moved from the target to the neighbour in one season only."""
    time, _quality, quarter, eph, pt, pn = rest
    m = (season_of(quarter) == season) & _in_transit(time, eph)
    out = cube.copy()
    out[m] += DEPTH * F_TARGET * _psf(pt) - DEPTH * RATIO * F_TARGET * _psf(pn)
    return out


def test_difference_images_recover_the_dip_pixels():
    cube, time, quality, quarter, eph, pt, _pn = make_cube("target")
    seasons = difference_images(cube, time, quality, quarter, eph)
    assert len(seasons) == 4 and all(s.n_events >= 2 for s in seasons)
    s = seasons[0]; r, c = np.unravel_index(np.argmax(s.diff / np.sqrt(s.var)), s.diff.shape)
    assert abs(c - pt[0]) < 1.1 and abs(r - pt[1]) < 1.1
    assert [im.season for im in seasons] == [0, 1, 2, 3] and [im.quarters for im in seasons] == [[4], [1], [2], [3]]
    assert all(im.diff.shape == im.var.shape == im.direct.shape == im.valid.shape == SHAPE for im in seasons)


def test_difference_images_drop_quarters_with_fewer_than_two_events():
    cube, time, quality, quarter, eph, *_ = make_cube("target", n_days=40)
    quarter = np.where(time > 145.0, 2, 1)          # quarter 1 holds one transit (137), quarter 2 the other three
    seasons = difference_images(cube, time, quality, quarter, eph)
    assert [im.quarters for im in seasons] == [[2]] and seasons[0].n_events == 3


def test_snr_box_centroid_finds_a_point_source_and_its_error():
    diff = 100.0 * _psf((3.3, 5.0)); var = np.full(SHAPE, 1.0); valid = np.ones(SHAPE, bool)
    (col, row), sigma, snr = snr_box_centroid(diff, var, valid, np.random.default_rng(3))
    assert abs(col - 3.3) < 0.2 and abs(row - 5.0) < 0.05
    assert 0 < sigma < 0.1 and snr > 15 and N_MC == 200


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


def test_localise_fails_a_dip_on_the_neighbour():
    loc = localise(*make_cube("neighbour"))
    assert loc.verdict == "fail" and "neighbour" in loc.reason


def test_localise_needs_three_usable_transits():
    loc = localise(*make_cube("target", n_days=25))        # two transits only
    assert loc.verdict == "undetermined" and loc.reason.startswith("pixels: 2 usable transits < 3")


def test_localise_is_undetermined_when_a_season_disagrees():
    cube, *rest = make_cube("target")
    # move the dip to the neighbour in season 2 only
    loc = localise(_swap_season(cube, rest, season=2), *rest)
    assert loc.verdict == "undetermined" and "season 2" in loc.reason
    assert loc.reason.startswith("pixels: season 2 disagrees (centroid nearer neighbour, ")


def test_localise_is_deterministic():
    a = localise(*make_cube("target")).metrics(); b = localise(*make_cube("target")).metrics()
    assert a == b


def _season(season, nearer="target", sep=10.0, d_t=(3000.0, 100.0), d_n=(0.0, 50.0), n=5):
    return SeasonResult(season=season, n_events=n, centroid_colrow=(4.0, 4.0), centroid_sigma_pix=0.1,
                        dist_target_pix=0.1, dist_neighbour_pix=2.9, nearer=nearer, separation_sigma=sep,
                        depth_target_ppm=d_t, depth_neighbour_ppm=d_n, max_snr=12.0)


def test_verdict_rules():
    ok = [_season(s) for s in range(4)]
    assert verdict(ok, 20)[0] == "pass"
    assert verdict(ok, 2) == ("undetermined", "pixels: 2 usable transits < 3")
    weak = ok[:1] + [_season(1, sep=2.0)] + ok[2:]
    assert verdict(weak, 20) == ("undetermined", "pixels: season 1 disagrees (centroid nearer target, 2.0 sigma)")
    leak = ok[:3] + [_season(3, d_n=(400.0, 50.0))]
    assert verdict(leak, 20)[0] == "undetermined" and "season 3" in verdict(leak, 20)[1]
    nb = [_season(s, nearer="neighbour", sep=-8.0) for s in range(3)] + ok[3:]
    v, why = verdict(nb, 20)
    assert v == "fail" and "neighbour" in why and "3/4" in why
    two_source = [_season(s, d_t=(50.0, 100.0), d_n=(900.0, 60.0)) for s in range(3)] + ok[3:]
    assert verdict(two_source, 20)[0] == "fail"
    one_event = ok[:3] + [_season(3, nearer="neighbour", sep=-8.0, n=1)]      # ignored: fewer than two events
    assert verdict(one_event, 20)[0] == "pass"


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
            "dist_neighbour_pix", "nearer", "separation_sigma", "depth_target_ppm", "depth_neighbour_ppm", "max_snr"}
    assert all(set(s) == keys for s in m["seasons"]) and len(m["seasons"]) == 4
    assert isinstance(m["seasons"][0]["centroid_colrow"][0], float) and isinstance(m["n_events_used"], int)
    assert m["centroid_sigma_pix_mean"] == np.mean([s["centroid_sigma_pix"] for s in m["seasons"]])
