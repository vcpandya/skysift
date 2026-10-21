import pytest
from sqlalchemy import func, select

from skysift.models import Signal, Target, TestResult, TestStatus
from skysift.store.db import (
    ArtifactRow,
    SignalRow,
    Store,
    TestRow,
    final_status_for,
    make_job_key,
    status_from_tests,
)


def make_store(tmp_path):
    s = Store(f"sqlite:///{tmp_path.as_posix()}/t.db"); s.init_schema(); return s


def test_run_and_signal_roundtrip(tmp_path):
    s = make_store(tmp_path)
    s.upsert_target(Target(source="kbonus_bkg", target_id="kic-005689121", ra=285.9, dec=40.9))
    key = make_job_key("kic-005689121", "box_dip", "1", "1")
    assert not s.job_done(key)
    rid = s.start_run(key, "kic-005689121", "box_dip", "1", "1")
    sig = Signal(target_id="kic-005689121", detector="box_dip", detector_version="1", period=13.77, t0=136.3,
                 duration=0.09, depth_ppm=1883, snr=18, sde=14, n_events=69)
    sid1 = s.add_signal(sig, rid); sid2 = s.add_signal(sig, rid)
    assert sid1 == sid2  # idempotent
    s.add_test(sid1, TestResult(rung="rung1", status=TestStatus.PASS, metrics={"sde": 14}), 0.01)
    s.finish_run(rid, "done")
    assert s.job_done(key)
    c = s.counts(); assert c["signals"] == 1 and c["tests"] == 1


def test_artifacts_and_queries(tmp_path):
    s = make_store(tmp_path)
    s.add_artifact("ab" * 32, 10, "https://x/y.fits", "/tmp/y.fits", "kic-1")
    assert len(s.unpinned_artifacts("kic-1")) == 1
    s.pin_target_artifacts("kic-1")
    assert s.unpinned_artifacts("kic-1") == []
    s.log_query("mast", "https://x", {"a": 1}, 200, 10, "ab" * 32)
    assert s.counts()["queries"] == 1


def test_signal_idempotency_is_per_run_and_period_tolerance(tmp_path):
    s = make_store(tmp_path)
    r1 = s.start_run("k1", "t", "box_dip", "1", "1"); r2 = s.start_run("k2", "t", "box_dip", "1", "1")
    base = {"target_id": "t", "detector": "box_dip", "detector_version": "1", "t0": 1.0, "duration": 0.1,
            "depth_ppm": 100, "snr": 9, "sde": 9, "n_events": 3}
    a = s.add_signal(Signal(period=5.0, **base), r1)
    assert s.add_signal(Signal(period=5.00005, **base), r1) == a        # within 1e-4, same run
    assert s.add_signal(Signal(period=5.01, **base), r1) != a           # outside tolerance
    assert s.add_signal(Signal(period=5.0, **base), r2) != a            # different run
    assert s.counts()["signals"] == 3


def test_artifact_dedupe_lru_order_and_deleted_exclusion(tmp_path):
    s = make_store(tmp_path)
    s.add_artifact("a" * 64, 1, "u1", "p1", "kic-1")
    s.add_artifact("b" * 64, 2, "u2", "p2", "kic-1")
    s.add_artifact("c" * 64, 3, "u3", "p3", "kic-1")
    assert s.unpinned_artifacts("kic-1")[0]["sha256"] == "a" * 64
    s.touch_artifact("a" * 64)                                            # a becomes most recently used
    assert [a["sha256"][0] for a in s.unpinned_artifacts()] == ["b", "c", "a"]
    s.mark_artifact_deleted("b" * 64)
    assert [a["sha256"][0] for a in s.unpinned_artifacts()] == ["c", "a"]
    s.add_artifact("d" * 64, 4, "u4", "p4", "kic-3", pinned=True)
    assert "d" * 64 not in [a["sha256"] for a in s.unpinned_artifacts()]
    assert s.counts()["artifacts"] == 4
    assert s.unpinned_artifacts(exclude_targets={"kic-1"}) == []
    assert [a["sha256"][0] for a in s.unpinned_artifacts(exclude_targets={"kic-9"})] == ["c", "a"]


