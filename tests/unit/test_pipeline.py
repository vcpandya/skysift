import json
import logging
import multiprocessing
import pickle
import threading
import warnings
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

import numpy as np
import pytest

from skysift import jsonutil, pipeline
from skysift.config import Thresholds, load_settings
from skysift.detectors.base import get_detector
from skysift.lc.ops import detrend
from skysift.models import LightCurve, Signal, Target, TestResult, TestStatus
from skysift.pipeline import (
    DEFAULT_LADDER_VERSION,
    LADDER_COMPATIBLE,
    MAX_ATTEMPTS,
    MAX_POOL_RESTARTS,
    _cpu_work,
    _select_targets,
    _write_results,
    build_ladder,
    halved_signal,
    priority_score,
    revet,
    run,
    search_version,
)
from skysift.store import db
from skysift.store.blob import LocalBlob
from skysift.store.db import Store, make_job_key
from skysift.store.retention import DiskBudgetExceeded
from tests.synthetic import _with_dip, inject_transit, inject_v_eclipse, make_lightcurve
from tests.unit import fake_fetch

TARGET = Target(source="fake", target_id="t", ra=0, dec=0, gmag=16.5)


class FakeSource:
    """Mirrors a real adapter: fetch_lightcurve records the downloaded file as an artifact itself."""
    name = "fake"

    def __init__(self, lcs, store=None, blob=None):
        self.lcs, self.store, self.blob = lcs, store, blob

    def list_targets(self, filter):
        for tid in self.lcs:
            yield Target(source="fake", target_id=tid, ra=0, dec=0, gmag=16.5, meta={"psffrac": 0.9, "crowdsap": 0.9})

    def fetch_lightcurve(self, target):
        if self.blob is not None:
            data = f"raw light curve bytes for {target.target_id}".encode()
            sha, loc = self.blob.put(data, f"{target.target_id}.fits")
            self.store.add_artifact(sha, len(data), f"https://fake/{target.target_id}", loc, target.target_id)
            return self.lcs[target.target_id], sha, f"https://fake/{target.target_id}"
        return self.lcs[target.target_id], "0" * 64, f"https://fake/{target.target_id}"


def test_priority_prefers_faint_unsearched_clean():
    a = Target(source="x", target_id="a", ra=0, dec=0, gmag=16.5, meta={"psffrac": 0.9, "crowdsap": 0.9})
    b = Target(source="x", target_id="b", ra=0, dec=0, gmag=19.5, kic=1, meta={"psffrac": 0.3, "crowdsap": 0.3})
    assert priority_score(a) > priority_score(b)
    assert priority_score(a) == 5 and priority_score(b) == -3


def test_priority_uses_quarter_availability():
    """A single-quarter target (Q0 only, 469 points) cannot reach MIN_POINTS: it must sort below everything searchable,
    and a long baseline earns a bonus. Targets without the field keep the old score."""
    base = {"source": "x", "ra": 0, "dec": 0, "gmag": 16.5}
    one = Target(**base, target_id="q1", meta={"psffrac": 0.9, "crowdsap": 0.9, "n_quarters": 1})
    few = Target(**base, target_id="q4", meta={"psffrac": 0.9, "crowdsap": 0.9, "n_quarters": 4})
    many = Target(**base, target_id="q17", meta={"psffrac": 0.9, "crowdsap": 0.9, "n_quarters": 17})
    unknown = Target(**base, target_id="na", meta={"psffrac": 0.9, "crowdsap": 0.9})
    assert priority_score(one) == -5 and priority_score(few) == 5 == priority_score(unknown) and priority_score(many) == 7
    assert priority_score(one) < priority_score(Target(**{**base, "gmag": 19.5}, target_id="faint", kic=1,
                                                       meta={"psffrac": 0.3, "crowdsap": 0.3, "n_quarters": 17}))


def test_build_ladder_orders_rungs_and_drops_novelty_on_request():
    costs = [r.cost for r in build_ladder().rungs]
    assert costs == sorted(costs) and build_ladder().rungs[-1].name == "rung4_novelty"
    assert [r.name for r in build_ladder().rungs] == ["rung1_detection", "rung1_events", "rung2_shape",
                                                       "rung3_stability", "rung4_novelty"]
    assert [r.name for r in build_ladder(with_novelty=False).rungs] == ["rung1_detection", "rung1_events",
                                                                         "rung2_shape", "rung3_stability"]
    assert pipeline.DEFAULT_LADDER_VERSION == "4"
    assert build_ladder("7").version == "7"


def test_write_results_keeps_nan_and_numpy_metrics(settings, tmp_path):
    store = Store(settings.database_url); store.init_schema()
    t = Target(source="fake", target_id="t1", ra=0, dec=0); store.upsert_target(t)
    rid = store.start_run(make_job_key("t1", "box_dip", "1", "1"), "t1", "box_dip", "1", "1")
    sig = Signal(target_id="t1", detector="box_dip", detector_version="1", period=3.0, t0=1.0, duration=0.1,
                 depth_ppm=500, snr=10, sde=12, n_events=5)
    r = TestResult(rung="shape", status=TestStatus.PASS, metrics={"nan": float("nan"), "np": np.float64(2.5),
                                                                  "arr": np.arange(3)})
    query = {"service": "exoarchive", "url": "https://x/TAP", "params": {"radius": np.float64(0.01)}, "status": 200,
             "nbytes": 10, "sha256": "ab" * 32, "error": "", "utc": "2026-10-14T00:00:00+00:00"}
    payload = jsonutil.dumps({"signals": [{"signal": sig.model_dump(), "outcome": "pass", "tests": [r.model_dump()],
                                           "timings": [0.1]}], "queries": [query]})
    assert _write_results(store, LocalBlob(tmp_path / "b"), rid, "t1", payload) == {"pass": 1, "fail": 0, "undetermined": 0}
    with store.session() as s:
        m = s.query(db.TestRow).one().metrics
        q = s.query(db.QueryRow).one()
        assert (q.service, q.status, json.loads(q.params)) == ("exoarchive", 200, {"radius": 0.01})
        assert (s.get(db.RunRow, rid).status, s.get(db.TargetRow, "t1").state) == ("done", "done")
    assert m["nan"] is None and m["np"] == 2.5 and m["arr"] == [0, 1, 2]
    # stored as strict JSON: no NaN/Infinity tokens a Postgres JSON column or a strict parser would reject
    with store.engine.connect() as c:
        raw = c.exec_driver_sql("select metrics from tests").scalar()

    def reject(token): raise ValueError(f"non-standard JSON token {token}")
    assert json.loads(raw, parse_constant=reject)["nan"] is None
    # a raw NaN written through the ORM is sanitized by the engine's serializer too
    store.add_test(1, TestResult(rung="x", status=TestStatus.PASS, metrics={"inf": float("inf")}), 0.0)
    with store.engine.connect() as c:
        raws = [row[0] for row in c.exec_driver_sql("select metrics from tests")]
    assert all("NaN" not in x and "Infinity" not in x for x in raws)


def _test_modules_in_worker() -> str:
    return "sorted(m for m in __import__('sys').modules if m == 'tests' or m.startswith('tests.'))"


def test_cpu_work_runs_in_spawned_worker_without_test_modules():
    lc = inject_transit(make_lightcurve(n_days=120, seed=3, noise_ppm=500), 7.3, 135.0, 0.1, 3000)
    target = Target(source="fake", target_id="w1", ra=0, dec=0, gmag=16.5)
    th = Thresholds(min_period_days=5, max_period_days=15)
    with ProcessPoolExecutor(1, mp_context=multiprocessing.get_context("spawn")) as pool:
        out = pool.submit(_cpu_work, pickle.dumps(lc), target.model_dump_json(), th.model_dump_json(), "box_dip", "1",
                          False).result()
        leaked = pool.submit(eval, _test_modules_in_worker()).result()
    assert leaked == []
    items = json.loads(out)["signals"]
    assert json.loads(out)["queries"] == []               # rungs 1-3 make no catalogue queries
    assert items and all(i["signal"]["target_id"] == "w1" for i in items)
    ratios = [i["signal"]["period"] / 7.3 for i in items]     # 120 d holds few events: the 2x alias may win
    assert any(abs(r - round(r)) < 0.01 and round(r) in (1, 2) for r in ratios)
    assert all(len(i["tests"]) == len(i["timings"]) >= 1 for i in items)


