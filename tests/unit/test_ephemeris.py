import dataclasses
import json

import numpy as np
import pytest

from skysift.ephemeris import (
    BKJD_OFFSET,
    CADENCE_MIN,
    DEGENERACY_REASON,
    ParityDepths,
    Template,
    bootstrap_shape,
    ingress_minutes,
    linear_fit,
    parity_depths,
    predict_from,
    secondary_limit,
    template_from,
    transit_times,
)
from skysift.lc.ops import detrend, event_depths
from skysift.lc.transit_model import _t14, fit_transit
from tests.synthetic import inject_model, inject_transit, make_lightcurve

P, T0, DUR = 5.4, 134.9, 3.2 / 24
# a/R* 12.9 makes the injected transit last DUR (3.20 h); the a/R* of 74 of test_dossier's scene belongs to its
# 40.29 d period and gives only 0.56 h at 5.4 d, shorter than the 3.2 h box the tests time it with
K, B, A_R = 0.047, 0.31, 12.9
POINT = {"k": K, "b": B, "a_over_r": A_R, "t14_h": 3.2, "fit_depth_ppm": 2209.0}
RUNG2 = {"secondary_ppm": 95.4, "control_rms_ppm": 128.2, "free_secondary_ppm": 341.7, "free_secondary_phase": 0.379,
         "free_secondary_sigma": 3.8, "fit_ok": True, "fit_k": 0.0478, "fit_a_over_r": 43.6}


def _planet(seed=3, noise=600):
    return inject_model(make_lightcurve(noise_ppm=noise, seed=seed), P, T0, K, B, A_R)


@pytest.fixture(scope="module")
def scene():
    lc = _planet()
    fn = detrend(lc)
    times = transit_times(lc.time, fn, P, T0, DUR, Template("box", 0.0022, DUR))
    return lc, fn, times


@pytest.fixture(scope="module")
def eph(scene):
    return linear_fit(scene[2], P, T0)


def test_the_scene_lasts_the_search_duration():
    assert _t14(P, K, B, A_R) * 24 == pytest.approx(3.2, abs=0.01)


def test_transit_times_recover_the_injected_mid_times(scene):
    _, _, tt = scene
    used = [t for t in tt if t.used]
    assert len(used) >= 150
    pulls = [(t.t_bkjd - (T0 + t.epoch * P)) / t.err_days for t in used]
    assert abs(np.mean(pulls)) < 0.3 and 0.6 < np.std(pulls) < 1.6
    assert all(t.reason is None and t.n_in >= 3 and t.n_out >= 6 for t in used)


def test_transit_times_give_the_reason_an_event_is_not_timed(scene):
    lc, fn, times = scene
    # events at quarter edges, partly inside the 1.5 d gap-edge mask
    starved = [t for t in times if not t.used]
    assert starved and all(t.t_bkjd is None and t.err_days is None for t in starved)
    assert {t.reason for t in starved} <= {"too few in-transit points", "too few out-of-transit points",
                                          "flat baseline", "no minimum inside the grid",
                                          "shift error above half a duration"}
    # epoch 10 keeps two in-transit points, epoch 20 gets a baseline of one repeated value
    timed = {t.epoch for t in times if t.used}
    assert {10, 20} <= timed
    epoch = np.round((lc.time - T0) / P)
    dt = np.abs(lc.time - (T0 + epoch * P))
    cut = fn.copy()
    gone = np.flatnonzero((epoch == 10) & (dt < 0.5 * DUR) & np.isfinite(cut))[2:]
    cut[gone] = np.nan
    cut[(epoch == 20) & (dt >= DUR) & (dt <= 3 * DUR) & np.isfinite(cut)] = 1.0
    by_epoch = {t.epoch: t for t in transit_times(lc.time, cut, P, T0, DUR, Template("box", 0.0022, DUR))}
    assert by_epoch[10].reason == "too few in-transit points" and by_epoch[10].n_in == 2
    assert by_epoch[20].reason == "flat baseline" and not by_epoch[20].used
    # a 1 ppm template barely bends the chi-square: its parabola is wider than half a duration
    faint = transit_times(lc.time, fn, P, T0, DUR, Template("box", 1e-6, DUR))
    wide = [t for t in faint if t.reason == "shift error above half a duration"]
    assert len(wide) > 0.2 * len(faint) and all(t.t_bkjd is None and t.chi2_red > 0 for t in wide)
    # predicted 1.5 durations late, the dip lies beyond the shift grid: the chi-square falls to the grid's edge
    late = transit_times(lc.time, fn, P, T0 + 1.5 * DUR, DUR, Template("box", 0.0022, DUR))
    edge = [t for t in late if t.reason == "no minimum inside the grid"]
    assert len(edge) > 0.8 * len(late) and all(t.t_bkjd is None and t.chi2_red > 0 for t in edge)


