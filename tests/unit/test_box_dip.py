import functools
import itertools
import json
import time

import numpy as np
import pytest

from skysift.config import Thresholds
from skysift.detectors.box_dip import BoxDip
from skysift.lc.ops import detrend, stacked_depth
from skysift.models import LightCurve, Signal
from tests.synthetic import inject_transit, make_lightcurve

NARROW = Thresholds(min_period_days=5.0, max_period_days=40.0)


def test_recovers_injected_transit():
    lc = inject_transit(make_lightcurve(n_days=500, seed=1), 13.769, 136.3, 0.09, 2000)
    sig = BoxDip().detect(lc, detrend(lc), NARROW)
    assert sig and abs(sig[0].period - 13.769) < 0.002 and sig[0].sde > 9 and sig[0].n_events >= 30
    assert 1500 < sig[0].depth_ppm < 2500
    assert sig[0].snr > Thresholds().snr_min
    assert sig[0].extra["alias_of"] is None and sig[0].extra["n_periods"] > 0
    coarse = BoxDip(two_stage=False).detect(lc, detrend(lc), NARROW)
    assert coarse and abs(coarse[0].period - sig[0].period) < 0.002 and coarse[0].sde > 9
    assert any(s.extra["alias_of"] == 0 for s in sig[1:])
    assert all(isinstance(s.extra["band"], int) and s.extra["sde_spectrum_std"] > 0 for s in sig)
    for s in sig:
        if s.extra["alias_of"] == 0:
            ratio = s.period / sig[0].period
            assert any(abs(ratio / r - 1) < 0.01 for r in (2, 3, 0.5, 1 / 3))


def test_recovers_deep_transit():
    lc = inject_transit(make_lightcurve(n_days=500, seed=4), 13.769, 136.3, 0.09, 20000)
    sig = BoxDip().detect(lc, detrend(lc), NARROW)
    assert sig and abs(sig[0].period - 13.769) < 0.002
    assert abs(sig[0].depth_ppm - 20000) < 0.15 * 20000 and sig[0].n_events >= 30
    assert all(a.sde >= b.sde for a, b in itertools.pairwise(sig))


@pytest.mark.parametrize("seed", [7, 11, 21])
def test_pure_noise_stays_below_threshold(seed):
    # Mirror padding no longer flattens the noise at segment edges, so noise peaks rose a little: seed 7's 19.585 d
    # peak went from SDE 8.82 to 9.39, and to 9.45 once the data start was masked too (seeds 11 and 21 peak at 6.21
    # and 6.57), over the old bound of 9. The bound is 10: tight enough to catch a real rise in the noise floor, and
    # still below sde_min (11).
    lc = make_lightcurve(n_days=500, seed=seed)
    sig = BoxDip().detect(lc, detrend(lc), NARROW)
    assert all(s.sde < 10 for s in sig), [(s.period, s.sde) for s in sig]


def test_short_or_empty_lightcurve_returns_nothing():
    lc = make_lightcurve(n_days=5, seed=2)
    assert BoxDip().detect(lc, detrend(lc), Thresholds()) == []
    nan_lc = LightCurve(np.arange(1000.0), np.full(1000, np.nan), np.ones(1000), np.zeros(1000, int), np.ones(1000, int))
    assert BoxDip().detect(nan_lc, detrend(nan_lc), Thresholds()) == []
    flat = LightCurve(np.arange(1000.0), np.ones(1000), np.ones(1000), np.zeros(1000, int), np.ones(1000, int))
    assert BoxDip().detect(flat, np.ones(1000), Thresholds()) == []


@pytest.mark.slow
def test_full_baseline_timing():
    """Full 1400 d curve over the default period window (~240 s); deselected by default, run with `-m slow`."""
    lc = inject_transit(make_lightcurve(seed=1), 13.769, 136.3, 0.09, 2000)
    fn = detrend(lc)
    t0 = time.perf_counter()
    sig = BoxDip().detect(lc, fn, Thresholds())
    elapsed = time.perf_counter() - t0
    assert sig and abs(sig[0].period - 13.769) < 0.002 and sig[0].sde > 9
    assert elapsed < 400


