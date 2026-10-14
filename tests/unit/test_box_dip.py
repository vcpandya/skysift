import itertools
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
    assert all(a.sde >= b.sde for a, b in itertools.pairwise(sig))


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


def test_min_period_below_the_shortest_trial_duration_does_not_crash():
    # 1 h is the shortest trial duration: a 0.03 d minimum period used to hand astropy duration >= period
    lc = inject_transit(make_lightcurve(n_days=15, seed=2, noise_ppm=500), 0.7, 132.0, 0.05, 3000)
    sig = BoxDip().detect(lc, detrend(lc), Thresholds(min_period_days=0.03, max_period_days=1.0))
    assert sig and all(s.duration < 0.9 * s.period for s in sig)
    assert any(abs(s.period / 0.7 - round(s.period / 0.7)) < 0.01 for s in sig)


WIDE = Thresholds(min_period_days=0.5, max_period_days=15.0)


def _binary(with_planet: bool, seed: int = 3):
    """A 0.7 d eclipsing binary (3000 ppm primary, 1500 ppm secondary at phase 0.5) on a 300 d curve, optionally with
    a 9.9 d, 900 ppm, 2.5 h transit on top: the real case of gaia-dr3-2075988698983795072 and KOI K04209.01."""
    lc = make_lightcurve(n_days=300, seed=seed, noise_ppm=500)
    lc = inject_transit(lc, 0.7, 131.9, 3 / 24, 3000)
    lc = inject_transit(lc, 0.7, 131.9 + 0.35, 3 / 24, 1500)
    return inject_transit(lc, 9.9, 133.1, 2.5 / 24, 900) if with_planet else lc


def test_second_pass_recovers_a_planet_hidden_behind_a_dominant_binary():
    lc = _binary(with_planet=True)
    sig = BoxDip().detect(lc, detrend(lc), WIDE)
    first = [s for s in sig if s.extra.get("pass", 1) == 1]
    second = [s for s in sig if s.extra.get("pass") == 2]
    assert first and abs(first[0].period - 0.7) < 0.002 and first[0].sde >= 9
    assert all(abs(s.period - 9.9) > 0.05 for s in first), "the binary's aliases fill the first pass"
    assert len(second) == 1
    s2 = second[0]
    assert abs(s2.period - 9.9) < 0.02 and s2.sde >= 9
    assert abs(s2.depth_ppm - 900) < 0.3 * 900
    assert abs(s2.extra["masked_period"] - first[0].period) < 1e-9 and s2.extra["alias_of"] is None
    assert sig.index(s2) == len(sig) - 1, "second-pass signals come after every first-pass peak"


def test_second_pass_adds_nothing_when_only_the_binary_is_there():
    lc = _binary(with_planet=False)
    sig = BoxDip().detect(lc, detrend(lc), WIDE)
    assert sig and abs(sig[0].period - 0.7) < 0.002
    assert not [s for s in sig if s.extra.get("pass") == 2]


def test_flat_curve_runs_a_single_pass(monkeypatch):
    det = BoxDip()
    calls = []
    orig = det._coarse_search
    monkeypatch.setattr(det, "_coarse_search", lambda *a, **k: (calls.append(1), orig(*a, **k))[1])
    sig = det.detect(make_lightcurve(n_days=300, seed=7, noise_ppm=500), detrend(make_lightcurve(n_days=300, seed=7, noise_ppm=500)), WIDE)
    assert sig and all(s.sde < 9 for s in sig)
    assert len(calls) == 1
    assert BoxDip(mask_iterations=0).mask_iterations == 0


def test_is_alias_covers_small_integer_ratios():
    from skysift.detectors.box_dip import _is_alias
    earlier = [0.7]
    assert _is_alias(1.4, earlier) and _is_alias(0.35, earlier) and _is_alias(1.05, earlier) and _is_alias(0.7 * 4 / 3, earlier)
    assert _is_alias(0.7 * 1.004, earlier)
    assert not _is_alias(9.9, earlier) and not _is_alias(0.7 * 5 / 4, earlier) and not _is_alias(0.7 * 1.02, earlier)


def test_is_alias_catches_combination_frequencies_of_a_multiperiodic_variable():
    """The real sidelobe: f3 = 2 f1 - f2 for the 0.6355 and 0.7216 d peaks of gaia-dr3-2075988698983795072, matched
    only to the spectral resolution of the baseline, so an unrelated 9.9 d planet is never caught by it."""
    from skysift.detectors.box_dip import _is_alias
    earlier = [0.6354676, 0.7216462]
    f_side = 2 / earlier[0] - 1 / earlier[1]
    assert _is_alias(1 / f_side, earlier, baseline=1430.0)
    assert _is_alias(1 / (1 / earlier[0] + 1 / earlier[1]), earlier, baseline=1430.0)
    assert not _is_alias(1 / f_side, earlier)                       # without a baseline only p/q ratios count
    assert not _is_alias(1 / (f_side + 0.01), earlier, baseline=1430.0)
    assert not _is_alias(9.909, earlier, baseline=1430.0) and not _is_alias(9.909, earlier[:1], baseline=1430.0)