def test_add_artifact_resurrects_a_deleted_row(tmp_path):
    s = make_store(tmp_path)
    s.add_artifact("a" * 64, 1, "u1", "p1", "kic-1")
    s.mark_artifact_deleted("a" * 64)
    assert s.unpinned_artifacts() == []
    s.pin_target_artifacts("kic-1")                                       # deleted rows are never pinned
    s.add_artifact("a" * 64, 7, "u1", "p1-new", "kic-2")                  # re-fetched: the row is live again
    assert s.counts()["artifacts"] == 1
    [a] = s.unpinned_artifacts()
    assert (a["nbytes"], a["local_path"]) == (7, "p1-new")
    assert s.unpinned_artifacts("kic-2") and s.unpinned_artifacts("kic-1") == []
    s.add_artifact("a" * 64, 7, "u1", "p1-new")                          # no target given: keeps kic-2
    assert s.unpinned_artifacts("kic-2")


def test_file_sqlite_uses_wal_and_busy_timeout(tmp_path):
    s = make_store(tmp_path)
    with s.engine.connect() as c:
        assert c.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert c.exec_driver_sql("PRAGMA busy_timeout").scalar() == 30000


def test_start_run_supersedes_interrupted_runs_of_the_same_job(tmp_path):
    s = make_store(tmp_path)
    r1 = s.start_run("k", "t", "box_dip", "1", "1"); other = s.start_run("k2", "t", "box_dip", "1", "1")
    r2 = s.start_run("k", "t", "box_dip", "1", "1")
    with s.session() as ss:
        from skysift.store.db import RunRow
        st = {r.id: (r.status, r.error) for r in ss.query(RunRow)}
    assert st[r1] == ("error", "superseded") and st[r2] == ("running", "") and st[other] == ("running", "")


def test_record_results_writes_everything_in_one_transaction(tmp_path):
    s = make_store(tmp_path)
    s.upsert_target(Target(source="x", target_id="t", ra=0, dec=0))
    s.add_artifact("a" * 64, 5, "u", "p", "t")
    rid = s.start_run("k", "t", "box_dip", "1", "1")
    sig = Signal(target_id="t", detector="box_dip", detector_version="1", period=3.0, t0=1.0, duration=0.1,
                 depth_ppm=500, snr=10, sde=12, n_events=5).model_dump()
    tests = [TestResult(rung="r1", status=TestStatus.PASS).model_dump()]
    items = [{"signal": sig, "outcome": "undetermined", "tests": tests, "timings": [0.1]}]
    q = {"service": "vizier", "url": "https://v", "params": {"a": 1}, "status": 200, "nbytes": 3}
    assert s.record_results(rid, "t", items, [q], pin_evidence=True) == {"pass": 0, "fail": 0, "undetermined": 1}
    assert s.job_done("k") and s.unpinned_artifacts() == []
    c = s.counts(); assert (c["signals"], c["tests"], c["queries"]) == (1, 1, 1)


def test_attempts_count_errors_but_not_superseded_runs(tmp_path):
    s = make_store(tmp_path)
    s.start_run("k", "t", "box_dip", "1+abcdef01", "1")       # interrupted, then superseded by the next start
    for _ in range(3):
        s.finish_run(s.start_run("k", "t", "box_dip", "1+abcdef01", "1"), "error", "boom")
    assert s.attempts("k") == 3 and s.error_capped_targets(3) == 1 and s.error_capped_targets(4) == 0
    s.start_run("k2", "t", "box_dip", "1+12345678", "1")      # the target's latest job key changed: not capped
    assert s.error_capped_targets(3) == 0


def test_error_capped_targets_count_pool_break_give_ups(tmp_path):
    from skysift.store.db import POOL_BROKE_ERROR, WORKER_DIED_ERROR
    s = make_store(tmp_path)
    for text in (POOL_BROKE_ERROR, POOL_BROKE_ERROR, WORKER_DIED_ERROR):
        s.finish_run(s.start_run("k", "t", "box_dip", "1+abcdef01", "1"), "error", text)
    assert s.attempts("k") == 1 and s.pool_breaks("k") == 3
    assert s.error_capped_targets(3) == 0                          # not three charged attempts
    assert s.error_capped_targets(3, max_pool_breaks=3) == 1       # but given up by the pool-break rule
    assert s.error_capped_targets(3, max_pool_breaks=4) == 0
    s.finish_run(s.start_run("k", "t", "box_dip", "1+abcdef01", "1"), "done")
    assert s.error_capped_targets(3, max_pool_breaks=3) == 0       # a done run clears it