def test_min_period_below_the_shortest_trial_duration_does_not_crash():
    # 1 h is the shortest trial duration: a 0.03 d minimum period used to hand astropy duration >= period
    lc = inject_transit(make_lightcurve(n_days=15, seed=2, noise_ppm=500), 0.7, 132.0, 0.05, 3000)
    sig = BoxDip().detect(lc, detrend(lc), Thresholds(min_period_days=0.03, max_period_days=1.0))
    assert sig and all(s.duration < 0.9 * s.period for s in sig)
    assert any(abs(s.period / 0.7 - round(s.period / 0.7)) < 0.01 for s in sig)


WIDE = Thresholds(min_period_days=0.5, max_period_days=15.0)


def _binary(with_planet: bool, seed: int = 3):
    """A 0.7 d eclipsing binary (3000 ppm primary, 1500 ppm secondary at phase 0.5) on a 300 d curve, optionally with
    a 9.9 d, 900 ppm, 2.5 h transit on top: the real case of gaia-dr3-2075988698983795072 and KOI K04209.01."""
    lc = make_lightcurve(n_days=300, seed=seed, noise_ppm=500)
    lc = inject_transit(lc, 0.7, 131.9, 3 / 24, 3000)
    lc = inject_transit(lc, 0.7, 131.9 + 0.35, 3 / 24, 1500)
    return inject_transit(lc, 9.9, 133.1, 2.5 / 24, 900) if with_planet else lc


def test_second_pass_recovers_a_planet_hidden_behind_a_dominant_binary():
    lc = _binary(with_planet=True)
    sig = BoxDip().detect(lc, detrend(lc), WIDE)
    first = [s for s in sig if s.extra.get("pass", 1) == 1]
    second = [s for s in sig if s.extra.get("pass") == 2]
    assert first and abs(first[0].period - 0.7) < 0.002 and first[0].sde >= 9
    assert all(abs(s.period - 9.9) > 0.05 for s in first), "the binary's aliases fill the first pass"
    assert len(second) == 1
    s2 = second[0]
    assert abs(s2.period - 9.9) < 0.02 and s2.sde >= 9
    assert abs(s2.depth_ppm - 900) < 0.3 * 900
    assert abs(s2.extra["masked_period"] - first[0].period) < 1e-9 and s2.extra["alias_of"] is None
    assert sig.index(s2) == len(sig) - 1, "second-pass signals come after every first-pass peak"


def test_second_pass_adds_nothing_when_only_the_binary_is_there():
    lc = _binary(with_planet=False)
    sig = BoxDip().detect(lc, detrend(lc), WIDE)
    assert sig and abs(sig[0].period - 0.7) < 0.002
    assert not [s for s in sig if s.extra.get("pass") == 2]


