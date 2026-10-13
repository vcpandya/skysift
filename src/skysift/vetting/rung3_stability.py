from __future__ import annotations

import numpy as np
from scipy.stats import chi2 as chi2_dist

from skysift.lc.ops import event_depths
from skysift.models import Signal, TestResult, TestStatus
from skysift.vetting.base import DataBundle


class Rung3Stability:
    name = "rung3_stability"
    cost = 0.05

    def run(self, s: Signal, b: DataBundle) -> TestResult:
        th = b.thresholds
        lc, fn = b.lc, b.fnorm
        ed = event_depths(lc.time, fn, lc.quarter, s.period, s.t0, s.duration)
        if ed.n < 4:
            return TestResult(rung=self.name, status=TestStatus.UNDETERMINED, reason="too few events")
        seasons = ed.quarters % 4
        means, errs = {}, {}
        for sn in np.unique(seasons):
            k = seasons == sn; w = 1 / ed.errors[k] ** 2
            mu = np.sum(w * ed.depths[k]) / np.sum(w); e = 1 / np.sqrt(np.sum(w))
            c2 = np.sum(w * (ed.depths[k] - mu) ** 2); e *= max(1.0, np.sqrt(c2 / max(1, k.sum() - 1)))
            means[int(sn)], errs[int(sn)] = float(mu), float(e)
        mu_all = ed.mean
        z = {k: (means[k] - mu_all) / errs[k] for k in means}
        chi2 = float(sum(v ** 2 for v in z.values())); dof = max(1, len(z) - 1)
        p = float(chi2_dist.sf(chi2, dof))
        n_outliers = sum(abs(v) > th.season_sigma for v in z.values())
        ratio = np.nan
        if b.fnorm_alt is not None:
            alt = event_depths(lc.time, b.fnorm_alt, lc.quarter, s.period, s.t0, s.duration)
            if alt.n and ed.mean: ratio = float(alt.mean / ed.mean)
        m = {"season_depths": means, "season_errs": errs, "season_z": z, "chi2": chi2, "dof": dof, "p_value": p, "sap_psf_ratio": ratio}
        if p < 0.0027 and n_outliers >= 2 and len(z) >= 3:
            return TestResult(rung=self.name, status=TestStatus.FAIL, metrics=m, reason=f"season-dependent depth (p={p:.1e})")
        return TestResult(rung=self.name, status=TestStatus.PASS, metrics=m)
