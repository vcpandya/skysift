import numpy as np

from skysift.lc import transit_model
from skysift.lc.ops import detrend, fold_phase
from skysift.lc.transit_model import TransitFit, fit_transit, uniform_disc_flux
from tests.synthetic import inject_model, inject_transit, inject_v_eclipse, make_lightcurve


def _t14(period, k, b, a_over_r):
    # deliberately independent of skysift.lc.transit_model._t14, so it checks the formula
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
    # a central transit lies in the non-grazing family, so the comparison fit finds the same minimum
    assert np.isfinite(fit.chi2_nongrazing) and abs(fit.chi2_nongrazing - fit.chi2) < 1e-3


def test_fit_flags_a_grazing_eclipse():
    period, t0, k, b, a = 5.2, 140.0, 0.5, 1.3, 8.0
    lc = inject_model(make_lightcurve(n_days=1400, seed=11, noise_ppm=500), period, t0, k, b, a)
    fit = fit_transit(lc.time, detrend(lc), period, t0, _t14(period, k, b, a))
    assert fit.ok
    assert fit.k + fit.b > 1.04
    # no non-grazing geometry (b <= 1 - k) reproduces the V: the comparison fit is far worse
    assert np.isfinite(fit.chi2_nongrazing) and fit.chi2_nongrazing - fit.chi2 > 25.0


def test_fit_returns_the_non_grazing_minimum_when_the_free_optimiser_misses_it():
    """A shallow 185 ppm, 1.8 h box at 0.9618 d fitted with the detector's 1.5 h grid duration: the free optimiser
    used to stop in a grazing local minimum (k 0.48, b 1.48, chi2 59.9) while the non-grazing family, a subset of
    the free one, holds a lower chi2 (41.3). The fit returns that lower solution, so k + b describes the real minimum and
    chi2_nongrazing - chi2 is 0."""
    lc = inject_transit(make_lightcurve(n_days=1400, cadence_min=29.4, seed=31, noise_ppm=600), 0.9618, 133.0,
                        1.8 / 24, 185)
    fit = fit_transit(lc.time, detrend(lc), 0.9618, 133.0, 1.5 / 24)
    assert fit.ok
    assert fit.k + fit.b < 1.0 and fit.chi2 < 45.0
    assert 0.0 <= fit.chi2_nongrazing - fit.chi2 < 1e-3
    assert abs(fit.depth_ppm - 185) < 0.3 * 185


def _box_like_planet():
    period, t0, k, b, a = 3.7, 140.0, 0.08, 0.2, 12.0
    lc = inject_model(make_lightcurve(n_days=1400, seed=11, noise_ppm=500), period, t0, k, b, a)
    return lc.time, detrend(lc), period, t0, _t14(period, k, b, a)


def _unconverged(monkeypatch, family):
    """Patch the module's ``least_squares`` so every start of one family (``"residuals"``, the free fit, or
    ``"residuals_nongrazing"``) reports no convergence; the other family runs as usual."""
    real = transit_model.least_squares

    def patched(fun, *args, **kwargs):
        out = real(fun, *args, **kwargs)
        if fun.__name__ == family:
            out.success = False
        return out
    monkeypatch.setattr(transit_model, "least_squares", patched)


def test_fit_falls_back_to_the_non_grazing_solution_when_the_free_fit_does_not_converge(monkeypatch):
    """A converged start beats an unconverged one, between the families as between starts: with no free start
    converged, the converged non-grazing solution is the fit (ok True, delta 0), even on the grazing eclipse whose
    unconverged free solution has the far lower chi2."""
    _unconverged(monkeypatch, "residuals")
    period, t0, k, b, a = 5.2, 140.0, 0.5, 1.3, 8.0
    lc = inject_model(make_lightcurve(n_days=1400, seed=11, noise_ppm=500), period, t0, k, b, a)
    fit = fit_transit(lc.time, detrend(lc), period, t0, _t14(period, k, b, a))
    assert fit.ok
    assert fit.chi2 == fit.chi2_nongrazing and np.isfinite(fit.chi2)
    assert fit.k + fit.b <= 1.0


def test_fit_keeps_the_free_solution_when_the_non_grazing_fit_does_not_converge(monkeypatch):
    """No non-grazing start converged: chi2_nongrazing is NaN (an unconverged chi2 would overstate the family's
    minimum), the free solution is the fit, and rung 2 records fit_delta_chi2 as None, which skips the V-shape rule."""
    from skysift.vetting.rung2_shape import _fit_metrics, _v_shape_applies
    _unconverged(monkeypatch, "residuals_nongrazing")
    fit = fit_transit(*_box_like_planet())
    assert fit.ok and np.isfinite(fit.chi2) and abs(fit.k - 0.08) < 0.01
    assert np.isnan(fit.chi2_nongrazing)
    assert _fit_metrics(fit)["fit_delta_chi2"] is None
    assert _v_shape_applies(fit, fit.depth_ppm) is False


def test_fit_on_a_v_eclipse_fixture():
    period, t0, width = 24.0, 140.0, 11 / 24
    lc = inject_v_eclipse(make_lightcurve(n_days=1400, seed=11, noise_ppm=500), period, t0, width, 40000)
    fit = fit_transit(lc.time, detrend(lc), period, t0, width)
    assert fit.ok
    assert fit.k + fit.b > 1.04
    assert fit.chi2_nongrazing - fit.chi2 > 25.0


def test_fit_is_not_ok_on_a_sparse_fold():
    lc = make_lightcurve(n_days=60, seed=11, noise_ppm=500, quarters=False, gaps=False)
    lc = inject_model(lc, 25.0, 140.0, 0.08, 0.2, 30.0)
    fit = fit_transit(lc.time, detrend(lc), 25.0, 140.0, 0.2)
    assert not fit.ok
    assert fit.n_bins < 20
    assert np.isnan([fit.k, fit.b, fit.a_over_r, fit.depth_ppm, fit.t14_days, fit.chi2, fit.chi2_nongrazing]).all()


def test_fit_is_not_ok_when_no_bin_lies_in_transit():
    period, t0, k, b, a = 3.7, 140.0, 0.08, 0.2, 12.0
    lc = inject_model(make_lightcurve(n_days=1400, seed=11, noise_ppm=500), period, t0, k, b, a)
    duration = _t14(period, k, b, a)
    fnorm = detrend(lc)
    fnorm[np.abs(fold_phase(lc.time, period, t0)) < 0.5 * duration] = np.nan      # the transit falls in a data gap
    fit = fit_transit(lc.time, fnorm, period, t0, duration)
    assert fit.n_bins >= 20                      # enough out-of-transit bins, so only the in-transit rule can fail it
    assert not fit.ok
    assert np.isnan([fit.k, fit.b, fit.a_over_r, fit.depth_ppm, fit.t14_days, fit.chi2]).all()
