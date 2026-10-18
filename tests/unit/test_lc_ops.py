import numpy as np
import pytest
from scipy.special import erfcinv

from skysift.lc.ops import (
    EVENT_COVERAGE_MIN,
    SHAPE_BINS_PER_DURATION,
    _circular,
    apply_mask,
    detrend,
    event_depths,
    event_quality,
    fold_phase,
    phase_significance,
    secondary_search,
    stacked_depth,
    sweet,
)
from skysift.models import LightCurve
from tests.synthetic import FQ_DUR, FQ_P, FQ_QUARTERS, FQ_T0, four_quarter_planet, inject_transit, make_lightcurve

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


def test_stacked_depth_residue_classes():
    """``epochs=(n, r)`` keeps the epochs with ``round((t - t0) / period) % n == r``: a transit injected at 3P and
    folded at P sits in class 0 only. The string forms are the classes of n = 2."""
    lc = inject_transit(make_lightcurve(n_days=500, seed=33, noise_ppm=800), 9.0, 133.0, 0.15, 3000)
    fn = detrend(lc)
    cls = [stacked_depth(lc.time, fn, 3.0, 133.0, 0.15, epochs=(3, r)) for r in range(3)]
    assert all(c.n_in > 0 for c in cls)
    assert abs(cls[0].depth_ppm - 3000) < 0.15 * 3000 and cls[0].depth_ppm > 10 * cls[0].err_ppm
    assert all(abs(c.depth_ppm) < 5 * c.err_ppm for c in cls[1:])
    for name, pair in (("odd", (2, 1)), ("even", (2, 0))):
        a = stacked_depth(lc.time, fn, 3.0, 133.0, 0.15, epochs=name)
        b = stacked_depth(lc.time, fn, 3.0, 133.0, 0.15, epochs=pair)
        assert (a.depth_ppm, a.err_ppm, a.n_in) == (b.depth_ppm, b.err_ppm, b.n_in)


def test_stacked_depth_inflates_its_error_under_red_noise():
    base = make_lightcurve(n_days=300, cadence_min=30, noise_ppm=300, seed=32)
    f = base.flux * (1 - 1500e-6 * np.cos(2 * np.pi * (base.time - 132.0) / 0.3 + np.pi / 2))     # a 0.3 d wave repeats on every fold: red noise, zero at t0
    red = inject_transit(LightCurve(base.time, f, base.flux_err, base.quality, base.quarter, f.copy()), 0.6, 132.0, 1 / 24, 600)
    d = stacked_depth(red.time, detrend(red), 0.6, 132.0, 1 / 24)
    assert d.inflation > 1.5 and abs(d.depth_ppm - 600) < 3 * d.err_ppm


def test_stacked_depth_inflation_ignores_a_real_secondary_among_its_controls():
    lc = inject_transit(make_lightcurve(n_days=500, seed=42, noise_ppm=800), 4.0, 133.0, 0.1, 20000)
    lc = inject_transit(lc, 4.0, 135.0, 0.1, 14000)                    # a 70 percent secondary at phase 0.5
    fn = detrend(lc)
    pri = stacked_depth(lc.time, fn, 4.0, 133.0, 0.1)
    assert pri.inflation < 3 and abs(pri.depth_ppm - 20000) < 0.1 * 20000


def test_detrend_masks_the_ramp_after_a_gap():
    rng = np.random.default_rng(12)
    t = np.arange(100.0, 120.0, 29.4 / 1440.0)
    t = t[(t < 108.0) | (t >= 111.0)]                               # a 3 d gap from 108 to 111
    start = t[t >= 111.0][0]
    f = 1.0 + rng.normal(0, 300e-6, t.size)
    after = t >= start
    f[after] *= 1 - 5000e-6 * np.exp(-(t[after] - start) / 0.5)     # a 5000 ppm thermal ramp decaying over 0.5 d
    lc = LightCurve(t, f, np.full(t.size, 300e-6), np.zeros(t.size, int), np.ones(t.size, int))
    # 1.5 d after the gap, and the first 1.5 d of the data: the start of the data counts as a gap edge
    window = ((t >= start) & (t < start + 1.5)) | (t < t[0] + 1.5)
    fn = detrend(lc)
    assert np.all(np.isnan(fn[window])) and np.all(np.isfinite(fn[~window]))
    assert int(np.isnan(fn).sum()) == int(window.sum())
    assert np.all(np.isfinite(detrend(lc, edge_mask_days=0)))       # the old behaviour: nothing masked


