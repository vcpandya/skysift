import time

import numpy as np
import pytest

from skysift.config import Thresholds
from skysift.detectors.box_dip import BoxDip
from skysift.lc.ops import detrend
from skysift.models import LightCurve
from tests.synthetic import inject_transit, make_lightcurve

NARROW = Thresholds(min_period_days=5.0, max_period_days=40.0)


def test_recovers_injected_transit():
    lc = inject_transit(make_lightcurve(n_days=500, seed=1), 13.769, 136.3, 0.09, 2000)
    sig = BoxDip().detect(lc, detrend(lc), NARROW)
    assert sig and abs(sig[0].period - 13.769) < 0.002 and sig[0].sde > 9 and sig[0].n_events >= 30
    assert 1500 < sig[0].depth_ppm < 2500
    assert sig[0].snr > Thresholds().snr_min
    assert sig[0].extra["alias_of"] is None and sig[0].extra["n_periods"] > 0
    coarse = BoxDip(two_stage=False).detect(lc, detrend(lc), NARROW)
    assert coarse and abs(coarse[0].period - sig[0].period) < 0.002 and coarse[0].sde > 9
    assert any(s.extra["alias_of"] == 0 for s in sig[1:])
    assert all(isinstance(s.extra["band"], int) and s.extra["sde_spectrum_std"] > 0 for s in sig)
    for s in sig:
        if s.extra["alias_of"] == 0:
            ratio = s.period / sig[0].period
            assert any(abs(ratio / r - 1) < 0.01 for r in (2, 3, 0.5, 1 / 3))


def test_recovers_deep_transit():
    lc = inject_transit(make_lightcurve(n_days=500, seed=4), 13.769, 136.3, 0.09, 20000)
    sig = BoxDip().detect(lc, detrend(lc), NARROW)
    assert sig and abs(sig[0].period - 13.769) < 0.002
    assert abs(sig[0].depth_ppm - 20000) < 0.15 * 20000 and sig[0].n_events >= 30


@pytest.mark.parametrize("seed", [7, 11, 21])
def test_pure_noise_stays_below_threshold(seed):
    lc = make_lightcurve(n_days=500, seed=seed)
    sig = BoxDip().detect(lc, detrend(lc), NARROW)
    assert all(s.sde < 9 for s in sig)


def test_short_or_empty_lightcurve_returns_nothing():
    lc = make_lightcurve(n_days=5, seed=2)
    assert BoxDip().detect(lc, detrend(lc), Thresholds()) == []
    nan_lc = LightCurve(np.arange(1000.0), np.full(1000, np.nan), np.ones(1000), np.zeros(1000, int), np.ones(1000, int))
    assert BoxDip().detect(nan_lc, detrend(nan_lc), Thresholds()) == []
    flat = LightCurve(np.arange(1000.0), np.ones(1000), np.ones(1000), np.zeros(1000, int), np.ones(1000, int))
    assert BoxDip().detect(flat, np.ones(1000), Thresholds()) == []


@pytest.mark.slow
def test_full_baseline_timing():
    """Full 1400 d curve over the default period window (~240 s); deselected by default, run with `-m slow`."""
    lc = inject_transit(make_lightcurve(seed=1), 13.769, 136.3, 0.09, 2000)
    fn = detrend(lc)
    t0 = time.perf_counter()
    sig = BoxDip().detect(lc, fn, Thresholds())
    elapsed = time.perf_counter() - t0
    assert sig and abs(sig[0].period - 13.769) < 0.002 and sig[0].sde > 9
    assert elapsed < 400
