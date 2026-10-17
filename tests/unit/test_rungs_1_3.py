import json

import numpy as np

from skysift.config import Thresholds
from skysift.lc.ops import detrend, fold_phase, season_of
from skysift.models import LightCurve, Signal, Target, TestStatus
from skysift.vetting.base import DataBundle
from skysift.vetting.rung1_detection import Rung1Detection
from skysift.vetting.rung2_shape import Rung2Shape
from skysift.vetting.rung3_stability import Rung3Stability
from tests.synthetic import inject_transit, make_lightcurve

P, T0, DUR = 13.769, 136.3, 0.09
TGT = Target(source="test", target_id="t", ra=0, dec=0)


def bundle(lc):
    return DataBundle(lc=lc, fnorm=detrend(lc), fnorm_alt=detrend(lc, flux=lc.flux_alt), target=TGT, thresholds=Thresholds())


def signal(depth_ppm=2000.0):
    """Signal built straight from the injected ephemeris (the box-dip search is too slow for unit tests)."""
    return Signal(target_id="t", detector="box_dip", detector_version="1", period=P, t0=T0, duration=DUR,
                  depth_ppm=depth_ppm, snr=20.0, sde=15.0, n_events=90)


def test_rung1_passes_good_signal_and_flags_alias():
    lc = inject_transit(make_lightcurve(seed=1), P, T0, DUR, 2000)
    assert Rung1Detection().run(signal(2000), bundle(lc)).status == TestStatus.PASS
    s = signal(2000).model_copy(update={"period": 93.2})
    r = Rung1Detection().run(s, bundle(lc))
    assert r.status == TestStatus.UNDETERMINED and r.reason == "systematic_alias"


def test_rung1_bad_stellar_meta_falls_back():
    lc = inject_transit(make_lightcurve(seed=1), P, T0, DUR, 2000)
    for meta in ({"mass": float("nan"), "radius": None}, {"mass": 0.0, "radius": -1.0}, {"mass": "x"}):
        b = bundle(lc)
        b.target = Target(source="test", target_id="t", ra=0, dec=0, meta=meta)
        r = Rung1Detection().run(signal(2000), b)
        assert r.status == TestStatus.PASS and r.metrics["stellar_source"] == "fallback", meta
        assert r.metrics["stellar_mass"] == 0.5 and r.metrics["stellar_radius"] == 0.5


def test_rung1_uses_valid_stellar_meta():
    lc = inject_transit(make_lightcurve(seed=1), P, T0, DUR, 2000)
    b = bundle(lc)
    b.target = Target(source="test", target_id="t", ra=0, dec=0, meta={"mass": 1.0, "radius": 1.0})
    r = Rung1Detection().run(signal(2000), b)
    assert r.status == TestStatus.PASS and r.metrics["stellar_source"] == "meta"
    assert r.metrics["stellar_mass"] == 1.0 and abs(r.metrics["duration_ratio"] - 0.5) < 0.1


def test_rung1_fails_low_sde():
    lc = inject_transit(make_lightcurve(seed=1), P, T0, DUR, 2000)
    s = signal(2000).model_copy(update={"sde": 4.0})
    assert Rung1Detection().run(s, bundle(lc)).status == TestStatus.FAIL


def test_rung2_pass_then_fail_on_alternating_depths():
    lc = inject_transit(make_lightcurve(seed=2, noise_ppm=800), P, T0, DUR, 2000)
    r_pass = Rung2Shape().run(signal(2000), bundle(lc))
    assert r_pass.status == TestStatus.PASS
    eb = inject_transit(make_lightcurve(seed=2, noise_ppm=800), 2 * P, T0, DUR, 6000)   # odd events deep
    eb = inject_transit(eb, 2 * P, T0 + P, DUR, 2000)                                    # even events shallow
    s = signal(2000).model_copy(update={"period": P})
    r = Rung2Shape().run(s, bundle(eb))
    assert r.status == TestStatus.FAIL and "odd_even" in r.reason


