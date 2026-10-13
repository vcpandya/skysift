import numpy as np

from skysift.config import Thresholds
from skysift.lc.ops import detrend
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
