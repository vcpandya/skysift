from datetime import datetime, timedelta

from skysift.store.blob import LocalBlob
from skysift.store.db import RunRow, Store
from skysift.store.retention import Retention, RetentionPolicy
from skysift.ui import queries
from tests.unit.ui_seed import VERSION, add_older_version, seed


def _env(tmp_path, settings):
    store = Store(settings.database_url); store.init_schema()
    blob = LocalBlob(tmp_path / "blobs")
    ids = seed(store, blob)
    return store, blob, ids


def test_summary_counts_states_outcomes_and_rungs(tmp_path, settings):
    store, blob, _ids = _env(tmp_path, settings)
    out = queries.summary(store, Retention(RetentionPolicy.parse("stream"), store, blob), settings)
    assert out["targets_by_state"] == {"done": 3}
    assert out["signal_outcomes"] == {"candidate_stage1": 1, "undetermined": 1, "rejected": 1}
    assert out["rung_counts"]["rung2_shape"] == {"pass": 2, "fail": 1, "undetermined": 0}
    assert out["rung_counts"]["rung4_novelty"] == {"pass": 1, "fail": 0, "undetermined": 1}
    assert out["counts"]["queries"] == 3 and out["error_capped_targets"] == 0 and out["last_errors"] == []
    assert out["mean_seconds_per_run"] is not None and out["cache_bytes"] > 0


def test_source_progress(tmp_path, settings):
    store, _blob, _ids = _env(tmp_path, settings)
    assert queries.source_progress(store) == {"kbonus_bkg": {"new": 0, "done": 3, "error": 0, "total": 3}}


def test_review_queue_default_and_sorting(tmp_path, settings):
    store, _blob, ids = _env(tmp_path, settings)
    rows = queries.review_queue(store)
    assert [r["id"] for r in rows] == [ids["candidate"]]
    r = rows[0]
    assert (r["last_rung"], r["verdict"], r["decision"], r["reason"]) == ("rung4_novelty", "pass", None, "")
    assert abs(r["duration_h"] - 0.096 * 24) < 1e-9 and r["kic"] == 5689121
    both = queries.review_queue(store, status="all", sort="period", desc=False)
    assert [round(b["period"], 2) for b in both] == [1.07, 3.21, 13.77]
    und = queries.review_queue(store, status="undetermined")[0]
    assert und["verdict"] == "undetermined" and und["reason"].startswith("outage:vizier")


def test_signal_detail_neighbours_and_history(tmp_path, settings):
    store, _blob, ids = _env(tmp_path, settings)
    d = queries.signal_detail(store, ids["candidate"])
    assert d["signal"]["period"] == 13.7695 and d["target"]["kic"] == 5689121 and d["run"]["status"] == "done"
    assert [t["rung"] for t in d["tests"]] == ["rung1_detection", "rung2_shape", "rung3_stability", "rung4_novelty"]
    assert d["candidate"] is None and d["decisions"] == []
    store.decide(ids["candidate"], "approve", "fine")
    d = queries.signal_detail(store, ids["candidate"])
    assert d["candidate"]["status"] == "approved" and d["decisions"][0]["decision"] == "approve"
    assert queries.signal_detail(store, 10**6) is None
    prev_id, next_id = queries.neighbours(store, ids["undetermined"], status="all", sort="period", desc=False)
    assert (prev_id, next_id) == (ids["rejected"], ids["candidate"])


def test_approved_and_review_document(tmp_path, settings):
    store, _blob, ids = _env(tmp_path, settings)
    store.decide(ids["candidate"], "approve", "fine"); store.decide(ids["undetermined"], "reject", "noise")
    rows = queries.approved(store)
    assert [r["id"] for r in rows] == [ids["candidate"]] and rows[0]["note"] == "fine"
    assert [r["id"] for r in queries.approved(store, status="rejected")] == [ids["undetermined"]]
    doc = queries.review_document(store, ids["candidate"])
    assert doc["signal"]["target_id"] == "kic-005689121" and len(doc["tests"]) == 4
    assert doc["queries"][0]["service"] == "exoarchive" and doc["decisions"][0]["decision"] == "approve"


def test_mean_seconds_matches_python_mean_and_handles_no_runs(tmp_path, settings):
    store = Store(settings.database_url); store.init_schema()
    retention = Retention(RetentionPolicy.parse("stream"), store, LocalBlob(tmp_path / "blobs"))
    assert queries.mean_seconds(store) is None and queries.run_time_totals(store) == (0, 0.0)
    assert queries.summary(store, retention, settings)["mean_seconds_per_run"] is None
    t0 = datetime(2026, 10, 14, 12, 0, 0)
    for i, (seconds, status) in enumerate([(10, "done"), (30, "done"), (None, "running")]):
        rid = store.start_run(f"job-{i}", f"t{i}", "box_dip", "1", "1")
        with store.session() as s:
            run = s.get(RunRow, rid)
            run.started, run.status = t0, status
            run.finished = t0 + timedelta(seconds=seconds) if seconds is not None else None
    assert queries.mean_seconds(store) == 20.0
    n, total = queries.run_time_totals(store)
    assert n == 2 and abs(total - 40.0) < 1e-3
    out = queries.summary(store, retention, settings)
    assert out["mean_seconds_per_run"] == 20.0 and out["targets_per_hour"] == round(2 / (40.0 / 3600), 1)


def test_current_search_version_is_the_latest_done_run(tmp_path, settings):
    store = Store(settings.database_url); store.init_schema()
    assert queries.current_search_version(store) is None
    seed(store)
    old = add_older_version(store)                             # written last, but its run started a day earlier
    assert old is not None and queries.current_search_version(store) == VERSION
    rid = store.start_run("job-new", "kic-005689121", "box_dip", "9+running", "1")   # newer but not done
    assert queries.current_search_version(store) == VERSION
    store.finish_run(rid, "error", "boom")
    assert queries.current_search_version(store) == VERSION


def test_queue_and_neighbours_keep_to_one_search_version(tmp_path, settings):
    store, _blob, ids = _env(tmp_path, settings)
    old = add_older_version(store)
    assert {r["id"] for r in queries.review_queue(store)} == {ids["candidate"], old}         # no version: everything
    assert [r["id"] for r in queries.review_queue(store, version=VERSION)] == [ids["candidate"]]
    assert queries.hidden_by_version(store, "candidate_stage1", VERSION) == 1
    assert queries.hidden_by_version(store, "all", VERSION) == 1
    assert queries.hidden_by_version(store, "rejected", VERSION) == 0
    assert queries.hidden_by_version(store, "candidate_stage1", None) == 0
    # the old signal has the higher SDE, so in the unfiltered queue it sits just before the current one
    assert queries.neighbours(store, ids["candidate"], "candidate_stage1", "sde", True) == (old, None)
    assert queries.neighbours(store, ids["candidate"], "candidate_stage1", "sde", True, version=VERSION) == (None, None)
    assert old not in queries.neighbours(store, ids["undetermined"], "all", "sde", True, version=VERSION)
    assert queries.neighbours(store, old, "candidate_stage1", "sde", True, version=VERSION) == (None, None)