def test_run_end_to_end_with_fake_source_and_resume(settings, monkeypatch):
    monkeypatch.setenv("SKYSIFT_WORKERS_CPU", "1")
    monkeypatch.setenv("SKYSIFT_THRESHOLDS__MIN_PERIOD_DAYS", "5")
    monkeypatch.setenv("SKYSIFT_THRESHOLDS__MAX_PERIOD_DAYS", "40")
    s = load_settings(); s.data_dir.mkdir(exist_ok=True)
    assert s.thresholds.min_period_days == 5 and s.thresholds.max_period_days == 40 and s.retention == "stream"
    store = Store(s.database_url); store.init_schema(); blob = LocalBlob(s.data_dir / "blobs")
    lcs = {"planet": inject_transit(make_lightcurve(n_days=500, seed=1, noise_ppm=800), 13.769, 136.3, 0.09, 2000),
           "noise": make_lightcurve(n_days=500, seed=9)}
    src = FakeSource(lcs, store, blob)
    seen = []
    # rung 4 would hit the network: with_novelty=False runs rungs 1-3 only (a monkeypatch would not reach the worker process)
    with warnings.catch_warnings():
        warnings.simplefilter("error", ResourceWarning)
        out = run(src, store, blob, s, filter={}, max_targets=2, with_novelty=False, progress=lambda x: seen.append(dict(x)))
    c = store.counts()
    assert out["processed"] == 2 and out["errors"] == 0 and out["skipped"] == 0 and len(seen) == 2
    assert c["targets"] == 2 and c["runs"] == 2 and c["signals"] >= 1 and c["tests"] >= 3
    assert out["signals"] == c["signals"] and sum(out["outcomes"].values()) == c["signals"]
    det = get_detector("box_dip"); ver = search_version(det, s.thresholds)
    assert ver.startswith(det.version + "+") and len(ver) == len(det.version) + 9
    planet_key = make_job_key("planet", "box_dip", ver, pipeline.DEFAULT_LADDER_VERSION)
    assert store.job_done(planet_key) and store.job_done(make_job_key("noise", "box_dip", ver, pipeline.DEFAULT_LADDER_VERSION))
    with store.session() as ss:
        assert {r.state for r in ss.query(db.TargetRow)} == {"done"}
        planet_sigs = ss.query(db.SignalRow).filter_by(target_id="planet").all()
        assert all(r.detector_version == search_version(get_detector("box_dip"), s.thresholds) for r in planet_sigs)
        assert all(r.extra["search_window"] == {"min_period_days": 5, "max_period_days": 40} for r in planet_sigs)
        assert any(abs(r.period - 13.769) < 0.05 for r in planet_sigs)
        assert all(r.final_status in ("candidate_stage1", "rejected", "undetermined") for r in ss.query(db.SignalRow))
        alive = {r.target_id for r in ss.query(db.SignalRow) if r.final_status != "rejected"}
        arts = {a.target_id: (a.pinned, a.deleted_at, a.local_path) for a in ss.query(db.ArtifactRow).filter_by(kind="raw")}
        summaries = {a.target_id: (a.pinned, a.deleted_at, a.local_path) for a in
                     ss.query(db.ArtifactRow).filter_by(kind="summary")}
        assert {r.target_id: r.periodogram_sha256 is not None for r in ss.query(db.RunRow)} == {"planet": True, "noise": True}
    assert "planet" in alive
    # stream retention: no unpinned raw file is left on disk; live signals keep their evidence pinned
    assert store.unpinned_artifacts(kind="raw") == []
    for tid, (pinned, deleted_at, path) in arts.items():
        assert pinned == (tid in alive) and (deleted_at is None) == pinned and Path(path).exists() == pinned
    # periodogram summaries stay in stream mode (the null set for calibration), pinned only with live evidence
    assert set(summaries) == {"planet", "noise"}
    for tid, (pinned, deleted_at, path) in summaries.items():
        assert pinned == (tid in alive) and deleted_at is None and Path(path).exists()
    # an interrupted rerun leaves a 'running' row; the earlier done run still checkpoints the target
    store.start_run(planet_key, "planet", "box_dip", ver, pipeline.DEFAULT_LADDER_VERSION)
    assert store.job_done(planet_key)
    # resume: second run skips both
    out2 = run(src, store, blob, s, filter={}, max_targets=2)
    assert out2["skipped"] == 2 and out2["processed"] == 0 and store.counts()["signals"] == c["signals"]
    assert store.counts()["runs"] == 3


@pytest.mark.parametrize("mode,expect_file", [("all", True), ("signals", False), ("off", False)])
def test_store_periodogram_modes(settings, monkeypatch, mode, expect_file):
    from skysift.detectors.periodogram import PeriodogramSummary
    s, store, blob = _env(monkeypatch, settings, THRESHOLDS__MIN_PERIOD_DAYS="5", THRESHOLDS__MAX_PERIOD_DAYS="40",
                          STORE_PERIODOGRAM=mode)
    lc = make_lightcurve(n_days=500, seed=11, noise_ppm=600)          # pure noise: the top peaks all fail rung 1
    src = ScriptedSource([_tgt("quiet")], {"quiet": lc}, store, blob)
    out = run(src, store, blob, s, filter={}, with_novelty=False)
    assert out["processed"] == 1
    with store.session() as ss:
        sha = ss.query(db.RunRow).one().periodogram_sha256
        arts = [a for a in ss.query(db.ArtifactRow) if a.kind == "summary"]
        ss.expunge_all()                                                 # keep the loaded rows readable below
    assert (sha is not None) == expect_file and (len(arts) == 1) == expect_file
    if expect_file:
        ps = PeriodogramSummary.from_npz(blob.get(arts[0].local_path))
        assert ps.n_bins == 2048 and len(ps.peaks) == 3                 # the detector's top three peaks, all rejected
        assert arts[0].url == "skysift://periodogram/quiet" and not arts[0].pinned and blob.exists(arts[0].local_path)


def test_signals_mode_stores_periodogram_when_a_signal_exists(settings, monkeypatch):
    s, store, blob = _env(monkeypatch, settings, THRESHOLDS__MIN_PERIOD_DAYS="5", THRESHOLDS__MAX_PERIOD_DAYS="40",
                          STORE_PERIODOGRAM="signals")
    lc = inject_transit(make_lightcurve(n_days=500, seed=5, noise_ppm=600), 12.5, 140.0, 0.12, 2000)
    src = ScriptedSource([_tgt("planet")], {"planet": lc}, store, blob)
    run(src, store, blob, s, filter={}, with_novelty=False)
    with store.session() as ss:
        run_row = ss.query(db.RunRow).one(); arts = [a for a in ss.query(db.ArtifactRow) if a.kind == "summary"]
        ss.expunge_all()                                                 # keep the loaded rows readable below
    assert run_row.periodogram_sha256 == arts[0].sha256 and arts[0].pinned       # pinned with the evidence


class ScriptedSource(FakeSource):
    """FakeSource with explicit targets, fetches that store a file and then fail, and a per-fetch hook."""

    def __init__(self, targets, lcs, store, blob, bad_fetch=(), on_fetch=None):
        super().__init__(lcs, store, blob)
        self.targets, self.bad_fetch, self.on_fetch = targets, set(bad_fetch), on_fetch

    def list_targets(self, filter):
        yield from self.targets

    def fetch_lightcurve(self, target):
        out = super().fetch_lightcurve(target)        # the file lands on disk and is recorded first
        if target.target_id in self.bad_fetch:
            raise ConnectionError(f"truncated download for {target.target_id}")
        if self.on_fetch: self.on_fetch(target)
        return out


def _env(monkeypatch, settings_fixture, **env):
    """Settings + store + blob; the 30-40 d window on 60 d curves makes the detector return [] at once."""
    env = {"WORKERS_CPU": "1", "THRESHOLDS__MIN_PERIOD_DAYS": "30", "THRESHOLDS__MAX_PERIOD_DAYS": "40", **env}
    for k, v in env.items(): monkeypatch.setenv(f"SKYSIFT_{k}", v)
    s = load_settings(); s.data_dir.mkdir(exist_ok=True)
    store = Store(s.database_url); store.init_schema()
    return s, store, LocalBlob(s.data_dir / "blobs")


def _tgt(tid, gmag=16.5, psffrac=0.9, crowdsap=0.9, kic=None):
    return Target(source="fake", target_id=tid, ra=0, dec=0, gmag=gmag, kic=kic,
                  meta={"psffrac": psffrac, "crowdsap": crowdsap})


def _runs(store):
    with store.session() as ss:
        return {r.target_id: (r.status, r.error) for r in ss.query(db.RunRow)}


def _states(store):
    with store.session() as ss:
        return {r.target_id: r.state for r in ss.query(db.TargetRow)}


def test_run_keeps_fetched_but_unwritten_targets_bounded(settings, monkeypatch):
    s, store, blob = _env(monkeypatch, settings, WORKERS_IO="2")
    assert (s.workers_io, s.workers_cpu) == (2, 1)
    lc = make_lightcurve(n_days=60, seed=4)
    targets = [_tgt(f"t{i:02d}") for i in range(20)]
    lock, fetched, finished, peak = threading.Lock(), [0], [0], [0]

    def on_fetch(_t):
        with lock:
            fetched[0] += 1; peak[0] = max(peak[0], fetched[0] - finished[0])

    def progress(summary):
        with lock: finished[0] = summary["processed"] + summary["errors"]

    src = ScriptedSource(targets, {t.target_id: lc for t in targets}, store, blob, on_fetch=on_fetch)
    out = run(src, store, blob, s, filter={}, with_novelty=False, progress=progress)
    assert out["processed"] == 20 and out["errors"] == 0 and fetched[0] == 20
    assert 1 <= peak[0] <= 2 * s.workers_io + 2 * s.workers_cpu + 1, peak[0]
    assert {st for st, _ in _runs(store).values()} == {"done"} and len(_runs(store)) == 20
    print(f"peak fetched-but-unwritten = {peak[0]} (bound {2 * s.workers_io + 2 * s.workers_cpu + 1})")


def test_max_targets_takes_highest_priority_after_full_enumeration(settings, monkeypatch):
    s, store, blob = _env(monkeypatch, settings)
    # enumerated lowest priority first, so "first N enumerated" would pick the wrong two
    targets = [_tgt("p_minus3", gmag=19.5, psffrac=0.3, crowdsap=0.3, kic=1), _tgt("p1", gmag=14, psffrac=0.3, crowdsap=0.3),
               _tgt("p3", psffrac=0.3, crowdsap=0.3), _tgt("p5"), _tgt("p4", crowdsap=0.3)]
    assert [priority_score(t) for t in targets] == [-3, 1, 3, 5, 4]
    lc = make_lightcurve(n_days=60, seed=4)
    src = ScriptedSource(targets, {t.target_id: lc for t in targets}, store, blob)
    out0 = run(src, store, blob, s, filter={}, max_targets=0, with_novelty=False)
    assert out0["processed"] == 0 and _runs(store) == {} and store.counts()["targets"] == 5
    out = run(src, store, blob, s, filter={}, max_targets=2, with_novelty=False)
    assert out["processed"] == 2 and set(_runs(store)) == {"p5", "p4"}