def test_gap_ramps_do_not_hide_a_long_period_transit():
    """Thermal ramps after every data gap (1 percent, decaying over 0.5 d) left in the flux line up at many long trial
    periods and bury a real 13.0 d transit; the gap-edge mask in ``detrend`` removes them. Two searches.

    Besides the quarter gaps the curve has Kepler's monthly downlinks, 0.9 d gaps every 25 to 35 d: with the 15
    quarter gaps alone no trial period lines up more than one ramp and the mask changed nothing (SDE 46.0 against 45.9
    for a 400 ppm transit, 1.0 d mask, 1400 d). The downlink gaps are shorter than ``gap_days``, so this also checks
    the 0.5 d trigger. The curve is 700 d, half the Kepler baseline, to keep the test near 50 s instead of 130 s.
    Measured: SDE 97.5 masked against 14.1 unmasked (157.3 against 25.6 on 1400 d)."""
    base = make_lightcurve(n_days=700, seed=7, noise_ppm=600)
    rng = np.random.default_rng(1)
    keep = np.ones(base.time.size, bool)
    g = base.time[0] + rng.uniform(25, 35)
    while g < base.time[-1]:
        keep &= (base.time < g) | (base.time >= g + 0.9)
        g += rng.uniform(25, 35)
    t, f = base.time[keep], base.flux[keep].copy()
    for start in t[1:][np.diff(t) > 0.5]:                            # the first point after each gap
        after = t >= start
        f[after] *= 1 - 0.01 * np.exp(-(t[after] - start) / 0.5)
    lc = LightCurve(t, f, base.flux_err[keep], base.quality[keep], base.quarter[keep], f.copy())
    lc = inject_transit(lc, 13.0, 140.0, 0.25, 1500)
    det = BoxDip(mask_iterations=0)
    masked = det.detect(lc, detrend(lc), NARROW)
    res = det.search(lc, detrend(lc, edge_mask_days=0), NARROW, keep_spectrum=True)
    near = [s.sde for s in res.signals if abs(s.period - 13.0) < 0.01]
    spec = res.spectrum
    sde_off = near[0] if near else float(np.max(spec["sde"][np.abs(spec["period"] / 13.0 - 1) < 0.001]))
    msg = f"masked: top {masked[0].period:.4f} d, SDE {masked[0].sde:.2f}; unmasked: SDE at 13.0 d {sde_off:.2f}, " \
          f"top {res.signals[0].period:.4f} d, SDE {res.signals[0].sde:.2f}"
    assert abs(masked[0].period - 13.0) < 0.01, msg
    assert masked[0].sde >= 1.5 * sde_off, msg


def test_flat_curve_runs_a_single_pass(monkeypatch):
    det = BoxDip()
    calls = []
    orig = det._coarse_search
    monkeypatch.setattr(det, "_coarse_search", lambda *a, **k: (calls.append(1), orig(*a, **k))[1])
    sig = det.detect(make_lightcurve(n_days=300, seed=7, noise_ppm=500), detrend(make_lightcurve(n_days=300, seed=7, noise_ppm=500)), WIDE)
    assert sig and all(s.sde < 9 for s in sig)
    assert len(calls) == 1
    assert BoxDip(mask_iterations=0).mask_iterations == 0


def test_is_alias_covers_small_integer_ratios():
    from skysift.detectors.box_dip import _is_alias
    earlier = [0.7]
    assert _is_alias(1.4, earlier) and _is_alias(0.35, earlier) and _is_alias(1.05, earlier) and _is_alias(0.7 * 4 / 3, earlier)
    assert _is_alias(0.7 * 1.004, earlier) and _is_alias(0.7 * 5 / 4, earlier)        # p/q up to 8 since 18 October
    assert not _is_alias(9.9, earlier) and not _is_alias(0.7 * 9 / 8, earlier) and not _is_alias(0.7 * 1.02, earlier)


def test_is_alias_catches_combination_frequencies_of_a_multiperiodic_variable():
    """The real sidelobe: f3 = 2 f1 - f2 for the 0.6355 and 0.7216 d peaks of gaia-dr3-2075988698983795072, matched
    only to the spectral resolution of the baseline, so an unrelated 9.9 d planet is never caught by it."""
    from skysift.detectors.box_dip import _is_alias
    earlier = [0.6354676, 0.7216462]
    f_side = 2 / earlier[0] - 1 / earlier[1]
    assert _is_alias(1 / f_side, earlier, baseline=1430.0)
    assert _is_alias(1 / (1 / earlier[0] + 1 / earlier[1]), earlier, baseline=1430.0)
    assert not _is_alias(1 / f_side, earlier)                       # without a baseline only p/q ratios count
    assert not _is_alias(1 / (f_side + 0.01), earlier, baseline=1430.0)
    assert not _is_alias(9.909, earlier, baseline=1430.0) and not _is_alias(9.909, earlier[:1], baseline=1430.0)


ALIAS_WINDOW = Thresholds(min_period_days=0.5, max_period_days=15.0)