def test_detrend_keeps_a_transit_at_a_segment_edge():
    """A 3 h, 3000 ppm transit within half a running-median window of a segment edge: at the first point the mask
    leaves at the start of the data and after a gap (both found from the detrended flux itself). Padding the edge with
    copies of its first value (mode "nearest") pulls the median to the transit level and flattens it (137 and 47 ppm
    left, measured); mirror padding duplicates only the few in-transit points and the depth survives (3003 and
    2996 ppm)."""
    rng = np.random.default_rng(5)
    t = np.arange(100.0, 120.0, 29.4 / 1440.0)
    t = t[(t < 108.0) | (t >= 111.0)]                               # a 3 d gap, then 1.5 masked days from 111
    f = 1.0 + rng.normal(0, 100e-6, t.size)
    lc = LightCurve(t, f, np.full(t.size, 100e-6), np.zeros(t.size, int), np.ones(t.size, int))
    kept = t[np.isfinite(detrend(lc))]                              # what the mask leaves: each segment's first point
    edges = [kept[0], *kept[1:][np.diff(kept) > 0.5]]
    assert len(edges) == 2
    for t0 in edges:
        transit = (t >= t0) & (t < t0 + 0.125)
        ff = f.copy(); ff[transit] *= 1 - 3000e-6
        fn = detrend(LightCurve(t, ff, lc.flux_err, lc.quality, lc.quarter))
        depth = (1 - np.nanmean(fn[transit])) * 1e6
        assert np.isfinite(fn[transit]).all() and depth > 0.8 * 3000, (t0, depth)


def test_apply_mask_sets_nan_exactly_inside_the_windows():
    from skysift.lc.ops import apply_mask
    t = np.arange(100.0, 200.0, 0.01)
    f = 1.0 + 1e-4 * np.sin(t)
    mask = [{"period": 10.0, "t0": 103.0037, "half_primary_days": 0.3051, "half_secondary_days": 0.2053},
            {"period": 7.0, "t0": 101.0029, "half_primary_days": 0.1047, "half_secondary_days": 0.0551}]
    out = apply_mask(t, f, mask)
    inside = np.zeros(t.size, bool)
    for m in mask:
        for k in range(-20, 20):
            inside |= np.abs(t - (m["t0"] + k * m["period"])) < m["half_primary_days"]
            inside |= np.abs(t - (m["t0"] + (k + 0.5) * m["period"])) < m["half_secondary_days"]
    assert inside.sum() > 0 and np.array_equal(np.isnan(out), inside)       # several epochs, both events, both signals
    assert np.isnan(out[np.abs(t - 108.0) < 0.19]).all()                       # a secondary of the 10 d signal
    assert np.array_equal(out[~inside], f[~inside])
    assert not np.isnan(f).any()                                               # the input is left alone
    assert apply_mask(t, f, []) is f and apply_mask(t, f, None) is f


# --- Robovetter-style fold helpers: model-shift uniqueness, SWEET, per-event quality (Thompson et al. 2018, App. A) ---

UP, UT0, UDUR = 4.1, 140.3, 0.125               # a 3 h transit every 4.1 d
EP, ET0, EDUR = 30.0, 145.0, 0.25               # a 6 h transit every 30 d


def _box_curve(period, t0, dur, depth, n_days=1400, seed=13, extra=()):
    """Noise-only curve (600 ppm, seed 13) with a box transit and any ``extra`` (period, t0, dur, depth) boxes."""
    lc = inject_transit(make_lightcurve(n_days=n_days, noise_ppm=600, seed=seed), period, t0, dur, depth)
    for p, t, d, dp in extra:
        lc = inject_transit(lc, p, t, d, dp)
    return lc


def test_phase_significance_of_a_clean_transit_is_unique():
    lc = _box_curve(UP, UT0, UDUR, 1500)
    ps = phase_significance(lc.time, detrend(lc), UP, UT0, UDUR)
    step = UDUR / UP / SHAPE_BINS_PER_DURATION
    assert ps.sigma_pri > 10 and ps.ms2 < 2.0 and ps.ms3 < 4.0
    assert abs(ps.phases[np.nanargmax(ps.sigma)]) < step
    assert ps.phases.size == ps.sigma.size and ps.phases.min() >= -0.5 and ps.phases.max() < 0.5
    assert abs(np.diff(ps.phases) - step).max() < 1e-9 and abs(ps.fa2 - np.sqrt(2) * erfcinv(UDUR / UP)) < 1e-12


