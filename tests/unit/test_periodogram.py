import json

import numpy as np

from skysift import jsonutil
from skysift.config import Thresholds
from skysift.detectors.box_dip import BoxDip
from skysift.detectors.periodogram import PeriodogramSummary
from skysift.lc.ops import detrend
from tests.synthetic import inject_transit, make_lightcurve


def test_envelope_has_gaps_for_empty_bins_and_round_trips():
    periods = np.array([0.6, 0.61, 5.0, 5.1, 150.0]); sde = np.array([3.0, 7.5, 2.0, 9.9, 4.0])
    ps = PeriodogramSummary.from_spectrum(periods, sde, 0.5, 200.0, band_edges=[0.5, 10.0, 200.0],
                                          peaks=[{"period": 5.1, "sde": 9.9, "duration": 0.1, "rank": 0}], n_bins=64)
    env = np.array(ps.envelope, dtype=float)
    assert env.size == 64 and np.isnan(env).sum() > 50                     # most bins hold no trial period
    centres = ps.bin_periods()
    assert np.nanmax(env) == 9.9 and abs(centres[int(np.nanargmax(env))] - 5.1) / 5.1 < 0.1
    assert env[np.searchsorted(centres, 0.61) - 1] == 7.5 or env[np.searchsorted(centres, 0.61)] == 7.5   # max per bin
    back = PeriodogramSummary.from_npz(ps.to_npz())
    assert back.peaks == ps.peaks and back.n_bins == 64 and np.allclose(np.array(back.envelope, float), env, equal_nan=True)
    assert len(ps.to_npz()) < 16_000
    text = jsonutil.dumps(ps.model_dump())
    assert "NaN" not in text and json.loads(text)["envelope"][0] is None


def test_box_dip_search_returns_signals_and_envelope():
    lc = inject_transit(make_lightcurve(n_days=500, seed=5, noise_ppm=600), 12.5, 140.0, 0.12, 2000)
    th = Thresholds(min_period_days=5, max_period_days=40)
    res = BoxDip().search(lc, detrend(lc), th)
    assert res.signals and abs(res.signals[0].period - 12.5) / 12.5 < 0.01 and res.spectrum is None
    ps = res.periodogram
    assert ps is not None and ps.n_bins == 2048 and (ps.min_period, ps.max_period) == (5.0, 40.0)
    assert ps.peaks[0]["period"] == res.signals[0].period and ps.peaks[0]["sde"] == res.signals[0].sde
    env = np.array(ps.envelope, float)
    assert abs(np.nanmax(env) - res.signals[0].sde) < 0.2 * res.signals[0].sde   # refinement moves the peak a little
    assert ps.n_periods == res.signals[0].extra["n_periods"] and len(ps.band_edges) == 25
    assert BoxDip().detect(lc, detrend(lc), th)[0].period == res.signals[0].period
    with_spec = BoxDip().search(lc, detrend(lc), th, keep_spectrum=True)
    assert set(with_spec.spectrum) == {"period", "power", "sde", "duration"} and len(with_spec.spectrum["period"]) == ps.n_periods


def test_search_on_too_short_curve_has_no_periodogram():
    lc = make_lightcurve(n_days=3, seed=1)
    res = BoxDip().search(lc, detrend(lc), Thresholds(min_period_days=5, max_period_days=40))
    assert res.signals == [] and res.periodogram is None
