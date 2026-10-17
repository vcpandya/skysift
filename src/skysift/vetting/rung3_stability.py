from __future__ import annotations

import numpy as np
from scipy.stats import chi2 as chi2_dist

from skysift.lc.ops import event_depths, n_window_epochs, season_of, stacked_depth
from skysift.models import Signal, TestResult, TestStatus
from skysift.vetting.base import DataBundle


def _season_agreement(means: dict[int, float], errs: dict[int, float]) -> tuple[dict[int, float], float, int, float]:
    """Leave-one-out z per season, the summed chi-square, its degrees of freedom and p-value.

    Each season is compared with the weighted mean of the *other* seasons, so a single deviant season cannot drag the
    reference and flag every other season. The summed z^2 is only approximately chi-square distributed with
    n_seasons - 1 dof (the z_k are correlated), which is adequate for a conservative 3-sigma gate."""
    keys = sorted(means)
    z = {}
    for k in keys:
        others = [j for j in keys if j != k]
        if not others:
            z[k] = 0.0
            continue
        w = np.array([1 / errs[j] ** 2 for j in others]); mu_o = float(np.sum(w * [means[j] for j in others]) / np.sum(w))
        e_o = float(1 / np.sqrt(np.sum(w)))
        z[k] = (means[k] - mu_o) / float(np.hypot(errs[k], e_o))
    chi2 = float(sum(v ** 2 for v in z.values())); dof = max(1, len(z) - 1)
    return z, chi2, dof, float(chi2_dist.sf(chi2, dof))


class Rung3Stability:
    name = "rung3_stability"
    cost = 0.05

    def run(self, s: Signal, b: DataBundle) -> TestResult:
        th = b.thresholds
        lc, fn = b.lc, b.fnorm
        ed = event_depths(lc.time, fn, lc.quarter, s.period, s.t0, s.duration)
        # an event with a zero or non-finite error (a flat baseline) carries no usable weight
        usable = np.isfinite(ed.depths) & np.isfinite(ed.errors) & (ed.errors > 0)
        stacked = int(usable.sum()) < 4
        if not stacked:
            depths, errors, seasons = ed.depths[usable], ed.errors[usable], season_of(ed.quarters[usable])
            means, errs = {}, {}
            for sn in np.unique(seasons):
                k = seasons == sn; w = 1 / errors[k] ** 2
                mu = np.sum(w * depths[k]) / np.sum(w); e = 1 / np.sqrt(np.sum(w))
                c2 = np.sum(w * (depths[k] - mu) ** 2); e *= max(1.0, np.sqrt(c2 / max(1, k.sum() - 1)))
                means[int(sn)], errs[int(sn)] = float(mu), float(e)
            n_events, all_depth = int(usable.sum()), float(np.average(depths, weights=1 / errors ** 2))
        else:
            # Single events hold too few points to measure (about two per event for sub-day periods at 30-minute
            # cadence), so measure each season on its own stacked fold, which holds every point of that season.
            args = (s.period, s.t0, s.duration)
            n_events = n_window_epochs(lc.time, fn, *args)
            season = season_of(lc.quarter)
            means, errs = {}, {}
            for sn in np.unique(season):
                k = season == sn
                st = stacked_depth(lc.time[k], fn[k], *args)
                if st.n_in and np.isfinite(st.err_ppm) and st.err_ppm > 0:      # a zero error is unmeasurable
                    means[int(sn)], errs[int(sn)] = st.depth_ppm, st.err_ppm
            all_depth = stacked_depth(lc.time, fn, *args).depth_ppm if len(means) >= 3 else np.nan
        if len(means) < 3:
            # one or two seasons cannot show a season-dependent depth (with two, each leave-one-out z mirrors the
            # other's), so the test has no answer on either path rather than a vacuous pass
            return TestResult(rung=self.name, status=TestStatus.UNDETERMINED, reason="too few seasons",
                              metrics={"stacked": stacked, "n_events": n_events, "n_seasons": len(means)})
        z, chi2, dof, p = _season_agreement(means, errs)
        ratio = np.nan
        if b.fnorm_alt is not None:
            if stacked:
                alt_depth = stacked_depth(lc.time, b.fnorm_alt, s.period, s.t0, s.duration).depth_ppm
                if all_depth: ratio = alt_depth / all_depth
            else:
                alt = event_depths(lc.time, b.fnorm_alt, lc.quarter, s.period, s.t0, s.duration)
                if alt.n and all_depth: ratio = float(alt.mean / all_depth)
        m = {"stacked": stacked, "season_depths": {str(k): v for k, v in means.items()},
             "season_errs": {str(k): v for k, v in errs.items()}, "season_z": {str(k): v for k, v in z.items()},
             "chi2": chi2, "dof": dof, "p_value": p, "n_events": n_events,
             "sap_psf_ratio": float(ratio) if np.isfinite(ratio) else None}
        if p < 0.0027 and any(abs(v) > th.season_sigma for v in z.values()) and len(z) >= 3:
            return TestResult(rung=self.name, status=TestStatus.FAIL, metrics=m, reason=f"season-dependent depth (p={p:.1e})")
        return TestResult(rung=self.name, status=TestStatus.PASS, metrics=m)
