"""rung5_pixels: which star the dip is on, from the Kepler target pixel files (mission control spec 4.4), on synthetic
TPFs of the test_pixels scene (``tests/unit/synthetic_tpf``): the KBonus target on pixel (4, 4) of a 9 x 9 stamp and
the Kepler target whose pixels hold it, five times brighter, 2.9 px away and turning about it from season to season."""
import copy
import io
import json
import math

import numpy as np
import pytest

from skysift.config import Thresholds
from skysift.lc.tpf import parse_tpf
from skysift.models import LightCurve, Signal, Target, TestStatus
from skysift.net import ServiceUnavailable
from skysift.pipeline import build_ladder
from skysift.store.blob import LocalBlob
from skysift.store.db import PIXELS_URL, ArtifactRow, Store
from skysift.vetting.base import DataBundle
from skysift.vetting.rung5_pixels import Rung5Pixels
from tests.unit.synthetic_tpf import NEIGHBOUR_KIC, TARGET_KIC, TARGET_RADEC, tpf_name, tpf_quarters
from tests.unit.test_pixels import EPH

SIGNAL = Signal(target_id="kic-006146676", detector="box_dip", detector_version="1", period=EPH.period, t0=EPH.t0,
                duration=EPH.duration, depth_ppm=3000.0, snr=40.0, sde=20.0, n_events=100)


def tpfs_of(mode: str, **kw) -> list:
    return [parse_tpf(b, tpf_name(NEIGHBOUR_KIC, b.quarter)) for b in tpf_quarters(mode, **kw)]


def loader(mode: str, calls: list | None = None, **kw):
    def load(kic, *, fetch, blob, store, target_id, timeout=600.0):
        if calls is not None:
            calls.append({"kic": kic, "blob": blob, "store": store, "target_id": target_id})
        return tpfs_of(mode, **kw)
    return load


def raising(exc):
    def load(kic, **_kw):
        raise exc
    return load