def test_rung2_flags_eccentric_secondary():
    lc = inject_transit(make_lightcurve(seed=3, noise_ppm=800), P, T0, DUR, 3000)
    lc = inject_transit(lc, P, T0 + 0.529 * P, 0.06, 2500)
    r = Rung2Shape().run(signal(3000), bundle(lc))
    assert r.status == TestStatus.FAIL and r.reason == "eccentric_secondary"
    assert abs(r.metrics["free_secondary_phase"] - 0.529) < 0.02


def test_rung3_fails_on_season_dependence():
    lc = make_lightcurve(seed=4, noise_ppm=800)
    season3 = (lc.quarter % 4) == 3
    f = lc.flux.copy()
    ph = (lc.time - T0 + 0.5 * P) % P - 0.5 * P
    f[(np.abs(ph) < DUR / 2) & season3] *= 1 - 12000e-6
    f[(np.abs(ph) < DUR / 2) & ~season3] *= 1 - 500e-6
    lc2 = LightCurve(lc.time, f, lc.flux_err, lc.quality, lc.quarter, lc.flux_alt)
    s = signal(3000)
    r = Rung3Stability().run(s, bundle(lc2))
    assert r.status == TestStatus.FAIL and "season" in r.reason
    good = inject_transit(make_lightcurve(seed=4, noise_ppm=800), P, T0, DUR, 2000)
    r_good = Rung3Stability().run(s, bundle(good))
    assert r_good.status == TestStatus.PASS


def test_rung2_secondary_near_half_phase_is_not_exempt():
    # 1500 ppm, 0.06 d secondary a little off phase 0.5: the old +-0.02 exemption let these through.
    for ph, expect in ((0.5, "secondary_at_0.5"), (0.505, "secondary_at_0.5"), (0.515, "eccentric_secondary")):
        lc = inject_transit(make_lightcurve(seed=3, noise_ppm=800), P, T0, DUR, 3000)
        lc = inject_transit(lc, P, T0 + ph * P, 0.06, 1500)
        r = Rung2Shape().run(signal(3000), bundle(lc))
        assert r.status == TestStatus.FAIL and r.reason.startswith(expect), (ph, r.reason)


def test_rung2_nonfinite_free_search_is_undetermined():
    lc = inject_transit(make_lightcurve(seed=2, noise_ppm=800), P, T0, DUR, 2000)
    b = bundle(lc)
    b.fnorm = np.full_like(b.fnorm, np.nan)
    r = Rung2Shape().run(signal(2000), b)
    assert r.status == TestStatus.UNDETERMINED


def test_rung3_single_deviant_season_fails_with_one_outlier():
    lc = inject_transit(make_lightcurve(seed=4, noise_ppm=800), P, T0, DUR, 2000)
    season3 = (lc.quarter % 4) == 3
    ph = (lc.time - T0 + 0.5 * P) % P - 0.5 * P
    f = lc.flux.copy()
    f[(np.abs(ph) < DUR / 2) & season3] *= 1 - 1000e-6     # 3000 ppm in season 3, 2000 ppm elsewhere
    lc2 = LightCurve(lc.time, f, lc.flux_err, lc.quality, lc.quarter, lc.flux_alt)
    r = Rung3Stability().run(signal(3000), bundle(lc2))
    assert r.status == TestStatus.FAIL and "season" in r.reason
    z = r.metrics["season_z"]
    # The deviant season stands far above the rest. The coordinator asked for exactly one |z| > 3, but with a
    # leave-one-out reference the other seasons' reference mean contains the deviant season, so they sit at |z| ~ 3
    # (see report); that count is not asserted.
    assert max(z, key=lambda k: abs(z[k])) == "3" and z["3"] > 9 and all(abs(v) < 4 for k, v in z.items() if k != "3")
    assert set(r.metrics["season_z"]) == {"0", "1", "2", "3"} and r.metrics["n_events"] > 4