def test_phase_significance_locates_an_equal_second_dip():
    lc = _box_curve(UP, UT0, UDUR, 1500, extra=[(UP, UT0 + 0.37 * UP, UDUR, 1500)])
    ps = phase_significance(lc.time, detrend(lc), UP, UT0, UDUR)
    assert abs(ps.phase_sec - 0.37) < 0.01 and 0.9 < ps.sigma_sec / ps.sigma_pri < 1.1


def test_phase_significance_flags_a_primary_that_is_not_the_deepest():
    lc = _box_curve(UP, UT0, UDUR, 1500, extra=[(UP, UT0 + 0.37 * UP, UDUR, 3000), (UP, UT0 + 0.71 * UP, UDUR, 3000)])
    ps = phase_significance(lc.time, detrend(lc), UP, UT0, UDUR)
    assert ps.ms2 > 2.0 and ps.sigma_ter > ps.sigma_pri
    assert ps.ms2 == pytest.approx(ps.fa2 - (ps.sigma_pri - ps.sigma_ter))


def test_phase_significance_flags_a_brightening():
    lc = _box_curve(UP, UT0, UDUR, 1500)
    bright = np.abs(fold_phase(lc.time, UP, UT0 + 0.3 * UP)) < 0.5 * UDUR
    f = lc.flux.copy(); f[bright] *= 1 + 3.0e-3
    lc = LightCurve(lc.time, f, lc.flux_err, lc.quality, lc.quarter, f.copy(), {})
    ps = phase_significance(lc.time, detrend(lc), UP, UT0, UDUR)
    assert ps.ms3 > 4.0 and ps.sigma_pos > ps.sigma_pri
    assert ps.ms3 == pytest.approx(ps.fa2 - (ps.sigma_pri - ps.sigma_pos))


def _quiet(n_days=1400):
    return make_lightcurve(n_days=n_days, noise_ppm=600, seed=13, quarters=False)


def test_sweet_finds_the_wave_at_the_period():
    lc = _quiet()
    flux = lc.flux * (1 + 2e-3 * np.sin(2 * np.pi * lc.time / 2.3))
    r = sweet(lc.time, flux, 2.3, 500)
    assert r.factor == 1.0 and abs(r.amplitude_ppm - 2000) < 200 and r.snr > 50


def test_sweet_finds_the_half_period_wave():
    lc = _quiet()
    flux = lc.flux * (1 + 2e-3 * np.sin(2 * np.pi * lc.time / 1.15))
    r = sweet(lc.time, flux, 2.3, 500)
    assert r.factor == 0.5 and r.snr > 50


def test_sweet_is_quiet_on_a_transit():
    # the 0.08 d width is tuned to the threshold: the first harmonic of a 2000 ppm box has about 145 ppm (snr 38 on
    # this noise); at 0.125 d it is 223 ppm (snr 55), and only the rung's amplitude-versus-depth rules spare a planet
    lc = inject_transit(_quiet(), 2.3, 140.0, 0.08, 2000)
    r = sweet(lc.time, lc.flux, 2.3, 500)
    assert r.snr < 50


def test_event_quality_coverage_and_ses():
    lc = _box_curve(EP, ET0, EDUR, 3000)
    fn = detrend(lc)
    eq = event_quality(lc.time, fn, EP, ET0, EDUR)
    assert eq.chases is None and eq.epochs.size == eq.coverage.size == eq.ses.size > 40
    t_event = ET0 + eq.epochs * EP
    # an event inside or touching a quarter gap (1.5 d gap plus 1.5 d of masked ramp after it) is short of points
    in_gap = ((t_event - 131.5 + EDUR) % 93.0 < 3.0 + 2 * EDUR) | ((t_event - 131.5 - EDUR) % 93.0 < 3.0)
    ok = ~in_gap
    assert ok.sum() > 30 and np.all((eq.coverage[ok] > 0.9) & (eq.coverage[ok] < 1.1))
    assert in_gap[(eq.coverage <= 0.9) | (eq.coverage >= 1.1)].all()      # measured: no epoch of this curve falls in a gap
    assert np.all(eq.ses[ok & np.isfinite(eq.ses)] > 3) and np.isfinite(eq.ses[ok]).all()
    k = int(np.where(ok)[0][5])
    cut = fn.copy()
    cut[(lc.time > t_event[k]) & (lc.time < t_event[k] + EDUR)] = np.nan
    eq2 = event_quality(lc.time, cut, EP, ET0, EDUR)
    j = int(np.where(eq2.epochs == eq.epochs[k])[0][0])
    assert eq2.coverage[j] < EVENT_COVERAGE_MIN and eq2.coverage[j] < eq.coverage[k]