def test_attempts_ignore_service_outages(tmp_path):
    s = make_store(tmp_path)
    for _ in range(3):
        s.finish_run(s.start_run("k", "t", "box_dip", "1+abcdef01", "1"), "error", "unavailable: MAST HTTP 503")
    assert s.attempts("k") == 0 and s.error_capped_targets(3) == 0     # outages are not the target's fault
    s.finish_run(s.start_run("k", "t", "box_dip", "1+abcdef01", "1"), "error", "ValueError('bad fits')")
    assert s.attempts("k") == 1


def test_log_query_records_target(tmp_path):
    s = make_store(tmp_path)
    s.log_query("mast", "https://x", {}, 200, 5, target_id="kic-1")
    with s.session() as ses:
        from skysift.store.db import QueryRow
        assert ses.query(QueryRow).one().target_id == "kic-1"


def test_readd_without_kind_keeps_summary(tmp_path):
    s = make_store(tmp_path)
    sha = "cd" * 32
    s.add_artifact(sha, 10, "https://x/s.json", "/tmp/s.json", "kic-1", kind="summary")
    s.add_artifact(sha, 12, "https://x/s.json", "/tmp/s.json", "kic-1")        # kind not given: keep it
    with s.session() as ses:
        assert ses.get(ArtifactRow, sha).kind == "summary"
    s.add_artifact(sha, 12, "https://x/s.json", "/tmp/s.json", "kic-1", kind="raw")
    with s.session() as ses:
        assert ses.get(ArtifactRow, sha).kind == "raw"
    s.add_artifact("ef" * 32, 1, "u", "p", "kic-1")
    with s.session() as ses:
        assert ses.get(ArtifactRow, "ef" * 32).kind == "raw"                      # new rows default to raw


VERSION = "v+abc"
LC_RUNGS = ("rung1_detection", "rung1_events", "rung2_shape", "rung3_stability", "rung4_novelty")
EXTRA = {"baseline_days": 1400.0, "search_window": {"min_period_days": 0.5, "max_period_days": 200.0}}


def _lc_tests(outcome: str) -> list[TestResult]:
    """The light-curve ladder for one outcome: every rung passes, rung 4 has no verdict (an outage), or rung 2 fails."""
    out = []
    for rung in LC_RUNGS:
        status, reason = TestStatus.PASS, ""
        if outcome == "undetermined" and rung == "rung4_novelty":
            status, reason = TestStatus.UNDETERMINED, "outage:exoarchive: HTTP 503"
        if outcome == "fail" and rung == "rung2_shape":
            status, reason = TestStatus.FAIL, "odd_even 4.2 sigma"
        out.append(TestResult(rung=rung, status=status, reason=reason))
    return out


def _record(store: Store, target_id: str, outcome: str, version: str = VERSION) -> int:
    """One target's run written the way the sweep writes it (``record_results``, ladder 3); returns the signal id."""
    store.upsert_target(Target(source="kbonus_bkg", target_id=target_id, ra=0, dec=0))
    rid = store.start_run(make_job_key(target_id, "box_dip", version, "3"), target_id, "box_dip", version, "3")
    sig = Signal(target_id=target_id, detector="box_dip", detector_version=version, period=3.0, t0=1.0, duration=0.1,
                 depth_ppm=800, snr=12, sde=11, n_events=9, extra=EXTRA)
    tests = _lc_tests(outcome)
    item = {"signal": sig.model_dump(), "outcome": outcome, "tests": [t.model_dump() for t in tests],
            "timings": [0.01] * len(tests)}
    store.record_results(rid, target_id, [item], [], pin_evidence=False)
    with store.session() as s:
        return s.scalar(select(SignalRow.id).where(SignalRow.run_id == rid))


@pytest.fixture
def store_with_signal(tmp_path):
    store = make_store(tmp_path)
    return store, _record(store, "kic-1", "undetermined")


@pytest.fixture
def store_with_signals(tmp_path):
    store = make_store(tmp_path)
    ids = {"candidate": _record(store, "kic-1", "pass"), "undetermined": _record(store, "kic-2", "undetermined"),
           "rejected": _record(store, "kic-3", "fail"), "approved": _record(store, "kic-4", "pass"),
           "other_version": _record(store, "kic-5", "pass", version="v+old")}
    store.decide(ids["approved"], "approve", "looks real")
    return store, ids