def test_target_ids_filter_sweeps_the_named_stars_in_list_order(settings, monkeypatch):
    """A sweep over a hand-picked list (`--targets FILE`): only the listed ids are searched, in the list's order
    rather than by priority, ids the source does not know are reported, and max_targets still caps the list."""
    s, store, blob = _env(monkeypatch, settings)
    targets = [_tgt("p5"), _tgt("p1", gmag=14, psffrac=0.3, crowdsap=0.3), _tgt("p4", crowdsap=0.3), _tgt("p3", psffrac=0.3, crowdsap=0.3)]
    lc = make_lightcurve(n_days=60, seed=4)
    src = ScriptedSource(targets, {t.target_id: lc for t in targets}, store, blob)
    order = []
    out = run(src, store, blob, s, filter={"target_ids": ["p1", "p3", "nope", "p5"]}, with_novelty=False,
              progress=lambda sm: order.append(sm["processed"]))
    assert out["processed"] == 3 and out["unknown_targets"] == 1 and set(_runs(store)) == {"p1", "p3", "p5"}
    assert store.counts()["targets"] == 4                                   # the whole source is still recorded
    det = get_detector("box_dip"); ver, lad = search_version(det, s.thresholds), pipeline.DEFAULT_LADDER_VERSION
    todo, _skipped, _gave_up, unknown = _select_targets(src, store, {"target_ids": ["p3", "p1", "zz"]}, det, ver, lad, None)
    assert [t.target_id for t, _k in todo] == [] and unknown == 1           # both done now; zz is not in the source
    todo, _s, _g, unknown = _select_targets(src, store, {"target_ids": ["p4", "p1", "p5"]}, det, ver, lad, 1)
    assert [t.target_id for t, _k in todo] == ["p4"] and unknown == 0       # list order, not priority, then the cap


def test_fetch_failure_marks_error_releases_file_and_continues(settings, monkeypatch):
    s, store, blob = _env(monkeypatch, settings)
    lc = make_lightcurve(n_days=60, seed=4)
    src = ScriptedSource([_tgt("good"), _tgt("bad")], {"good": lc, "bad": lc}, store, blob, bad_fetch={"bad"})
    seen = []
    out = run(src, store, blob, s, filter={}, with_novelty=False, progress=lambda x: seen.append(dict(x)))
    assert out["processed"] == 1 and out["errors"] == 1 and len(seen) == 2     # progress after the failure too
    runs = _runs(store)
    assert runs["good"][0] == "done" and runs["bad"][0] == "error" and "truncated download" in runs["bad"][1]
    assert _states(store) == {"good": "done", "bad": "error"}
    with store.session() as ss:
        bad = ss.query(db.ArtifactRow).filter_by(target_id="bad").one()
        assert not bad.pinned and bad.deleted_at is not None and not Path(bad.local_path).exists()
    assert store.unpinned_artifacts() == []


def test_worker_failure_marks_error_and_continues(settings, monkeypatch):
    s, store, blob = _env(monkeypatch, settings)
    good = make_lightcurve(n_days=60, seed=4)
    broken = make_lightcurve(n_days=60, seed=5); broken.flux = broken.flux[:-7]     # length mismatch -> worker raises
    src = ScriptedSource([_tgt("good"), _tgt("broken")], {"good": good, "broken": broken}, store, blob)
    out = run(src, store, blob, s, filter={}, with_novelty=False)
    assert out["processed"] == 1 and out["errors"] == 1
    runs = _runs(store)
    assert runs["good"][0] == "done" and runs["broken"][0] == "error" and runs["broken"][1]
    assert _states(store) == {"good": "done", "broken": "error"}
    assert store.unpinned_artifacts() == [] and multiprocessing.active_children() == []


def test_worker_crash_charges_the_lone_target_and_the_sweep_continues(settings, monkeypatch):
    """A worker process that dies (here os._exit through the test hook) breaks the whole pool. Alone in the pool, the
    crashing target is the culprit: its run is charged an attempt, and the sweep goes on in a new pool."""
    s, store, blob = _env(monkeypatch, settings, WORKERS_IO="1")
    good = make_lightcurve(n_days=60, seed=4)
    crash = make_lightcurve(n_days=60, seed=5); crash.meta["__crash_worker__"] = True
    first_done, crash_failed = threading.Event(), threading.Event()

    def progress(summary):
        if summary["processed"] >= 1: first_done.set()
        if summary["errors"] >= 1: crash_failed.set()

    def on_fetch(t):
        # two CPU jobs may be in flight (2 * WORKERS_CPU): hold the fetches back so the crashing job is alone in the
        # pool when it dies, otherwise a neighbour sharing the pool would fail with it
        if t.target_id == "crash": first_done.wait(120)
        if t.target_id == "third": crash_failed.wait(120)

    src = ScriptedSource([_tgt("first"), _tgt("crash"), _tgt("third")], {"first": good, "crash": crash, "third": good},
                         store, blob, on_fetch=on_fetch)
    out = run(src, store, blob, s, filter={}, with_novelty=False, progress=progress)
    assert out["pool_restarts"] == 1 and out["processed"] == 2 and out["errors"] == 1
    assert json.loads(jsonutil.dumps(out))["pool_restarts"] == 1                  # strict JSON for the CLI
    runs = _runs(store)
    assert runs == {"first": ("done", ""), "crash": ("error", "worker died: pool broke"), "third": ("done", "")}
    assert _states(store) == {"first": "done", "crash": "error", "third": "done"}
    assert store.attempts(_job_key(store, "crash")) == 1                           # charged toward the cap
    assert store.unpinned_artifacts() == [] and multiprocessing.active_children() == []


_EMPTY_PAYLOAD = json.dumps({"signals": [], "queries": [], "periodogram": None})


class _FakePool:
    """Stands in for the CPU pool: submit() hands out futures that ``script(pool, future)`` settles (or raises), so a
    test decides which job finishes, which one breaks the pool, and when."""

    def __init__(self, script):
        self.script, self.futures = script, []

    def submit(self, *a, **kw):
        f = Future(); self.futures.append(f)
        self.script(self, f)
        return f

    def shutdown(self, *a, **kw): pass


def _fake_pools(monkeypatch, *scripts):
    """Every cpu_pool call builds the next scripted pool (the last script repeats); returns the pools built."""
    pools = []

    def make(_settings):
        pools.append(_FakePool(scripts[min(len(pools), len(scripts) - 1)])); return pools[-1]
    monkeypatch.setattr(pipeline, "cpu_pool", make)
    return pools


def _job_key(store, target_id):
    with store.session() as ss:
        return ss.query(db.RunRow).filter_by(target_id=target_id).one().job_key


def test_a_pool_break_holding_several_runs_charges_none(settings, monkeypatch):
    def break_both(pool, _f):         # the first job waits until the second is submitted; then the pool breaks
        if len(pool.futures) == 2:
            for f in pool.futures: f.set_exception(BrokenProcessPool("a child process terminated abruptly"))
    pools = _fake_pools(monkeypatch, break_both)
    s, store, blob = _env(monkeypatch, settings)
    lc = make_lightcurve(n_days=60, seed=4)
    src = ScriptedSource([_tgt("a"), _tgt("b")], {"a": lc, "b": lc}, store, blob)
    out = run(src, store, blob, s, filter={}, with_novelty=False)
    assert out["pool_restarts"] == 1 and out["errors"] == 2 and len(pools) == 2
    assert _runs(store) == {"a": ("error", "unavailable: worker pool broke"), "b": ("error", "unavailable: worker pool broke")}
    assert store.attempts(_job_key(store, "a")) == store.attempts(_job_key(store, "b")) == 0
    assert store.unpinned_artifacts() == []


def test_a_result_finished_before_the_break_is_still_written(settings, monkeypatch):
    def finish_first_then_break(pool, _f):      # the first job finishes; the pool breaks on the next submit
        if len(pool.futures) == 2:
            pool.futures[0].set_result(_EMPTY_PAYLOAD)
            raise BrokenProcessPool("a child process terminated abruptly")

    def healthy(_pool, f): f.set_result(_EMPTY_PAYLOAD)
    pools = _fake_pools(monkeypatch, finish_first_then_break, healthy)
    s, store, blob = _env(monkeypatch, settings)
    lc = make_lightcurve(n_days=60, seed=4)
    src = ScriptedSource([_tgt("a"), _tgt("b")], {"a": lc, "b": lc}, store, blob)
    out = run(src, store, blob, s, filter={}, with_novelty=False)
    assert out["pool_restarts"] == 1 and out["processed"] == 2 and out["errors"] == 0 and len(pools) == 2
    assert _runs(store) == {"a": ("done", ""), "b": ("done", "")}     # one salvaged, one resubmitted to the new pool


def test_a_worker_error_held_at_a_break_keeps_its_own_text(settings, monkeypatch):
    def error_then_break(pool, f):
        if len(pool.futures) == 2:
            first = pool.futures[0]

            def result(timeout=None):      # the first job's own error lands just before the break is handled
                first.set_exception(ValueError("bad flux")); return Future.result(f, timeout)
            f.result = result
            f.set_exception(BrokenProcessPool("a child process terminated abruptly"))
    _fake_pools(monkeypatch, error_then_break)
    s, store, blob = _env(monkeypatch, settings)
    lc = make_lightcurve(n_days=60, seed=4)
    src = ScriptedSource([_tgt("a"), _tgt("b")], {"a": lc, "b": lc}, store, blob)
    out = run(src, store, blob, s, filter={}, with_novelty=False)
    assert out["pool_restarts"] == 1 and out["errors"] == 2
    # the worker error is the target's own; the break, with no other run lost, is the lone crash
    assert sorted(_runs(store).values()) == [("error", "ValueError('bad flux')"), ("error", "worker died: pool broke")]
    assert store.attempts(_job_key(store, "a")) == store.attempts(_job_key(store, "b")) == 1


