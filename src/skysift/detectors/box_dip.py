"""Complete BLS search over 0.5 to 200 d on a 4-year Kepler baseline costs ~250 s single-core (astropy BLS passes over every point per trial period); throughput comes from the process pool; `oversample` and `n_refine` are the knobs for a faster, less complete sweep, to be judged by injection-recovery."""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import numpy as np
from astropy.timeseries import BoxLeastSquares

from skysift.config import Thresholds
from skysift.detectors.base import register
from skysift.detectors.periodogram import PeriodogramSummary
from skysift.lc.ops import circular_duration_days, n_window_epochs, stacked_depth
from skysift.models import LightCurve, Signal

MIN_POINTS = 500
DURATIONS_H = np.array([1, 1.5, 2, 3, 4, 6, 9, 12])
ALIAS_RATIOS = (2.0, 3.0, 4.0, 0.5, 1 / 3, 0.25)   # 4 and 1/4: a P/2 peak becomes P/4 once the top signal is doubled
MIN_BAND_PERIODS = 50
MAX_DURATION_FRACTION = 0.9     # trial durations must stay below this fraction of the band's shortest period
ALIAS_PARITY_SIGMA = 5.0        # odd and even stacked depths of a half-period alias differ by this many combined errors
ALIAS_PARITY_RATIO = 0.5        # and the shallower parity is under this fraction of the deeper one


@dataclass
class SearchResult:
    signals: list[Signal] = field(default_factory=list)
    periodogram: PeriodogramSummary | None = None
    spectrum: dict | None = None            # full arrays (period, power, sde, duration), only on request


def _band_durations(p_mid: float) -> np.ndarray:
    """Trial durations (days) plausible for a circular orbit at p_mid: 0.3 to 3 times the central-transit duration."""
    all_d = DURATIONS_H / 24.0
    t_circ = circular_duration_days(p_mid)
    keep = all_d[(all_d >= 0.3 * t_circ) & (all_d <= 3.0 * t_circ)]
    if keep.size < 3:
        keep = np.sort(all_d[np.argsort(np.abs(all_d - t_circ))[:3]])
    return keep


def _alias_of(period: float, earlier: list[Signal]) -> int | None:
    for j, s in enumerate(earlier):
        ratio = period / s.period
        if any(abs(ratio / r - 1.0) < 0.01 for r in ALIAS_RATIOS):
            return j
    return None


# every ratio p/q with 1 <= p, q <= MASK_ALIAS_ORDER: the harmonics and sub-harmonics a masked periodic signal (and the
# window function its mask leaves behind) can still raise in the second-pass spectrum
MASK_ALIAS_ORDER = 4
MASK_ALIAS_RATIOS = tuple(sorted({p / q for p in range(1, MASK_ALIAS_ORDER + 1) for q in range(1, MASK_ALIAS_ORDER + 1)}))
MASK_WINDOW_DURATIONS = 0.75    # half-width of the mask in durations (a 1.5-duration window) around the primary and
                                # around phase 0.5; wider windows swallow a short-period curve entirely


COMBINATION_ORDER = 2           # m f_i - n f_j with m, n up to this, plus f_i + f_j
COMBINATION_RESOLUTION = 2.0    # frequency tolerance for a combination match, in units of 1/baseline


def _is_alias(period: float, earlier_periods: list[float], baseline: float | None = None) -> bool:
    """True when ``period`` sits within 1 percent of a small-integer ratio (p/q, p and q up to 4) of an earlier period,
    or, given the baseline, within COMBINATION_RESOLUTION/baseline in frequency of a combination of two earlier
    frequencies (m f_i - n f_j with m, n up to COMBINATION_ORDER, or f_i + f_j). A multi-periodic variable (pulsation,
    differential rotation) raises sidelobes at exactly those combination frequencies, equally spaced in frequency with
    its main peaks, and masking the strongest peak only promotes the next sidelobe (gaia-dr3-2075988698983795072:
    1.574, 1.386 and 1.762 per day, spaced by 0.188)."""
    if any(abs(period / e / r - 1.0) < 0.01 for e in earlier_periods for r in MASK_ALIAS_RATIOS):
        return True
    if baseline is None or len(earlier_periods) < 2:
        return False
    f, tol = 1.0 / period, COMBINATION_RESOLUTION / baseline
    freqs = [1.0 / e for e in earlier_periods]
    combos = [fi + fj for i, fi in enumerate(freqs) for fj in freqs[i + 1:]]
    combos += [m * fi - n * fj for fi in freqs for fj in freqs if fi != fj
               for m in range(1, COMBINATION_ORDER + 1) for n in range(1, COMBINATION_ORDER + 1)]
    return any(c > 0 and abs(f - c) < tol for c in combos)


