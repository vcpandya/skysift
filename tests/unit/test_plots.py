import hashlib
import json

import numpy as np
import pytest

from skysift import jsonutil
from skysift.config import Thresholds
from skysift.detectors.periodogram import PeriodogramSummary
from skysift.lc.ops import detrend
from skysift.net import ServiceUnavailable
from skysift.store.blob import LocalBlob
from skysift.store.db import ArtifactRow, Store
from skysift.ui.plots import LightCurveLoader, build_plots
from tests.synthetic import inject_transit, make_lightcurve


def _signal(period=7.3, t0=135.0, duration=0.1, depth=3000.0):
    return {"id": 1, "target_id": "w1", "period": period, "t0": t0, "duration": duration, "depth_ppm": depth, "sde": 12.0,
            "extra": {"search_window": {"min_period_days": 0.5, "max_period_days": 200.0}}}


def _tests():
    return [{"rung": "rung3_stability", "status": "pass", "metrics": {"season_depths": {"0": 2900.0, "1": 3100.0},
                                                                     "season_errs": {"0": 150.0, "1": 160.0}}}]


def test_build_plots_shapes_and_fold_centres_the_dip():
    lc = inject_transit(make_lightcurve(n_days=400, seed=3, noise_ppm=500), 7.3, 135.0, 0.1, 3000)
    ps = PeriodogramSummary.from_spectrum(np.array([7.3, 3.65]), np.array([12.0, 6.0]), 0.5, 133.0, [0.5, 133.0],
                                          [{"period": 7.3, "sde": 12.0, "duration": 0.1, "rank": 0}], n_bins=256)
    out = build_plots(lc, detrend(lc), _signal(), _tests(), ps, Thresholds())
    assert set(out) == {"periodogram", "full", "fold", "oddeven", "secondary", "seasons"}
    assert len(out["full"]["time"]) <= 20000 and 40 < len(out["full"]["transit_times"]) <= 56   # 400 d / 7.3 d epochs
    fold = out["fold"]
    assert min(fold["phase_h"]) >= -0.5 * 7.3 * 24 - 1e-6
    bins = np.array(fold["bin_phase_h"]); means = np.array(fold["bin_flux"], float)
    assert abs(bins[np.nanargmin(means)]) < 1.0 and 1 - np.nanmin(means) > 2000e-6     # dip within 1 h of phase 0
    assert fold["model_depth_ppm"] == 3000.0 and fold["model_duration_h"] == pytest.approx(2.4)
    assert set(out["oddeven"]) == {"odd_phase_h", "odd_flux", "even_phase_h", "even_flux", "bin_phase_h", "odd_bin", "even_bin"}
    assert abs(np.nanmean(out["secondary"]["bin_flux"]) - 1) < 500e-6                      # no secondary injected
    assert out["seasons"] == {"season": [0, 1], "depth_ppm": [2900.0, 3100.0], "err_ppm": [150.0, 160.0],
                              "mean_ppm": pytest.approx(2993.6, abs=1.0)}
    pg = out["periodogram"]
    assert pg["sde_min"] == 9.0 and pg["detected_period"] == 7.3 and pg["aliases"] == [3.65, 14.6, 21.9]
    assert len(pg["period"]) == 256 and pg["systematic_periods"] == [93.0, 46.5, 372.5, 186.25]
    text = jsonutil.dumps(out)
    assert "NaN" not in text and json.loads(text)["periodogram"]["sde"][0] is None


def test_build_plots_without_periodogram_or_rung3():
    lc = inject_transit(make_lightcurve(n_days=120, seed=3, noise_ppm=500), 7.3, 135.0, 0.1, 3000)
    out = build_plots(lc, detrend(lc), _signal(), [], None, Thresholds())
    assert out["periodogram"] is None and out["seasons"] is None and out["fold"]["bin_flux"]


