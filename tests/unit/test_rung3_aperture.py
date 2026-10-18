"""Rung 3 aperture-dependent depth: the stacked path, both sides of the ratio window, and precedence over the season verdict."""
import json

import numpy as np
import pytest

from skysift.models import LightCurve, Signal, TestStatus
from skysift.vetting.rung3_stability import Rung3Stability
from tests.synthetic import inject_transit, make_lightcurve
from tests.unit.test_rungs_1_3 import DUR, T0, P, bundle, signal


def _alt_box(depth_ppm, alt_depth_ppm, seed=23, period=3.3, t0=135.0, duration=3 / 24, **lc_kw):
    """A light curve with a box of ``depth_ppm`` in ``flux`` and ``alt_depth_ppm`` in ``flux_alt``. The noise is the same
    in both fluxes (``make_lightcurve`` copies it)."""
    lc = make_lightcurve(seed=seed, **lc_kw)
    primary = inject_transit(lc, period, t0, duration, depth_ppm)
    alt = inject_transit(lc, period, t0, duration, alt_depth_ppm).flux
    return LightCurve(primary.time, primary.flux, primary.flux_err, primary.quality, primary.quarter, alt, dict(primary.meta))


def _signal(period, t0, duration, depth_ppm):
    return Signal(target_id="t", detector="box_dip", detector_version="1", period=period, t0=t0, duration=duration,
                  depth_ppm=depth_ppm, snr=25.0, sde=30.0, n_events=1000)


def test_flags_an_aperture_dependent_depth_on_the_stacked_path():
    """A 0.7 d, 1 h box holds about two points per event at 30-minute cadence: the stacked fold carries the check."""
    lc = _alt_box(4000, 1000, seed=41, period=0.7, t0=132.0, duration=1 / 24, n_days=1400, cadence_min=30, noise_ppm=500)
    r = Rung3Stability().run(_signal(0.7, 132.0, 1 / 24, 4000), bundle(lc))
    assert r.metrics["stacked"] is True
    assert r.status == TestStatus.UNDETERMINED and r.reason.startswith("aperture-dependent"), (r.status, r.reason)
    assert abs(r.metrics["aperture_z"]) > 3 and r.metrics["aperture_ratio"] == pytest.approx(0.25, abs=0.05)
    json.dumps(r.metrics, allow_nan=False)


def test_flags_a_psf_depth_more_than_twice_the_sap_depth():
    """The upper bound of the ratio window: 4000 ppm in the PSF flux against 1000 ppm in the SAP flux."""
    r = Rung3Stability().run(_signal(3.3, 135.0, 3 / 24, 1000), bundle(_alt_box(1000, 4000)))
    assert r.status == TestStatus.UNDETERMINED and r.reason.startswith("aperture-dependent"), (r.status, r.reason)
    assert r.metrics["aperture_z"] > 3 and r.metrics["aperture_ratio"] == pytest.approx(4.0, abs=0.2)


def test_reports_the_season_before_the_aperture_when_both_fire():
    lc = make_lightcurve(seed=4, noise_ppm=800)          # flux_alt carries no transit at all
    season3 = (lc.quarter % 4) == 3
    f = lc.flux.copy()
    ph = (lc.time - T0 + 0.5 * P) % P - 0.5 * P
    f[(np.abs(ph) < DUR / 2) & season3] *= 1 - 12000e-6
    f[(np.abs(ph) < DUR / 2) & ~season3] *= 1 - 500e-6
    lc2 = LightCurve(lc.time, f, lc.flux_err, lc.quality, lc.quarter, lc.flux_alt)
    r = Rung3Stability().run(signal(3000), bundle(lc2))
    assert abs(r.metrics["aperture_z"]) > 3 and not 0.5 <= r.metrics["aperture_ratio"] <= 2.0     # the aperture gate fires too
    assert r.status == TestStatus.UNDETERMINED and r.reason.startswith("season-dependent")