def test_status_from_tests_uses_the_latest_row_per_rung():
    rows = [{"id": 1, "rung": "rung1_detection", "status": "pass"},
            {"id": 2, "rung": "rung4_novelty", "status": "undetermined"},
            {"id": 3, "rung": "rung4_novelty", "status": "pass"}]
    assert status_from_tests(rows) == "pass" and status_from_tests([]) == "pending"
    assert final_status_for("pass", rows) == "candidate_stage1"
    assert final_status_for("pass", rows + [{"id": 4, "rung": "rung5_pixels", "status": "pass"}]) == "candidate_stage2"


def test_status_from_tests_precedence_and_orm_rows():
    rows = [TestRow(id=1, rung="rung2_shape", status="fail"),
            TestRow(id=2, rung="rung4_novelty", status="undetermined")]
    assert status_from_tests(rows) == "fail"                                   # a failure outranks no verdict
    assert status_from_tests(list(reversed(rows))) == "fail"
    later_pass = TestRow(id=3, rung="rung2_shape", status="pass")
    assert status_from_tests([later_pass, *rows]) == "undetermined"           # the highest id wins, not list order
    assert final_status_for("fail", rows) == "rejected" and final_status_for("undetermined", rows) == "undetermined"
    assert final_status_for("pending", []) == "pending"
    pixels_failed_later = [{"id": 1, "rung": "rung5_pixels", "status": "pass"},
                           {"id": 2, "rung": "rung5_pixels", "status": "fail"}]
    assert final_status_for("pass", pixels_failed_later) == "candidate_stage1"


def test_recheck_rows_and_latest_tests(store_with_signal):
    store, sid = store_with_signal          # one signal with rung1..rung4 rows, rung4 undetermined (outage)
    rid = store.add_recheck(sid, "catalogue", "4", "pass", "retried exoarchive")
    store.add_test(sid, TestResult(rung="rung4_novelty", status=TestStatus.PASS), 0.1, recheck_id=rid)
    latest = store.latest_tests(sid)
    assert [t["rung"] for t in latest] == ["rung1_detection", "rung1_events", "rung2_shape", "rung3_stability",
                                           "rung4_novelty"]
    assert latest[-1]["status"] == "pass" and latest[-1]["recheck_id"] == rid
    assert store.refresh_status(sid) == "candidate_stage1"
    assert store.rechecks(sid)[0]["step"] == "catalogue"


def test_recheck_history_is_append_only_and_refresh_persists(store_with_signal):
    store, sid = store_with_signal
    first = store.add_recheck(sid, "catalogue", "4", "undetermined", "exoarchive still down")
    second = store.add_recheck(sid, "pixels", "4", "pass")
    hist = store.rechecks(sid)
    assert [(h["id"], h["step"], h["outcome"], h["reason"], h["new_signal_id"]) for h in hist] == [
        (first, "catalogue", "undetermined", "exoarchive still down", None), (second, "pixels", "pass", "", None)]
    assert all(h["ladder_version"] == "4" and h["utc"] for h in hist)
    assert store.signal_status(sid) == "undetermined"
    store.add_test(sid, TestResult(rung="rung4_novelty", status=TestStatus.PASS), 0.1, recheck_id=first)
    store.add_test(sid, TestResult(rung="rung5_pixels", status=TestStatus.PASS), 0.1, recheck_id=second)
    assert store.signal_status(sid) == "undetermined"                         # add_test alone changes no status
    assert store.refresh_status(sid) == "candidate_stage2" and store.signal_status(sid) == "candidate_stage2"
    assert [t["rung"] for t in store.latest_tests(sid)][-1] == "rung5_pixels"
    store.add_test(sid, TestResult(rung="rung2_shape", status=TestStatus.PASS), 0.1, recheck_id=second)
    latest = store.latest_tests(sid)                                            # a late rung 2 retry keeps its place
    assert [t["rung"] for t in latest] == [*LC_RUNGS, "rung5_pixels"]
    assert latest[2]["recheck_id"] == second and latest[2]["id"] == max(t["id"] for t in latest)
    with store.session() as s:                                                  # the old rung 4 row is kept
        assert s.scalar(select(func.count()).select_from(TestRow).where(TestRow.signal_id == sid)) == 8