@functools.cache
def _half_period_planet():
    """A 6.5 d, 20000 ppm planet that the per-band SDE ranks at 3.25 d (SDE 146.9 against 136.9 at 6.5 d; 147.3
    against 136.0 before the start of the data was masked as a gap edge). One search
    with the alias resolution on and one with it off, shared by the tests below. Seed 68 since the gap-edge mask and
    mirror padding of 17 October 2026: under them the seed-42 curve (145.9 against 137.4 before) ranks at 6.5 d."""
    lc = inject_transit(make_lightcurve(n_days=500, seed=68, noise_ppm=1500), 6.5, 133.0, 0.1, 20000)
    fnorm = detrend(lc)
    on = BoxDip(mask_iterations=0).detect(lc, fnorm, ALIAS_WINDOW)
    off = BoxDip(mask_iterations=0, alias_resolution=0).detect(lc, fnorm, ALIAS_WINDOW)
    return lc, fnorm, on, off


def test_deep_transit_ranked_at_half_period_is_doubled():
    _, _, sig, off = _half_period_planet()
    assert abs(sig[0].period - 6.5) < 0.01
    resolved = sig[0].extra["alias_resolved"]
    assert abs(resolved["from_period"] - 3.25) < 0.01 and resolved["factor"] == 2 and resolved["factors"] == [2]
    assert resolved["class_depths_ppm"][0] > 15000 or resolved["class_depths_ppm"][1] > 15000
    assert resolved["contrast_sigma"] > 5
    json.dumps(resolved, allow_nan=False)                          # strict JSON
    assert abs(sig[0].depth_ppm - 20000) < 0.15 * 20000
    assert all(abs(s.period - 6.5) / 6.5 >= 0.02 for s in sig[1:])
    # the dropped 6.5 d candidate lends its refined SDE at the resolved period; the resolved signal keeps the larger
    # of that and the 3.25 d peak that made the detection (146.9 against 136.9), and records both
    dropped = [s for s in off if abs(s.period - 6.5) / 6.5 < 0.02]
    assert dropped and abs(resolved["sde_resolved"] - dropped[0].sde) < 1e-9
    assert abs(resolved["sde_original"] - off[0].sde) < 1e-9 and off[0].sde > dropped[0].sde
    assert abs(sig[0].sde - max(off[0].sde, dropped[0].sde)) < 1e-9
    assert sig[0].extra["band"] == off[0].extra["band"]           # the band of the SDE kept
    # a lower-ranked P/3 peak is resolved too and collapses onto the 6.5 d signal; anything left is flagged
    assert all(abs(s.period - 6.5 / 3) / (6.5 / 3) >= 0.02 for s in sig[1:])
    assert all(s.extra["alias_of"] == 0 for s in sig[1:] if abs(s.period - 6.5 / 4) < 0.01)


def test_the_duplicate_drop_after_doubling_is_three_coarse_steps_wide():
    """Only a lower-ranked peak at the resolved period itself is dropped: three coarse frequency steps at the trial
    nearest the new period, or 0.5 percent without a spectrum. An unrelated signal 2 percent away stays."""
    from skysift.detectors.box_dip import _duplicates

    def sig(p):
        return Signal(target_id="x", detector="box_dip", detector_version="1", period=p, t0=0.0, duration=0.1,
                      depth_ppm=1000, snr=10, sde=10, n_events=10)
    spec = {"period": np.array([6.0, 6.5, 7.0]), "df": np.array([1e-4, 2e-5, 1e-4])}
    near, edge, far = sig(1 / (1 / 6.5 + 5e-5)), sig(1 / (1 / 6.5 + 7e-5)), sig(6.5 * 1.02)
    assert _duplicates(6.5, [near, edge, far], spec) == [near]
    assert _duplicates(6.5, [sig(6.5 * 1.004), sig(6.5 * 1.006), far], None) == [sig(6.5 * 1.004)]


