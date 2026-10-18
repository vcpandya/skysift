import json

import numpy as np
import pytest

from skysift.config import Thresholds
from skysift.lc.ops import detrend, fold_phase, season_of
from skysift.models import LightCurve, Signal, Target, TestStatus
from skysift.vetting.base import DataBundle
from skysift.vetting.rung1_detection import Rung1Detection
from skysift.vetting.rung2_shape import Rung2Shape
from skysift.vetting.rung3_stability import Rung3Stability
from tests.synthetic import inject_model, inject_transit, inject_v_eclipse, make_lightcurve

P, T0, DUR = 13.769, 136.3, 0.09
TGT = Target(source="test", target_id="t", ra=0, dec=0)


def bundle(lc):
    return DataBundle(lc=lc, fnorm=detrend(lc), fnorm_alt=detrend(lc, flux=lc.flux_alt), target=TGT, thresholds=Thresholds())


def in_both_fluxes(lc):
    """``lc`` with its flux copied into ``flux_alt``: a transit injected into ``flux`` shows in the PSF flux too, as a
    real one on the target does (``inject_transit`` leaves ``flux_alt`` untouched)."""
    return LightCurve(lc.time, lc.flux, lc.flux_err, lc.quality, lc.quarter, lc.flux.copy(), dict(lc.meta))


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


def test_rung3_flags_season_dependence():
    lc = make_lightcurve(seed=4, noise_ppm=800)
    season3 = (lc.quarter % 4) == 3
    f = lc.flux.copy()
    ph = (lc.time - T0 + 0.5 * P) % P - 0.5 * P
    f[(np.abs(ph) < DUR / 2) & season3] *= 1 - 12000e-6
    f[(np.abs(ph) < DUR / 2) & ~season3] *= 1 - 500e-6
    lc2 = LightCurve(lc.time, f, lc.flux_err, lc.quality, lc.quarter, lc.flux_alt)
    s = signal(3000)
    r = Rung3Stability().run(s, bundle(lc2))
    assert r.status == TestStatus.UNDETERMINED and "season" in r.reason
    good = in_both_fluxes(inject_transit(make_lightcurve(seed=4, noise_ppm=800), P, T0, DUR, 2000))
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


def test_rung3_single_deviant_season_flags_with_one_outlier():
    lc = inject_transit(make_lightcurve(seed=4, noise_ppm=800), P, T0, DUR, 2000)
    season3 = (lc.quarter % 4) == 3
    ph = (lc.time - T0 + 0.5 * P) % P - 0.5 * P
    f = lc.flux.copy()
    f[(np.abs(ph) < DUR / 2) & season3] *= 1 - 1000e-6     # 3000 ppm in season 3, 2000 ppm elsewhere
    lc2 = LightCurve(lc.time, f, lc.flux_err, lc.quality, lc.quarter, lc.flux_alt)
    r = Rung3Stability().run(signal(3000), bundle(lc2))
    assert r.status == TestStatus.UNDETERMINED and "season" in r.reason
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
    lc = in_both_fluxes(inject_transit(make_lightcurve(n_days=1400, cadence_min=30, noise_ppm=500, seed=35), 0.6, 132.0, 1 / 24, 600))
    s = Signal(target_id="t", detector="box_dip", detector_version="1", period=0.6, t0=132.0, duration=1 / 24,
               depth_ppm=600, snr=25.0, sde=30.0, n_events=2300)
    r = Rung3Stability().run(s, bundle(lc))
    assert r.status == TestStatus.PASS and r.metrics["stacked"] is True and len(r.metrics["season_depths"]) == 4
    json.dumps(r.metrics, allow_nan=False)


def test_stacked_seasons_flag_a_half_depth_season():
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
    assert r.status == TestStatus.UNDETERMINED and "season" in r.reason and r.metrics["stacked"] is True
    assert abs(r.metrics["season_depths"]["2"] - 300) < 80 and abs(r.metrics["season_z"]["2"]) > 4