def test_record_results_writes_ladder_version_and_add_signal_links_children(store_with_signal):
    store, sid = store_with_signal
    with store.session() as s:
        row = s.get(SignalRow, sid)
        assert (row.ladder_version, row.parent_signal_id) == ("3", None)
        run_id = row.run_id
    half = Signal(target_id="kic-1", detector="box_dip", detector_version=VERSION, period=1.5, t0=1.0, duration=0.1,
                  depth_ppm=800, snr=12, sde=11, n_events=18)
    child = store.add_signal(half, run_id, parent_signal_id=sid, ladder_version="4")
    kids = store.children(sid)
    assert [k["id"] for k in kids] == [child]
    assert (kids[0]["parent_signal_id"], kids[0]["ladder_version"], kids[0]["period"]) == (sid, "4", 1.5)
    assert store.children(child) == [] and store.signal_status(child) == "pending"
    rid = store.add_recheck(sid, "period", "4", "superseded", "P/2 settles it", new_signal_id=child)
    [h] = store.rechecks(sid)
    assert (h["id"], h["step"], h["outcome"], h["new_signal_id"]) == (rid, "period", "superseded", child)


def test_recheck_queue_filters_status_version_and_decisions(store_with_signals):
    store, ids = store_with_signals  # candidate, undetermined, rejected, approved candidate, other version
    q = [r["id"] for r in store.recheck_queue("v+abc")]
    assert ids["candidate"] in q and ids["undetermined"] in q
    assert ids["rejected"] not in q and ids["approved"] not in q and ids["other_version"] not in q


def test_recheck_queue_rows_carry_signal_fields_and_latest_tests(store_with_signals):
    store, ids = store_with_signals
    assert [r["id"] for r in store.recheck_queue("v+abc")] == [ids["candidate"], ids["undetermined"]]   # by id
    store.set_signal_status(ids["undetermined"], "superseded")
    [row] = store.recheck_queue("v+abc")                                        # superseded is not rechecked
    assert set(row) == {"id", "target_id", "final_status", "ladder_version", "branch", "period", "t0", "duration",
                        "depth_ppm", "extra", "latest_tests"}
    assert (row["id"], row["target_id"], row["final_status"], row["ladder_version"], row["branch"]) == (
        ids["candidate"], "kic-1", "candidate_stage1", "3", "planet")
    assert row["extra"] == EXTRA and row["latest_tests"] == store.latest_tests(ids["candidate"])
    assert [t["rung"] for t in row["latest_tests"]] == list(LC_RUNGS)
    assert store.recheck_queue("v+none") == []


def test_refresh_status_keeps_superseded_and_reviewer_rejections(store_with_signals):
    store, ids = store_with_signals
    store.set_signal_status(ids["candidate"], "superseded")
    assert store.refresh_status(ids["candidate"]) == "superseded"
    store.decide(ids["undetermined"], "reject", "bad")
    assert store.refresh_status(ids["undetermined"]) == "rejected"


def test_refresh_status_rederives_after_an_un_reject_and_corrects_stale_status(store_with_signals):
    store, ids = store_with_signals
    store.decide(ids["undetermined"], "reject", "bad"); store.decide(ids["undetermined"], "needs_data", "wait")
    assert store.signal_status(ids["undetermined"]) == "undetermined"          # back to what its tests say
    store.set_signal_status(ids["rejected"], "candidate_stage1")
    assert store.refresh_status(ids["rejected"]) == "rejected"                # rung 2 failed: the engine's word
    assert store.signal_status(ids["rejected"]) == "rejected"


def test_ui_seed_stage2_signal_carries_its_pixel_recheck(tmp_path):
    from tests.unit.ui_seed import seed
    store = make_store(tmp_path)
    ids = seed(store)
    [h] = store.rechecks(ids["stage2"])
    assert (h["step"], h["ladder_version"], h["outcome"]) == ("pixels", "4", "pass")
    latest = store.latest_tests(ids["stage2"])
    assert latest[-1]["rung"] == "rung5_pixels" and latest[-1]["recheck_id"] == h["id"]
    assert all(t["recheck_id"] is None for t in latest[:-1])                  # the sweep's own rows
    assert store.signal_status(ids["stage2"]) == "candidate_stage2"


