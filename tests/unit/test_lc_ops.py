import numpy as np

from skysift.lc.ops import detrend, event_depths, fold_phase, secondary_search
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
