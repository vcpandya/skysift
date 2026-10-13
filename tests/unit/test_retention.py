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


def test_budget_evicts_lru_and_raises_when_all_pinned(tmp_path):
    st, b, r = setup(tmp_path, "budget:1")
    r.policy.budget_bytes = 25
    put(st, b, "t1", b"a" * 10); put(st, b, "t2", b"b" * 10); put(st, b, "t3", b"c" * 10)
    assert r.enforce_budget() >= 10
    for t in ("t1", "t2", "t3"): st.pin_target_artifacts(t)
    put(st, b, "t4", b"d" * 20); st.pin_target_artifacts("t4")
    with pytest.raises(DiskBudgetExceeded):
        r.enforce_budget()