def test_salvaging_several_results_never_evicts_the_unwritten_ones(settings, monkeypatch):
    def finish_two_then_break(pool, _f):        # two jobs finish; the pool breaks on the third submit
        if len(pool.futures) == 3:
            for f in pool.futures[:2]: f.set_result(_EMPTY_PAYLOAD)
            raise BrokenProcessPool("a child process terminated abruptly")

    def healthy(_pool, f): f.set_result(_EMPTY_PAYLOAD)
    _fake_pools(monkeypatch, finish_two_then_break, healthy)
    s, store, blob = _env(monkeypatch, settings, WORKERS_CPU="2")
    real, calls = pipeline.Retention.enforce_budget, []

    def spy(self, exclude_targets=None):        # every target with an unfinished run must be safe from eviction
        running = {t for t, (st, _) in _runs(store).items() if st == "running"}
        calls.append((running, set(exclude_targets or ())))
        return real(self, exclude_targets=exclude_targets)
    monkeypatch.setattr(pipeline.Retention, "enforce_budget", spy)
    lc = make_lightcurve(n_days=60, seed=4)
    src = ScriptedSource([_tgt("a"), _tgt("b"), _tgt("c")], {"a": lc, "b": lc, "c": lc}, store, blob)
    out = run(src, store, blob, s, filter={}, with_novelty=False)
    assert out["pool_restarts"] == 1 and out["processed"] == 3 and out["errors"] == 0
    assert len(calls) == 3 and len(calls[0][0]) == 2                # first salvage: one salvaged and one queued wait
    assert all(running <= excluded for running, excluded in calls), calls


def test_a_third_break_ends_the_sweep_after_two_restarts(settings, monkeypatch):
    def break_at_once(_pool, f): f.set_exception(BrokenProcessPool("a child process terminated abruptly"))
    pools = _fake_pools(monkeypatch, break_at_once)
    s, store, blob = _env(monkeypatch, settings, WORKERS_IO="1")
    failed = [threading.Event() for _ in range(3)]
    seen = []

    def progress(summary):
        seen.append(dict(summary))
        if summary["errors"]: failed[summary["errors"] - 1].set()

    def on_fetch(t):                  # one job per pool: t1 breaks the first, t2 the second, t3 the third
        if t.target_id != "t1": failed[int(t.target_id[1]) - 2].wait(30)

    lc = make_lightcurve(n_days=60, seed=4)
    src = ScriptedSource([_tgt("t1"), _tgt("t2"), _tgt("t3")], {"t1": lc, "t2": lc, "t3": lc}, store, blob,
                         on_fetch=on_fetch)
    with pytest.raises(BrokenProcessPool):
        run(src, store, blob, s, filter={}, with_novelty=False, progress=progress)
    assert len(pools) == 1 + MAX_POOL_RESTARTS == 3
    assert seen[-1]["pool_restarts"] == 2 and seen[-1]["errors"] == 3
    assert _runs(store) == {f"t{i}": ("error", "worker died: pool broke") for i in (1, 2, 3)}


def test_a_pool_broken_before_any_submit_ends_the_sweep_after_two_restarts(settings, monkeypatch):
    def dead(_pool, _f): raise BrokenProcessPool("a child process terminated abruptly")
    pools = _fake_pools(monkeypatch, dead)
    s, store, blob = _env(monkeypatch, settings)
    src = ScriptedSource([_tgt("t1")], {"t1": make_lightcurve(n_days=60, seed=4)}, store, blob)
    with pytest.raises(BrokenProcessPool):
        run(src, store, blob, s, filter={}, with_novelty=False)
    assert len(pools) == 1 + MAX_POOL_RESTARTS == 3
    assert _runs(store) == {"t1": ("running", "")}      # never submitted: the next sweep supersedes it, uncharged


def test_disk_budget_exceeded_stops_run_after_planet_is_written(settings, monkeypatch):
    s, store, blob = _env(monkeypatch, settings, RETENTION="budget:0",
                          THRESHOLDS__MIN_PERIOD_DAYS="5", THRESHOLDS__MAX_PERIOD_DAYS="15")
    # 220 d spans three seasons, so rung 3 can pass (two seasons leave it undetermined)
    planet = inject_transit(make_lightcurve(n_days=220, seed=1, noise_ppm=800), 7.3, 135.0, 0.1, 3000)
    src = ScriptedSource([_tgt("planet")], {"planet": planet}, store, blob)
    with pytest.raises(DiskBudgetExceeded) as exc:
        run(src, store, blob, s, filter={}, with_novelty=False)
    assert multiprocessing.active_children() == []
    assert exc.value.summary["processed"] == 1 and exc.value.summary["signals"] >= 1     # reported by the CLI
    assert _runs(store)["planet"][0] == "done" and _states(store)["planet"] == "done"
    with store.session() as ss:
        assert ss.query(db.SignalRow).filter_by(final_status="candidate_stage1").count() >= 1
        art = ss.query(db.ArtifactRow).filter_by(kind="raw").one()
        assert art.pinned and art.deleted_at is None and Path(art.local_path).exists()
        summary = ss.query(db.ArtifactRow).filter_by(kind="summary").one()
        assert summary.pinned and summary.deleted_at is None and Path(summary.local_path).exists()


def test_rung4_catalogue_queries_are_logged_during_a_sweep(settings, monkeypatch):
    s, store, blob = _env(monkeypatch, settings, THRESHOLDS__MIN_PERIOD_DAYS="5", THRESHOLDS__MAX_PERIOD_DAYS="15")
    # 220 d spans three seasons, so rung 3 can pass and the ladder reaches rung 4 with a passing signal
    planet = inject_transit(make_lightcurve(n_days=220, seed=1, noise_ppm=800), 7.3, 135.0, 0.1, 3000)
    src = ScriptedSource([_tgt("planet")], {"planet": planet}, store, blob)
    out = run(src, store, blob, s, filter={}, with_novelty=True, fetch_module="tests.unit.fake_fetch")
    assert out["processed"] == 1 and out["errors"] == 0 and out["outcomes"]["pass"] >= 1
    assert store.counts()["queries"] > 0
    with store.session() as ss:
        rows = ss.query(db.QueryRow).all()
        assert {r.service for r in rows} == {"exoarchive", "vizier"}
        assert all(r.url.startswith("https://") and r.status == 200 and r.sha256 for r in rows)
        rung4 = ss.query(db.TestRow).filter_by(rung="rung4_novelty").all()
        assert rung4 and all(t.status == "pass" for t in rung4)


def test_result_write_is_atomic_and_a_rerun_does_not_duplicate(settings, monkeypatch):
    s, store, blob = _env(monkeypatch, settings, THRESHOLDS__MIN_PERIOD_DAYS="5", THRESHOLDS__MAX_PERIOD_DAYS="15")
    planet = inject_transit(make_lightcurve(n_days=120, seed=1, noise_ppm=800), 7.3, 135.0, 0.1, 3000)
    src = ScriptedSource([_tgt("planet")], {"planet": planet}, store, blob)
    real, calls = Store._add_test_row, [0]

    def flaky(self, *a, **kw):              # fails midway: after a signal row and its first test row were added
        calls[0] += 1
        if calls[0] == 2: raise RuntimeError("database went away")
        return real(self, *a, **kw)

    monkeypatch.setattr(Store, "_add_test_row", flaky)
    out = run(src, store, blob, s, filter={}, with_novelty=False)
    assert out["errors"] == 1 and out["processed"] == 0
    assert store.counts()["signals"] == 0 and store.counts()["tests"] == 0
    assert _runs(store)["planet"][0] == "error" and _states(store)["planet"] == "error"
    monkeypatch.setattr(Store, "_add_test_row", real)
    out2 = run(src, store, blob, s, filter={}, with_novelty=False)
    assert out2["processed"] == 1 and out2["signals"] >= 1
    with store.session() as ss:
        sigs = ss.query(db.SignalRow).filter_by(target_id="planet").all()
        assert len(sigs) == out2["signals"] and len({r.run_id for r in sigs}) == 1
        assert len({round(r.period, 4) for r in sigs}) == len(sigs)
        assert all(r.final_status != "pending" for r in sigs)


def test_job_key_depends_on_the_search_window(settings, monkeypatch):
    s, store, blob = _env(monkeypatch, settings, THRESHOLDS__MIN_PERIOD_DAYS="5", THRESHOLDS__MAX_PERIOD_DAYS="40")
    lc = make_lightcurve(n_days=15, seed=4)            # 13.5 d baseline: a 5-40 d window is empty, 0.5-40 is cheap
    src = ScriptedSource([_tgt("t1")], {"t1": lc}, store, blob)
    assert run(src, store, blob, s, filter={}, with_novelty=False)["processed"] == 1
    s.thresholds.min_period_days = 0.5                  # a wider window is new work, not a done job
    out = run(src, store, blob, s, filter={}, with_novelty=False)
    assert out["processed"] == 1 and out["skipped"] == 0
    out = run(src, store, blob, s, filter={}, with_novelty=False)            # same window again: done
    assert out["processed"] == 0 and out["skipped"] == 1
    det = get_detector("box_dip")
    v_narrow = search_version(det, Thresholds(min_period_days=5, max_period_days=40))
    assert v_narrow != search_version(det, s.thresholds) and v_narrow == search_version(det, Thresholds(
        min_period_days=5, max_period_days=40, sde_min=12))           # vetting thresholds are not search config
    with store.session() as ss:
        assert {r.detector_version for r in ss.query(db.RunRow)} == {v_narrow, search_version(det, s.thresholds)}
    from skysift.detectors.box_dip import BoxDip
    assert search_version(BoxDip(), s.thresholds) == search_version(det, s.thresholds)
    assert search_version(BoxDip(alias_resolution=0), s.thresholds) != search_version(det, s.thresholds)