def test_unequal_eclipses_are_doubled_to_the_binary_period():
    lc = inject_transit(make_lightcurve(n_days=500, seed=42, noise_ppm=800), 4.0, 133.0, 0.1, 20000)
    lc = inject_transit(lc, 4.0, 135.0, 0.1, 6000)                      # secondary a third as deep
    fnorm = detrend(lc)
    sig = BoxDip(mask_iterations=0).detect(lc, fnorm, ALIAS_WINDOW)
    assert abs(sig[0].period - 4.0) < 0.01 and "alias_resolved" in sig[0].extra
    m = np.isfinite(fnorm)
    on_t0 = stacked_depth(lc.time[m], fnorm[m], sig[0].period, sig[0].t0, sig[0].duration)
    assert on_t0.depth_ppm > 15000, "t0 sits on the primary eclipse"
    assert sig[0].extra["alias_resolved"]["factor"] == 2
    # a lower-ranked P/4 or P/2 peak resolves (through P/2 for the quarter) onto the binary period and is dropped
    assert all(abs(s.period - a) / a >= 0.02 for s in sig[1:] for a in (1.0, 2.0)), "sub-harmonics collapse"


def test_correctly_ranked_planet_keeps_its_period():
    lc = inject_transit(make_lightcurve(n_days=500, seed=41, noise_ppm=1500), 3.0, 133.0, 0.06, 20000)
    sig = BoxDip(mask_iterations=0).detect(lc, detrend(lc), ALIAS_WINDOW)
    assert abs(sig[0].period - 3.0) < 0.003 and "alias_resolved" not in sig[0].extra


def test_alias_resolution_off_leaves_the_ranking():
    _, _, _, off = _half_period_planet()
    assert abs(off[0].period - 3.25) < 0.01 and "alias_resolved" not in off[0].extra


def test_doubling_stays_inside_the_search_window():
    lc, fnorm, _, off = _half_period_planet()
    m = np.isfinite(fnorm)
    det = BoxDip(mask_iterations=0)
    assert det._resolve_alias(lc.time[m], fnorm[m], off[0], 6.0, None) is off[0]       # 6.5 d is past pmax
    assert abs(det._resolve_alias(lc.time[m], fnorm[m], off[0], 15.0, None).period - 6.5) < 0.01


SUB_WINDOW = Thresholds(min_period_days=0.5, max_period_days=30.0)
ECLIPSE_P, ECLIPSE_T0, ECLIPSE_DUR, ECLIPSE_PPM = 24.0, 140.0, 4 / 24, 5000


@functools.cache
def _long_period_eclipse():
    """A 24.0 d, 4 h, 5000 ppm box on a 1400 d curve (seed 51, 800 ppm noise): the K06356.01 configuration of the
    live run, whose first pass held 24.09/2, /3, /7 and /35 d."""
    lc = inject_transit(make_lightcurve(n_days=1400, seed=51, noise_ppm=800), ECLIPSE_P, ECLIPSE_T0, ECLIPSE_DUR,
                        ECLIPSE_PPM)
    return lc, detrend(lc)


def test_deep_eclipse_ranked_at_a_third_of_its_period_is_resolved():
    """Whatever period BLS ranks first, one object leaves one signal at its true period. Measured 18 October 2026:
    the raw first pass ranks 24.0 d first (SDE 173.9), then 12.0 (126.7) and 8.0 d (110.6), which the resolver
    folds back onto 24.0 d and drops as duplicates; the factor is asserted only when a sub-harmonic ranked first."""
    from skysift.detectors.box_dip import MAX_SUBHARMONIC
    lc, fnorm = _long_period_eclipse()
    sig = BoxDip(mask_iterations=0).detect(lc, fnorm, SUB_WINDOW)
    off = BoxDip(mask_iterations=0, alias_resolution=0).detect(lc, fnorm, SUB_WINDOW)
    assert abs(sig[0].period - ECLIPSE_P) < 0.02
    if abs(off[0].period - ECLIPSE_P) >= 0.02:                     # the raw ranking put a sub-harmonic first
        resolved = sig[0].extra["alias_resolved"]
        assert resolved["factor"] in {2, 3, 4, 5, 6, 7}
        assert abs(resolved["from_period"] - off[0].period) < 1e-9
    aliases = [ECLIPSE_P / n for n in range(1, MAX_SUBHARMONIC + 1)]
    assert all(abs(s.period - a) / a >= 0.02 for s in sig[1:] for a in aliases)


