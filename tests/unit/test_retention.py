import pytest

from skysift.store.blob import LocalBlob
from skysift.store.db import Store
from skysift.store.retention import DiskBudgetExceeded, Retention, RetentionPolicy


def setup(tmp_path, policy):
    st = Store(f"sqlite:///{tmp_path.as_posix()}/t.db"); st.init_schema()
    b = LocalBlob(tmp_path / "blobs")
    return st, b, Retention(RetentionPolicy.parse(policy), st, b)


def put(st, b, target, payload):
    sha, loc = b.put(payload, "f.fits"); st.add_artifact(sha, len(payload), "https://x", loc, target); return sha, loc


def test_parse():
    assert RetentionPolicy.parse("budget:2").budget_bytes == 2 * 1024**3
    assert RetentionPolicy.parse("stream").mode == "stream"


def test_stream_releases_unpinned_only(tmp_path):
    st, b, r = setup(tmp_path, "stream")
    _, l1 = put(st, b, "t1", b"a" * 10); put(st, b, "t1", b"b" * 10)
    st.pin_target_artifacts("t1")
    assert r.release_target("t1") == 0 and b.exists(l1)
    _, l3 = put(st, b, "t2", b"c" * 10)
    assert r.release_target("t2") == 10 and not b.exists(l3)


def test_stream_refetch_after_release_is_counted_and_released_again(tmp_path):
    st, b, r = setup(tmp_path, "stream")
    _, loc = put(st, b, "t1", b"x" * 10)
    assert r.release_target("t1") == 10 and not b.exists(loc)
    _, loc2 = put(st, b, "t1", b"x" * 10)                 # an errored target is retried and fetched again
    assert loc2 == loc and b.exists(loc) and r.cached_bytes() == 10
    assert r.release_target("t1") == 10 and not b.exists(loc) and r.cached_bytes() == 0


def test_pin_skips_deleted_rows(tmp_path):
    st, b, r = setup(tmp_path, "stream")
    put(st, b, "t1", b"x" * 10); r.release_target("t1")
    st.pin_target_artifacts("t1")
    assert r.cached_bytes() == 0
    _, loc = put(st, b, "t1", b"x" * 10)                  # re-fetched: must come back unpinned, so it is released
    assert r.release_target("t1") == 10 and not b.exists(loc)


class LockedBlob(LocalBlob):
    """A file held open by antivirus: delete raises."""
    def delete(self, location): raise PermissionError(f"locked: {location}")


def test_delete_failure_is_logged_and_the_sweep_continues(tmp_path, caplog):
    st = Store(f"sqlite:///{tmp_path.as_posix()}/t.db"); st.init_schema()
    b = LockedBlob(tmp_path / "blobs"); r = Retention(RetentionPolicy("budget", 0), st, b)
    put(st, b, "t1", b"a" * 10); put(st, b, "t2", b"b" * 10)
    with pytest.raises(DiskBudgetExceeded):                # nothing could be freed
        r.enforce_budget()
    assert len(st.unpinned_artifacts()) == 2               # rows not marked deleted: the files are still there
    assert "could not delete" in caplog.text
    r.policy = RetentionPolicy("stream")
    assert r.release_target("t1") == 0 and len(st.unpinned_artifacts()) == 2


def test_budget_never_evicts_in_flight_targets(tmp_path):
    st, b, r = setup(tmp_path, "budget:1")
    r.policy.budget_bytes = 15
    _, old = put(st, b, "done1", b"a" * 10); _, live = put(st, b, "inflight", b"b" * 10)
    assert r.enforce_budget(exclude_targets={"inflight"}) == 10
    assert not b.exists(old) and b.exists(live)
    r.policy.budget_bytes = 5                              # over budget only because of the in-flight file
    assert r.enforce_budget(exclude_targets={"inflight"}) == 0 and b.exists(live)


def test_budget_evicts_lru_and_raises_when_all_pinned(tmp_path):
    st, b, r = setup(tmp_path, "budget:1")
    r.policy.budget_bytes = 25
    put(st, b, "t1", b"a" * 10); put(st, b, "t2", b"b" * 10); put(st, b, "t3", b"c" * 10)
    assert r.enforce_budget() >= 10
    for t in ("t1", "t2", "t3"): st.pin_target_artifacts(t)
    put(st, b, "t4", b"d" * 20); st.pin_target_artifacts("t4")
    with pytest.raises(DiskBudgetExceeded):
        r.enforce_budget()


def test_stream_keeps_summary_artifacts(tmp_path):
    st, b, r = setup(tmp_path, "stream")
    _, raw = put(st, b, "t1", b"raw" * 10)
    sha, loc = b.put(b"npz" * 10, "t1_periodogram.npz")
    st.add_artifact(sha, 30, "skysift://periodogram/t1", loc, "t1", kind="summary")
    assert r.release_target("t1") == 30 and not b.exists(raw) and b.exists(loc)
    assert [a["kind"] for a in st.unpinned_artifacts("t1")] == ["summary"]
    assert st.unpinned_artifacts("t1", kind="raw") == []