def test_search_version_follows_the_detector_version(monkeypatch):
    """The distinct-power band sigma of 19 October 2026 changed what BoxDip returns on sparse curves, so its version is
    5 and every stored run of an earlier version is a different job and is searched again."""
    from skysift.detectors.box_dip import BoxDip
    det, th = BoxDip(), Thresholds()
    now = search_version(det, th)
    assert BoxDip.version == "5" and now.startswith("5+")
    monkeypatch.setattr(BoxDip, "version", "2")
    assert search_version(det, th) != now and search_version(det, th).startswith("2+")


def test_search_version_ignores_the_rung_2_thresholds():
    """MS2 and MS3 are vetting thresholds: they change no flux the detector sees, so a run with other values is the
    same search job (a change of verdict is the ladder version's business, not the search version's)."""
    det, th = get_detector("box_dip"), Thresholds()
    assert th.ms2_max == 2.0 and th.ms3_max == 4.0
    assert search_version(det, th) == search_version(det, th.model_copy(update={"ms2_max": 9.0, "ms3_max": 9.0}))
    assert search_version(det, th) == search_version(det, Thresholds(ms2_max=9.0, ms3_max=9.0))


def test_a_masked_signal_sees_the_detrended_flux_masked_too():
    """A second-pass signal is vetted with its mask applied to every flux the rungs read, the detrended flux of SWEET
    included; a signal without a mask gets the bundle as it is."""
    from skysift.lc.ops import apply_mask
    from skysift.vetting.base import DataBundle
    lc = make_lightcurve(n_days=60, seed=3, noise_ppm=500)
    fn = np.ones_like(lc.time)
    b = DataBundle(lc=lc, fnorm=fn, target=Target(source="fake", target_id="m", ra=0, dec=0), thresholds=Thresholds(),
                   flux_detrended=fn.copy())
    mask = [{"period": 7.0, "t0": 135.0, "half_primary_days": 0.2, "half_secondary_days": 0.0}]
    sig = Signal(target_id="m", detector="box_dip", detector_version="1", period=3.3, t0=134.0, duration=0.1,
                 depth_ppm=500, snr=10.0, sde=12.0, n_events=10, extra={"mask": mask})
    vb = pipeline._vetting_bundle(b, sig)
    assert np.isnan(vb.flux_detrended).any()
    assert np.array_equal(vb.flux_detrended, apply_mask(lc.time, fn, mask), equal_nan=True)
    assert vb.fnorm_unmasked is fn and b.fnorm_unmasked is None          # the observed cadences for rung 1 events
    assert pipeline._vetting_bundle(b, sig.model_copy(update={"extra": {}})) is b


@pytest.mark.parametrize("name,value", [("DETREND_VERSION", 2), ("GAP_EDGE_MASK_DAYS", 0),
                                        ("GAP_EDGE_TRIGGER_DAYS", 1.0), ("MEDIAN_PAD_MODE", "nearest")])
def test_search_version_depends_on_every_detrend_setting(monkeypatch, name, value):
    """The detrend version, the gap-edge mask, its trigger and the median padding each change the flux every rung
    sees, so each is part of the search version (and of the record stored with every signal)."""
    det, th = get_detector("box_dip"), Thresholds()
    before = search_version(det, th)
    monkeypatch.setattr(pipeline, name, value)
    assert search_version(det, th) != before
    assert value in pipeline.detrend_settings().values()