def test_build_plots_arrays_are_plain_lists_and_downsampled():
    lc = inject_transit(make_lightcurve(n_days=1400, seed=4, noise_ppm=500), 7.3, 135.0, 0.1, 3000)
    out = build_plots(lc, detrend(lc), _signal(), _tests(), None, Thresholds())
    assert len(out["full"]["time"]) <= 20000 and len(out["full"]["time"]) == len(out["full"]["flux"])
    for panel in out.values():
        for v in (panel or {}).values():
            assert not isinstance(v, np.ndarray)
    jsonutil.dumps(out)                                        # strict: raises on NaN or Infinity


def test_odd_even_split_follows_epoch_parity():
    lc = inject_transit(make_lightcurve(n_days=400, seed=3, noise_ppm=500), 7.3, 135.0, 0.1, 3000)
    oe = build_plots(lc, detrend(lc), _signal(), [], None, Thresholds())["oddeven"]
    odd, even = np.array(oe["odd_bin"], float), np.array(oe["even_bin"], float)
    assert np.nanmin(odd) < 1 - 2000e-6 and np.nanmin(even) < 1 - 2000e-6                  # both parities carry the dip
    assert abs(len(oe["odd_flux"]) - len(oe["even_flux"])) < 0.2 * len(oe["even_flux"])


class _Source:
    def __init__(self, lc, data, store, blob, fail=False):
        self.lc, self.data, self.store, self.blob, self.fail, self.calls = lc, data, store, blob, fail, 0

    def fetch_lightcurve(self, target):
        self.calls += 1
        if self.fail: raise ServiceUnavailable("mast down")
        sha = hashlib.sha256(self.data).hexdigest()
        _, loc = self.blob.put(self.data, "w1.fits")
        self.store.add_artifact(sha, len(self.data), "https://mast/w1.fits", loc, target["target_id"])
        return self.lc, sha, "https://mast/w1.fits"


def test_loader_refetches_released_file_and_verifies_sha(tmp_path):
    store = Store(f"sqlite:///{tmp_path.as_posix()}/t.db"); store.init_schema()
    blob = LocalBlob(tmp_path / "blobs")
    lc = make_lightcurve(n_days=30, seed=1)
    data = b"FITS-like bytes"
    sha = hashlib.sha256(data).hexdigest()
    _, loc = blob.put(data, "w1.fits"); store.add_artifact(sha, len(data), "https://mast/w1.fits", loc, "w1")
    blob.delete(loc); store.mark_artifact_deleted(sha)                       # stream retention released it
    src = _Source(lc, data, store, blob)
    loader = LightCurveLoader(store, blob, source_factory=lambda name, st, bl: src, parsers={"fake": lambda b: lc})
    target = {"target_id": "w1", "source": "fake"}
    assert loader.load(target) is lc and src.calls == 1
    with store.session() as s:
        row = s.get(ArtifactRow, sha)
        assert row.deleted_at is None and row.pinned                          # resurrected and pinned as evidence
    assert loader.load(target) is lc and src.calls == 1                      # now served from the blob
    bad = _Source(lc, b"different bytes", store, blob)
    blob.delete(loc); store.mark_artifact_deleted(sha)
    loader2 = LightCurveLoader(store, blob, source_factory=lambda name, st, bl: bad, parsers={"fake": lambda b: lc})
    with pytest.raises(ValueError, match="sha256"):
        loader2.load(target)
    # the mismatching file is discarded, so the next call repeats the check instead of serving it
    with pytest.raises(ValueError, match="sha256"):
        loader2.load(target)
    assert bad.calls == 2


def test_loader_reports_outage(tmp_path):
    store = Store(f"sqlite:///{tmp_path.as_posix()}/t.db"); store.init_schema()
    blob = LocalBlob(tmp_path / "blobs")
    src = _Source(None, b"", store, blob, fail=True)
    loader = LightCurveLoader(store, blob, source_factory=lambda name, st, bl: src)
    with pytest.raises(ServiceUnavailable):
        loader.load({"target_id": "w1", "source": "kbonus_bkg"})