def _in_window(t: np.ndarray, period: float, t0: float, half_width: float) -> np.ndarray:
    ph = (t - t0 + 0.5 * period) % period - 0.5 * period
    return np.abs(ph) < half_width


DUPLICATE_COARSE_STEPS = 3      # after a doubling, a lower-ranked peak this many coarse frequency steps from the new
DUPLICATE_FALLBACK = 0.005      # period is the same signal; without a spectrum, this fraction of the period


def _duplicates(period: float, others: list[Signal], spec: dict | None) -> list[Signal]:
    """The signals of ``others`` that are the signal at ``period`` again: within DUPLICATE_COARSE_STEPS coarse
    frequency steps (``spec["df"]`` at the trial period nearest ``period``) or, without a spectrum, within
    DUPLICATE_FALLBACK of the period. That is the resolution the search itself has there, so an unrelated signal a
    couple of percent away is kept."""
    if spec is not None and spec["period"].size:
        tol = DUPLICATE_COARSE_STEPS * float(spec["df"][int(np.argmin(np.abs(spec["period"] - period)))])
    else:
        tol = DUPLICATE_FALLBACK / period
    return [s for s in others if abs(1.0 / s.period - 1.0 / period) < tol]


def _separated(order: np.ndarray, periods: np.ndarray, k: int) -> list[int]:
    """Indices from `order` (best first) whose periods differ by more than 2 percent from every earlier pick."""
    picked: list[int] = []
    for i in order:
        if all(abs(periods[i] - periods[j]) / periods[j] >= 0.02 for j in picked):
            picked.append(int(i))
            if len(picked) >= k:
                break
    return picked