def test_errors_are_retried_at_most_three_times(settings, monkeypatch):
    from typer.testing import CliRunner

    from skysift.cli import app

    s, store, blob = _env(monkeypatch, settings)
    lc = make_lightcurve(n_days=60, seed=4)
    src = ScriptedSource([_tgt("bad")], {"bad": lc}, store, blob, bad_fetch={"bad"})
    calls = []
    real_fetch = src.fetch_lightcurve
    src.fetch_lightcurve = lambda t: (calls.append(t.target_id), real_fetch(t))[1]
    for _ in range(MAX_ATTEMPTS):
        out = run(src, store, blob, s, filter={}, with_novelty=False)
        assert out["errors"] == 1 and out["gave_up"] == 0
    assert len(calls) == MAX_ATTEMPTS == 3
    out = run(src, store, blob, s, filter={}, with_novelty=False)
    assert out["gave_up"] == 1 and out["errors"] == 0 and out["processed"] == 0 and len(calls) == 3
    r = CliRunner().invoke(app, ["status"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)["error_capped_targets"] == 1


def test_a_job_in_three_pool_breaks_is_given_up(settings, monkeypatch):
    """At production depth a break almost always holds several runs, so none is charged an attempt; a target that
    always kills its worker must still stop being retried. Three pool-break runs of one job (either text) give it up,
    whatever the attempt count; two leave it in the sweep."""
    s, store, blob = _env(monkeypatch, settings)
    lc = make_lightcurve(n_days=60, seed=4)
    targets = [_tgt("killer"), _tgt("twice"), _tgt("calm")]
    store.upsert_targets(targets)
    version = search_version(get_detector("box_dip"), s.thresholds)
    texts = {"killer": [pipeline.POOL_BROKE_ERROR, pipeline.WORKER_DIED_ERROR, pipeline.POOL_BROKE_ERROR],
             "twice": [pipeline.POOL_BROKE_ERROR, pipeline.POOL_BROKE_ERROR]}
    for tid, errors in texts.items():
        key = make_job_key(tid, "box_dip", version, pipeline.DEFAULT_LADDER_VERSION)
        for text in errors:
            store.finish_run(store.start_run(key, tid, "box_dip", version, pipeline.DEFAULT_LADDER_VERSION), "error",
                             text)
    killer = make_job_key("killer", "box_dip", version, pipeline.DEFAULT_LADDER_VERSION)
    assert store.attempts(killer) == 1 < MAX_ATTEMPTS                 # attempts() keeps its meaning
    assert store.pool_breaks(killer) == pipeline.MAX_POOL_BREAKS == 3
    src = ScriptedSource(targets, {t.target_id: lc for t in targets}, store, blob)
    out = run(src, store, blob, s, filter={}, with_novelty=False)
    assert out["gave_up"] == 1 and out["processed"] == 2 and out["errors"] == 0
    with store.session() as ss:
        assert ss.query(db.RunRow).filter_by(target_id="killer").count() == 3        # not selected: no fourth run
        assert ss.query(db.RunRow).filter_by(target_id="twice", status="done").count() == 1


def test_service_outages_do_not_use_up_attempts(settings, monkeypatch):
    from skysift.net import ServiceUnavailable

    s, store, blob = _env(monkeypatch, settings)
    lc = make_lightcurve(n_days=60, seed=4)
    src = ScriptedSource([_tgt("down")], {"down": lc}, store, blob)
    calls = []

    def outage(t):
        calls.append(t.target_id); raise ServiceUnavailable("mast https://x -> HTTP 503")
    src.fetch_lightcurve = outage
    for _ in range(MAX_ATTEMPTS + 2):
        out = run(src, store, blob, s, filter={}, with_novelty=False)
        assert out["errors"] == 1 and out["gave_up"] == 0
    assert len(calls) == MAX_ATTEMPTS + 2                       # still retried: an outage is not an attempt
    with store.session() as ss:
        errors = {r.error for r in ss.query(db.RunRow)}
    assert errors and all(e.startswith("unavailable:") for e in errors)


def test_duplicate_target_ids_start_one_run(settings, monkeypatch):
    s, store, blob = _env(monkeypatch, settings)
    lc = make_lightcurve(n_days=15, seed=4)
    t = _tgt("twin")
    src = ScriptedSource([t, t], {"twin": lc}, store, blob)
    out = run(src, store, blob, s, filter={}, with_novelty=False)
    assert out["processed"] == 1 and out["errors"] == 0
    with store.session() as ss:
        assert ss.query(db.RunRow).count() == 1


def test_salvaged_queries_carry_the_target_id(settings, monkeypatch):
    s, store, blob = _env(monkeypatch, settings, THRESHOLDS__MIN_PERIOD_DAYS="5", THRESHOLDS__MAX_PERIOD_DAYS="15")
    planet = inject_transit(make_lightcurve(n_days=120, seed=1, noise_ppm=800), 7.3, 135.0, 0.1, 3000)
    src = ScriptedSource([_tgt("planet")], {"planet": planet}, store, blob)

    def broken(self, *a, **kw): raise RuntimeError("database went away")
    monkeypatch.setattr(Store, "record_results", broken)
    out = run(src, store, blob, s, filter={}, with_novelty=True, fetch_module="tests.unit.fake_fetch")
    assert out["errors"] == 1 and out["processed"] == 0
    with store.session() as ss:
        rows = ss.query(db.QueryRow).all()
        assert rows and all(r.target_id == "planet" for r in rows)       # salvaged, and still tied to their target


def test_graceful_stop_finishes_in_flight_targets_and_supersedes_nothing(settings, monkeypatch):
    """A stop requested mid-sweep: the targets whose fetch was already submitted are vetted and written as done,
    nothing else is started, the summary says so, and the next sweep picks up the rest with no superseded run."""
    s, store, blob = _env(monkeypatch, settings, WORKERS_IO="2")
    lc = make_lightcurve(n_days=60, seed=4)
    targets = [_tgt(f"t{i:02d}") for i in range(20)]
    flag = threading.Event()
    src = ScriptedSource(targets, {t.target_id: lc for t in targets}, store, blob, on_fetch=lambda _t: flag.set())
    out = run(src, store, blob, s, filter={}, with_novelty=False, stop_requested=flag.is_set)
    in_flight = 2 * s.workers_io                       # the first top-up fills every fetch slot before any fetch lands
    assert out["stopped"] == "graceful" and out["processed"] == in_flight and out["errors"] == 0
    runs = _runs(store)
    assert len(runs) == in_flight and {st for st, _ in runs.values()} == {"done"}
    assert multiprocessing.active_children() == []
    out2 = run(src, store, blob, s, filter={}, with_novelty=False)
    assert "stopped" not in out2 and out2["skipped"] == in_flight and out2["processed"] == 20 - in_flight
    runs = _runs(store)
    assert len(runs) == 20 and {st for st, _ in runs.values()} == {"done"}      # no 'superseded' anywhere


def test_default_stop_check_is_the_stop_file_under_the_data_dir(settings, monkeypatch):
    from skysift.config import stop_request_path
    s, store, blob = _env(monkeypatch, settings)
    path = stop_request_path(s.data_dir)
    assert path == s.data_dir / "ui" / "sweeps" / "stop.request"
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text("now", encoding="utf-8")
    lc = make_lightcurve(n_days=60, seed=4)
    src = ScriptedSource([_tgt("t1"), _tgt("t2")], {"t1": lc, "t2": lc}, store, blob)
    out = run(src, store, blob, s, filter={}, with_novelty=False)
    assert out["stopped"] == "graceful" and out["processed"] == 0 and _runs(store) == {}
    assert path.exists()                                # the launcher owns the file; the engine only reads it


def test_cpu_workers_ignore_sigint_so_a_terminal_ctrl_c_reaches_only_the_scheduler(settings):
    import signal

    from skysift.pipeline import cpu_pool
    s = load_settings()
    pool = cpu_pool(s)
    try:
        assert pool.submit(signal.getsignal, signal.SIGINT).result(timeout=60) == signal.SIG_IGN
    finally:
        pool.shutdown()


def test_terminate_workers_makes_the_hard_stop_immediate():
    import time

    from skysift.pipeline import _cpu_pool, _terminate_workers, cpu_pool, terminate_workers
    assert (_cpu_pool, _terminate_workers) == (cpu_pool, terminate_workers)     # the old names stay
    pool = cpu_pool(load_settings())
    fut = pool.submit(time.sleep, 60)
    t0 = time.monotonic()
    terminate_workers(pool)
    pool.shutdown(wait=True, cancel_futures=True)
    assert time.monotonic() - t0 < 20 and fut.done() and multiprocessing.active_children() == []


def test_second_interrupt_drops_in_flight_work_for_the_next_sweep_to_supersede(settings, monkeypatch):
    s, store, blob = _env(monkeypatch, settings, WORKERS_IO="2")
    lc = make_lightcurve(n_days=60, seed=4)
    targets = [_tgt(f"t{i:02d}") for i in range(6)]
    src = ScriptedSource(targets, {t.target_id: lc for t in targets}, store, blob)

    def second_ctrl_c(_summary):
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        run(src, store, blob, s, filter={}, with_novelty=False, progress=second_ctrl_c)
    assert multiprocessing.active_children() == []
    runs = _runs(store)
    assert runs and sum(st == "done" for st, _ in runs.values()) == 1 and "running" in {st for st, _ in runs.values()}
    out = run(src, store, blob, s, filter={}, with_novelty=False)
    assert out["processed"] == 5 and out["skipped"] == 1
    with store.session() as ss:
        assert ss.query(db.RunRow).filter_by(error="superseded").count() >= 1
    assert {st for st, _ in _runs(store).values()} == {"done"}


def test_pipeline_whitens_a_pulsator_and_records_the_components(settings, monkeypatch):
    """A delta Scuti forest on top of a planet: the worker pre-whitens before the search, the planet is still found,
    and every stored signal carries the removed components so the dashboard can show the same flux."""
    s, store, blob = _env(monkeypatch, settings, THRESHOLDS__MIN_PERIOD_DAYS="5", THRESHOLDS__MAX_PERIOD_DAYS="40")
    lc = inject_transit(make_lightcurve(n_days=500, seed=5, noise_ppm=600), 12.5, 140.0, 0.12, 2000)
    f = lc.flux.copy()
    for k, (freq, amp) in enumerate([(15.0479, 1100.0), (13.4216, 600.0), (16.0676, 500.0)]):
        f *= 1 - amp * 1e-6 * np.cos(2 * np.pi * (freq * lc.time + 0.3 * (k + 1)))
    lc = LightCurve(lc.time, f, lc.flux_err, lc.quality, lc.quarter, f.copy(), dict(lc.meta))
    src = ScriptedSource([_tgt("pulsing")], {"pulsing": lc}, store, blob)
    out = run(src, store, blob, s, filter={}, with_novelty=False)
    assert out["processed"] == 1 and out["signals"] >= 1
    with store.session() as ss:
        sigs = ss.query(db.SignalRow).all(); ss.expunge_all()
    planet = next((x for x in sigs if abs(x.period - 12.5) < 0.02), None)
    assert planet is not None and planet.sde > 9
    pw = planet.extra["prewhitening"]
    assert pw["n"] >= 3 and len(pw["components"]) == pw["n"]
    assert {round(c["frequency"], 1) for c in pw["components"]} >= {15.0, 13.4, 16.1}
    assert all(set(c) == {"frequency", "amplitude_ppm", "snr", "coef"} for c in pw["components"])
    det = get_detector("box_dip")
    assert search_version(det, s.thresholds) != search_version(det, s.thresholds.model_copy(update={"prewhiten_max_components": 0}))


def test_every_signal_stores_the_detrend_it_was_measured_with():
    """The dashboard replays the stored detrend settings, so a later change to the detrend never redraws an old signal
    on flux it was not measured on. The record crosses the process boundary: strict JSON."""
    lc = inject_transit(make_lightcurve(n_days=120, seed=3, noise_ppm=500), 7.3, 135.0, 0.1, 3000)
    target = Target(source="fake", target_id="w1", ra=0, dec=0, gmag=16.5)
    th = Thresholds(min_period_days=5, max_period_days=15)
    out = _cpu_work(pickle.dumps(lc), target.model_dump_json(), th.model_dump_json(), "box_dip", "1", False)
    items = json.loads(out)["signals"]
    expected = {"version": pipeline.DETREND_VERSION, "edge_mask_days": pipeline.GAP_EDGE_MASK_DAYS,
                "edge_gap_days": pipeline.GAP_EDGE_TRIGGER_DAYS, "median_mode": pipeline.MEDIAN_PAD_MODE}
    assert items and all(i["signal"]["extra"]["detrend"] == expected for i in items)
    assert expected == {"version": 3, "edge_mask_days": 1.5, "edge_gap_days": 0.5, "median_mode": "mirror"}
    _fn, prewhitening, record, detrended = pipeline.whitened_flux(lc, th)
    assert record == expected and prewhitening["n"] == len(prewhitening["components"])
    # the fourth element is the detrended flux before pre-whitening (rung 2's SWEET reads it)
    assert np.array_equal(detrended, pipeline.whitened_flux(lc, th.model_copy(update={"prewhiten_max_components": 0}))[0],
                          equal_nan=True)
    # a star whose whitening stopped at the cap, not at an insignificant peak, says so in its record
    assert th.prewhiten_max_components == 100 and prewhitening["capped"] is False
    f = lc.flux * (1 - 2000e-6 * np.cos(2 * np.pi * 3.1 * lc.time)) * (1 - 1500e-6 * np.cos(2 * np.pi * 4.7 * lc.time))
    wavy = LightCurve(lc.time, f, lc.flux_err, lc.quality, lc.quarter, f.copy(), dict(lc.meta))
    capped = pipeline.whitened_flux(wavy, th.model_copy(update={"prewhiten_max_components": 1}))[1]
    assert capped["n"] == 1 and capped["capped"] is True
    json.dumps(prewhitening, allow_nan=False)
    json.dumps(record, allow_nan=False)



def test_second_pass_signal_is_vetted_on_the_masked_flux():
    """The masked second pass finds a companion only because the binary's eclipses are masked; vetted on the unmasked
    flux, the eclipses folded at the companion's period built a spurious secondary and rung 2 rejected it
    (K06356.01, 18 October 2026: ``secondary_at_0.5 5.3 sigma`` on a 2.108 d, 271 ppm injection). The rungs must see
    the flux the detector saw. The curve is the V binary of the detector tests (24.0 d, 11 h, 40000 ppm) with a 2.1 d,
    2.2 h, 500 ppm box, searched without pre-whitening as the detector tests are: with it, the whitened eclipse leaves
    24.0/n residues that take the second pass (0.5217 d) and the companion is not returned at all (measured 18 October
    2026). Without the mask its rung 2 was ``fail eccentric_secondary``; with it, rung 2 and the ladder pass."""
    lc = inject_v_eclipse(make_lightcurve(n_days=1400, seed=71, noise_ppm=700), 24.0, 140.0, 11 / 24, 40000)
    lc = inject_transit(lc, 2.1, 132.3, 2.2 / 24, 500)
    target = Target(source="fake", target_id="v1", ra=0, dec=0, gmag=16.5)
    th = Thresholds(min_period_days=0.5, max_period_days=30.0, prewhiten_max_components=0)
    out = _cpu_work(pickle.dumps(lc), target.model_dump_json(), th.model_dump_json(), "box_dip", "1", False,
                    fetch_module="tests.unit.fake_fetch", store_periodogram="off")
    items = json.loads(out)["signals"]
    found = [(round(i["signal"]["period"], 4), i["signal"]["extra"].get("pass", 1), i["outcome"],
              [(r["rung"], r["status"], r["reason"]) for r in i["tests"]]) for i in items]
    comp = [i for i in items if abs(i["signal"]["period"] - 2.1) / 2.1 < 0.01 and i["signal"]["extra"].get("pass") == 2]
    assert comp, found
    rung2 = [r for r in comp[0]["tests"] if r["rung"] == "rung2_shape"]
    assert rung2, found                                       # rung 1 passed, so rung 2 ran
    status, reason = rung2[0]["status"], rung2[0]["reason"] or ""
    assert not (status == "fail" and reason.startswith(("secondary_at_0.5", "eccentric_secondary"))), (status, reason)
    mask = comp[0]["signal"]["extra"].get("mask")
    assert mask and all(set(m) == {"period", "t0", "half_primary_days", "half_secondary_days"} for m in mask), mask
    assert any(abs(m["period"] - 24.0) / 24.0 < 0.01 for m in mask), mask
    # the mask is not missing data: rung 1 events counts the masked cadences as observed (final review, finding 2).
    # The 15 quarter gaps of the curve still leave about 22 of the 665 epochs uncovered (measured 18 October 2026),
    # so the masked counts must equal those of the same signal on the unmasked flux: the mask costs no coverage
    events = [r for r in comp[0]["tests"] if r["rung"] == "rung1_events"]
    assert events and events[0]["status"] == "pass", found
    from skysift.lc.ops import EVENT_COVERAGE_MIN, event_quality
    sig = comp[0]["signal"]
    unmasked = event_quality(lc.time, pipeline.whitened_flux(lc, th)[0], sig["period"], sig["t0"], sig["duration"])
    m = events[0]["metrics"]
    assert m["n_events_with_data"] == unmasked.epochs.size, m
    assert m["n_covered"] == int(np.count_nonzero(unmasked.coverage >= EVENT_COVERAGE_MIN)), m
    assert m["n_covered"] >= 0.95 * m["n_events_with_data"], m


class _InlinePool:
    """Stands in for the CPU pool and runs each job in this process at submit, so a monkeypatch reaches the worker."""

    def submit(self, fn, *a, **kw):
        f = Future()
        try: f.set_result(fn(*a, **kw))
        except Exception as e: f.set_exception(e)  # noqa: BLE001  (a worker error travels in its future, as in a pool)
        return f

    def shutdown(self, *a, **kw): pass


def test_pipeline_restores_removed_harmonics_of_a_detection(settings, monkeypatch):
    """A wave at exactly the transit frequency makes pre-whitening (shape guard off) remove the transit's own
    fundamental and harmonics with it; the detection then sits on flux that lost most of its depth. The worker puts
    the removed components at the detection's harmonics back, searches once more and stores that search, whose
    record no longer lists them, so the dashboard replays exactly the flux searched."""
    period, t0, duration, depth = 1.5, 132.2, 3 / 24, 1500.0
    s, store, blob = _env(monkeypatch, settings, THRESHOLDS__MIN_PERIOD_DAYS="1", THRESHOLDS__MAX_PERIOD_DAYS="5",
                          THRESHOLDS__PREWHITEN_MAX_COMPONENTS="2")
    lc = inject_transit(make_lightcurve(n_days=500, seed=7, noise_ppm=600), period, t0, duration, depth)
    # the wave is odd about mid-transit, so it leaves the box depth alone when it is put back
    f = lc.flux * (1 - 300e-6 * np.sin(2 * np.pi * (lc.time - t0) / period))
    lc = LightCurve(lc.time, f, lc.flux_err, lc.quality, lc.quarter, f.copy(), dict(lc.meta))
    real = pipeline.prewhiten
    monkeypatch.setattr(pipeline, "prewhiten", lambda *a, **kw: real(*a, **{**kw, "shape_guard": False}))
    monkeypatch.setattr(pipeline, "cpu_pool", lambda _settings: _InlinePool())
    searched = []                   # the flux of every search, in order: the last one is the search stored
    real_search = pipeline._search
    monkeypatch.setattr(pipeline, "_search", lambda det, lc_, fn, *a, **kw: (searched.append(np.array(fn)),
                                                                             real_search(det, lc_, fn, *a, **kw))[1])
    src = ScriptedSource([_tgt("eaten")], {"eaten": lc}, store, blob)
    out = run(src, store, blob, s, filter={}, with_novelty=False)
    assert out["processed"] == 1 and out["errors"] == 0
    with store.session() as ss:
        sigs = ss.query(db.SignalRow).all(); ss.expunge_all()
    planet = next((x for x in sigs if abs(x.period - period) < 0.01), None)
    assert planet is not None, [(x.period, x.sde) for x in sigs]
    pw = planet.extra["prewhitening"]
    assert pw.get("restored"), pw
    assert all(set(r) == {"frequency", "amplitude_ppm"} for r in pw["restored"])
    assert any(abs(r["frequency"] - 1 / period) < 0.01 for r in pw["restored"])
    assert all(abs(c["frequency"] - 1 / period) > 0.01 for c in pw["components"]) and pw["n"] == len(pw["components"])
    assert abs(planet.depth_ppm - depth) < 0.15 * depth, planet.depth_ppm
    assert all(x.extra["prewhitening"] == pw for x in sigs), "every signal of the target carries the searched record"
    assert planet.extra["detrend"] == pipeline.detrend_settings()
    # the dashboard's replay of the stored record is exactly the flux the stored (second) search ran on
    from skysift.ui import plots
    replay = plots.whitened_fnorm(lc, {"extra": planet.extra})
    assert len(searched) == 2 and np.array_equal(np.isfinite(replay), np.isfinite(searched[-1]))
    good = np.isfinite(replay)
    assert np.max(np.abs(replay[good] - searched[-1][good])) < 1e-9


def test_restore_harmonics_leaves_a_wave_deeper_than_the_detection_removed():
    """A pulsation that happens to sit on a detection's harmonic comb is not part of the transit: a box of depth d has
    every Fourier amplitude below d, so a removed component larger than the detection's depth stays removed, while a
    smaller one at the same comb comes back."""
    time = np.arange(0, 400, 1 / 48)
    flux = np.ones_like(time)
    f = 1 / 2.5
    record = {"n": 3, "components": [
        {"frequency": f, "amplitude_ppm": 40.0, "snr": 10.0, "coef": [0.0, 40e-6, 0.0, 0.0, 0.0]},        # transit fundamental: back
        {"frequency": 2 * f, "amplitude_ppm": 900.0, "snr": 10.0, "coef": [0.0, 900e-6, 0.0, 0.0, 0.0]},  # a wave on the comb: stays
        {"frequency": 0.37, "amplitude_ppm": 30.0, "snr": 10.0, "coef": [0.0, 30e-6, 0.0, 0.0, 0.0]},     # unrelated: stays
    ], "skipped": []}
    out = pipeline.restore_harmonics(time, flux, record, [(f, 500.0)])
    assert out is not None
    _flux, new = out
    assert [r["frequency"] for r in new["restored"]] == [f]
    assert [c["frequency"] for c in new["components"]] == [2 * f, 0.37] and new["n"] == 2
    assert pipeline.restore_harmonics(time, flux, record, [(f, 20.0)]) is None, "nothing under a 20 ppm detection"


def test_restore_leaves_a_wave_at_three_halves_of_the_detection_frequency():
    """A transit has no Fourier power at half-integer harmonics of its frequency, so a component removed at 1.5 f is a
    stellar wave: put back, it alternates sign between consecutive transits and fails rung 2 as odd_even (the 0.721 d
    binary gaia-dr3-2076894352958341376 had its fundamental at 1.387 per day restored onto a 1.057 d injected transit).
    Only the detection's integer harmonics come back."""
    time = np.arange(0, 400, 1 / 48)
    flux = np.ones_like(time)
    f = 1 / 1.057
    record = {"n": 3, "components": [
        {"frequency": f, "amplitude_ppm": 40.0, "snr": 10.0, "coef": [0.0, 40e-6, 0.0, 0.0, 0.0]},
        {"frequency": 1.5 * f, "amplitude_ppm": 150.0, "snr": 10.0, "coef": [0.0, 150e-6, 0.0, 0.0, 0.0]},
        {"frequency": 2 * f, "amplitude_ppm": 30.0, "snr": 10.0, "coef": [0.0, 30e-6, 0.0, 0.0, 0.0]},
    ], "skipped": []}
    out = pipeline.restore_harmonics(time, flux, record, [(f, 1000.0)])
    assert out is not None
    _flux, new = out
    assert [r["frequency"] for r in new["restored"]] == [f, 2 * f]
    assert [c["frequency"] for c in new["components"]] == [1.5 * f] and new["n"] == 1


def _rows(results) -> list:
    """Test results as the sweep stores them (strict JSON), for comparing a re-vet with the sweep rung by rung."""
    return [(t["rung"], t["status"], t["reason"], t["metrics"])
            for t in json.loads(jsonutil.dumps([r.model_dump() for r in results], default=str))]


def test_revet_reproduces_the_sweep_verdict(settings, caplog):
    """Every signal of the target, rung by rung: the same status, reason and metrics as the sweep. A 2 d stellar wave
    (the sixth harmonic of the 12 d transit) is whitened away and then restored, from the 12, 24, 36 and 48 d
    detections of the first search, so the restore replay is what is under test. For the 12 d signal the secondary
    flux restored from its own period is the same as from ``restored_for``; for the others (24 d among them) it is not,
    so those re-vets hold only if ``revet_flux`` restores from ``restored_for``. With ``restored_for`` dropped (a
    signal swept before it was stored) the re-vet says so at debug level and falls back to the signal's own period."""
    th = settings.thresholds
    base = inject_transit(make_lightcurve(noise_ppm=500, seed=5), 12.0, 140.0, 0.1, 3000.0)
    lc = _with_dip(base, 1.0 + 500e-6 * np.sin(2 * np.pi * base.time / 2.0))
    out = json.loads(_cpu_work(pickle.dumps(lc), TARGET.model_dump_json(), th.model_dump_json(),
                               "box_dip", "4", True, fetch_module="tests.unit.fake_fetch"))
    flux = pipeline._sweep_flux(lc, th)
    gaps = []
    for item in out["signals"]:
        sig = Signal(**item["signal"])
        pw = sig.extra["prewhitening"]
        assert pw["restored"] and len(pw["restored_for"]) >= 2
        exact = pipeline._restore_alt(lc.time, flux.alt, flux.alt_record, [tuple(d) for d in pw["restored_for"]])
        own = pipeline._restore_alt(lc.time, flux.alt, flux.alt_record, [(1.0 / sig.period, sig.depth_ppm)])
        np.testing.assert_array_equal(pipeline.revet_flux(lc, th, sig)[1], exact)
        gaps.append((round(sig.period, 1), float(np.nanmax(np.abs(own - exact)))))
        caplog.clear()
        with caplog.at_level(logging.DEBUG, logger="skysift.pipeline"):
            outcome, results = revet(lc, TARGET, th, sig, build_ladder("4"), fetch=fake_fetch.fetch)
        mine = [r for r in caplog.records if r.name == "skysift.pipeline"]
        assert not [r for r in mine if r.levelno >= logging.WARNING or "restored_for" in r.getMessage()]
        assert outcome == item["outcome"] and _rows(results) == [(t["rung"], t["status"], t["reason"], t["metrics"])
                                                                 for t in item["tests"]], sig.period
    # the secondary flux of at least one signal (the 24 d one) depends on restored_for by more than 100 ppm
    assert any(abs(period - 24.0) < 0.5 and gap > 100e-6 for period, gap in gaps), gaps
    sig = Signal(**out["signals"][0]["signal"]); pw = sig.extra["prewhitening"]
    old = sig.model_copy(update={"extra": {**sig.extra, "prewhitening": {k: v for k, v in pw.items()
                                                                         if k != "restored_for"}}})
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="skysift.pipeline"):
        revet(lc, TARGET, th, old, build_ladder("4", with_novelty=False))
    assert any("no restored_for" in r.getMessage() and r.levelno == logging.DEBUG for r in caplog.records)
    assert not [r for r in caplog.records if r.name == "skysift.pipeline" and r.levelno >= logging.WARNING]


