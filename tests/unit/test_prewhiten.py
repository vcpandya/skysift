"""Harmonic pre-whitening: iterative removal of significant sinusoidal components (to the Nyquist frequency) before
the box search, so pulsators and rotational variables neither hide transits nor masquerade as them."""
import numpy as np

from skysift.config import Thresholds
from skysift.detectors.box_dip import BoxDip
from skysift.lc.ops import detrend, stacked_depth
from skysift.lc.prewhiten import _box_like, _design, prewhiten, prewhiten_model
from skysift.models import LightCurve
from tests.synthetic import inject_transit, make_lightcurve

WIDE = Thresholds(min_period_days=0.5, max_period_days=15.0)
PULSATION = [(15.0479, 1100.0), (13.4216, 600.0), (16.0676, 500.0)]      # the delta Scuti gaia-dr3-2052626821369997696


def _with_waves(lc: LightCurve, waves, phase0=0.3) -> LightCurve:
    f = lc.flux.copy()
    for k, (freq, amp) in enumerate(waves):
        f *= 1 - amp * 1e-6 * np.cos(2 * np.pi * (freq * lc.time + phase0 * (k + 1)))
    return LightCurve(lc.time, f, lc.flux_err, lc.quality, lc.quarter, f.copy(), dict(lc.meta))


def test_removes_a_delta_scuti_forest_and_its_subharmonic_box_detections():
    lc = _with_waves(make_lightcurve(n_days=300, seed=11, noise_ppm=500), PULSATION)
    fn = detrend(lc)
    raw = BoxDip(mask_iterations=0).detect(lc, fn, WIDE)
    assert raw and raw[0].sde > 9, "the pulsation must produce sub-harmonic box detections before whitening"
    pw = prewhiten(lc.time, fn)
    found = sorted(c.frequency for c in pw.components)
    assert len(pw.components) >= 3
    for freq, _ in PULSATION:
        assert min(abs(f - freq) for f in found) < 0.01, (freq, found)
    good = np.isfinite(fn)
    assert np.std(pw.residual[good]) < 1.25 * 500e-6
    white = BoxDip(mask_iterations=0).detect(lc, pw.residual, WIDE)
    assert all(s.sde < 9 for s in white), [(s.period, s.sde) for s in white]
    # the stored model reproduces the subtraction, so the dashboard can show the same flux without refitting
    model = prewhiten_model(lc.time, pw.components)
    assert np.allclose((fn - model)[good], pw.residual[good], atol=1e-9)


def test_a_transit_behind_the_pulsation_keeps_its_depth_and_detection():
    base = make_lightcurve(n_days=300, seed=12, noise_ppm=500)
    clean = inject_transit(base, 9.9, 133.1, 2.5 / 24, 900)
    noisy = _with_waves(clean, PULSATION)
    det = BoxDip(mask_iterations=0)
    ref = next(s for s in det.detect(clean, detrend(clean), WIDE) if abs(s.period - 9.9) < 0.02)
    fn = detrend(noisy)
    pw = prewhiten(noisy.time, fn)
    got = next((s for s in det.detect(noisy, pw.residual, WIDE) if abs(s.period - 9.9) < 0.02), None)
    assert got is not None and got.sde > 9
    # depths differ through the duration grid (a 2 h or 3 h box around a 2.5 h transit), so both are held to the
    # injected 900 ppm rather than to each other
    assert got.sde > 0.8 * ref.sde and abs(got.depth_ppm - 900) < 0.3 * 900 and abs(ref.depth_ppm - 900) < 0.3 * 900
    assert all(abs(c.frequency - 1 / 9.9) > 0.01 for c in pw.components), "the transit itself is never a component"


def test_box_shaped_eclipses_are_left_alone():
    """An eclipsing binary is periodic but not sinusoidal: its fold is a box, so the shape guard keeps it for the
    detector (and the EB branch) instead of whitening it away."""
    lc = inject_transit(make_lightcurve(n_days=300, seed=13, noise_ppm=500), 0.7, 131.9, 3 / 24, 3000)
    lc = inject_transit(lc, 0.7, 131.9 + 0.35, 3 / 24, 1500)
    fn = detrend(lc)
    pw = prewhiten(lc.time, fn)
    f_eb = 1 / 0.7
    assert all(abs(c.frequency / f_eb - round(c.frequency / f_eb)) > 0.02 for c in pw.components), [c.frequency for c in pw.components]
    sig = BoxDip(mask_iterations=0).detect(lc, pw.residual, WIDE)
    assert sig and abs(sig[0].period - 0.7) < 0.002 and sig[0].sde > 50


def test_flat_curve_yields_no_components_and_an_unchanged_residual():
    lc = make_lightcurve(n_days=300, seed=14, noise_ppm=500)
    fn = detrend(lc)
    pw = prewhiten(lc.time, fn)
    assert pw.components == []
    good = np.isfinite(fn)
    assert np.array_equal(pw.residual[good], fn[good]) and np.isnan(pw.residual[~good]).all()
    assert prewhiten(lc.time, fn, max_components=0).components == []


def test_component_record_is_plain_json_friendly():
    lc = _with_waves(make_lightcurve(n_days=120, seed=15, noise_ppm=500), PULSATION[:1])
    pw = prewhiten(lc.time, detrend(lc))
    c = pw.components[0]
    d = c.as_dict()
    assert set(d) == {"frequency", "amplitude_ppm", "snr", "coef"} and len(d["coef"]) == 5
    assert abs(d["frequency"] - PULSATION[0][0]) < 0.01 and abs(d["amplitude_ppm"] - PULSATION[0][1]) < 0.2 * PULSATION[0][1]