def test_a_fit_template_times_the_transit_as_well_as_the_box(scene):
    lc, fn, _ = scene
    tmpl = template_from({"fit_ok": True, "fit_k": K, "fit_b": B, "fit_a_over_r": A_R}, 2209.0, DUR)
    assert tmpl.kind == "fit" and tmpl.flux(np.array([0.0]), P)[0] == pytest.approx(1 - 2209e-6)
    assert template_from({"fit_ok": False, "fit_k": None}, 2200.0, DUR).kind == "box"
    # a fitted geometry whose disc never touches the star has no dip to scale: refused, not timed with a flat line
    with pytest.raises(ValueError, match="dipping geometry"):
        template_from({"fit_ok": True, "fit_k": 0.05, "fit_b": 1.2, "fit_a_over_r": 12.0}, 2200.0, DUR)
    with pytest.raises(ValueError, match="kind"):
        Template("trapezoid", 0.002, DUR)
    used = [t for t in transit_times(lc.time, fn, P, T0, DUR, tmpl) if t.used]
    pulls = [(t.t_bkjd - (T0 + t.epoch * P)) / t.err_days for t in used]
    assert len(used) >= 150 and abs(np.mean(pulls)) < 0.3 and 0.6 < np.std(pulls) < 1.6


def test_linear_fit_recovers_the_period_within_two_sigma_and_centres_the_epoch(eph):
    assert eph.ok and abs(eph.period - P) < 2 * eph.period_err and eph.period_err < 2e-5
    used = [t for t in eph.times if t.used]
    # Deeg 2015's central epoch is the error-weighted mean epoch, where t_ref and the period are uncorrelated
    centre = np.average([t.epoch for t in used], weights=[t.err_days ** -2 for t in used])
    assert abs(eph.epoch_ref - centre) <= 0.5 and abs(eph.cov) < 0.05 * eph.t_ref_err * eph.period_err
    assert eph.t_ref_err < eph.t0_err                       # Deeg 2015: smallest at the centre
    assert abs(eph.t0 - T0) < 3 * eph.t0_err
    far = eph.predict(eph.epoch_ref + 400)[1]
    assert far > eph.t_ref_err and far == pytest.approx(np.hypot(eph.t_ref_err, 400 * eph.period_err))
    assert eph.inflation >= 1.0 and eph.dof == eph.n_used - 2 and 2 < eph.rms_min < 20


def test_next_transits_follow_the_date_with_their_windows(eph):
    nxt = eph.next_transits(2000.0)
    e0 = nxt[0]["epoch"]
    assert [r["epoch"] for r in nxt] == [e0, e0 + 1, e0 + 2]
    assert 2000.0 < nxt[0]["t_bkjd"] < 2000.0 + P
    assert nxt[0]["t_bjd"] == pytest.approx(nxt[0]["t_bkjd"] + BKJD_OFFSET)
    assert nxt[0]["err_days"] == pytest.approx(eph.predict(nxt[0]["epoch"])[1])


def test_predict_from_the_stored_block_matches_the_object(eph):
    assert predict_from(eph.as_dict(), 123) == pytest.approx(eph.predict(123))
    assert predict_from(json.loads(json.dumps(eph.as_dict())), 123) == pytest.approx(eph.predict(123))