class BoxDip:
    name = "box_dip"
    version = "1"
    cost_class = "cpu_medium"

    def __init__(self, top_k: int = 3, oversample: float = 1.5, n_bands: int = 24, refine_oversample: float = 6.0,
                 n_refine: int = 40, two_stage: bool = True, mask_iterations: int = 1, alias_resolution: int = 1):
        self.top_k, self.oversample, self.n_bands = top_k, oversample, n_bands
        self.refine_oversample, self.n_refine, self.two_stage = refine_oversample, n_refine, two_stage
        # after a first pass whose top peak clears the detection threshold, mask that signal (primary and phase 0.5)
        # and search the remaining points once more, so a dominant variable cannot hide an independent signal
        self.mask_iterations = mask_iterations
        # re-issue the strongest signal at twice its period when its odd and even events differ (0 turns it off)
        self.alias_resolution = alias_resolution

    def _coarse_search(self, model: BoxLeastSquares, baseline: float, thresholds: Thresholds, pmax: float) -> dict:
        """Coarse stage. Log-spaced period bands; in each, durations follow the circular-orbit transit duration and the
        frequency step keeps the transit drift over the baseline (df * P * baseline) below duration/oversample, using the
        band's longest period (Ofir 2014). Returns the concatenated spectrum plus per-band durations and SDE norms."""
        edges = np.geomspace(thresholds.min_period_days, pmax, self.n_bands + 1)
        band_durs = []
        keys = ("period", "power", "duration", "t0", "depth", "err", "df", "band")
        parts: dict[str, list[np.ndarray]] = {key: [] for key in keys}
        for b, (p_lo, p_hi) in enumerate(itertools.pairwise(edges)):
            durs = _band_durations(float(np.sqrt(p_lo * p_hi)))
            durs = durs[durs < MAX_DURATION_FRACTION * p_lo]     # astropy needs every duration below every period
            band_durs.append(durs)
            if durs.size == 0:                                   # band shorter than any trial duration: nothing to do
                for key in keys: parts[key].append(np.array([]))
                continue
            df = durs.min() / (self.oversample * baseline * p_hi)
            periods = 1 / np.arange(1 / p_hi, 1 / p_lo, df)[::-1]
            res = model.power(periods, durs, objective="snr")
            parts["period"].append(periods)
            parts["power"].append(np.asarray(res.power))
            parts["duration"].append(np.asarray(res.duration))
            parts["t0"].append(np.asarray(res.transit_time))
            parts["depth"].append(np.asarray(res.depth))
            parts["err"].append(np.asarray(res.depth_err))
            parts["df"].append(np.full(periods.size, df))
            parts["band"].append(np.full(periods.size, b))
        spec = {key: np.concatenate(parts[key]) for key in keys}
        spec["band"] = spec["band"].astype(int)                  # empty skipped bands concatenate as float
        # SDE normalisation per band: the noise level of the BLS spectrum rises with period, so each band gets its own
        # median and MAD-sigma; bands with fewer than MIN_BAND_PERIODS trial periods fall back to the global values.
        power = spec["power"]
        gmed = float(np.median(power)); gstd = 1.4826 * float(np.median(np.abs(power - gmed))) or 1e-12
        band_med, band_std = np.full(self.n_bands, gmed), np.full(self.n_bands, gstd)
        for b, pw in enumerate(parts["power"]):
            if pw.size >= MIN_BAND_PERIODS:
                band_med[b] = np.median(pw)
                band_std[b] = 1.4826 * float(np.median(np.abs(pw - band_med[b]))) or 1e-12
        spec.update(band_durs=band_durs, band_med=band_med, band_std=band_std,
                    sde=(power - band_med[spec["band"]]) / band_std[spec["band"]])
        return spec

    def _refine(self, model: BoxLeastSquares, spec: dict, i: int) -> tuple:
        """Re-search +-2 coarse steps around coarse peak i on a symmetric grid that contains the coarse frequency."""
        f0, df, b = 1 / spec["period"][i], spec["df"][i], int(spec["band"][i])
        n = round(2 * self.refine_oversample / self.oversample)
        freqs = f0 + df * np.linspace(-2, 2, 2 * n + 1)
        rp = np.sort(1 / freqs[freqs > 0])
        res = model.power(rp, spec["band_durs"][b], objective="snr")
        j = int(np.argmax(res.power))
        return (float(rp[j]), float(res.power[j]), float(res.duration[j]), float(res.transit_time[j]),
                float(res.depth[j]), float(res.depth_err[j]), b)

    def _rank(self, model: BoxLeastSquares, spec: dict) -> tuple[list[tuple], list[float], int]:
        """Candidates ``(period, power, duration, t0, depth, depth_err, band)`` with their per-band SDE. Selection and
        ranking use the SDE, not raw power, so every band competes on the same scale."""
        periods, power = spec["period"], spec["power"]
        order = np.argsort(spec["sde"])[::-1]
        if self.two_stage:
            cands = [self._refine(model, spec, i) for i in _separated(order, periods, self.n_refine)]
            n_refined = len(cands)
        else:
            cands = [(float(periods[i]), float(power[i]), float(spec["duration"][i]), float(spec["t0"][i]),
                      float(spec["depth"][i]), float(spec["err"][i]), int(spec["band"][i]))
                     for i in _separated(order, periods, self.top_k)]
            n_refined = 0
        sdes = [float((c[1] - spec["band_med"][c[6]]) / spec["band_std"][c[6]]) for c in cands]
        return cands, sdes, n_refined

    def _signal(self, lc: LightCurve, t: np.ndarray, y: np.ndarray, spec: dict, cand: tuple, sde: float,
                baseline: float, n_refined: int) -> Signal:
        p, _pw, dur, t0, depth, err, b = cand
        n_events = n_window_epochs(t, y, p, t0, dur)
        return Signal(target_id=str(lc.meta.get("target_id", "")), detector=self.name, detector_version=self.version,
                      period=p, t0=t0, duration=dur, depth_ppm=depth * 1e6, snr=float(depth / max(err, 1e-12)),
                      sde=sde, n_events=n_events,
                      extra={"baseline_days": baseline, "sde_spectrum_std": float(spec["band_std"][b]), "band": b,
                             "alias_of": None, "n_periods": int(spec["period"].size),
                             "n_bands": self.n_bands, "two_stage": self.two_stage, "n_refined": n_refined})

    def _resolve_alias(self, t: np.ndarray, y: np.ndarray, sig: Signal, pmax: float, spec: dict | None,
                       others: list[Signal] = ()) -> Signal:
        """Per-band SDE ranking can put a deep signal at half its period: the P/2 fold stacks every event with an
        empty (or, for a binary, a secondary) window and still wins its band. At P/2 a planet's events alternate
        transit and nothing, a binary's alternate primary and secondary, so a strong contrast between the odd and the
        even stacked depths is the signature of a halved period. When the contrast exceeds ALIAS_PARITY_SIGMA combined
        errors and the shallower parity is under ALIAS_PARITY_RATIO of the deeper one, the signal is re-issued at
        twice its period, t0 on the deeper parity, with that parity's depth and snr and events recounted. A correctly
        ranked planet has equal parities and is never touched; the ratio keeps a 5-sigma noise fluke from doubling a
        real planet.

        The SDE at 2P is the refined SDE of the best lower-ranked signal of ``others`` at the new period (see
        ``_duplicates``), else the per-band SDE of the trial period nearest it when the spectrum is given. The doubled
        signal keeps the larger of that and the P/2 peak's own SDE, which made the detection, with the band of the one
        kept; ``extra["alias_resolved"]`` records both (``sde_half``, ``sde_double``, None when neither source exists)."""
        p = sig.period
        if not self.alias_resolution or 2 * p > pmax:
            return sig
        odd = stacked_depth(t, y, p, sig.t0, sig.duration, epochs="odd")
        even = stacked_depth(t, y, p, sig.t0, sig.duration, epochs="even")
        vals = (odd.depth_ppm, odd.err_ppm, even.depth_ppm, even.err_ppm)
        if not (odd.n_in and even.n_in and np.all(np.isfinite(vals)) and odd.err_ppm > 0 and even.err_ppm > 0):
            return sig
        sigma = abs(odd.depth_ppm - even.depth_ppm) / float(np.hypot(odd.err_ppm, even.err_ppm))
        deep, shallow = (odd, even) if odd.depth_ppm > even.depth_ppm else (even, odd)
        if not (sigma > ALIAS_PARITY_SIGMA and shallow.depth_ppm < ALIAS_PARITY_RATIO * deep.depth_ppm):
            return sig
        # epoch parity is round((t - t0) / p) % 2, as stacked_depth defines it: odd events sit at t0 + p (2k + 1)
        t0 = sig.t0 + p if deep is odd else sig.t0
        sde_double, band_double = None, None
        dup = _duplicates(2 * p, list(others), spec)
        if dup:
            sde_double, band_double = float(dup[0].sde), (dup[0].extra["band"], dup[0].extra["sde_spectrum_std"])
        elif spec is not None and spec["period"].size:
            i = int(np.argmin(np.abs(spec["period"] - 2 * p)))
            b = int(spec["band"][i])
            sde_double, band_double = float(spec["sde"][i]), (b, float(spec["band_std"][b]))
        extra = dict(sig.extra)
        extra["alias_resolved"] = {"from_period": float(p), "odd_ppm": float(odd.depth_ppm),
                                   "even_ppm": float(even.depth_ppm), "parity_sigma": float(sigma),
                                   "sde_half": float(sig.sde), "sde_double": sde_double}
        update = {"period": 2 * p, "t0": t0, "depth_ppm": float(deep.depth_ppm),
                  "snr": float(deep.depth_ppm / deep.err_ppm),
                  "n_events": n_window_epochs(t, y, 2 * p, t0, sig.duration), "extra": extra}
        if sde_double is not None and sde_double > sig.sde:
            update["sde"] = sde_double
            extra.update(band=band_double[0], sde_spectrum_std=band_double[1])
        return sig.model_copy(update=update)

    def _masked_pass(self, lc: LightCurve, t: np.ndarray, y: np.ndarray, sigma: float, baseline: float,
                     th: Thresholds, pmax: float, first: list[Signal]) -> list[Signal]:
        """Second pass with the strongest first-pass signal masked: its points within MASK_WINDOW_DURATIONS durations of
        every primary and of every phase-0.5 secondary are dropped, the same grid is searched again, and the strongest
        remaining peak is kept when it clears the detection threshold on the masked spectrum and is not a small-integer
        alias of any first-pass period. One iteration, at most one extra signal: the cost is one more coarse search."""
        top = first[0]
        half = MASK_WINDOW_DURATIONS * top.duration
        keep = ~_in_window(t, top.period, top.t0, half) & ~_in_window(t, top.period, top.t0 + 0.5 * top.period, half)
        if keep.sum() < MIN_POINTS:
            return []
        t2, y2 = t[keep], y[keep]
        model = BoxLeastSquares(t2, y2, dy=np.full(t2.size, sigma))
        spec = self._coarse_search(model, baseline, th, pmax)
        if spec["period"].size == 0:
            return []
        cands, sdes, n_refined = self._rank(model, spec)
        earlier = [s.period for s in first]
        for ci in np.argsort(sdes)[::-1]:
            if sdes[ci] < th.sde_min:
                return []
            if _is_alias(cands[ci][0], earlier, baseline):
                continue
            sig = self._signal(lc, t2, y2, spec, cands[ci], sdes[ci], baseline, n_refined)
            sig.extra.update({"pass": 2, "masked_period": top.period, "masked_points": int((~keep).sum())})
            return [sig]
        return []

    def detect(self, lc: LightCurve, fnorm: np.ndarray, thresholds: Thresholds) -> list[Signal]:
        return self.search(lc, fnorm, thresholds).signals

    def search(self, lc: LightCurve, fnorm: np.ndarray, thresholds: Thresholds, keep_spectrum: bool = False) -> SearchResult:
        """Signals plus the periodogram summary built from the same per-band SDE spectrum the ranking uses; the full
        spectrum arrays come back only with ``keep_spectrum``."""
        th = thresholds
        m = np.isfinite(fnorm) & np.isfinite(lc.time)
        if m.sum() < MIN_POINTS:
            return SearchResult()
        t, y = lc.time[m], fnorm[m]
        med = np.median(y); mad = 1.4826 * np.median(np.abs(y - med))
        if mad == 0:
            return SearchResult()
        # clip upward outliers only: transits are downward and must survive however deep they are
        k = (y - med) < 5 * mad
        if k.sum() < MIN_POINTS:
            return SearchResult()
        t, y = t[k], y[k]
        baseline = float(t.max() - t.min())
        pmax = min(th.max_period_days, baseline / 3.0)
        if pmax <= th.min_period_days:
            return SearchResult()
        # constant per-point uncertainty from the robust scatter, so depth_err (and snr) are in real units
        sigma = max(1.4826 * float(np.median(np.abs(y - np.median(y)))), 1e-12)
        model = BoxLeastSquares(t, y, dy=np.full(t.size, sigma))
        spec = self._coarse_search(model, baseline, th, pmax)
        periods, power = spec["period"], spec["power"]
        if periods.size == 0:
            return SearchResult()
        cands, sdes, n_refined = self._rank(model, spec)
        cp = np.array([c[0] for c in cands])
        keep = _separated(np.argsort(sdes)[::-1], cp, self.top_k)
        out: list[Signal] = []
        for ci in keep:
            sig = self._signal(lc, t, y, spec, cands[ci], float(sdes[ci]), baseline, n_refined)
            sig.extra["alias_of"] = _alias_of(sig.period, out)
            out.append(sig)
        top = self._resolve_alias(t, y, out[0], pmax, spec, others=out[1:])
        if top is not out[0]:
            # the doubled period may now coincide with a lower-ranked peak: drop those (within three coarse frequency
            # steps; the best one already lent its refined SDE as the 2P value) and redo the alias bookkeeping of the
            # rest against the resolved period (the first-pass list then holds top_k - 1 signals)
            dup = _duplicates(top.period, out[1:], spec)
            out = [top] + [s for s in out[1:] if all(s is not d for d in dup)]
            for j in range(1, len(out)):
                out[j].extra["alias_of"] = _alias_of(out[j].period, out[:j])
        # the periodogram summary and its peaks describe the first-pass spectrum only; a second-pass signal has its
        # own SDE on the masked spectrum and is not drawn on this envelope
        edges = np.geomspace(th.min_period_days, pmax, self.n_bands + 1)
        peaks = [{"period": s.period, "sde": s.sde, "duration": s.duration, "rank": i} for i, s in enumerate(out)]
        if self.mask_iterations and out and out[0].sde >= th.sde_min:
            out.extend(self._masked_pass(lc, t, y, sigma, baseline, th, pmax, out))
        summary = PeriodogramSummary.from_spectrum(periods, spec["sde"], th.min_period_days, pmax, edges, peaks)
        spectrum = ({"period": periods, "power": power, "sde": spec["sde"], "duration": spec["duration"]}
                    if keep_spectrum else None)
        return SearchResult(signals=out, periodogram=summary, spectrum=spectrum)


register(BoxDip())