def test_stacked_seasons_stay_undetermined_when_fewer_than_three_seasons_are_measurable():
    """150 days cover quarters 1 and 2 only, so two seasons are present."""
    lc = inject_transit(make_lightcurve(n_days=150, cadence_min=30, noise_ppm=500, seed=38), 0.6, 132.0, 1 / 24, 600)
    s = Signal(target_id="t", detector="box_dip", detector_version="1", period=0.6, t0=132.0, duration=1 / 24,
               depth_ppm=600, snr=25.0, sde=30.0, n_events=300)
    r = Rung3Stability().run(s, bundle(lc))
    assert r.status == TestStatus.UNDETERMINED and r.reason == "too few seasons"
    assert r.metrics["stacked"] is True and r.metrics["n_events"] > 0 and r.metrics["n_seasons"] == 2
    json.dumps(r.metrics, allow_nan=False)


def test_per_event_seasons_stay_undetermined_when_fewer_than_three_seasons_are_measured():
    """The per-event path used to pass two seasons vacuously (one leave-one-out z per season, never three to fail)."""
    lc = inject_transit(make_lightcurve(n_days=150, noise_ppm=500, seed=39), P, T0, 0.2, 2000)
    s = signal(2000).model_copy(update={"duration": 0.2, "n_events": 10})
    r = Rung3Stability().run(s, bundle(lc))
    assert r.status == TestStatus.UNDETERMINED and r.reason == "too few seasons"
    assert r.metrics["stacked"] is False and r.metrics["n_events"] >= 4 and r.metrics["n_seasons"] == 2
    json.dumps(r.metrics, allow_nan=False)


def _with_alt_box(depth_ppm, alt_depth_ppm, seed=23):
    """A light curve with a 3.3 d, 3 h box of ``depth_ppm`` in ``flux`` and ``alt_depth_ppm`` in ``flux_alt``."""
    lc = make_lightcurve(seed=seed)
    primary = inject_transit(lc, 3.3, 135.0, 3 / 24, depth_ppm)
    alt = inject_transit(lc, 3.3, 135.0, 3 / 24, alt_depth_ppm).flux
    return LightCurve(primary.time, primary.flux, primary.flux_err, primary.quality, primary.quarter, alt, dict(primary.meta))


def _signal_3_3(depth_ppm):
    return Signal(target_id="t", detector="box_dip", detector_version="1", period=3.3, t0=135.0, duration=3 / 24,
                  depth_ppm=depth_ppm, snr=20.0, sde=15.0, n_events=420)


def test_rung3_flags_an_aperture_dependent_depth():
    """4000 ppm in the SAP flux but only 1000 ppm in the PSF flux: the signal does not follow the target's aperture."""
    r = Rung3Stability().run(_signal_3_3(4000), bundle(_with_alt_box(4000, 1000)))
    assert r.status == TestStatus.UNDETERMINED and r.reason.startswith("aperture-dependent"), (r.status, r.reason)
    assert abs(r.metrics["aperture_z"]) > 3 and r.metrics["aperture_ratio"] == pytest.approx(0.25, abs=0.02)
    json.dumps(r.metrics, allow_nan=False)


def test_rung3_passes_equal_depths_in_both_fluxes():
    """The noise is identical in both fluxes (``make_lightcurve`` copies it), so z is exactly 0: this shows the gate stays
    quiet, not that the noise scaling of z is sane."""
    r = Rung3Stability().run(_signal_3_3(4000), bundle(_with_alt_box(4000, 4000)))
    assert r.status == TestStatus.PASS, (r.status, r.reason)
    assert abs(r.metrics["aperture_z"]) < 3 and r.metrics["alt_depth_err_ppm"] > 0
    json.dumps(r.metrics, allow_nan=False)


def test_rung3_tolerates_a_mild_aperture_depth_difference():
    """3000 ppm in the PSF flux against 4000 ppm in the SAP flux: far beyond 3 sigma here, but a ratio of 0.75 is within
    the dilution difference of two apertures."""
    r = Rung3Stability().run(_signal_3_3(4000), bundle(_with_alt_box(4000, 3000)))
    assert abs(r.metrics["aperture_z"]) > 3 and r.metrics["aperture_ratio"] == pytest.approx(0.75, abs=0.02)
    assert r.status == TestStatus.PASS, (r.status, r.reason)


