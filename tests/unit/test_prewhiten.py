"""Harmonic pre-whitening: iterative removal of significant sinusoidal components (to the Nyquist frequency) before
the box search, so pulsators and rotational variables neither hide transits nor masquerade as them."""
import numpy as np

from skysift.config import Thresholds
from skysift.detectors.box_dip import BoxDip
from skysift.lc.ops import detrend
from skysift.lc.prewhiten import prewhiten, prewhiten_model
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