def test_an_outlier_is_dropped_by_the_mad_rule(scene):
    times = list(scene[2])
    i = next(i for i, t in enumerate(times) if t.used and t.epoch > 50)
    times[i] = dataclasses.replace(times[i], t_bkjd=times[i].t_bkjd + 40 / 1440)
    eph = linear_fit(times, P, T0)
    assert eph.n_outliers == 1 and any(t.reason and t.reason.startswith("outlier") for t in eph.times)
    moved = next(t for t in eph.times if t.epoch == times[i].epoch)
    assert not moved.used and moved.reason.startswith("outlier") and eph.n_used == eph.n_measured - 1


def test_linear_fit_with_two_events_has_no_error(scene):
    times = [t for t in scene[2] if t.used]
    eph = linear_fit(times[:2], P, T0)
    assert not eph.ok and eph.period_err is None and eph.t0 == T0 and "fewer than 3" in eph.reason
    assert eph.predict(10) == (T0 + 10 * P, None) and predict_from(eph.as_dict(), 10) == (T0 + 10 * P, None)


def _rung2_point(fit):
    return {"k": fit.k, "b": fit.b, "a_over_r": fit.a_over_r, "t14_h": fit.t14_days * 24,
            "fit_depth_ppm": fit.depth_ppm}


def test_bootstrap_brackets_the_rung2_radius_ratio_and_the_injected_one_lies_within_three_sigma(scene):
    lc, fn, _ = scene
    # the point is what rung 2 reports: the fit of the whole curve (k 0.0467 on this noise draw, 1.6 sigma below K)
    fit = fit_transit(lc.time, fn, P, T0, DUR)
    point = _rung2_point(fit)
    se = bootstrap_shape(lc.time, fn, P, T0, DUR, point, n_boot=20, seed=1)
    # every resample of this clean scene converges, and the k interval brackets the injected k
    assert se.ok and se.n_converged == 20 and se.k["minus"] > 0 and se.k["plus"] > 0
    assert se.k["boot_median"] - 3 * se.k["minus"] <= K <= se.k["boot_median"] + 3 * se.k["plus"]
    assert se.k["minus"] < 0.01 and se.k["plus"] < 0.01 and se.t14_h["median"] == point["t14_h"]
    assert se.k["median"] == fit.k and se.model_depth_ppm["median"] == fit.depth_ppm and se.box_duration_h is None
    assert se.n_boot == 20 and se.seed == 1 and set(se.b) == {"median", "minus", "plus", "boot_median"}
    # the ingress (about 10 minutes) is shorter than the cadence: b and a/R* keep their point values, no errors
    assert se.ingress_min == pytest.approx(ingress_minutes(P, fit.k, fit.b, fit.a_over_r)) and se.ingress_min < 20
    assert se.degenerate and se.degeneracy_reason == DEGENERACY_REASON
    for block, value in ((se.b, fit.b), (se.a_over_r, fit.a_over_r)):
        assert block == {"median": value, "minus": None, "plus": None, "boot_median": None}
    assert se.t14_h["plus"] > 0 and se.model_depth_ppm["minus"] > 0
    d = se.as_dict()
    assert d["degenerate"] is True and d["b"]["minus"] is None and d["ingress_min"] == se.ingress_min


def test_bootstrap_quotes_b_and_a_over_r_when_the_ingress_is_resolved():
    # a 6 h transit of k 0.1 at b 0.5 has a 43 minute ingress, longer than the 29.4 minute cadence
    k, b, dur = 0.1, 0.5, 6.0 / 24
    a = np.sqrt((1 + k) ** 2 - b ** 2) / np.sin(np.pi * dur / P)
    assert _t14(P, k, b, a) == pytest.approx(dur) and ingress_minutes(P, k, b, a) > CADENCE_MIN + 10
    lc = inject_model(make_lightcurve(noise_ppm=600, seed=3), P, T0, k, b, a)
    fn = detrend(lc)
    fit = fit_transit(lc.time, fn, P, T0, dur)
    se = bootstrap_shape(lc.time, fn, P, T0, dur, _rung2_point(fit), n_boot=20, seed=1)
    assert se.ok and not se.degenerate and se.degeneracy_reason is None and se.ingress_min > CADENCE_MIN
    for block in (se.b, se.a_over_r):
        assert None not in block.values() and block["minus"] > 0 and block["plus"] > 0
    # the injected b and a/R* lie within three half-widths of the bootstrap median, on their own side
    for block, true in ((se.b, b), (se.a_over_r, a)):
        assert block["boot_median"] - 3 * block["minus"] <= true <= block["boot_median"] + 3 * block["plus"]


