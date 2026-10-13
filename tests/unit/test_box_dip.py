import numpy as np

from skysift.config import Thresholds
from skysift.detectors.box_dip import BoxDip
from skysift.lc.ops import detrend
from skysift.models import LightCurve
from tests.synthetic import inject_transit, make_lightcurve


def test_recovers_injected_transit():
    lc = inject_transit(make_lightcurve(seed=1), 13.769, 136.3, 0.09, 2000)
    sig = BoxDip().detect(lc, detrend(lc), Thresholds())
    assert sig and abs(sig[0].period - 13.769) < 0.002 and sig[0].sde > 9 and sig[0].n_events >= 60
    assert 1500 < sig[0].depth_ppm < 2500
    assert sig[0].snr > Thresholds().snr_min


def test_pure_noise_stays_below_threshold():
    lc = make_lightcurve(seed=7)
    sig = BoxDip().detect(lc, detrend(lc), Thresholds())
    assert all(s.sde < 9 for s in sig)


def test_short_or_empty_lightcurve_returns_nothing():
    lc = make_lightcurve(n_days=5, seed=2)
    assert BoxDip().detect(lc, detrend(lc), Thresholds()) == []
    nan_lc = LightCurve(np.arange(1000.0), np.full(1000, np.nan), np.ones(1000), np.zeros(1000, int), np.ones(1000, int))
    assert BoxDip().detect(nan_lc, detrend(nan_lc), Thresholds()) == []