def test_rung3_aperture_z_is_none_without_a_psf_flux():
    b = bundle(_with_alt_box(4000, 4000))
    b.fnorm_alt = None
    r = Rung3Stability().run(_signal_3_3(4000), b)
    assert r.status == TestStatus.PASS and r.metrics["aperture_z"] is None and r.metrics["alt_depth_ppm"] is None
    assert r.metrics["aperture_ratio"] is None


# ---- rung 2 against the Kepler DR25 Robovetter (Thompson et al. 2018): uniqueness, SWEET, transit fit, exceptions

T0_R2 = 133.0       # a multiple of 0.7 d, so the 0.7 d wave of the SWEET EB fixture is orthogonal to the box


R2_NEW_METRICS = ("sigma_pri", "sigma_sec", "phase_sec", "sigma_ter", "sigma_pos", "fa2", "ms2", "ms3", "fit_ok",
                  "fit_k", "fit_b", "fit_a_over_r", "fit_depth_ppm", "fit_t14_h", "fit_chi2", "sweet_factor",
                  "sweet_amplitude_ppm", "sweet_snr", "suggested_period", "occultation_albedo",
                  "planet_radius_rearth", "secondary_difference_sigma", "occultation_exception")


def _assert_new_metrics_strict(r):
    """The metrics this rung added are None or finite (strict JSON), never NaN."""
    keys = [k for k in R2_NEW_METRICS if k in r.metrics]
    json.dumps({k: r.metrics[k] for k in keys}, allow_nan=False)


def _signal_r2(period, duration, depth_ppm, t0=T0_R2):
    return Signal(target_id="t", detector="box_dip", detector_version="1", period=period, t0=t0, duration=duration,
                  depth_ppm=depth_ppm, snr=30.0, sde=20.0, n_events=100)


def test_rung2_fails_three_equal_dips_as_not_unique():
    """A 4.1 d, 3 h, 1500 ppm box plus two more dips at phases 0.37 and 0.71: the fold holds a tertiary event at least
    as significant as the primary, so the detection is not unique (MS2 compares the primary with the tertiary; a lone
    second dip is the secondary rules' case). The two extra dips are 1800 ppm, not 1500: with three exactly equal dips
    MS2 = FA2 - (sigma_pri - sigma_ter) sits at FA2 (2.16) minus the noise of the difference, which straddles the 2.0
    threshold (the tertiary is the weaker of the two extra dips), so an equal-depth fixture is a coin toss."""
    lc = make_lightcurve(seed=19, noise_ppm=600)
    lc = inject_transit(lc, 4.1, T0_R2, 3 / 24, 1500)
    for phase in (0.37, 0.71):
        lc = inject_transit(lc, 4.1, T0_R2 + phase * 4.1, 3 / 24, 1800)
    r = Rung2Shape().run(_signal_r2(4.1, 3 / 24, 1500), bundle(lc))
    assert r.status == TestStatus.FAIL and r.reason.startswith("not unique"), (r.status, r.reason)
    assert r.metrics["ms2"] > 2.0 and r.metrics["sigma_ter"] is not None
    _assert_new_metrics_strict(r)


def test_rung2_sweet_fails_a_short_period_wave_removed_by_detrending():
    """The detrended flux before pre-whitening carries a 3000 ppm wave at the 2.3 d period that the whitened flux no
    longer shows: SWEET fails the signal as sinusoidal (the wave is larger than the 1500 ppm dip)."""
    lc = inject_transit(make_lightcurve(seed=19, noise_ppm=600), 2.3, T0_R2, 2 / 24, 1500)
    b = bundle(lc)
    b.flux_detrended = b.fnorm * (1 + 3e-3 * np.sin(2 * np.pi * lc.time / 2.3))
    r = Rung2Shape().run(_signal_r2(2.3, 2 / 24, 1500), b)
    assert r.status == TestStatus.FAIL and r.reason.startswith("sinusoidal (SWEET"), (r.status, r.reason)
    assert r.metrics["sweet_amplitude_ppm"] > r.metrics["depth_ppm"] and r.metrics["sweet_snr"] > 50
    _assert_new_metrics_strict(r)
    # without the detrended flux SWEET is skipped and the planet passes
    b.flux_detrended = None
    r = Rung2Shape().run(_signal_r2(2.3, 2 / 24, 1500), b)
    assert r.status == TestStatus.PASS and r.metrics["sweet_snr"] is None, (r.status, r.reason)


