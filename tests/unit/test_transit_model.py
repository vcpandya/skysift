import json

import numpy as np
import pytest

from skysift.lc import transit_model
from skysift.lc.ops import detrend, fold_phase
from skysift.lc.transit_model import (
    A_LABEL_FITTED,
    A_LABEL_STELLAR,
    LD_TABLE,
    NO_DENSITY,
    UNCONSTRAINED,
    ShapeFit,
    TransitFit,
    compare_fits,
    exposure_flux,
    fit_shape,
    fit_transit,
    limb_darkening,
    quadratic_ld_flux,
    stellar_density,
    uniform_disc_flux,
)
from tests.synthetic import _with_dip, inject_model, inject_transit, inject_v_eclipse, make_lightcurve

EXPOSURE = 29.4 / 1440


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


# ---------------------------------------------------------------------------------------------------- the shape fit


def _ld_planet(period, t0, k, b, a, u1, u2, noise, seed):
    lc = make_lightcurve(noise_ppm=noise, seed=seed)
    ph = (lc.time - t0 + 0.5 * period) % period - 0.5 * period
    return _with_dip(lc, exposure_flux(ph, period, k, b, a, u1, u2, EXPOSURE))


def test_without_limb_darkening_the_quadratic_model_is_the_uniform_disc():
    dt = np.linspace(-0.1, 0.1, 2001)
    assert np.max(np.abs(quadratic_ld_flux(dt, 13.77, 0.046, 0.3, 38.0, 0.0, 0.0)
                         - uniform_disc_flux(dt, 13.77, 0.046, 0.3, 38.0))) < 1e-10


def test_the_central_depth_of_a_small_planet_follows_the_limb_darkening():
    u1, u2, k = 0.4158, 0.3203, 0.046
    depth = 1.0 - float(quadratic_ld_flux(np.array([0.0]), 13.77, k, 0.0, 38.0, u1, u2)[0])
    assert depth == pytest.approx(k ** 2 / (1 - u1 / 3 - u2 / 6), rel=1e-3)        # 2,618 ppm, not k^2 = 2,116


def test_the_coefficients_interpolate_in_teff_and_hold_the_table_edges():
    ld = limb_darkening(3582.0)                                   # KIC 5689121's Teff
    assert ld["u1"] == pytest.approx(0.4158 + 82 / 250 * (0.4224 - 0.4158), abs=1e-6)
    assert ld["u2"] == pytest.approx(0.3203 + 82 / 250 * (0.3037 - 0.3203), abs=1e-6)
    assert ld["law"] == "quadratic" and ld["logg"] == 4.5 and "Claret and Bloemen 2011" in ld["source"]
    assert (limb_darkening(3000.0)["u1"], limb_darkening(9000.0)["u2"]) == (LD_TABLE[0][1], LD_TABLE[-1][2])
    assert limb_darkening(None)["teff"] is None and limb_darkening(None)["u1"] == 0.4035      # the 5750 K row


def test_a_disc_over_the_centre_of_the_star_is_integrated_whole():
    """Where the disc covers the star's centre (z < k) the annuli inside k - z are covered whole: that part is taken
    in closed form, so the uniform case matches Mandel and Agol there too, small planets and deep eclipses alike."""
    for k in (0.046, 0.3, 1.5):
        dt = np.linspace(-0.02, 0.02, 801)
        assert np.max(np.abs(quadratic_ld_flux(dt, 13.77, k, 0.0, 38.0, 0.0, 0.0)
                             - uniform_disc_flux(dt, 13.77, k, 0.0, 38.0))) < 1e-9, k


def test_the_exposure_grid_reads_the_integral_from_its_table():
    """The exposure grid interpolates the blocked light in z on a table of the integral: within 1e-7 of it for a
    planet and 1e-5 for an eclipsing binary's radius ratio."""
    for k, tol in ((0.01, 1e-7), (0.046, 1e-7), (0.1, 1e-7), (0.8, 1e-5), (1.5, 1e-5)):
        z = np.linspace(0.0, 1.0 + k, 20001)[:-1]
        table = np.interp(z, *transit_model._blocked_table(k, 0.42, 0.31))
        assert np.max(np.abs(table - transit_model._blocked(z, k, 0.42, 0.31))) < tol, k


def test_the_exposure_average_keeps_the_bottom_and_rounds_the_contact():
    dt = np.array([0.0, 0.5 * _t14(13.77, 0.046, 0.0, 38.0)])     # mid-transit and first contact (T14 2.9 h)
    plain = quadratic_ld_flux(dt, 13.77, 0.046, 0.0, 38.0, 0.42, 0.31)
    smeared = exposure_flux(dt, 13.77, 0.046, 0.0, 38.0, 0.42, 0.31, EXPOSURE)
    assert smeared[0] == pytest.approx(plain[0], abs=2e-5) and smeared[1] < plain[1]
    assert np.array_equal(exposure_flux(dt, 13.77, 0.046, 0.0, 38.0, 0.42, 0.31, 0.05 / 1440), plain)