def _bundle(target: Target, store=None) -> DataBundle:
    t = np.arange(131.5, 131.5 + 465.0, 29.4 / 1440)
    lc = LightCurve(time=t, flux=np.ones(t.size), flux_err=np.full(t.size, 1e-3), quality=np.zeros(t.size, int),
                    quarter=np.minimum(1 + ((t - 131.5) // 93.0).astype(int), 5), meta={})
    return DataBundle(lc=lc, fnorm=np.ones(t.size), target=target, thresholds=Thresholds(), store=store)


def _target(**kw) -> Target:
    meta = kw.pop("meta", {"tpf_org": NEIGHBOUR_KIC})
    return Target(**{"source": "kbonus_bkg", "target_id": "kic-006146676", "ra": TARGET_RADEC[0],
                     "dec": TARGET_RADEC[1], "kic": TARGET_KIC, **kw}, meta=meta)


@pytest.fixture
def bundle_with_tpfs():
    return _bundle(_target())


@pytest.fixture
def bundle_same_star():
    return _bundle(_target(kic=NEIGHBOUR_KIC))


def _strict(metrics: dict) -> None:
    json.dumps(metrics, allow_nan=False)            # strict JSON: no NaN, no numpy scalars


def test_rung5_passes_when_the_dip_is_on_the_target(bundle_with_tpfs):
    r = Rung5Pixels(tpf_loader=loader("target")).run(SIGNAL, bundle_with_tpfs)
    assert r.status == TestStatus.PASS and r.metrics["n_events_used"] >= 3 and r.metrics["tpf_manifest"]
    assert r.reason.startswith("the dip is on the target in 4/4 seasons")
    m = r.metrics
    assert m["n_tpf"] == 5 and m["tpf_manifest"] == [tpf_name(NEIGHBOUR_KIC, q) for q in range(1, 6)]
    assert m["n_seasons"] == 4 and m["n_seasons_voting"] == 4 and m["unusable_quarters"] == []
    assert m["neighbour_kic"] == NEIGHBOUR_KIC and m["neighbour_position"] == "tpf header"
    assert len(m["leak_depth_ppm"]) == 2 and m["pixels_sha256"] is None      # no store: nothing written
    assert all(s["nearer"] == "target" and s["voting"] for s in m["seasons"])
    assert {"two_source", "pos_target", "pos_neighbour", "non_voting_reason", "centroid_sigma_axis_pix"} <= set(
        m["seasons"][0])
    _strict(m)


def test_rung5_fails_when_the_dip_is_on_the_neighbour(bundle_with_tpfs):
    r = Rung5Pixels(tpf_loader=loader("neighbour")).run(SIGNAL, bundle_with_tpfs)
    assert r.status == TestStatus.FAIL and r.reason.startswith("the dip is on a neighbouring star")
    assert r.reason.endswith("4/4 seasons)") and " sigma, " in r.reason
    d, e = r.metrics["leak_depth_ppm"]
    assert d is not None and e is not None and d > 5 * e        # the Kepler target's own aperture shows the dip
    _strict(r.metrics)


def test_rung5_without_a_neighbour_is_undetermined(bundle_same_star):
    calls = []
    r = Rung5Pixels(tpf_loader=loader("target", calls)).run(SIGNAL, bundle_same_star)
    assert r.status == TestStatus.UNDETERMINED and r.reason == "pixels: no neighbour to test against"
    assert calls == []                                        # nothing fetched


def test_rung5_without_pixel_files_is_undetermined():
    star = _bundle(_target(kic=None, meta={}))
    r = Rung5Pixels(tpf_loader=loader("target")).run(SIGNAL, star)
    assert r.status == TestStatus.UNDETERMINED and r.reason == "pixels: no pixel files for this star"
    empty = Rung5Pixels(tpf_loader=lambda kic, **kw: []).run(SIGNAL, _bundle(_target()))
    assert empty.status == TestStatus.UNDETERMINED and empty.reason == "pixels: no pixel files for this star"


def test_rung5_finds_the_kepler_target_in_the_light_curve_header_when_the_catalogue_lacks_it():
    b = _bundle(_target(meta={}))
    b.lc.meta["tpf_org"] = NEIGHBOUR_KIC
    calls = []
    r = Rung5Pixels(tpf_loader=loader("target", calls)).run(SIGNAL, b)
    assert r.status == TestStatus.PASS and calls[0]["kic"] == NEIGHBOUR_KIC
    assert calls[0]["target_id"] == SIGNAL.target_id


def test_rung5_outage_is_undetermined(bundle_with_tpfs):
    r = Rung5Pixels(tpf_loader=raising(ServiceUnavailable("mast"))).run(SIGNAL, bundle_with_tpfs)
    assert r.status == TestStatus.UNDETERMINED and r.reason.startswith("outage:mast")
    assert r.reason == "outage:mast: mast"


def test_rung5_takes_the_neighbour_position_from_the_catalogue_when_given():
    ra, dec = tpf_quarters("target")[0].neighbour_radec
    b = _bundle(_target(meta={"tpf_org": NEIGHBOUR_KIC, "tpf_org_ra": ra, "tpf_org_dec": dec}))
    r = Rung5Pixels(tpf_loader=loader("target")).run(SIGNAL, b)
    assert r.status == TestStatus.PASS and r.metrics["neighbour_position"] == "catalogue"
    b = _bundle(_target(meta={"tpf_org": NEIGHBOUR_KIC, "tpf_org_ra": float("nan"), "tpf_org_dec": 0.0}))
    r = Rung5Pixels(tpf_loader=loader("target")).run(SIGNAL, b)
    # a position that cannot be placed on the pixels makes every quarter unusable
    assert r.metrics["neighbour_position"] == "catalogue" and r.metrics["unusable_quarters"] == [1, 2, 3, 4, 5]
    assert r.status == TestStatus.UNDETERMINED and r.reason.startswith("pixels: ")
    _strict(r.metrics)


def test_a_quarter_whose_wcs_fails_is_unusable(bundle_with_tpfs):
    def broken(kic, **kw):
        tpfs = [copy.copy(t) for t in tpfs_of("target")]
        tpfs[1].wcs = None                                    # quarter 2
        return tpfs
    r = Rung5Pixels(tpf_loader=broken).run(SIGNAL, bundle_with_tpfs)
    assert r.metrics["unusable_quarters"] == [2] and r.metrics["n_seasons"] == 3
    assert r.status == TestStatus.PASS and r.reason.startswith("the dip is on the target in 3/3 seasons")


def test_a_target_off_the_stamp_is_undetermined_with_the_frame_reason(bundle_with_tpfs):
    # columns 4 to 8 only: the KBonus target sits on the stamp's first column in every quarter, its 3x3 box incomplete
    r = Rung5Pixels(tpf_loader=loader("target", stamp=((9, 5), (4, 0)))).run(SIGNAL, bundle_with_tpfs)
    assert r.status == TestStatus.UNDETERMINED
    assert r.reason == "pixels: no season holds both stars on the frame"
    assert r.metrics["n_seasons_voting"] == 0 and r.metrics["n_seasons"] == 4
    assert r.metrics["seasons_silenced"] == {"box incomplete: target": 4}
    _strict(r.metrics)


def test_rung5_stores_the_season_arrays_once_pinned_with_the_signal(tmp_path, bundle_with_tpfs):
    store = Store(f"sqlite:///{tmp_path.as_posix()}/t.db"); store.init_schema()
    blob = LocalBlob(tmp_path / "blobs")
    b = _bundle(_target(), store=store)
    calls = []
    rung = Rung5Pixels(tpf_loader=loader("target", calls), blob=blob, signal_id=7)
    r = rung.run(SIGNAL, b)
    assert calls[0]["blob"] is blob and calls[0]["store"] is store
    sha = r.metrics["pixels_sha256"]
    with store.session() as s:
        row = s.get(ArtifactRow, sha)
        assert row.kind == "pixels" and row.pinned and row.url == PIXELS_URL.format(signal_id=7)
        assert row.target_id == SIGNAL.target_id
        loc = row.local_path
    with np.load(io.BytesIO(blob.get(loc))) as z:
        seasons = [int(v) for v in z["seasons"]]
        assert seasons == [0, 1, 2, 3] and z["seasons"].dtype.kind == "i"
        for s in seasons:
            shape = z[f"diff_{s}"].shape
            assert len(shape) == 2 and all(z[f"{k}_{s}"].shape == shape for k in ("var", "direct", "valid"))
            assert z[f"valid_{s}"].dtype == bool
            assert z[f"pos_target_{s}"].shape == (2,) and z[f"pos_neighbour_{s}"].shape == (2,)
        by_season = {m["season"]: m for m in r.metrics["seasons"]}
        assert list(z[f"pos_target_{seasons[1]}"]) == pytest.approx(by_season[seasons[1]]["pos_target"])
    # the same run again writes the same file: content addressed and deterministic, one artifact row
    again = rung.run(SIGNAL, b)
    assert again.metrics["pixels_sha256"] == sha and store.counts()["artifacts"] == 1


def test_rung5_inside_a_worker_recorder_skips_the_arrays(bundle_with_tpfs, caplog, tmp_path):
    class Recorder:                                           # the worker's stand-in for the store
        def log_query(self, *a, **kw): pass
    b = _bundle(_target(), store=Recorder())
    r = Rung5Pixels(tpf_loader=loader("target"), blob=LocalBlob(tmp_path / "b"), signal_id=1).run(SIGNAL, b)
    assert r.status == TestStatus.PASS and r.metrics["pixels_sha256"] is None
    assert "pixels.npz not stored" in caplog.text


def test_rung5_is_deterministic(bundle_with_tpfs):
    a = Rung5Pixels(tpf_loader=loader("target")).run(SIGNAL, bundle_with_tpfs)
    b = Rung5Pixels(tpf_loader=loader("target")).run(SIGNAL, bundle_with_tpfs)
    assert a.metrics == b.metrics and a.reason == b.reason


def test_build_ladder_with_pixels_appends_rung5():
    assert [r.name for r in build_ladder("4", with_pixels=True).rungs][-1] == "rung5_pixels"
    assert "rung5_pixels" not in [r.name for r in build_ladder("4").rungs]
    rung = build_ladder("4", with_pixels=True).rungs[-1]
    assert rung.cost == 1000.0 and math.isfinite(rung.cost)
    assert [r.name for r in build_ladder("4", with_novelty=False, with_pixels=True).rungs][-2:] == [
        "rung3_stability", "rung5_pixels"]
