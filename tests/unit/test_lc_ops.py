import numpy as np

from skysift.lc.ops import detrend, event_depths, fold_phase, secondary_search, stacked_depth
from skysift.models import LightCurve
from tests.synthetic import inject_transit, make_lightcurve

P, T0, DUR, DEPTH = 13.769, 136.3, 0.09, 2000.0


def test_detrend_removes_quarter_offsets():
    lc = make_lightcurve()
    fn = detrend(lc)
    good = np.isfinite(fn)
    assert abs(np.nanmedian(fn[lc.quarter == 1]) - np.nanmedian(fn[lc.quarter == 2])) < 2e-4
    assert good.sum() > 0.9 * lc.time.size


def test_event_depths_recovers_injection():
    lc = inject_transit(make_lightcurve(), P, T0, DUR, DEPTH)
    fn = detrend(lc)
    ed = event_depths(lc.time, fn, lc.quarter, P, T0, DUR)
    assert ed.n >= 80
    assert abs(ed.mean - DEPTH) < 3 * ed.err_scaled
    assert ed.err_scaled < 120
    lc = inject_transit(make_lightcurve(seed=0, noise_ppm=200), P, T0, DUR, DEPTH)
    ed = event_depths(lc.time, detrend(lc), lc.quarter, P, T0, DUR)
    assert abs(ed.mean - DEPTH) < 30


def test_fold_phase_range():
    ph = fold_phase(np.array([0.0, 6.9, 13.769, 20.0]), P, 0.0)
    assert np.all(ph >= -P / 2) and np.all(ph < P / 2) and abs(ph[2]) < 1e-9


def test_secondary_search_finds_offset_eclipse():
    lc = inject_transit(make_lightcurve(seed=3), P, T0, DUR, 3000)
    lc = inject_transit(lc, P, T0 + 0.529 * P, 0.06, 2500)      # eccentric secondary
    fn = detrend(lc)
    phase, _depth, sigma = secondary_search(lc.time, fn, P, T0, DUR)
    assert abs(phase - 0.529) < 0.02 and sigma > 5


def test_secondary_search_phase_0535_needs_fine_grid():
    lc = inject_transit(make_lightcurve(seed=3), P, T0, DUR, 3000)
    lc = inject_transit(lc, P, T0 + 0.535 * P, 0.06, 2500)
    phase, _depth, sigma = secondary_search(lc.time, detrend(lc), P, T0, DUR)
    assert abs(phase - 0.535) < 0.01 and sigma > 5


def test_secondary_search_degenerate_input_returns_nan():
    lc = make_lightcurve()
    r = secondary_search(lc.time, np.full(lc.time.size, np.nan), P, T0, DUR)
    assert len(r) == 3 and all(np.isnan(x) for x in r)
    short = make_lightcurve(n_days=1400)
    n = 30
    r = secondary_search(short.time[:n], detrend(short)[:n], P, T0, DUR)
    assert all(np.isnan(x) for x in r)
    r = secondary_search(np.array([]), np.array([]), P, T0, DUR, phases=np.array([]))
    assert all(np.isnan(x) for x in r)


def test_detrend_flux_override_masks_nonfinite():
    lc = make_lightcurve()
    alt = lc.flux_alt.copy()
    bad = np.array([500, 501, 3000])
    alt[bad] = np.nan
    out = detrend(lc, flux=alt)
    assert np.all(np.isnan(out[bad]))
    # NaN points must behave exactly as if flagged bad: dropped from the series, not fed to the filter
    q = lc.quality.copy(); q[bad] = 1
    flagged = LightCurve(lc.time, lc.flux_alt, lc.flux_err, q, lc.quarter, lc.flux_alt, {})
    ref = detrend(flagged)
    assert np.array_equal(np.isnan(out), np.isnan(ref))
    ok = np.isfinite(out)
    assert np.max(np.abs(out[ok] - ref[ok])) < 1e-9


def test_long_duration_transit_recovered():
    lc = inject_transit(make_lightcurve(seed=5, noise_ppm=200), 60.0, 140.0, 0.5, 2000)
    ed = event_depths(lc.time, detrend(lc), lc.quarter, 60.0, 140.0, 0.5)
    assert ed.n >= 15
    assert abs(ed.mean - 2000) < 60


USP_P, USP_T0, USP_DUR = 0.6, 132.0, 0.075      # duration/period 0.125: the 0.1/0.9 trial boxes reach the primary


def _usp_lc(seed=0):
    lc = make_lightcurve(n_days=90, cadence_min=30, noise_ppm=500, seed=seed)
    return inject_transit(lc, USP_P, USP_T0, USP_DUR, 3000)


def test_secondary_search_ignores_the_primary_of_an_ultra_short_period_planet():
    for seed in (0, 1):
        lc = _usp_lc(seed)
        phase, _depth, sigma = secondary_search(lc.time, detrend(lc), USP_P, USP_T0, USP_DUR)
        assert sigma < 5 and 0.1 <= phase <= 0.9, (seed, phase, sigma)
    ed = event_depths(lc.time, detrend(lc), lc.quarter, USP_P, USP_T0, USP_DUR)
    assert ed.control_rms < 100                      # control boxes no longer sit on the transit


def test_secondary_search_still_finds_a_half_phase_eclipse_on_a_short_period():
    lc = inject_transit(_usp_lc(0), USP_P, USP_T0 + 0.5 * USP_P, USP_DUR, 1500)
    phase, _depth, sigma = secondary_search(lc.time, detrend(lc), USP_P, USP_T0, USP_DUR)
    assert abs(phase - 0.5) < 0.03 and sigma > 5


def test_stacked_depth_measures_a_short_transit_and_its_parity():
    lc = inject_transit(make_lightcurve(n_days=300, cadence_min=30, noise_ppm=500, seed=31), 0.6, 132.0, 1 / 24, 600)
    fn = detrend(lc)
    d_all = stacked_depth(lc.time, fn, 0.6, 132.0, 1 / 24)
    assert d_all.n_in > 800 and abs(d_all.depth_ppm - 600) < 3 * d_all.err_ppm and d_all.err_ppm < 60
    odd, even = (stacked_depth(lc.time, fn, 0.6, 132.0, 1 / 24, epochs=e) for e in ("odd", "even"))
    assert abs(odd.depth_ppm - even.depth_ppm) < 3 * np.hypot(odd.err_ppm, even.err_ppm)
    sec = stacked_depth(lc.time, fn, 0.6, 132.0, 1 / 24, phase_offset=0.5)
    assert abs(sec.depth_ppm) < 3 * sec.err_ppm
    assert stacked_depth(lc.time[:40], fn[:40], 0.6, 132.0, 1 / 24).n_in == 0


def test_stacked_depth_inflates_its_error_under_red_noise():
    base = make_lightcurve(n_days=300, cadence_min=30, noise_ppm=300, seed=32)
    f = base.flux * (1 - 1500e-6 * np.cos(2 * np.pi * (base.time - 132.0) / 0.3 + np.pi / 2))     # a 0.3 d wave repeats on every fold: red noise, zero at t0
    red = inject_transit(LightCurve(base.time, f, base.flux_err, base.quality, base.quarter, f.copy()), 0.6, 132.0, 1 / 24, 600)
    d = stacked_depth(red.time, detrend(red), 0.6, 132.0, 1 / 24)
    assert d.inflation > 1.5 and abs(d.depth_ppm - 600) < 3 * d.err_ppm
