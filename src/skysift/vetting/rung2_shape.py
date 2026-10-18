"""Rung 2: the shape of the folded signal. A sinusoid that fits the fold as well as a box, a sine wave in the
detrended flux (SWEET), a fold with another event as significant as the transit (the model-shift uniqueness test), odd
and even depths that differ, a secondary eclipse and a V-shaped or grazing transit each fail the signal, with two
exceptions for the secondary: an equal secondary at phase 0.5 (period halving: the signal may have been detected at
twice the true period, both dips being transits) and a shallow secondary a planet's own occultation explains. The tests
and their limits follow the Kepler DR25 Robovetter (Thompson et al. 2018, "T18", Appendix A)."""
from __future__ import annotations

import math

import numpy as np

from skysift.lc.ops import (
    PhaseSignificance,
    event_depths,
    n_window_epochs,
    phase_significance,
    secondary_search,
    shape_contrast,
    stacked_depth,
    sweet,
)
from skysift.lc.transit_model import TransitFit, fit_transit
from skysift.models import Signal, TestResult, TestStatus
from skysift.vetting.base import DataBundle
from skysift.vetting.rung1_detection import _stellar_param

# SWEET (T18 A.3.2; its eclipsing-binary use A.4.2): a sine at P/2, P or 2P in the detrended flux, fitted with
# signal-to-noise above SWEET_SNR, fails a short-period signal it outweighs (a variable star) and a binary's
# ellipsoidal variation
SWEET_SNR = 50.0
SWEET_PLANET_MAX_PERIOD = 5.0
SWEET_EB_MIN_AMPLITUDE_PPM = 5000.0
SWEET_EB_MAX_PERIOD = 10.0
V_SHAPE_MAX = 1.04              # T18 A.4.3: radius ratio plus impact parameter of the transit fit above it is grazing
R_SUN_M = 6.957e8
R_EARTH_M = 6.371e6
GM_SUN = 1.32712440018e20       # m^3 s^-2
# the planet-occultation exception (T18 A.4.1.3): geometric albedo below OCC_MAX_ALBEDO, a planet-sized companion, a
# secondary well below the transit and a transit that is not grazing
OCC_MAX_ALBEDO = 1.0
OCC_MAX_RADIUS_REARTH = 30.0
OCC_MAX_DEPTH_RATIO = 0.1
OCC_MAX_B = 0.95
HALVING_MAX_PHASE_DURATIONS = 0.25  # period halving (T18 A.4.1.3): the equal secondary within this of phase 0.5


def _num(x) -> float | None:
    """A finite metric as a float; NaN, infinity and None as None (the metrics are stored as strict JSON)."""
    return float(x) if x is not None and math.isfinite(x) else None


def _fit_metrics(fit: TransitFit) -> dict:
    """The transit-fit metrics: ``fit_ok`` always, the fitted values only when the fit is ok (None otherwise)."""
    ok = bool(fit.ok)
    values = {"fit_k": fit.k, "fit_b": fit.b, "fit_a_over_r": fit.a_over_r, "fit_depth_ppm": fit.depth_ppm,
              "fit_t14_h": fit.t14_days * 24.0, "fit_chi2": fit.chi2}
    return {"fit_ok": ok, **{name: _num(v) if ok else None for name, v in values.items()}}