def test_resolver_handles_every_residue_class():
    """A hand-built signal at P/7, t0 on each of the seven sub-harmonic epochs in turn (the first on an eclipse), so
    the eclipses sit in every residue class modulo 7 once: each resolves to factor 7 with t0 on an eclipse."""
    lc, fnorm = _long_period_eclipse()
    m = np.isfinite(fnorm)
    t, y, p = lc.time[m], fnorm[m], ECLIPSE_P / 7
    det = BoxDip()
    for shift in range(7):
        hand = Signal(target_id="x", detector="box_dip", detector_version=det.version, period=p,
                      t0=ECLIPSE_T0 + 3 * ECLIPSE_P + shift * p, duration=ECLIPSE_DUR, depth_ppm=ECLIPSE_PPM / 7,
                      snr=10, sde=20, n_events=300, extra={"band": 11, "sde_spectrum_std": 1.0, "alias_of": None})
        r = det._resolve_alias(t, y, hand, SUB_WINDOW.max_period_days, None)
        resolved = r.extra["alias_resolved"]
        assert resolved["factor"] == 7 and resolved["factors"] == [7] and len(resolved["class_depths_ppm"]) == 7
        assert abs(r.period - ECLIPSE_P) < 1e-9 and resolved["from_period"] == p
        assert abs(r.t0 - (ECLIPSE_T0 + ECLIPSE_P * round((r.t0 - ECLIPSE_T0) / ECLIPSE_P))) < 0.05
        assert abs(r.depth_ppm - ECLIPSE_PPM) < 0.15 * ECLIPSE_PPM
        assert resolved["sde_original"] == 20 and resolved["sde_resolved"] is None and r.sde == 20
        json.dumps(resolved, allow_nan=False)                      # strict JSON


def test_a_binary_with_a_deep_secondary_is_not_folded_away():
    """Primary 5000 and secondary 3500 ppm at 6.0 d, ranked at 3.0 d: the secondary is above half the primary, so the
    signal stays at 3.0 d and rung 2 sees the odd and even eclipses differ."""
    lc = inject_transit(make_lightcurve(n_days=500, seed=42, noise_ppm=800), 6.0, 133.0, 0.1, 5000)
    lc = inject_transit(lc, 6.0, 136.0, 0.1, 3500)
    fnorm = detrend(lc)
    sig = BoxDip(mask_iterations=0).detect(lc, fnorm, ALIAS_WINDOW)
    assert abs(sig[0].period - 3.0) < 0.01 and "alias_resolved" not in sig[0].extra
    m = np.isfinite(fnorm)
    odd, even = (stacked_depth(lc.time[m], fnorm[m], sig[0].period, sig[0].t0, sig[0].duration, epochs=e)
                 for e in ("odd", "even"))
    assert abs(odd.depth_ppm - even.depth_ppm) > Thresholds().parity_sigma * np.hypot(odd.err_ppm, even.err_ppm)


def inject_v_eclipse(lc: LightCurve, period, t0, width_days, depth_ppm):
    """A triangular (V-shaped) eclipse: ``depth_ppm`` at mid-eclipse falling linearly to zero at +-width_days/2."""
    ph = (lc.time - t0 + 0.5 * period) % period - 0.5 * period
    shape = np.clip(1.0 - np.abs(ph) / (0.5 * width_days), 0.0, None)
    f = lc.flux * (1 - depth_ppm * 1e-6 * shape)
    return LightCurve(lc.time, f, lc.flux_err, lc.quality, lc.quarter, lc.flux_alt, dict(lc.meta))


