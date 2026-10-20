import pytest

from skysift.models import Signal, Target, TestResult, TestStatus
from skysift.store.db import CandidateRow, DecisionRow, SignalRow, Store, make_job_key


def _store_with_signal(tmp_path):
    s = Store(f"sqlite:///{tmp_path.as_posix()}/t.db"); s.init_schema()
    s.upsert_target(Target(source="kbonus_bkg", target_id="kic-1", ra=0, dec=0))
    rid = s.start_run(make_job_key("kic-1", "box_dip", "1+0", "1"), "kic-1", "box_dip", "1+0", "1")
    sig = Signal(target_id="kic-1", detector="box_dip", detector_version="1+0", period=3.0, t0=1.0, duration=0.1,
                 depth_ppm=800, snr=12, sde=11, n_events=9)
    sid = s.add_signal(sig, rid); s.set_signal_status(sid, "candidate_stage1")
    for rung in LC_RUNGS:                      # a stage-1 candidate: every light-curve rung passed
        s.add_test(sid, TestResult(rung=rung, status=TestStatus.PASS), 0.01)
    return s, sid


LC_RUNGS = ("rung1_detection", "rung1_events", "rung2_shape", "rung3_stability", "rung4_novelty")


@pytest.fixture
def store_stage2_signal(tmp_path):
    """A stage-1 candidate whose pixel recheck passed: a rechecks row and the rung 5 row it wrote."""
    s, sid = _store_with_signal(tmp_path)
    rid = s.add_recheck(sid, "pixels", "4", "pass")
    s.add_test(sid, TestResult(rung="rung5_pixels", status=TestStatus.PASS), 1.5, recheck_id=rid)
    assert s.refresh_status(sid) == "candidate_stage2"
    return s, sid


def test_approve_creates_candidate_and_history(tmp_path):
    s, sid = _store_with_signal(tmp_path)
    out = s.decide(sid, "approve", "clean fold, no secondary")
    assert out["status"] == "approved" and out["final_status"] == "candidate_stage1" and out["decided_at"]
    with s.session() as ses:
        c = ses.query(CandidateRow).one()
        assert (c.status, c.note, c.branch) == ("approved", "clean fold, no secondary", "planet") and c.decided_at
        assert ses.query(DecisionRow).count() == 1
    assert s.decisions(sid)[0]["decision"] == "approve"
    assert s.decisions(sid)[0]["utc"] == out["decided_at"]               # one naive-UTC form, written and read back


def test_note_is_stripped_once(tmp_path):
    s, sid = _store_with_signal(tmp_path)
    out = s.decide(sid, "reject", "  odd/even differ  ")
    assert out["note"] == "odd/even differ" and s.decisions(sid)[0]["note"] == "odd/even differ"
    with s.session() as ses:
        assert ses.query(CandidateRow).one().note == "odd/even differ"


def test_reject_requires_note_and_marks_signal(tmp_path):
    s, sid = _store_with_signal(tmp_path)
    with pytest.raises(ValueError):
        s.decide(sid, "reject", "   ")
    s.decide(sid, "reject", "odd/even differ by 4 sigma")
    with s.session() as ses:
        assert ses.get(SignalRow, sid).final_status == "rejected"
        assert ses.query(CandidateRow).one().status == "rejected"


def test_revisit_appends_history_and_moves_state(tmp_path):
    s, sid = _store_with_signal(tmp_path)
    s.decide(sid, "reject", "looked like an EB")
    s.decide(sid, "approve", "secondary was a data gap artefact")
    hist = s.decisions(sid)
    assert [h["decision"] for h in hist] == ["approve", "reject"]          # newest first
    with s.session() as ses:
        assert ses.query(CandidateRow).count() == 1                          # one candidate row per signal
        assert ses.query(CandidateRow).one().status == "approved"
        assert ses.get(SignalRow, sid).final_status == "candidate_stage1"   # back in the queue's status space
    s.decide(sid, "needs_data", "wait for rung 5")
    with s.session() as ses:
        assert ses.query(CandidateRow).one().status == "review"
    assert len(s.decisions(sid)) == 3


def test_unknown_decision_or_signal(tmp_path):
    s, sid = _store_with_signal(tmp_path)
    with pytest.raises(ValueError): s.decide(sid, "delete")
    with pytest.raises(KeyError): s.decide(sid + 99, "approve")


def test_un_reject_returns_to_the_engine_status_derived_from_tests(store_stage2_signal):
    store, sid = store_stage2_signal
    store.decide(sid, "reject", "mistake"); store.decide(sid, "approve")
    assert store.signal_status(sid) == "candidate_stage2"


def test_un_reject_keeps_an_engine_failure_and_plain_decisions_keep_the_status(store_stage2_signal):
    store, sid = store_stage2_signal
    store.set_signal_status(sid, "superseded")
    assert store.decide(sid, "needs_data", "see the P/2 signal")["final_status"] == "superseded"
    store.set_signal_status(sid, "candidate_stage2")
    store.add_test(sid, TestResult(rung="rung2_shape", status=TestStatus.FAIL, reason="odd_even 4 sigma"), 0.01)
    assert store.decide(sid, "approve")["final_status"] == "candidate_stage2"     # no rejection to undo: untouched
    store.decide(sid, "reject", "eclipsing binary")
    assert store.decide(sid, "approve", "I disagree")["final_status"] == "rejected"  # the engine's own failure stands
