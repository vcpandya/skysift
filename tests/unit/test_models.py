import numpy as np

from skysift.models import LightCurve, TestResult, TestStatus


def test_good_mask_drops_nonfinite_zero_err_and_bad_quality():
    t = np.arange(10, dtype=float)
    f = np.ones(10); f[2] = np.nan
    e = np.full(10, 1e-3); e[3] = 0.0; e[4] = np.inf
    q = np.zeros(10, dtype=int); q[5] = 16
    lc = LightCurve(time=t, flux=f, flux_err=e, quality=q, quarter=np.zeros(10, dtype=int))
    m = lc.good_mask()
    assert m.sum() == 6 and not m[2] and not m[3] and not m[4] and not m[5]
    assert lc.cleaned().n_good == 6


def test_testresult_defaults():
    r = TestResult(rung="r1", status=TestStatus.UNDETERMINED, reason="outage")
    assert r.metrics == {} and r.artifacts == []
