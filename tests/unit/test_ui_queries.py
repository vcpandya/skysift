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
    assert out["targets_by_state"] == {"done": 7}
    assert out["signal_outcomes"] == {"candidate_stage1": 3, "candidate_stage2": 1, "undetermined": 1, "rejected": 1,
                                      "pending": 1}
    assert out["rung_counts"]["rung2_shape"] == {"pass": 5, "fail": 1, "undetermined": 0}
    assert out["rung_counts"]["rung4_novelty"] == {"pass": 4, "fail": 0, "undetermined": 1}
    assert out["counts"]["queries"] == 3 and out["error_capped_targets"] == 0 and out["last_errors"] == []
    assert out["mean_seconds_per_run"] is not None and out["cache_bytes"] > 0


def test_source_progress(tmp_path, settings):
    store, _blob, _ids = _env(tmp_path, settings)
    assert queries.source_progress(store) == {"kbonus_bkg": {"new": 0, "done": 7, "error": 0, "total": 7}}


def test_review_queue_default_and_sorting(tmp_path, settings):
    store, _blob, ids = _env(tmp_path, settings)
    rows = queries.review_queue(store)
    assert [r["id"] for r in rows] == [ids["candidate"], ids["approved"], ids["legacy"]]     # by SDE, highest first
    r = rows[0]
    assert (r["last_rung"], r["verdict"], r["decision"], r["reason"]) == ("rung4_novelty", "pass", None, "")
    assert abs(r["duration_h"] - 0.096 * 24) < 1e-9 and r["kic"] == 5689121
    both = queries.review_queue(store, status="all", sort="period", desc=False)
    assert [round(b["period"], 2) for b in both] == [1.07, 3.21, 5.43, 7.65, 13.77, 40.29]   # every status; pending is no queue
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
    assert (prev_id, next_id) == (ids["rejected"], ids["legacy"])


def test_approved_and_review_document(tmp_path, settings):
    store, _blob, ids = _env(tmp_path, settings)
    store.decide(ids["candidate"], "approve", "fine"); store.decide(ids["undetermined"], "reject", "noise")
    rows = queries.approved(store)
    assert [r["id"] for r in rows] == [ids["candidate"], ids["approved"]] and rows[0]["note"] == "fine"
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
    current = [ids["candidate"], ids["approved"], ids["legacy"]]                              # by SDE, highest first
    assert {r["id"] for r in queries.review_queue(store)} == {*current, old}                 # no version: everything
    assert [r["id"] for r in queries.review_queue(store, version=VERSION)] == current
    assert queries.hidden_by_version(store, "candidate_stage1", VERSION) == 1
    assert queries.hidden_by_version(store, "all", VERSION) == 1
    assert queries.hidden_by_version(store, "rejected", VERSION) == 0
    assert queries.hidden_by_version(store, "candidate_stage1", None) == 0
    # the old signal has the higher SDE, so in the unfiltered queue it sits just before the current one
    assert queries.neighbours(store, ids["candidate"], "candidate_stage1", "sde", True) == (old, ids["approved"])
    assert (queries.neighbours(store, ids["candidate"], "candidate_stage1", "sde", True, version=VERSION)
            == (None, ids["approved"]))
    assert old not in queries.neighbours(store, ids["undetermined"], "all", "sde", True, version=VERSION)
    assert queries.neighbours(store, old, "candidate_stage1", "sde", True, version=VERSION) == (None, None)


def test_queue_statuses_are_the_chips_in_order():
    assert queries.QUEUE_STATUSES == ("candidate_stage1", "candidate_stage2", "undetermined", "rejected", "superseded",
                                      "approved", "needs_data", "all")


def test_review_queue_rows_carry_the_verdict_in_words(tmp_path, settings):
    store, _blob, ids = _env(tmp_path, settings)
    rows = {r["id"]: r for r in queries.review_queue(store, status="all")}
    c = rows[ids["candidate"]]
    assert (c["verdict_word"], c["stage"], c["verdict_tone"], c["reviewer"]) == ("Candidate", "stage 1", "pass", None)
    assert c["summary"] == "A dip of 0.18 percent repeats every 13.8 days on a star of magnitude 16.4."
    assert (rows[ids["stage2"]]["stage"], rows[ids["stage2"]]["verdict_word"]) == ("stage 2", "Candidate")
    u = rows[ids["undetermined"]]
    assert (u["verdict_word"], u["stage"], u["verdict_tone"]) == ("Undetermined", None, "undetermined")
    assert (rows[ids["rejected"]]["verdict_word"], rows[ids["rejected"]]["verdict_tone"]) == ("Rejected", "fail")
    assert rows[ids["approved"]]["reviewer"] == "Approved by reviewer"
    store.decide(ids["undetermined"], "needs_data", "")
    assert queries.review_queue(store, status="undetermined")[0]["reviewer"] == "Needs data"


def test_review_queue_loads_the_test_rows_in_one_query(tmp_path, settings):
    from sqlalchemy import event
    store, _blob, _ids = _env(tmp_path, settings)
    statements = []

    def listen(conn, cursor, statement, *args):
        statements.append(statement)

    event.listen(store.engine, "before_cursor_execute", listen)
    try:
        rows = queries.review_queue(store, status="all")
    finally:
        event.remove(store.engine, "before_cursor_execute", listen)
    assert len(rows) == 6 and len([s for s in statements if s.lstrip().upper().startswith("SELECT")]) == 2


def test_review_queue_filters_by_the_latest_decision(tmp_path, settings):
    store, _blob, ids = _env(tmp_path, settings)
    assert [r["id"] for r in queries.review_queue(store, status="approved")] == [ids["approved"]]
    assert queries.review_queue(store, status="needs_data") == []
    store.decide(ids["candidate"], "needs_data", "")
    store.decide(ids["undetermined"], "needs_data", ""); store.decide(ids["undetermined"], "approve", "")
    assert [r["id"] for r in queries.review_queue(store, status="needs_data")] == [ids["candidate"]]
    assert {r["id"] for r in queries.review_queue(store, status="approved")} == {ids["approved"], ids["undetermined"]}
    assert queries.status_counts(store)["needs_data"] == 1 and queries.status_counts(store)["approved"] == 2


def test_review_queue_search_and_limit(tmp_path, settings):
    store, _blob, ids = _env(tmp_path, settings)
    assert [r["id"] for r in queries.review_queue(store, status="all", search="KIC-00")] == [ids["stage2"],
                                                                                            ids["candidate"]]
    assert queries.review_queue(store, status="all", search="kic_00") == []                # LIKE wildcards are literal
    assert queries.review_queue(store, status="all", search="10%") == []
    assert len(queries.review_queue(store, status="all", limit=2)) == 2
    assert queries.neighbours(store, ids["candidate"], "all", "sde", True, search="kic") == (ids["stage2"], None)


def test_status_counts_follow_the_version_filter(tmp_path, settings):
    store, _blob, _ids = _env(tmp_path, settings)
    add_older_version(store)
    assert queries.status_counts(store) == {"candidate_stage1": 4, "candidate_stage2": 1, "undetermined": 1,
                                            "rejected": 1, "superseded": 0, "approved": 1, "needs_data": 0, "all": 7}
    assert queries.status_counts(store, VERSION)["candidate_stage1"] == 3
    assert queries.status_counts(store, VERSION)["all"] == 6
    assert queries.hidden_by_version(store, "approved", VERSION) == 0