def test_rung2_sweet_eb_fails_ellipsoidal_variation():
    """A 6000 ppm, 3 h box at 1.4 d on a 5500 ppm wave at half the period (the ellipsoidal variation of a close
    binary): the wave is smaller than the eclipse but above 5000 ppm, so SWEET fails it as ellipsoidal."""
    lc = inject_transit(make_lightcurve(seed=19, noise_ppm=600), 1.4, T0_R2, 3 / 24, 6000)
    b = bundle(lc)
    b.flux_detrended = b.fnorm * (1 + 5.5e-3 * np.sin(2 * np.pi * lc.time / 0.7))
    r = Rung2Shape().run(_signal_r2(1.4, 3 / 24, 6000), b)
    assert r.status == TestStatus.FAIL and r.reason.startswith("ellipsoidal"), (r.status, r.reason)
    assert r.metrics["sweet_factor"] == 0.5 and 5000 < r.metrics["sweet_amplitude_ppm"] < r.metrics["depth_ppm"]
    _assert_new_metrics_strict(r)


def test_rung2_halves_the_period_of_equal_eclipses():
    """A 3.0 d train of equal 4000 ppm, 3 h transits vetted at 6.0 d: the fold shows an equal secondary at phase 0.5
    (the odd transits) and equal odd and even depths, so the signal may be a planet detected at twice its true period
    (T18 A.4.1.3), both dips being its transits: undetermined, with P / 2 = 3.0 d suggested."""
    lc = inject_transit(make_lightcurve(seed=19, noise_ppm=600), 3.0, T0_R2, 3 / 24, 4000)
    r = Rung2Shape().run(_signal_r2(6.0, 3 / 24, 4000), bundle(lc))
    assert r.status == TestStatus.UNDETERMINED, (r.status, r.reason)
    assert r.reason == "equal secondary at phase 0.5: true period may be P / 2 (3 d)", r.reason
    assert abs(r.metrics["suggested_period"] - 3.0) < 1e-6 and r.metrics["secondary_difference_sigma"] < r.metrics["fa2"]
    _assert_new_metrics_strict(r)


def _t14_days(period, k, b, a_over_r):
    return period / np.pi * np.arcsin(np.sqrt((1 + k) ** 2 - b * b) / a_over_r)


def _host(mass, radius):
    return Target(source="test", target_id="t", ra=0, dec=0, meta={"radius": radius, "mass": mass})


A_OVER_R_SUN_2P2 = 7.118    # a / R_star of a 2.2 d orbit around a 1 M_sun, 1 R_sun star (Kepler's third law)


def test_rung2_keeps_a_planet_with_a_shallow_occultation():
    """A close-in giant (k 0.3, b 0.3, a/R* 3, 2.2 d) on a 0.2 M_sun, 0.2 R_sun host with a 60 ppm occultation at
    phase 0.5: the secondary is significant, but R_p = 6.5 R_earth and the geometric albedo it implies,
    A_g = d_sec (a / R_p)^2 with d_sec fractional, is about 0.36 (the detrend reads the secondary as 75 ppm), so it is
    the planet's own occultation, not a stellar eclipse."""
    t14 = _t14_days(2.2, 0.3, 0.3, 3.0)
    lc = inject_model(make_lightcurve(n_days=1500, noise_ppm=100, seed=29), 2.2, T0_R2, 0.3, 0.3, 3.0)
    lc = inject_transit(lc, 2.2, T0_R2 + 1.1, t14, 60)
    b = bundle(lc)
    b.target = _host(0.2, 0.2)
    r = Rung2Shape().run(_signal_r2(2.2, t14, 90000), b)
    assert r.status == TestStatus.PASS and r.reason == "secondary consistent with planet occultation", (r.status, r.reason)
    assert r.metrics["secondary_sigma"] > 3 and 0.2 < r.metrics["occultation_albedo"] < 0.6
    assert abs(r.metrics["planet_radius_rearth"] - 6.55) < 0.5 and r.metrics["fit_ok"] is True
    assert r.metrics["occultation_exception"] is True
    _assert_new_metrics_strict(r)