def test_superseded_recheck_survives_a_reject_and_an_approve(store_with_signals):
    store, ids = store_with_signals
    sid = ids["candidate"]
    store.add_recheck(sid, "period", "4", "superseded", "P/2 settles it")
    assert store.refresh_status(sid) == "superseded"
    store.decide(sid, "reject", "looked like an EB")
    assert store.signal_status(sid) == "rejected" and store.refresh_status(sid) == "rejected"
    assert store.decide(sid, "approve", "it was the P/2 one")["final_status"] == "superseded"
    assert store.refresh_status(sid) == "superseded"


def test_record_results_derives_stage_2_from_a_rung5_pass_and_refuses_an_unknown_run(tmp_path):
    store = make_store(tmp_path)
    store.upsert_target(Target(source="kbonus_bkg", target_id="kic-9", ra=0, dec=0))
    rid = store.start_run(make_job_key("kic-9", "box_dip", VERSION, "4"), "kic-9", "box_dip", VERSION, "4")
    sig = Signal(target_id="kic-9", detector="box_dip", detector_version=VERSION, period=40.29, t0=1.0,
                 duration=0.2, depth_ppm=2200, snr=60, sde=50, n_events=34)
    tests = [*_lc_tests("pass"), TestResult(rung="rung5_pixels", status=TestStatus.PASS)]
    item = {"signal": sig.model_dump(), "outcome": "pass", "tests": [t.model_dump() for t in tests],
            "timings": [0.01] * len(tests)}
    with pytest.raises(KeyError):
        store.record_results(rid + 99, "kic-9", [item], [], pin_evidence=False)
    assert store.counts()["signals"] == 0                                       # nothing landed
    assert store.record_results(rid, "kic-9", [item], [], pin_evidence=False) == {"pass": 1, "fail": 0,
                                                                                    "undetermined": 0}
    with store.session() as s:
        sid = s.scalar(select(SignalRow.id).where(SignalRow.run_id == rid))
    assert store.signal_status(sid) == "candidate_stage2" == store.refresh_status(sid)   # both paths agree


def test_dossier_rows_round_trip_and_join_status(store_with_signals):
    from datetime import datetime
    store, ids = store_with_signals
    fields = {"signal_id": ids["approved"], "generated": datetime(2026, 10, 21, 9, 30), "data_path": "d/kic-4.json",
              "search_version": VERSION, "ladder_version": "3", "dossier_version": "1", "fap": 1 / 1700, "fpp": 0.03,
              "fap_detail": {"k": 0, "n": 1699}, "fpp_detail": {"value": 0.03}, "tests_fingerprint": "a" * 64,
              "status_at_generation": "candidate_stage1"}
    assert store.dossier("kic-4") is None and store.dossiers() == []
    store.upsert_dossier(target_id="kic-4", **fields)
    store.upsert_dossier(target_id="kic-1", **{**fields, "signal_id": ids["candidate"], "fpp": 0.2})
    row = store.dossier("kic-4")
    assert row["fap_detail"] == {"k": 0, "n": 1699} and row["generated"] == datetime(2026, 10, 21, 9, 30)
    assert row["export_path"] == "" and row["pack_steps"] == {} and row["pack_built"] is None
    store.upsert_dossier(target_id="kic-4", export_path="out.html")              # a partial update keeps the rest
    assert store.dossier("kic-4")["export_path"] == "out.html" and store.dossier("kic-4")["fpp"] == 0.03
    store.decide(ids["approved"], "needs_data", "deeper pixels")
    rows = {r["target_id"]: r for r in store.dossiers()}
    assert list(rows) == ["kic-1", "kic-4"]
    assert rows["kic-4"]["final_status"] == "candidate_stage1" and rows["kic-4"]["decision"] == "needs_data"
    assert rows["kic-1"]["decision"] is None and rows["kic-1"]["fpp"] == 0.2
    store.set_pack("kic-4", "packs/kic-4.zip", datetime(2026, 10, 22))
    store.set_pack_steps("kic-4", {"1": True, "2": False})
    row = store.dossier("kic-4")
    assert row["pack_path"] == "packs/kic-4.zip" and row["pack_steps"] == {"1": True, "2": False}
    with pytest.raises(TypeError):
        store.upsert_dossier(target_id="kic-4", colour="red")
    with pytest.raises(KeyError):
        store.set_pack("kic-9", "p", datetime(2026, 10, 22))