def test_the_stellar_density_of_kic_5689121s_orbit():
    assert stellar_density(38.1, 13.769414) == pytest.approx(5.52, abs=0.01)    # the live dossier's 5.52 g/cm3


def test_fit_shape_recovers_a_limb_darkened_transit():
    period, t0, k, b, a = 5.4, 134.9, 0.08, 0.2, 12.9
    lc = _ld_planet(period, t0, k, b, a, 0.42, 0.315, 300, 7)
    fit = fit_shape(lc.time, detrend(lc), period, t0, _t14(period, k, b, a), u1=0.42, u2=0.315,
                    exposure_days=EXPOSURE)
    assert fit.ok and fit.variant == "free" and fit.chi2_prior == 0.0 and fit.prior_gcc is None
    assert abs(fit.k - k) < 0.002 and abs(fit.b - b) < 0.1 and abs(fit.a_over_r - a) < 1.0
    assert fit.n_points > 5000 and fit.exposure_min == pytest.approx(29.4)
    json.dumps(fit.as_dict(), allow_nan=False)


def test_a_cadence_smeared_transit_leaves_b_and_a_over_r_to_the_density():
    """KIC 5689121's geometry (13.77 d, k 0.046, a/R* 38.1) at 1,500 ppm: the true-density prior moves the chi-square
    by far less than 2, so the prior fit is quoted and a/R* comes from the stellar parameters."""
    period, t0, k, b, a = 13.77, 136.3, 0.046, 0.3, 38.1
    lc = _ld_planet(period, t0, k, b, a, 0.42, 0.315, 1500, 5)
    args = (lc.time, detrend(lc), period, t0, _t14(period, k, b, a))
    kw = {"u1": 0.42, "u2": 0.315, "exposure_days": EXPOSURE}
    free = fit_shape(*args, **kw)
    prior = fit_shape(*args, **kw, density=(stellar_density(a, period), 0.5))
    cmp = compare_fits(free, prior)
    assert prior.variant == "prior" and prior.chi2_total == pytest.approx(prior.chi2_data + prior.chi2_prior)
    assert cmp["constrained"] is False and cmp["quoted"] == "prior" and cmp["delta_chi2"] <= 2.0
    assert cmp["a_over_r_label"] == A_LABEL_STELLAR and cmp["reason"] == UNCONSTRAINED
    assert abs(prior.a_over_r - a) < 2.0
    # fix round 1, M-1: in a flat valley the free fit's density means nothing, so no tension is stored
    assert cmp["density_tension_sigma"] is None
    assert UNCONSTRAINED == "unconstrained at this cadence (the quoted values follow the host's density)"


def test_the_fit_weights_each_point_by_the_scatter_outside_the_transit():
    """Fix round 1, M-6: the per-point error is the robust scatter of the window's points beyond one duration of
    mid-transit, as ``ephemeris.transit_times`` takes it, so a deep transit does not inflate it: the reduced
    chi-square of a good fit is about 1."""
    period, t0, k, b, a = 5.4, 134.9, 0.1, 0.2, 12.9
    lc = _ld_planet(period, t0, k, b, a, 0.42, 0.315, 300, 7)
    fit = fit_shape(lc.time, detrend(lc), period, t0, _t14(period, k, b, a), u1=0.42, u2=0.315,
                    exposure_days=EXPOSURE)
    assert fit.ok and 0.9 < fit.chi2_data / fit.n_points < 1.1


def test_a_shape_that_contradicts_the_density_is_quoted_free_with_the_tension():
    """A prior eight times the injected density (a/R* doubled), at 10 per cent: the prior term costs about 76 in
    chi-square, so the free values are quoted and the tension is stated in sigma."""
    period, t0, k, b, a = 5.4, 134.9, 0.08, 0.2, 12.9
    lc = _ld_planet(period, t0, k, b, a, 0.42, 0.315, 300, 7)
    args = (lc.time, detrend(lc), period, t0, _t14(period, k, b, a))
    kw = {"u1": 0.42, "u2": 0.315, "exposure_days": EXPOSURE}
    wrong = stellar_density(2 * a, period)
    cmp = compare_fits(fit_shape(*args, **kw), fit_shape(*args, **kw, density=(wrong, 0.1 * wrong)))
    assert cmp["constrained"] is True and cmp["quoted"] == "free" and cmp["a_over_r_label"] == A_LABEL_FITTED
    assert cmp["delta_chi2"] > 2.0 and cmp["density_tension_sigma"] < -5


def test_without_a_density_the_free_fit_is_quoted():
    free = ShapeFit(True, None, "free", 0.05, 0.3, 20.0, 0.1, 2600.0, 100.0, 0.0, 100.0, 500, 0.4, 0.3, 29.4, 5.0,
                    None)
    assert compare_fits(free, None) == {"constrained": None, "quoted": "free", "delta_chi2": None,
                                        "density_tension_sigma": None, "a_over_r_label": A_LABEL_FITTED,
                                        "reason": NO_DENSITY}