V_P, V_T0, V_WIDTH = 24.0, 140.0, 11 / 24
COMP_P, COMP_T0, COMP_DUR = 2.1, 132.3, 2.2 / 24


def _v_binary(depth_ppm: float, comp_ppm: float):
    """The K06356.01 configuration of the injection study: a V-shaped 24.0 d eclipse of 11 h total width on a 1400 d
    curve (seed 71, 700 ppm noise) plus a 2.1 d, 2.2 h box transit (the companion)."""
    lc = inject_v_eclipse(make_lightcurve(n_days=1400, seed=71, noise_ppm=700), V_P, V_T0, V_WIDTH, depth_ppm)
    lc = inject_transit(lc, COMP_P, COMP_T0, COMP_DUR, comp_ppm)
    return lc, detrend(lc)


@functools.cache
def _deep_v_binary_search():
    """One full search (both passes) of a 40000 ppm V binary with a 271 ppm companion (the injected depth of the real
    miss). At 6000 ppm on white noise the wings a 0.75-duration mask leaves stay under the threshold (SDE 11.6 at
    24.0 d and at most 7.0 at 24.0/n, measured 18 October 2026), so the defect needs a deeper eclipse to show; at 40000
    ppm the old mask left 24.0/5 d at SDE 47.0 above the companion's 28.2, and the order-8 filter alone would have
    taken 24.0/9 d (SDE 34.0)."""
    lc, fnorm = _v_binary(40000, 271)
    return BoxDip().detect(lc, fnorm, SUB_WINDOW)


def test_masked_pass_recovers_a_companion_behind_a_v_shaped_binary():
    """A narrow box fits the V better than a wide one (6 h for the 11 h eclipse), so a mask sized from the box left the
    eclipse wings in the masked curve and they, not the companion, ruled the second pass (24.09/7 d on the real
    binary)."""
    sig = _deep_v_binary_search()
    found = [(round(s.period, 4), round(s.sde, 1), s.extra.get("pass", 1)) for s in sig]
    assert any(abs(s.period - COMP_P) / COMP_P < 0.01 for s in sig), found
    residues = [V_P / n for n in range(2, 9)]
    assert all(abs(s.period - r) / r >= 0.01 for s in sig for r in residues), found
    for s in sig:
        if s.extra.get("pass") == 2:                               # the mask covers the whole eclipse, not the box
            assert s.extra["mask_half_days"][0] >= 0.5 * V_WIDTH, s.extra["mask_half_days"]


def test_mask_extent_is_measured_on_the_fold():
    from skysift.detectors.box_dip import _event_extent
    lc, fnorm = _v_binary(40000, 271)
    m = np.isfinite(fnorm)
    ext = _event_extent(lc.time[m], fnorm[m], V_P, V_T0, 6 / 24, 0.0)
    assert ext is not None and abs(ext - V_WIDTH) < 0.25 * V_WIDTH, ext
    assert _event_extent(lc.time[m], fnorm[m], V_P, V_T0, 6 / 24, 0.5) is None          # no secondary
    box = inject_transit(make_lightcurve(n_days=500, seed=72, noise_ppm=700), 7.3, 135.0, 3 / 24, 1500)
    fb = detrend(box); mb = np.isfinite(fb)
    ext = _event_extent(box.time[mb], fb[mb], 7.3, 135.0, 3 / 24, 0.0)
    assert ext is not None and abs(ext - 3 / 24) < 0.25 * 3 / 24, ext
    noise = make_lightcurve(n_days=500, seed=72, noise_ppm=700)
    fn = detrend(noise); mn = np.isfinite(fn)
    assert _event_extent(noise.time[mn], fn[mn], 7.3, 135.0, 3 / 24, 0.0) is None


def test_second_pass_filters_aliases_to_order_eight():
    from skysift.detectors.box_dip import _is_alias
    assert _is_alias(24.0 / 7, [24.0], 1400.0)
    assert not _is_alias(24.0 / 9, [24.0], 1400.0)