def test_rung2_passes_an_ultra_short_period_planet():
    # 3000 ppm, P 0.6 d, 0.075 d transit: before the primary mask, the free-phase search found the transit itself
    # at phase 0.1/0.9 and failed the planet as an eccentric_secondary
    for seed in (0, 1):
        lc = inject_transit(make_lightcurve(n_days=90, cadence_min=30, noise_ppm=500, seed=seed), 0.6, 132.0, 0.075, 3000)
        s = Signal(target_id="t", detector="box_dip", detector_version="1", period=0.6, t0=132.0, duration=0.075,
                   depth_ppm=3000, snr=50.0, sde=20.0, n_events=140)
        r = Rung2Shape().run(s, bundle(lc))
        assert r.status == TestStatus.PASS, (seed, r.reason, r.metrics["free_secondary_sigma"])
        assert r.metrics["free_secondary_sigma"] < 5


def test_rung1_applies_thresholds_before_the_systematic_alias_check():
    lc = inject_transit(make_lightcurve(seed=1), P, T0, DUR, 2000)
    weak = signal(2000).model_copy(update={"period": 46.6, "sde": 3.0, "snr": 1.0})
    r = Rung1Detection().run(weak, bundle(lc))
    assert r.status == TestStatus.FAIL and r.reason.startswith("sde ")
    strong = signal(2000).model_copy(update={"period": 46.6})
    r = Rung1Detection().run(strong, bundle(lc))
    assert r.status == TestStatus.UNDETERMINED and r.reason == "systematic_alias" and r.metrics["period"] == 46.6


def _sinusoid(lc, period, t0, amp_ppm):
    """Smooth variability (pulsation or spots) with its minimum at t0."""
    f = lc.flux * (1 - amp_ppm * 1e-6 * np.cos(2 * np.pi * (lc.time - t0) / period))
    return LightCurve(lc.time, f, lc.flux_err, lc.quality, lc.quarter, f.copy())


def test_rung2_rejects_a_sinusoidal_variable():
    """A pulsator or spotted rotator folds into a smooth wave, so a two-harmonic sine explains the fold at least as well
    as a box: rung 2 fails it as `sinusoidal`. The 0.5677 d second-pass peak of gaia-dr3-2075988698983795072 (a
    frequency-comb sidelobe of a 0.64 d variable) passed every rung before this test existed."""
    per, t0 = 0.5677, 132.0
    var = _sinusoid(make_lightcurve(n_days=200, cadence_min=30, noise_ppm=500, seed=5), per, t0, 1500)
    s = Signal(target_id="t", detector="box_dip", detector_version="1", period=per, t0=t0, duration=0.2 * per,
               depth_ppm=1400, snr=40.0, sde=40.0, n_events=350)
    r = Rung2Shape().run(s, bundle(var))
    assert r.status == TestStatus.FAIL and r.reason == "sinusoidal"
    assert r.metrics["sine_chi2"] < r.metrics["box_chi2"] and r.metrics["shape_n"] >= 50


def test_rung2_box_beats_sine_for_a_transit_on_a_variable_star():
    """A transit folds to a flat baseline with a dip even when the star varies at another period: the box wins."""
    var = _sinusoid(make_lightcurve(n_days=200, cadence_min=30, noise_ppm=500, seed=6), 0.7, 132.0, 1500)
    lc = inject_transit(var, 9.9, 133.1, 2.5 / 24, 2500)
    s = Signal(target_id="t", detector="box_dip", detector_version="1", period=9.9, t0=133.1, duration=2.5 / 24,
               depth_ppm=2500, snr=20.0, sde=15.0, n_events=20)
    r = Rung2Shape().run(s, bundle(lc))
    assert r.metrics["box_chi2"] < r.metrics["sine_chi2"] and r.reason != "sinusoidal"
    clean = inject_transit(make_lightcurve(n_days=200, cadence_min=30, noise_ppm=500, seed=6), 9.9, 133.1, 2.5 / 24, 2500)
    assert Rung2Shape().run(s, bundle(clean)).status == TestStatus.PASS


