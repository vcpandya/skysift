from skysift.models import Signal, Target, TestResult, TestStatus
from skysift.store.db import Store, make_job_key


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