class Rung2Shape:
    name = "rung2_shape"
    cost = 0.05

    def run(self, s: Signal, b: DataBundle) -> TestResult:
        th = b.thresholds
        lc, fn = b.lc, b.fnorm
        ep = np.round((lc.time - s.t0) / s.period).astype(int)
        odd = ep % 2 == 1; even = ~odd
        d_odd = event_depths(lc.time[odd], fn[odd], lc.quarter[odd], s.period, s.t0, s.duration)
        d_even = event_depths(lc.time[even], fn[even], lc.quarter[even], s.period, s.t0, s.duration)
        d_sec = event_depths(lc.time, fn, lc.quarter, s.period, s.t0 + 0.5 * s.period, s.duration)
        all_d = event_depths(lc.time, fn, lc.quarter, s.period, s.t0, s.duration)
        free_phase, free_depth, free_sigma = secondary_search(lc.time, fn, s.period, s.t0, s.duration)
        parity_sigma = abs(d_odd.mean - d_even.mean) / np.hypot(d_odd.err_scaled, d_even.err_scaled)
        sec_sigma = d_sec.mean / d_sec.err_scaled if d_sec.n else 0.0
        sec_depth, sec_err = d_sec.mean, d_sec.err_scaled
        timing_rms_min = float(np.nan)
        box_chi2, sine_chi2, shape_n = shape_contrast(lc.time, fn, s.period, s.t0, s.duration)
        m = {"stacked": False, "depth_ppm": all_d.mean, "depth_err_ppm": all_d.err_scaled, "n_events": all_d.n,
             "odd_ppm": d_odd.mean, "even_ppm": d_even.mean,
             "parity_sigma": float(parity_sigma), "secondary_ppm": d_sec.mean, "secondary_sigma": float(sec_sigma),
             "free_secondary_phase": free_phase, "free_secondary_ppm": free_depth, "free_secondary_sigma": free_sigma,
             "control_rms_ppm": all_d.control_rms, "timing_rms_min": timing_rms_min,
             "box_chi2": box_chi2, "sine_chi2": sine_chi2, "shape_n": shape_n}
        # the stacked fold has every point, so this verdict holds even when the per-event tests below are undetermined
        # (about two points per event for sub-day periods at 30-minute cadence)
        if np.isfinite(sine_chi2) and sine_chi2 <= box_chi2:
            return self._result(TestStatus.FAIL, m, "sinusoidal")
        parity_unmeasurable = False
        if not np.isfinite(parity_sigma) or d_odd.n < 2 or d_even.n < 2:
            # per-event depths are starved (about two points per event for sub-day periods at 30-minute cadence), so
            # measure odd, even and secondary on the stacked fold, which holds every point
            args = (lc.time, fn, s.period, s.t0, s.duration)
            st_odd, st_even = (stacked_depth(*args, epochs=e) for e in ("odd", "even"))
            st_sec, st_all = stacked_depth(*args, phase_offset=0.5), stacked_depth(*args)
            parity_err = np.hypot(st_odd.err_ppm, st_even.err_ppm)
            if not (st_odd.n_in and st_even.n_in and parity_err > 0):
                parity_unmeasurable = True      # reported at the odd/even step, after SWEET and the uniqueness test
            else:
                parity_sigma = abs(st_odd.depth_ppm - st_even.depth_ppm) / parity_err
                sec_ok = bool(st_sec.n_in and st_sec.err_ppm > 0)   # a zero error is unmeasurable, not infinitely sure
                sec_sigma = st_sec.depth_ppm / st_sec.err_ppm if sec_ok else 0.0
                sec_depth, sec_err = (st_sec.depth_ppm, st_sec.err_ppm) if sec_ok else (np.nan, np.nan)
                m.update({"stacked": True, "odd_ppm": st_odd.depth_ppm, "even_ppm": st_even.depth_ppm,
                          "parity_sigma": float(parity_sigma), "depth_ppm": st_all.depth_ppm,
                          "depth_err_ppm": st_all.err_ppm,
                          "secondary_ppm": st_sec.depth_ppm if sec_ok else None, "secondary_sigma": float(sec_sigma),
                          "n_events": n_window_epochs(lc.time, fn, s.period, s.t0, s.duration)})

        r = self._sweet(s, b, m)
        if r is not None:
            return r
        ps = phase_significance(lc.time, fn, s.period, s.t0, s.duration)
        r = self._uniqueness(ps, th, m)
        if r is not None:
            return r

        if parity_unmeasurable or not np.isfinite(parity_sigma):
            return self._result(TestStatus.UNDETERMINED, m, "too few odd/even events")
        if parity_sigma > th.parity_sigma:
            return self._result(TestStatus.FAIL, m, f"odd_even {parity_sigma:.1f} sigma")

        # the transit fit, once: it feeds the V-shape rule and the planet-occultation exception
        fit = fit_transit(lc.time, fn, s.period, s.t0, s.duration)
        m.update(_fit_metrics(fit))

        # secondary eclipse, with the two T18 A.4.1.3 exceptions. Period halving is judged on the phase-0.5 path only:
        # it compares the phase-0.5 depth with the primary on the same estimator (per-event or stacked boxes); a
        # secondary within 0.25 duration of phase 0.5 sits inside that box, so the phase-0.5 rule meets it first.
        occultation = False
        if sec_sigma > th.secondary_sigma:
            r = self._period_halving(s, ps, m, sec_depth, sec_err)
            if r is not None:
                return r
            if not self._occultation(s, b, fit, m, sec_depth):
                return self._result(TestStatus.FAIL, m, f"secondary_at_0.5 {sec_sigma:.1f} sigma")
            occultation = True
        if not np.isfinite(free_sigma) or not np.isfinite(free_phase):
            return self._result(TestStatus.UNDETERMINED, m, "free_secondary_undetermined")
        if free_sigma > th.free_secondary_sigma:
            # A secondary within one transit duration of phase 0.5 is a circular-orbit eclipse the phase-0.5 box
            # (+-0.8 half-durations) can narrowly miss; anything further out routes to the EB branch.
            if abs(free_phase - 0.5) * s.period <= s.duration:
                reason = f"secondary_at_0.5 {free_sigma:.1f} sigma (free-phase search, phase {free_phase:.3f})"
            else:
                reason = "eccentric_secondary"
            if not self._occultation(s, b, fit, m, free_depth):
                return self._result(TestStatus.FAIL, m, reason)
            occultation = True

        if fit.ok and fit.k + fit.b > V_SHAPE_MAX:      # T18 A.4.3
            return self._result(TestStatus.FAIL, m, f"V-shaped or deep (k + b = {fit.k + fit.b:.2f} > {V_SHAPE_MAX})")
        return self._result(TestStatus.PASS, m, "secondary consistent with planet occultation" if occultation else "")

    def _result(self, status: TestStatus, m: dict, reason: str) -> TestResult:
        return TestResult(rung=self.name, status=status, metrics=m, reason=reason)

    def _sweet(self, s: Signal, b: DataBundle, m: dict) -> TestResult | None:
        """SWEET (T18 A.3.2, A.4.2) on the detrended flux before pre-whitening, which still holds the waves the
        whitening removes: the FAIL, or None. Skipped (metrics None) without a detrended flux."""
        m.update({"sweet_factor": None, "sweet_amplitude_ppm": None, "sweet_snr": None})
        if b.flux_detrended is None:
            return None
        depth = m["depth_ppm"]
        sw = sweet(b.lc.time, b.flux_detrended, s.period, depth)
        m.update({"sweet_factor": _num(sw.factor), "sweet_amplitude_ppm": _num(sw.amplitude_ppm),
                  "sweet_snr": _num(sw.snr)})
        what = f"{sw.factor:g} P, {sw.amplitude_ppm:.0f} ppm, snr {sw.snr:.0f}"
        if sw.snr > SWEET_SNR and sw.amplitude_ppm > depth and s.period < SWEET_PLANET_MAX_PERIOD:
            return self._result(TestStatus.FAIL, m, f"sinusoidal (SWEET {what})")
        if sw.snr > SWEET_SNR and SWEET_EB_MIN_AMPLITUDE_PPM < sw.amplitude_ppm < depth and s.period < SWEET_EB_MAX_PERIOD:
            return self._result(TestStatus.FAIL, m, f"ellipsoidal (SWEET EB {what})")
        return None

    def _uniqueness(self, ps: PhaseSignificance, th, m: dict) -> TestResult | None:
        """Model-shift uniqueness (T18 A.3.4): MS2 fails a fold whose tertiary event is about as significant as the
        transit, MS3 one whose strongest brightening is. The FAIL, or None; the statistics go into ``m``."""
        m.update({"sigma_pri": _num(ps.sigma_pri), "sigma_sec": _num(ps.sigma_sec), "phase_sec": _num(ps.phase_sec),
                  "sigma_ter": _num(ps.sigma_ter), "sigma_pos": _num(ps.sigma_pos), "fa2": _num(ps.fa2),
                  "ms2": _num(ps.ms2), "ms3": _num(ps.ms3)})
        if np.isfinite(ps.ms2) and ps.ms2 > th.ms2_max:
            return self._result(TestStatus.FAIL, m, f"not unique (MS2 {ps.ms2:.1f} > {th.ms2_max:g})")
        if np.isfinite(ps.ms3) and ps.ms3 > th.ms3_max:
            return self._result(TestStatus.FAIL, m,
                                f"positive event as significant as the transit (MS3 {ps.ms3:.1f} > {th.ms3_max:g})")
        return None

    def _period_halving(self, s: Signal, ps: PhaseSignificance, m: dict, d_sec: float,
                        e_sec: float) -> TestResult | None:
        """Period halving (T18 A.4.1.3): a secondary at phase 0.5 as deep as the primary means the signal may be a
        planet "detected at twice the true orbital period", both dips being its transits. UNDETERMINED with
        ``suggested_period = P / 2``, or None.

        Equality is judged on the depths in their combined error (FA2 standard errors, the significance a single fold
        trial exceeds by chance), not on the difference of ``sigma_pri`` and ``sigma_sec``: the primary's baseline in
        ``phase_significance`` holds the secondary's points, which inflate its scatter by a few per cent, and on a
        deep eclipse a few per cent of the significance is far more than FA2. The phase gate asks that the strongest
        off-primary event of the fold (``ps.phase_sec``) sits within 0.25 duration of phase 0.5."""
        d_pri, e_pri = m["depth_ppm"], m["depth_err_ppm"]
        err = float(np.hypot(e_pri, e_sec)) if e_pri is not None and e_sec is not None else float("nan")
        diff_sigma = abs(d_pri - d_sec) / err if np.isfinite(err) and err > 0 else float("nan")
        m["secondary_difference_sigma"] = _num(diff_sigma)
        off_half = abs(abs(ps.phase_sec) - 0.5) * s.period    # phase_sec is in [-0.5, 0.5): phase 0.5 reads -0.5
        if not (diff_sigma < ps.fa2 and off_half <= HALVING_MAX_PHASE_DURATIONS * s.duration):
            return None
        m["suggested_period"] = 0.5 * s.period
        return self._result(TestStatus.UNDETERMINED, m,
                            f"equal secondary at phase 0.5: true period may be P / 2 ({0.5 * s.period:.6g} d)")

    def _occultation(self, s: Signal, b: DataBundle, fit: TransitFit, m: dict, d_sec_ppm: float) -> bool:
        """Planet occultation (T18 A.4.1.3): True when the secondary is the planet's own occultation. A reflecting
        planet's occultation depth is A_g (R_p / a)^2 of the stellar flux, so the geometric albedo it implies is
        A_g = d_sec (a / R_p)^2 with d_sec fractional; R_p = k R_star and a from Kepler's third law, with the meta
        radius and mass or rung 1's 0.5 fallback. The exception needs an ok fit, A_g < 1, R_p < 30 R_earth,
        d_sec < 0.1 d_pri and b < 0.95. ``occultation_albedo``, ``planet_radius_rearth`` and ``occultation_exception`` (the
        verdict) go into ``m`` either way."""
        meta = b.target.meta or {}
        radius, _ = _stellar_param(meta, "radius")
        mass, _ = _stellar_param(meta, "mass")
        rp_m = fit.k * radius * R_SUN_M
        a_m = (GM_SUN * mass * (s.period * 86400.0) ** 2 / (4.0 * np.pi ** 2)) ** (1.0 / 3.0)
        albedo = d_sec_ppm * 1e-6 * (a_m / rp_m) ** 2 if rp_m > 0 else float("nan")
        rp_rearth = rp_m / R_EARTH_M
        waived = bool(fit.ok and albedo < OCC_MAX_ALBEDO and rp_rearth < OCC_MAX_RADIUS_REARTH
                      and d_sec_ppm < OCC_MAX_DEPTH_RATIO * m["depth_ppm"] and fit.b < OCC_MAX_B)
        # occultation_exception: True when the secondary was waived, False when evaluated and refused (the dashboard
        # keys on it; the albedo and radius are kept either way for the reviewer)
        m.update({"occultation_albedo": _num(albedo), "planet_radius_rearth": _num(rp_rearth),
                  "occultation_exception": waived})
        return waived
