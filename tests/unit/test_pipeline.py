import json
import multiprocessing
import pickle
import threading
import warnings
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pytest

from skysift import jsonutil
from skysift.config import Thresholds, load_settings
from skysift.detectors.base import get_detector
from skysift.models import Signal, Target, TestResult, TestStatus
from skysift.pipeline import _cpu_work, _write_results, build_ladder, priority_score, run
from skysift.store import db
from skysift.store.blob import LocalBlob
from skysift.store.db import Store, make_job_key
from skysift.store.retention import DiskBudgetExceeded
from tests.synthetic import inject_transit, make_lightcurve


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


def test_build_ladder_orders_rungs_and_drops_novelty_on_request():
    costs = [r.cost for r in build_ladder().rungs]
    assert costs == sorted(costs) and build_ladder().rungs[-1].name == "rung4_novelty"
    assert len(build_ladder().rungs) == 4 and len(build_ladder(with_novelty=False).rungs) == 3
    assert build_ladder("7").version == "7"


def test_write_results_keeps_nan_and_numpy_metrics(settings):
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
    assert _write_results(store, rid, "t1", payload) == {"pass": 1, "fail": 0, "undetermined": 0}
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
    det = get_detector("box_dip")
    planet_key = make_job_key("planet", "box_dip", det.version, "1")
    assert store.job_done(planet_key) and store.job_done(make_job_key("noise", "box_dip", det.version, "1"))
    with store.session() as ss:
        assert {r.state for r in ss.query(db.TargetRow)} == {"done"}
        planet_sigs = ss.query(db.SignalRow).filter_by(target_id="planet").all()
        assert any(abs(r.period - 13.769) < 0.05 for r in planet_sigs)
        assert all(r.final_status in ("candidate_stage1", "rejected", "undetermined") for r in ss.query(db.SignalRow))
        alive = {r.target_id for r in ss.query(db.SignalRow) if r.final_status != "rejected"}
        arts = {a.target_id: (a.pinned, a.deleted_at, a.local_path) for a in ss.query(db.ArtifactRow)}
    assert "planet" in alive
    # stream retention: nothing unpinned is left on disk; live signals keep their evidence pinned
    assert store.unpinned_artifacts() == []
    for tid, (pinned, deleted_at, path) in arts.items():
        assert pinned == (tid in alive) and (deleted_at is None) == pinned and Path(path).exists() == pinned
    # an interrupted rerun leaves a 'running' row; the earlier done run still checkpoints the target
    store.start_run(planet_key, "planet", "box_dip", det.version, "1")
    assert store.job_done(planet_key)
    # resume: second run skips both
    out2 = run(src, store, blob, s, filter={}, max_targets=2)
    assert out2["skipped"] == 2 and out2["processed"] == 0 and store.counts()["signals"] == c["signals"]
    assert store.counts()["runs"] == 3


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


def test_disk_budget_exceeded_stops_run_after_planet_is_written(settings, monkeypatch):
    s, store, blob = _env(monkeypatch, settings, RETENTION="budget:0",
                          THRESHOLDS__MIN_PERIOD_DAYS="5", THRESHOLDS__MAX_PERIOD_DAYS="15")
    planet = inject_transit(make_lightcurve(n_days=120, seed=1, noise_ppm=800), 7.3, 135.0, 0.1, 3000)
    src = ScriptedSource([_tgt("planet")], {"planet": planet}, store, blob)
    with pytest.raises(DiskBudgetExceeded):
        run(src, store, blob, s, filter={}, with_novelty=False)
    assert multiprocessing.active_children() == []
    assert _runs(store)["planet"][0] == "done" and _states(store)["planet"] == "done"
    with store.session() as ss:
        assert ss.query(db.SignalRow).filter_by(final_status="candidate_stage1").count() >= 1
        art = ss.query(db.ArtifactRow).one()
        assert art.pinned and art.deleted_at is None and Path(art.local_path).exists()


def test_rung4_catalogue_queries_are_logged_during_a_sweep(settings, monkeypatch):
    s, store, blob = _env(monkeypatch, settings, THRESHOLDS__MIN_PERIOD_DAYS="5", THRESHOLDS__MAX_PERIOD_DAYS="15")
    planet = inject_transit(make_lightcurve(n_days=120, seed=1, noise_ppm=800), 7.3, 135.0, 0.1, 3000)
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