def test_stacked_odd_even_passes_a_short_period_planet():
    """K04802.01 at 30-minute cadence: one hour of transit is two points per event, so the per-event odd/even test is
    starved; the stacked folds answer instead."""
    lc = inject_transit(make_lightcurve(n_days=300, cadence_min=30, noise_ppm=500, seed=33), 0.6, 132.0, 1 / 24, 600)
    s = Signal(target_id="t", detector="box_dip", detector_version="1", period=0.6, t0=132.0, duration=1 / 24,
               depth_ppm=600, snr=25.0, sde=30.0, n_events=480)
    r = Rung2Shape().run(s, bundle(lc))
    assert r.status == TestStatus.PASS, (r.reason, r.metrics)
    assert r.metrics["stacked"] is True and r.metrics["parity_sigma"] < 3 and r.metrics["n_events"] > 0


def test_stacked_odd_even_fails_alternating_eclipses():
    lc = inject_transit(make_lightcurve(n_days=300, cadence_min=30, noise_ppm=500, seed=34), 1.2, 132.0, 1 / 24, 2500)
    lc = inject_transit(lc, 1.2, 132.6, 1 / 24, 1200)
    s = Signal(target_id="t", detector="box_dip", detector_version="1", period=0.6, t0=132.0, duration=1 / 24,
               depth_ppm=1800, snr=40.0, sde=50.0, n_events=480)
    r = Rung2Shape().run(s, bundle(lc))
    assert r.status == TestStatus.FAIL and r.reason.startswith("odd_even") and r.metrics["stacked"] is True


def test_stacked_seasons_are_determinate_for_a_short_period_planet():
    lc = inject_transit(make_lightcurve(n_days=1400, cadence_min=30, noise_ppm=500, seed=35), 0.6, 132.0, 1 / 24, 600)
    s = Signal(target_id="t", detector="box_dip", detector_version="1", period=0.6, t0=132.0, duration=1 / 24,
               depth_ppm=600, snr=25.0, sde=30.0, n_events=2300)
    r = Rung3Stability().run(s, bundle(lc))
    assert r.status == TestStatus.PASS and r.metrics["stacked"] is True and len(r.metrics["season_depths"]) == 4
    json.dumps(r.metrics, allow_nan=False)


def test_stacked_seasons_catch_a_half_depth_season():
    """Season 2 carries a 300 ppm transit against 600 ppm elsewhere; the quarter offsets are left in place."""
    lc = make_lightcurve(n_days=1400, cadence_min=30, noise_ppm=500, seed=37)
    dip = np.abs(fold_phase(lc.time, 0.6, 132.0)) < 0.5 / 24
    depth = np.where(season_of(lc.quarter) == 2, 300e-6, 600e-6)
    f = lc.flux.copy()
    f[dip] *= 1 - depth[dip]
    lc = LightCurve(lc.time, f, lc.flux_err, lc.quality, lc.quarter, f.copy())
    s = Signal(target_id="t", detector="box_dip", detector_version="1", period=0.6, t0=132.0, duration=1 / 24,
               depth_ppm=600, snr=25.0, sde=30.0, n_events=2300)
    r = Rung3Stability().run(s, bundle(lc))
    assert r.status == TestStatus.FAIL and r.metrics["stacked"] is True
    assert abs(r.metrics["season_depths"]["2"] - 300) < 80 and abs(r.metrics["season_z"]["2"]) > 4


def test_stacked_seasons_stay_undetermined_when_fewer_than_three_seasons_are_measurable():
    """150 days cover quarters 1 and 2 only, so two seasons are present."""
    lc = inject_transit(make_lightcurve(n_days=150, cadence_min=30, noise_ppm=500, seed=38), 0.6, 132.0, 1 / 24, 600)
    s = Signal(target_id="t", detector="box_dip", detector_version="1", period=0.6, t0=132.0, duration=1 / 24,
               depth_ppm=600, snr=25.0, sde=30.0, n_events=300)
    r = Rung3Stability().run(s, bundle(lc))
    assert r.status == TestStatus.UNDETERMINED and r.reason == "too few events"
    assert r.metrics["stacked"] is False and r.metrics["n_events"] > 0
    json.dumps(r.metrics, allow_nan=False)