def test_halved_signal_measures_the_true_period():
    lc = inject_transit(make_lightcurve(noise_ppm=400, seed=7), 8.0, 140.0, 0.1, 2500.0)
    fn = detrend(lc)
    doubled = Signal(target_id="t", detector="box_dip", detector_version="v", period=16.0, t0=140.0, duration=0.1,
                     depth_ppm=2500, snr=30, sde=20, n_events=80)
    half = halved_signal(doubled, lc, fn)
    assert half.period == 8.0 and abs(half.depth_ppm - 2500) < 200 and half.n_events > doubled.n_events
    assert half.extra["parent_period"] == 16.0 and half.sde == 20 and half.extra["depth_err_ppm"] > 0


def test_halved_signal_without_points_in_transit_is_none():
    """Too few points to measure at P/2: no signal with NaN fields; the recheck records a skipped step instead."""
    lc = make_lightcurve(n_days=60, seed=4)
    doubled = Signal(target_id="t", detector="box_dip", detector_version="v", period=16.0, t0=140.0, duration=0.1,
                     depth_ppm=2500, snr=30, sde=20, n_events=4)
    assert halved_signal(doubled, lc, np.full(lc.time.size, np.nan)) is None


def test_build_ladder_version_4_is_the_default_and_three_is_compatible():
    assert DEFAULT_LADDER_VERSION == "4" and LADDER_COMPATIBLE["4"] == ("3",)