def test_rung2_fails_an_occultation_too_bright_for_a_planet():
    """A 1 percent transit (k 0.1, b 0.3) at 2.2 d on a 1 M_sun, 1 R_sun star with a 300 ppm secondary: R_p = 11
    R_earth, the secondary is well under 10 percent of the transit and b is small, but reflected light would need a
    geometric albedo of about 1.6, so the secondary is not the planet's occultation and the signal fails."""
    t14 = _t14_days(2.2, 0.1, 0.3, A_OVER_R_SUN_2P2)
    lc = inject_model(make_lightcurve(n_days=1500, noise_ppm=100, seed=29), 2.2, T0_R2, 0.1, 0.3, A_OVER_R_SUN_2P2)
    lc = inject_transit(lc, 2.2, T0_R2 + 1.1, t14, 300)
    b = bundle(lc)
    b.target = _host(1.0, 1.0)
    r = Rung2Shape().run(_signal_r2(2.2, t14, 10000), b)
    assert r.status == TestStatus.FAIL and r.reason.startswith("secondary_at_0.5"), (r.status, r.reason)
    assert r.metrics["occultation_albedo"] > 1 and r.metrics["planet_radius_rearth"] < 30
    assert r.metrics["occultation_exception"] is False
    _assert_new_metrics_strict(r)


def test_rung2_fails_a_stellar_secondary():
    """An eclipsing binary with 50 percent primary and 5 percent secondary eclipses (3 h boxes, 2.2 d) on a 1 R_sun
    star: the fit's companion is far larger than 30 R_earth and grazing (k + b at the bound), so the occultation
    exception does not apply, whatever albedo the fractional secondary depth implies (about 0.6 here)."""
    lc = inject_transit(make_lightcurve(seed=19, noise_ppm=600), 2.2, T0_R2, 3 / 24, 500000)
    lc = inject_transit(lc, 2.2, T0_R2 + 1.1, 3 / 24, 50000)
    b = bundle(lc)
    b.target = _host(1.0, 1.0)
    r = Rung2Shape().run(_signal_r2(2.2, 3 / 24, 500000), b)
    assert r.status == TestStatus.FAIL and r.reason.startswith("secondary_at_0.5"), (r.status, r.reason)
    assert r.metrics["planet_radius_rearth"] > 30 and r.metrics["fit_b"] > 0.95
    assert r.metrics["occultation_exception"] is False
    _assert_new_metrics_strict(r)


def test_rung2_fails_a_v_shaped_eclipse():
    """Equal 30000 ppm V-shaped eclipses, 6 h wide, at 9.0 d with no secondary: odd/even and the secondary pass, and
    the transit fit puts k + b above 1.04 (the grazing geometry of an eclipsing binary)."""
    lc = inject_v_eclipse(make_lightcurve(seed=19, noise_ppm=600), 9.0, T0_R2, 6 / 24, 30000)
    r = Rung2Shape().run(_signal_r2(9.0, 6 / 24, 30000), bundle(lc))
    assert r.status == TestStatus.FAIL and r.reason.startswith("V-shaped"), (r.status, r.reason)
    assert r.metrics["fit_k"] + r.metrics["fit_b"] > 1.04 and r.metrics["parity_sigma"] < 3
    _assert_new_metrics_strict(r)


def test_rung2_reports_the_new_metrics_for_a_clean_planet():
    lc = inject_transit(make_lightcurve(seed=2, noise_ppm=800), P, T0, DUR, 2000)
    r = Rung2Shape().run(signal(2000), bundle(lc))
    assert r.status == TestStatus.PASS and r.reason == "", (r.status, r.reason)
    for key in ("sigma_pri", "sigma_sec", "phase_sec", "sigma_ter", "sigma_pos", "fa2", "ms2", "ms3", "fit_ok", "fit_k",
                "fit_b", "fit_a_over_r", "fit_depth_ppm", "fit_t14_h", "fit_chi2", "sweet_factor",
                "sweet_amplitude_ppm", "sweet_snr"):
        assert key in r.metrics, key
    assert r.metrics["ms2"] < 2 and r.metrics["ms3"] < 4 and r.metrics["fit_k"] + r.metrics["fit_b"] < 1.04
    _assert_new_metrics_strict(r)
