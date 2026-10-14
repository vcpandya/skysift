import hashlib
import json

import numpy as np
import pytest

from skysift import jsonutil
from skysift.config import Thresholds
from skysift.detectors.periodogram import PeriodogramSummary
from skysift.lc.ops import detrend, fold_phase
from skysift.models import LightCurve
from skysift.net import ServiceUnavailable
from skysift.store.blob import LocalBlob
from skysift.store.db import ArtifactRow, Store
from skysift.ui.plots import IntegrityError, LightCurveLoader, build_plots
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

def _eb_curve(t0, p_true_dur=0.1, even_ppm=4000.0, odd_ppm=2000.0):
    """Eclipsing-binary-like curve folded at the half period P: even epochs (from t0) are 4000 ppm, odd 2000 ppm."""
    lc = make_lightcurve(n_days=600, seed=5, noise_ppm=300)
    ep = np.round((lc.time - t0) / 7.3).astype(int)
    ph = (lc.time - t0 - ep * 7.3)
    f = lc.flux.copy()
    inside = np.abs(ph) < 0.5 * p_true_dur
    f[inside & (ep % 2 == 0)] *= 1 - even_ppm * 1e-6
    f[inside & (ep % 2 == 1)] *= 1 - odd_ppm * 1e-6
    return LightCurve(lc.time, f, lc.flux_err, lc.quality, lc.quarter, lc.flux_alt, dict(lc.meta))


@pytest.mark.parametrize("t0", [135.0, 135.0 + 7.3 * 40, 135.0 + 7.3 * 41])      # epochs positive, negative, parity flipped
def test_odd_even_depths_recover_the_injected_values_and_are_not_swapped(t0):
    lc = _eb_curve(t0)
    oe = build_plots(lc, detrend(lc), _signal(t0=t0), [], None, Thresholds())["oddeven"]
    centre = np.abs(np.array(oe["bin_phase_h"], float)) < 0.6                              # flat bottom of a 2.4 h dip
    odd = 1 - np.nanmean(np.array(oe["odd_bin"], float)[centre])
    even = 1 - np.nanmean(np.array(oe["even_bin"], float)[centre])
    # epochs are counted from t0, so even is the t0 transit's parity whatever the sign of the epoch numbers
    assert even == pytest.approx(4000e-6, rel=0.1) and odd == pytest.approx(2000e-6, rel=0.1)


def test_full_curve_keeps_in_transit_points_when_decimated():
    lc = inject_transit(make_lightcurve(n_days=1400, seed=4, noise_ppm=500), 7.3, 135.0, 0.1, 3000)
    fn = detrend(lc)
    out = build_plots(lc, fn, _signal(), [], None, Thresholds())["full"]
    n_good = int(np.isfinite(fn).sum())
    assert len(out["time"]) <= 20000 and len(out["time"]) < n_good                          # stride > 1 for the rest
    ph = np.abs(fold_phase(lc.time[np.isfinite(fn)], 7.3, 135.0))
    ph_out = np.abs(fold_phase(np.array(out["time"]), 7.3, 135.0))
    assert int((ph_out <= 0.09).sum()) >= int((ph <= 0.09).sum())                           # every in-transit point survives
    assert min(out["flux"]) < 1 - 2000e-6                                                   # so the dips are visible


class _Source:
    """Adapter stand-in that, like KBonusBkg, hands its bytes to the blob and store it was constructed with."""

    def __init__(self, lc, data, store, blob, fail=False):
        self.lc, self.data, self.store, self.blob, self.fail = lc, data, store, blob, fail

    def fetch_lightcurve(self, target):
        if self.fail: raise ServiceUnavailable("mast down")
        sha = hashlib.sha256(self.data).hexdigest()
        _, loc = self.blob.put(self.data, "w1.fits")
        self.store.add_artifact(sha, len(self.data), "https://mast/w1.fits", loc, target["target_id"])
        return self.lc, sha, "https://mast/w1.fits"


def _factory(lc, data, calls, fail=False):
    def make(name, store, blob):
        calls.append(name)
        return _Source(lc, data, store, blob, fail=fail)
    return make


def _store_blob(tmp_path):
    store = Store(f"sqlite:///{tmp_path.as_posix()}/t.db"); store.init_schema()
    return store, LocalBlob(tmp_path / "blobs")