SHORT_P, SHORT_T0, SHORT_DUR, SHORT_DEPTH = 0.6056, 131.9, 1 / 24, 130.0      # K04802.01: 0.6056 d, 1 h, 130 ppm


def _short_transit_curve(noise_ppm):
    return inject_transit(make_lightcurve(n_days=1400, seed=61, noise_ppm=noise_ppm), SHORT_P, SHORT_T0, SHORT_DUR,
                          SHORT_DEPTH)


def test_guard_catches_a_short_transit_at_a_smeared_frequency():
    """The Lomb-Scargle peak of a short box sits up to 1.5 resolution elements off the true frequency; folded there a
    1 h dip drifts 1.5 cycles over the baseline and smears over every phase, so the single-frequency guard sees no box.
    The scan around the peak folds at the true frequency too and recognises the transit."""
    lc = _short_transit_curve(200)
    fn = detrend(lc)
    good = np.isfinite(fn)
    t, y = lc.time[good], fn[good]
    resolution = 1.0 / (t.max() - t.min())
    f0 = 1 / SHORT_P + 1.5 * resolution
    coef = [float(c) for c in np.linalg.lstsq(_design(t, f0), y, rcond=None)[0]]
    assert not _box_like(t, y, f0, coef, resolution, scan=False), "the single fold at the smeared peak sees no box"
    assert _box_like(t, y, f0, coef, resolution)


def test_prewhiten_keeps_a_shallow_short_transit():
    """End to end: three 40 ppm waves of a few days (the kind K04802's star has) are removed and the 130 ppm, 1 h
    transit keeps its stacked depth. The waves are added to the detrended flux, so they enter at 40 ppm whatever the
    2 d running median would have taken from them."""
    lc = _short_transit_curve(500)
    fn = detrend(lc)
    waves = [(2.9, 0.1), (3.1, 0.7), (5.5, 1.9)]
    for period, phase in waves:
        fn = fn + 40e-6 * np.cos(2 * np.pi * (lc.time / period + phase))
    before = stacked_depth(lc.time, fn, SHORT_P, SHORT_T0, SHORT_DUR).depth_ppm
    pw = prewhiten(lc.time, fn)
    after = stacked_depth(lc.time, pw.residual, SHORT_P, SHORT_T0, SHORT_DUR).depth_ppm
    found = [c.frequency for c in pw.components]
    for period, _ in waves:
        assert min((abs(f - 1 / period) for f in found), default=np.inf) < 0.002, (period, found, pw.skipped)
    assert abs(after - before) < 0.15 * before, f"stacked depth {before:.0f} ppm before whitening, {after:.0f} after"


def test_guard_scan_ignores_boxes_narrower_than_a_cadence():
    """At a 95.7 min period every short box width of the scan is under a third of the 29.4 min cadence, so each
    trial holds one or two points per cycle and the box-versus-sine contrast on it is noise; on the real delta Scuti
    gaia-dr3-2052626821369997696 the scan refused the star's own 15.06 cycles per day pulsation as box-like, left it
    in, and its sub-harmonics came out of the search at SDE 42. Boxes narrower than GUARD_SCAN_MIN_WIDTH cadences are
    not tried, so the pulsation is removed. (A synthetic 400 d copy of the pulsation does not reproduce the refusal
    even with the floor off; the real star does, measured on its cached file.)"""
    from skysift.lc import prewhiten as pwm
    lc = _with_waves(make_lightcurve(n_days=400, seed=23, noise_ppm=800), PULSATION[:1])
    fn = detrend(lc)
    good = np.isfinite(fn)
    t, y = lc.time[good], fn[good]
    f0 = PULSATION[0][0]
    resolution = 1.0 / (t.max() - t.min())
    coef = list(np.linalg.lstsq(_design(t, f0), y, rcond=None)[0])
    cadence = float(np.median(np.diff(t)))
    assert all(frac / f0 < cadence for frac in pwm.GUARD_BOX_FRACTIONS if frac <= pwm.GUARD_SCAN_MAX_FRACTION)
    assert _box_like(t, y, f0, coef, resolution) is False
    pw = prewhiten(lc.time, fn, max_components=5, fap_max=1e-6)
    assert pw.skipped == [] and any(abs(c.frequency - f0) < 2 * resolution for c in pw.components), pw.skipped


def test_guard_scan_survives_a_peak_at_the_first_grid_frequency():
    """Astropy's grid starts at exactly 2 / baseline, and a wave of half the baseline peaks there; the guard scan
    reaches down 2 resolution elements, so its lowest trial frequency was exactly 0 and ``1.0 / 0.0`` raised
    ZeroDivisionError, which errored the target and charged it an attempt. Trials at or below half a resolution
    element are skipped: the wave is removed or refused, either way the loop returns."""
    t = np.linspace(0.0, 90.0, 90 * 48 + 1)                 # 30 min cadence, baseline exactly 90 d
    flux = 1 + 0.01 * np.sin(2 * np.pi * t / 45.0)          # period half the baseline: the first grid frequency
    pw = prewhiten(t, flux, max_components=3, fap_max=1e-6)
    near = [f for f in [c.frequency for c in pw.components] + pw.skipped if abs(f - 1 / 45.0) < 0.5 / 90.0]
    assert near, (pw.components, pw.skipped)