def test_event_quality_counts_an_epoch_without_points():
    lc = _box_curve(EP, ET0, EDUR, 3000, n_days=130)
    fn = detrend(lc)
    full = event_quality(lc.time, fn, EP, ET0, EDUR)
    k = 2
    cut = fn.copy()
    cut[np.abs(lc.time - (ET0 + full.epochs[k] * EP)) < 2 * EDUR] = np.nan
    eq = event_quality(lc.time, cut, EP, ET0, EDUR)
    assert np.array_equal(eq.epochs, full.epochs)
    assert eq.coverage[k] == 0.0 and np.isnan(eq.ses[k]) and np.isnan(eq.chases[k])
    assert np.isfinite(np.delete(eq.ses, k)).all()


def test_event_quality_ignores_epochs_in_unobserved_quarters():
    lc = four_quarter_planet()
    eq = event_quality(lc.time, detrend(lc), FQ_P, FQ_T0, FQ_DUR)
    mids = FQ_T0 + FQ_P * np.arange(-20, 200)
    inside = sum(int(np.count_nonzero((mids >= lc.time[lc.quarter == q].min()) & (mids <= lc.time[lc.quarter == q].max())))
                 for q in FQ_QUARTERS)
    assert inside > 25 and abs(eq.epochs.size - inside) <= 0.1 * inside, (eq.epochs.size, inside)
    assert np.all(eq.coverage >= 0.9), eq.coverage


def test_event_quality_counts_masked_cadences_as_observed():
    """A detector mask (a second-pass signal is vetted with the first-pass eclipses NaN) is not missing data: with
    ``observed`` from the unmasked flux the masked cadences still count towards coverage, while SES uses only the
    finite points. The mask is a 0.72 d binary's primary at +-0.144 d, 40 percent of the points."""
    period, t0, dur = 2.1, 133.6, 2.2 / 24
    lc = inject_transit(make_lightcurve(n_days=210, noise_ppm=600, seed=13, quarters=False, gaps=False),
                        period, t0, dur, 1500)
    fn = detrend(lc)
    masked = apply_mask(lc.time, fn, [{"period": 0.72, "t0": 131.9, "half_primary_days": 0.144,
                                       "half_secondary_days": 0.0}])
    frac = np.isnan(masked[np.isfinite(fn)]).mean()
    assert 0.35 < frac < 0.45, frac
    plain = event_quality(lc.time, masked, period, t0, dur)
    assert np.mean(plain.coverage >= EVENT_COVERAGE_MIN) < 0.9          # without ``observed`` the mask reads as gaps
    eq = event_quality(lc.time, masked, period, t0, dur, observed=np.isfinite(fn))
    assert eq.epochs.size > 90 and np.all(eq.coverage >= 0.9), eq.coverage
    ph = fold_phase(lc.time, period, t0)
    ep = np.round((lc.time - t0) / period).astype(int)
    ok = np.isfinite(masked)
    half = dur / 2
    for i, e in enumerate(eq.epochs):
        s = ok & (ep == e)
        n_box = np.count_nonzero(s & (np.abs(ph) < 0.8 * half))
        n_base = np.count_nonzero(s & (np.abs(ph) > 1.5 * half) & (np.abs(ph) < max(0.5, 4 * half)))
        if n_box >= 3 and n_base >= 20:
            assert np.isfinite(eq.ses[i]), (e, n_box, n_base)
        else:
            assert np.isnan(eq.ses[i]), (e, n_box, n_base)


def _chases_curve(extra=()):
    return _box_curve(EP, ET0, EDUR, 3000, n_days=130, extra=extra)


def test_event_quality_chases_is_near_one_for_clean_events():
    lc = _chases_curve()
    eq = event_quality(lc.time, detrend(lc), EP, ET0, EDUR)
    assert eq.epochs.size == 4 and eq.chases is not None
    c = eq.chases[np.isfinite(eq.chases)]
    assert c.size == 4 and np.all(c > 0.8) and np.all(c <= 1.0)


def test_event_quality_chases_drops_to_zero_next_to_a_comparable_dip():
    lc = _chases_curve(extra=[(1e4, ET0 + 2 * EP + 1.2, EDUR, 3000)])      # once, 1.2 d after the third event
    eq = event_quality(lc.time, detrend(lc), EP, ET0, EDUR)
    hit = int(np.where(eq.epochs == 2)[0][0])
    others = np.arange(eq.epochs.size) != hit
    assert eq.chases[hit] < 0.5 and np.all(eq.chases[others] > 0.8), eq.chases


