import numpy as np

from skysift.lc.ops import detrend
from skysift.lc.transit_model import TransitFit, fit_transit, uniform_disc_flux
from tests.synthetic import inject_model, inject_v_eclipse, make_lightcurve


def _t14(period, k, b, a_over_r):
    return period / np.pi * np.arcsin(np.sqrt((1 + k) ** 2 - b ** 2) / a_over_r)


def test_uniform_disc_flux_limits():
    period = 3.0
    assert abs(float(uniform_disc_flux(np.array([0.0]), period, 0.1, 0.0, 20.0)[0]) - 0.99) < 1e-9
    assert float(uniform_disc_flux(np.array([period / 4]), period, 0.1, 0.0, 20.0)[0]) == 1.0
    dt = np.linspace(-0.5 * period, 0.5 * period, 2001)
    assert np.all(uniform_disc_flux(dt, period, 0.1, 1.2, 20.0) == 1.0)


def test_fit_recovers_a_box_like_planet():
    period, t0, k, b, a = 3.7, 140.0, 0.08, 0.2, 12.0
    lc = inject_model(make_lightcurve(n_days=1400, seed=11, noise_ppm=500), period, t0, k, b, a)
    fit = fit_transit(lc.time, detrend(lc), period, t0, _t14(period, k, b, a))
    assert isinstance(fit, TransitFit)
    assert fit.ok
    assert abs(fit.k - k) < 0.01
    assert fit.k + fit.b < 1.04
    assert abs(fit.depth_ppm - 6400) < 0.15 * 6400
    assert fit.n_bins >= 20


def test_fit_flags_a_grazing_eclipse():
    period, t0, k, b, a = 5.2, 140.0, 0.5, 1.3, 8.0
    lc = inject_model(make_lightcurve(n_days=1400, seed=11, noise_ppm=500), period, t0, k, b, a)
    fit = fit_transit(lc.time, detrend(lc), period, t0, _t14(period, k, b, a))
    assert fit.ok
    assert fit.k + fit.b > 1.04


def test_fit_on_a_v_eclipse_fixture():
    period, t0, width = 24.0, 140.0, 11 / 24
    lc = inject_v_eclipse(make_lightcurve(n_days=1400, seed=11, noise_ppm=500), period, t0, width, 40000)
    fit = fit_transit(lc.time, detrend(lc), period, t0, width)
    print(f"V eclipse fit: k={fit.k:.4f} b={fit.b:.4f} a/R={fit.a_over_r:.3f} depth={fit.depth_ppm:.0f} "
          f"t14={fit.t14_days:.4f} chi2={fit.chi2:.1f} n_bins={fit.n_bins} ok={fit.ok}")
    assert fit.k + fit.b > 1.04


def test_fit_is_not_ok_on_a_sparse_fold():
    lc = make_lightcurve(n_days=60, seed=11, noise_ppm=500, quarters=False, gaps=False)
    lc = inject_model(lc, 25.0, 140.0, 0.08, 0.2, 30.0)
    fit = fit_transit(lc.time, detrend(lc), 25.0, 140.0, 0.2)
    assert (not fit.ok) or fit.n_bins < 20