def test_loader_refetches_released_file_and_verifies_sha(tmp_path):
    store, blob = _store_blob(tmp_path)
    lc = make_lightcurve(n_days=30, seed=1)
    data = b"FITS-like bytes"
    sha = hashlib.sha256(data).hexdigest()
    _, loc = blob.put(data, "w1.fits"); store.add_artifact(sha, len(data), "https://mast/w1.fits", loc, "w1")
    blob.delete(loc); store.mark_artifact_deleted(sha)                       # stream retention released it
    calls = []
    loader = LightCurveLoader(store, blob, source_factory=_factory(lc, data, calls), parsers={"fake": lambda b: lc})
    target = {"target_id": "w1", "source": "fake"}
    assert loader.load(target) is lc and len(calls) == 1
    with store.session() as s:
        row = s.get(ArtifactRow, sha)
        assert row.deleted_at is None and row.pinned and row.kind == "raw"    # recorded again and pinned as evidence
        assert blob.exists(row.local_path) and blob.get(row.local_path) == data
    assert loader.load(target) is lc and len(calls) == 1                     # now served from the blob


def test_loader_mismatch_stores_nothing_and_repeats_the_check(tmp_path):
    store, blob = _store_blob(tmp_path)
    lc = make_lightcurve(n_days=30, seed=1)
    data = b"FITS-like bytes"
    sha = hashlib.sha256(data).hexdigest()
    _, loc = blob.put(data, "w1.fits"); store.add_artifact(sha, len(data), "https://mast/w1.fits", loc, "w1")
    blob.delete(loc); store.mark_artifact_deleted(sha)
    calls = []
    loader = LightCurveLoader(store, blob, source_factory=_factory(lc, b"different bytes", calls),
                              parsers={"fake": lambda b: lc})
    target = {"target_id": "w1", "source": "fake"}
    for n in (1, 2):                                                          # the second call repeats the check
        with pytest.raises(IntegrityError, match="sha256"):
            loader.load(target)
        assert len(calls) == n
    other = hashlib.sha256(b"different bytes").hexdigest()
    with store.session() as s:
        assert s.get(ArtifactRow, other) is None                              # no row for the unverified file
        assert s.get(ArtifactRow, sha).deleted_at is not None                 # the evidence row is untouched
    assert not any(p.is_file() for p in (tmp_path / "blobs").rglob("*"))      # and nothing was written to the blob


def test_loader_pins_when_serving_from_the_blob(tmp_path):
    store, blob = _store_blob(tmp_path)
    lc = make_lightcurve(n_days=30, seed=1)
    data = b"FITS-like bytes"
    sha = hashlib.sha256(data).hexdigest()
    _, loc = blob.put(data, "w1.fits"); store.add_artifact(sha, len(data), "https://mast/w1.fits", loc, "w1")
    with store.session() as s:
        assert not s.get(ArtifactRow, sha).pinned
    loader = LightCurveLoader(store, blob, source_factory=_factory(lc, data, []), parsers={"fake": lambda b: lc})
    assert loader.load({"target_id": "w1", "source": "fake"}) is lc
    with store.session() as s:
        assert s.get(ArtifactRow, sha).pinned                                 # retention cannot evict what is on screen
    assert store.unpinned_artifacts("w1") == []


def test_loader_reports_a_corrupt_local_file_as_integrity_error(tmp_path):
    store, blob = _store_blob(tmp_path)
    data = b"truncated"
    sha, loc = blob.put(data, "w1.fits"); store.add_artifact(sha, len(data), "https://mast/w1.fits", loc, "w1")

    def broken(b):
        raise OSError("not a FITS file")
    loader = LightCurveLoader(store, blob, source_factory=_factory(None, b"", []), parsers={"fake": broken})
    with pytest.raises(IntegrityError, match="cannot be read"):
        loader.load({"target_id": "w1", "source": "fake"})


def test_loader_reports_outage(tmp_path):
    store, blob = _store_blob(tmp_path)
    loader = LightCurveLoader(store, blob, source_factory=_factory(None, b"", [], fail=True))
    with pytest.raises(ServiceUnavailable):
        loader.load({"target_id": "w1", "source": "kbonus_bkg"})


def test_loader_reports_a_source_that_is_not_live_as_unavailable(tmp_path):
    store, blob = _store_blob(tmp_path)
    loader = LightCurveLoader(store, blob)                                    # the real make_source
    with pytest.raises(ServiceUnavailable, match="not live"):
        loader.load({"target_id": "w1", "source": "tess_spoc"})