def test_error_capped_targets_add_up_attempts_across_compatible_ladders(tmp_path):
    """The dashboard's give-up count follows the sweep's selection: failed attempts and pool breaks under ladder 3
    and ladder 4 (LADDER_COMPATIBLE) add up for the same search, and a done run under either clears the target."""
    from skysift.store.db import POOL_BROKE_ERROR
    s = make_store(tmp_path)
    ver = "1+abcdef01"

    def run(tid, lad, status="error", error="boom"):
        s.finish_run(s.start_run(make_job_key(tid, "box_dip", ver, lad), tid, "box_dip", ver, lad), status, error)

    for lad in ("3", "3", "4"): run("split", lad)                       # 2 + 1: given up
    for lad in ("3", "4"): run("two", lad)                              # 1 + 1: not yet
    for lad in ("3", "3", "3"): run("done4", lad)
    run("done4", "4", "done", "")                                       # done under ladder 4: not given up
    for lad in ("3", "4", "4"): run("breaks", lad, error=POOL_BROKE_ERROR)   # three uncharged pool breaks
    assert s.error_capped_targets(3) == 1
    assert s.error_capped_targets(3, max_pool_breaks=3) == 2
    assert s.error_capped_targets(3, ladders=("4",)) == 0              # one ladder alone: no target reaches three
    # a recheck's own run (P/2 signal) is not the target's latest sweep job
    s.finish_run(s.start_run("recheck:P/2:1", "split", "box_dip", ver, "4"), "done")
    assert s.error_capped_targets(3) == 1


@pytest.mark.parametrize("decision", ["approve", "needs_data", "reject"])
def test_a_recheck_step_is_refused_after_any_reviewer_decision(tmp_path, decision):
    """The queue leaves out every signal a reviewer decided on, so a decision found at write time arrived while the
    step ran: whatever it was (approve, needs_data or reject), only the single skipped row is written."""
    from skysift.store.db import REVIEWER_DECIDED_REASON, StepWrite
    s = make_store(tmp_path)
    s.upsert_target(Target(source="kbonus_bkg", target_id="kic-1", ra=1.0, dec=2.0))
    rid = s.start_run(make_job_key("kic-1", "box_dip", "1", "4"), "kic-1", "box_dip", "1", "4")
    sid = s.add_signal(Signal(target_id="kic-1", detector="box_dip", detector_version="1", period=10.0, t0=1.0,
                              duration=0.1, depth_ppm=1000, snr=20, sde=12, n_events=40), rid)
    s.add_test(sid, TestResult(rung="rung1_detection", status=TestStatus.PASS), 0.0)
    s.refresh_status(sid)
    s.decide(sid, decision, note="looked at it")
    tests_before = s.counts()["tests"]
    done = s.commit_recheck_step(StepWrite(signal_id=sid, step="pixels", ladder_version="4", outcome="pass",
                                           tests=[(TestResult(rung="rung5_pixels", status=TestStatus.PASS), 1.0)]))
    assert done["written"] is False and s.counts()["tests"] == tests_before
    assert [(r["step"], r["outcome"], r["reason"]) for r in s.rechecks(sid)] == [
        ("pixels", "skipped", REVIEWER_DECIDED_REASON)]


def test_set_candidate_pack_writes_only_an_existing_candidate_row(tmp_path):
    """The pack folder goes on the signal's candidate row; a signal no reviewer decided on has none, and none is made
    (a new row would read as "needs data")."""
    from skysift.store.db import CandidateRow
    s = make_store(tmp_path)
    s.upsert_target(Target(source="kbonus_bkg", target_id="kic-1", ra=1.0, dec=2.0))
    rid = s.start_run(make_job_key("kic-1", "box_dip", "1", "4"), "kic-1", "box_dip", "1", "4")
    sid = s.add_signal(Signal(target_id="kic-1", detector="box_dip", detector_version="1", period=10.0, t0=1.0,
                              duration=0.1, depth_ppm=1000, snr=20, sde=12, n_events=40), rid)
    assert s.set_candidate_pack(sid, "/packs/kic-1/20261021") is False
    assert s.counts()["candidates"] == 0
    s.decide(sid, "approve", "clean")
    assert s.set_candidate_pack(sid, "/packs/kic-1/20261021") is True
    with s.session() as session:
        row = session.scalars(select(CandidateRow).where(CandidateRow.signal_id == sid)).one()
        assert row.pack_path == "/packs/kic-1/20261021" and row.status == "approved"