def test_select_targets_skips_a_target_done_under_ladder_3(settings, monkeypatch):
    """Ladder 4 adds the recheck, not a new search: a target finished under ladder 3 with the same detector version is
    not swept again. One finished under ladder 2 (not compatible) is, and so is one done under another search."""
    s, store, blob = _env(monkeypatch, settings)
    targets = [_tgt("old3"), _tgt("old2"), _tgt("other"), _tgt("fresh")]
    src = ScriptedSource(targets, {t.target_id: make_lightcurve(n_days=60, seed=4) for t in targets}, store, blob)
    det = get_detector("box_dip"); ver = search_version(det, s.thresholds)
    for tid, v, lad in (("old3", ver, "3"), ("old2", ver, "2"), ("other", "0+00000000", "3")):
        store.finish_run(store.start_run(make_job_key(tid, "box_dip", v, lad), tid, "box_dip", v, lad), "done")
    todo, skipped, gave_up, _unknown = _select_targets(src, store, {}, det, ver, "4", None)
    assert sorted(t.target_id for t, _k in todo) == ["fresh", "old2", "other"] and skipped == 1 and gave_up == 0
    assert all(k == make_job_key(t.target_id, "box_dip", ver, "4") for t, k in todo)


def test_select_targets_counts_give_ups_across_compatible_ladders(settings, monkeypatch):
    """A target given up under ladder 3 (three failed attempts, or a known worker killer) is not retried under
    ladder 4, and attempts under the two versions add up."""
    s, store, blob = _env(monkeypatch, settings)
    targets = [_tgt("tried3"), _tgt("split"), _tgt("killer"), _tgt("fresh")]
    src = ScriptedSource(targets, {t.target_id: make_lightcurve(n_days=60, seed=4) for t in targets}, store, blob)
    det = get_detector("box_dip"); ver = search_version(det, s.thresholds)

    def fail(tid, lad, error="boom"):
        key = make_job_key(tid, "box_dip", ver, lad)
        store.finish_run(store.start_run(key, tid, "box_dip", ver, lad), "error", error)

    for _ in range(MAX_ATTEMPTS): fail("tried3", "3")
    for lad in ["3"] * (MAX_ATTEMPTS - 1) + ["4"]: fail("split", lad)
    for _ in range(pipeline.MAX_POOL_BREAKS): fail("killer", "3", db.POOL_BROKE_ERROR)
    todo, skipped, gave_up, _unknown = _select_targets(src, store, {}, det, ver, "4", None)
    assert [t.target_id for t, _k in todo] == ["fresh"] and skipped == 0 and gave_up == 3


def _current_signal(th, **kw) -> Signal:
    base = {"target_id": "t", "detector": "box_dip", "detector_version": search_version(get_detector("box_dip"), th),
            "period": 5.0, "t0": 135.0, "duration": 0.1, "depth_ppm": 1000, "snr": 10, "sde": 10, "n_events": 10}
    return Signal(**{**base, **kw})


def test_revet_stamps_the_target_on_catalogue_queries(settings):
    """Rung 4 passes no target id to fetch; a re-vet logs into the real store, so revet stamps its target."""
    from skysift.vetting.base import Ladder
    from skysift.vetting.rung4_novelty import Rung4Novelty
    seen, logged = [], []

    def fetch(url, **kw):
        seen.append(kw.get("target_id"))
        return fake_fetch.fetch(url, **kw)

    class Log:
        def log_query(self, *a, target_id=None, **k): logged.append(target_id)

    lc = make_lightcurve(n_days=60, seed=4)
    _outcome, results = revet(lc, TARGET, settings.thresholds, _current_signal(settings.thresholds),
                              Ladder([Rung4Novelty()], "4"), fetch=fetch, store=Log())
    assert [r.rung for r in results] == ["rung4_novelty"] and seen and set(seen) == {"t"} and set(logged) == {"t"}


def test_revet_refuses_a_signal_of_another_search_version(settings):
    from skysift.vetting.base import Ladder
    sig = _current_signal(settings.thresholds, detector_version="0+00000000")
    with pytest.raises(ValueError, match="search version"):
        revet(make_lightcurve(n_days=60, seed=4), TARGET, settings.thresholds, sig, Ladder([], "4"))


def test_restore_recorded_replays_a_restore_from_a_second_detection(caplog):
    """The sweep restored from two detections, the signal at P and a peak at 2P: the component at 1.5 / P is a
    harmonic of 1 / (2P) only, so the signal's own rule (the brief's fallback) would leave it out. The replay from the
    stored record puts back exactly what the sweep put back."""
    period, depth = 10.0, 3000.0
    time = np.arange(0.0, 400.0, 0.02)

    def comp(f, a):
        return {"frequency": f, "amplitude_ppm": a * 1e6, "snr": 50.0, "coef": [0.0, a, 0.0, 0.0, 0.0]}

    record = {"n": 3, "components": [comp(1 / period, 2e-4), comp(1.5 / period, 1e-4), comp(0.37, 3e-4)],
              "skipped": [], "capped": False}
    fn = 1.0 + np.random.default_rng(1).normal(0, 5e-4, time.size)
    swept, stored = pipeline.restore_harmonics(time, fn, record, [(1 / period, depth), (1 / (2 * period), depth)])
    assert sorted(round(r["frequency"], 6) for r in stored["restored"]) == [0.1, 0.15]
    sig = Signal(target_id="t", detector="box_dip", detector_version="v", period=period, t0=0.0, duration=0.1,
                 depth_ppm=depth, snr=20, sde=20, n_events=40)
    with caplog.at_level(logging.WARNING, logger="skysift.pipeline"):
        got = pipeline._restore_recorded(time, fn, record, stored, sig, [(1 / period, depth)])
    assert not caplog.records
    np.testing.assert_array_equal(got, swept)
    own_rule = pipeline.restore_harmonics(time, fn, record, [(1 / period, depth)])[0]
    assert np.max(np.abs(own_rule - got)) > 5e-5