def test_event_quality_chases_is_none_above_five_events():
    lc = _box_curve(EP, ET0, EDUR, 3000)
    eq = event_quality(lc.time, detrend(lc), EP, ET0, EDUR)
    assert eq.epochs.size > 5 and eq.chases is None
    short = _box_curve(EP, ET0, EDUR, 3000, n_days=130)
    assert event_quality(short.time, detrend(short), EP, ET0, EDUR).chases is not None


def _direct_sigma(time, fn, period, t0, duration, phases):
    """The plain definition, one phase at a time: box of circular distance below half a duration, baseline at least
    1.5 durations from the box centre and from phase 0, sigma = depth / (1.4826 MAD / sqrt(n_box)); NaN with fewer
    than 5 box points, 50 baseline points or a zero MAD."""
    good = np.isfinite(fn)
    cyc, y = fold_phase(time[good], period, t0) / period, fn[good]
    w = duration / period
    want = np.full(phases.size, np.nan)
    for k, c in enumerate(phases):
        d = _circular(cyc - c, 1.0)
        inside, base = d < 0.5 * w, (np.abs(cyc) >= 1.5 * w) & (d >= 1.5 * w)
        if inside.sum() < 5 or base.sum() < 50:
            continue
        lvl = np.median(y[base]); scat = 1.4826 * np.median(np.abs(y[base] - lvl))
        if scat > 0:
            want[k] = (1 - y[inside].mean() / lvl) / (scat / np.sqrt(inside.sum()))
    return want


def _quantised(lc, period, t0, dur):
    fn = np.round(detrend(lc), 4)               # many tied values
    fn[::7] = np.nan
    return lc.time, fn, period, t0, dur


def _constant_with_dips():
    """Flux exactly 1 except a primary and a secondary dip: every baseline is more than half exact ties (MAD 0)."""
    lc = _box_curve(UP, UT0, UDUR, 0, n_days=300)
    f = np.ones(lc.time.size)
    for centre in (UT0, UT0 + 0.37 * UP):
        f[np.abs(fold_phase(lc.time, UP, centre)) < 0.5 * UDUR] = 0.998
    return lc.time, f, UP, UT0, UDUR


@pytest.mark.parametrize("name", ["transit_and_second_dip", "sub_day_period_wide_box", "zero_mad_baseline"])
def test_phase_significance_matches_a_direct_evaluation_at_every_phase(name):
    """The sliding box is evaluated from sorted sums and ranks; every phase, NaN pattern included, must equal the
    plain definition. The cases: a quantised curve with NaN points and two dips (ties, varying parity of the baseline
    count); a 0.9 d period with a 0.2 d box, where the exclusion windows take most of the fold and some phases have too
    thin a baseline; a flux of exact ties, whose baseline MAD is 0 (sigma NaN, not 1e15)."""
    if name == "transit_and_second_dip":
        lc = _box_curve(UP, UT0, UDUR, 1500, n_days=300, extra=[(UP, UT0 + 0.37 * UP, UDUR, 800)])
        case = _quantised(lc, UP, UT0, UDUR)
    elif name == "sub_day_period_wide_box":
        lc = _box_curve(0.9, 140.3, 0.2, 3000, n_days=130)
        case = _quantised(lc, 0.9, 140.3, 0.2)
    else:
        case = _constant_with_dips()
    ps = phase_significance(*case)
    want = _direct_sigma(*case, ps.phases)
    np.testing.assert_allclose(ps.sigma, want, atol=1e-8, equal_nan=True)
    if name == "zero_mad_baseline":
        assert np.isnan(ps.sigma).all() and np.isnan(ps.ms2)
    else:
        assert np.isfinite(ps.sigma).sum() > 0
    if name == "sub_day_period_wide_box":
        assert np.isnan(ps.sigma).sum() > 0                  # the NaN pattern is compared, not vacuous


def test_event_quality_guards_duplicated_stamps_and_brightenings():
    t = np.array([100.0, 100.0, 100.0, 101.0, 101.0, 102.0])
    eq = event_quality(t, np.ones(t.size), 1.0, 100.0, 0.1)
    assert eq.epochs.size > 0 and np.isnan(eq.coverage).all()               # cadence 0: no division by zero
    lc = _chases_curve(extra=[(1e4, ET0 + EP, EDUR, -6000)])                # the second event: net +3000 ppm
    eq = event_quality(lc.time, detrend(lc), EP, ET0, EDUR)
    assert eq.ses[1] < -3 and np.isnan(eq.chases[1]) and np.isfinite(eq.chases[[0, 2, 3]]).all()
