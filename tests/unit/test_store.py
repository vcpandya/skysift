from skysift.models import Signal, Target, TestResult, TestStatus
from skysift.store.db import ArtifactRow, Store, make_job_key


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