def test_bootstrap_without_a_fit_gives_box_depth_and_duration(scene):
    lc, fn, _ = scene
    se = bootstrap_shape(lc.time, fn, P, T0, DUR, None, n_boot=20)
    assert se.ok and se.k is None and se.box_duration_h["median"] == pytest.approx(3.2, abs=0.8)
    assert se.box_depth_ppm["median"] == pytest.approx(2200, abs=300)
    assert se.box_depth_ppm["minus"] == se.box_depth_ppm["plus"] > 0


def test_bootstrap_with_too_few_converged_resamples_says_so(scene):
    lc, fn, _ = scene
    two = lc.time < T0 + P + 2.0           # the transits at 134.9 and 140.3 only
    se = bootstrap_shape(lc.time[two], fn[two], P, T0, DUR, POINT, n_boot=20, seed=1)
    assert not se.ok and "converged" in se.reason and se.k is None and se.n_converged < 20


def test_parity_depths_agree_on_a_planet_and_differ_on_an_alternating_dip(scene):
    lc, fn, _ = scene
    pd = parity_depths(event_depths(lc.time, fn, lc.quarter, P, T0, DUR))
    assert isinstance(pd, ParityDepths) and abs(pd.difference_sigma) < 3
    assert pd.odd["n"] > 50 and pd.even["n"] > 50 and pd.odd["inflation"] >= 1.0
    alt = inject_transit(inject_transit(make_lightcurve(noise_ppm=600, seed=3), 2 * P, T0, DUR, 2600),
                         2 * P, T0 + P, DUR, 1800)
    fa = detrend(alt)
    pd = parity_depths(event_depths(alt.time, fa, alt.quarter, P, T0, DUR))
    assert pd.difference_sigma > 5
    assert pd.even["depth_ppm"] == pytest.approx(2600, abs=200) and pd.odd["depth_ppm"] == pytest.approx(1800, abs=200)


def test_secondary_limit_from_rung2_metrics():
    sl = secondary_limit(RUNG2)
    assert sl.limit_3sigma_ppm == pytest.approx(95.4 + 3 * 128.2)
    assert sl.albedo_limit == pytest.approx(480e-6 * (43.6 / 0.0478) ** 2, rel=1e-2)
    assert (sl.strongest_ppm, sl.strongest_phase, sl.strongest_sigma) == (341.7, 0.379, 3.8)
    assert secondary_limit({}) is None
    assert secondary_limit({**RUNG2, "secondary_ppm": -50.0}).limit_3sigma_ppm == pytest.approx(3 * 128.2)
    assert secondary_limit({**RUNG2, "fit_ok": False}).albedo_limit is None


def test_every_block_is_strict_json(scene, eph):
    lc, fn, times = scene
    two = lc.time < T0 + P + 2.0
    blocks = [eph.as_dict(), linear_fit([t for t in times if t.used][:2], P, T0).as_dict(),
              *(t.as_dict() for t in times), bootstrap_shape(lc.time, fn, P, T0, DUR, None, n_boot=20).as_dict(),
              bootstrap_shape(lc.time[two], fn[two], P, T0, DUR, POINT, n_boot=20).as_dict(),
              parity_depths(event_depths(lc.time, fn, lc.quarter, P, T0, DUR)).as_dict(),
              secondary_limit(RUNG2).as_dict(), secondary_limit({**RUNG2, "fit_k": float("nan")}).as_dict()]
    for block in blocks:
        json.dumps(block, allow_nan=False)
    assert eph.as_dict()["times"][0]["t_bjd"] is None or eph.as_dict()["times"][0]["t_bjd"] > BKJD_OFFSET
