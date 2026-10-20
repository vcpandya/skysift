import pytest

from skysift.store.db import Store
from skysift.ui import explain, queries
from tests.unit.ui_seed import seed

REASONS = ["too few odd/even events", "too few events", "too few seasons",
           "equal secondary at phase 0.5: true period may be P / 2 (40.2899 d)", "season-dependent depth (p=5.7e-04)",
           "aperture-dependent depth (3.9 sigma)", "systematic_alias", "outage:exoarchive: HTTP 403",
           "period matches cumulative K01234.01 but epoch differs (5.1 h)", "exception: ValueError('x')"]
ROLES = ("candidate", "undetermined", "rejected", "legacy", "stage2", "approved", "pending")


@pytest.fixture
def seed_detail(settings):
    store = Store(settings.database_url); store.init_schema()
    ids = seed(store)
    return lambda role: queries.signal_detail(store, ids[role])


def _args(d):
    return {"signal": d["signal"], "tests": d["tests"], "decision": d["candidate"]}


@pytest.mark.parametrize("reason", REASONS)
def test_every_rung_reason_maps_to_words_and_a_next_step(reason):
    missing, nxt = explain.reason_in_words(reason)
    assert missing and nxt and "undetermined" not in missing.lower() and nxt != "needs a person"


@pytest.mark.parametrize(("reason", "step"), [
    ("free_secondary_undetermined", "needs a person"), ("schema:toi:missing pl_orbper", "retry the catalogue"),
    ("exception: rung returned NoneType", explain.REVET + "; stays flagged for a person if it crashes again"),
    ("pixels: 2 usable transits < 3", "pixel stage"), ("pixels: season 2 disagrees (4.1 sigma)", "pixel stage"),
    ("the dip is on a neighbouring star (3 of 4 seasons)", explain.FAILED), ("outage:mast: HTTP 503", "pixel stage")])
def test_the_other_reasons_the_rungs_emit_map_too(reason, step):
    missing, nxt = explain.reason_in_words(reason)
    assert missing != reason and nxt == step and "—" not in missing + nxt


def test_reason_table_reproduces_the_spec_words():
    assert explain.reason_in_words("too few seasons")[0] == "Fewer than three seasons have data"
    assert explain.reason_in_words("outage:vizier: HTTP 503") == ("A catalogue did not answer when we asked",
                                                                  "retry the catalogue")
    assert explain.reason_in_words("systematic_alias")[1] == "pixel stage"
    assert explain.reason_in_words("pixels: no neighbour to test against")[1] == "pixel stage"


def test_unknown_reason_falls_back_to_the_raw_text():
    assert explain.reason_in_words("weird new reason") == ("weird new reason", "needs a person")


def test_why_lists_rungs_in_ladder_order_with_skipped_after_a_fail(seed_detail):
    rows = explain.why(seed_detail("rejected")["tests"])
    assert [w.rung for w in rows] == explain.LADDER_ORDER
    assert rows[2].status == "fail" and rows[3].status == "skipped" and rows[-1].status == "skipped"
    assert "4.2" in rows[2].sentence and rows[2].numbers
    assert rows[2].sentence == "Odd and even transits differ by 4.2 sigma, above the usual limit of 3"
    assert rows[3].sentence == "Skipped: an earlier check failed" and rows[1].sentence == "Not run under this ladder"


def test_why_for_an_undetermined_signal_carries_missing_and_next(seed_detail):
    rows = explain.why(seed_detail("undetermined")["tests"])
    u = next(w for w in rows if w.status == "undetermined")
    assert u.rung == "rung4_novelty" and "catalogue" in u.missing.lower() and "retry" in u.next_step.lower()
    assert all(w.missing is None for w in rows if w.status == "pass")


def test_why_uses_the_latest_row_of_each_rung():
    old = {"rung": "rung4_novelty", "status": "undetermined", "reason": "outage:vizier: HTTP 503", "metrics": {}}
    new = {"rung": "rung4_novelty", "status": "pass", "reason": "", "metrics": {"cumulative": 0, "toi": 0},
           "created": "2026-10-20T03:04:05"}
    row = explain.why([old, new])[4]
    assert row.status == "pass" and row.missing is None and row.when == "2026-10-20"


def test_why_sentences_carry_the_deciding_numbers(seed_detail):
    rows = {w.rung: w for w in explain.why(seed_detail("stage2")["tests"])}
    assert rows["rung1_detection"].sentence == "SDE 50.5, above the usual minimum of 11"
    assert "32 of 37 predicted transits have data" in rows["rung1_events"].sentence
    assert "0.3 sigma" in rows["rung2_shape"].sentence and "2,202" in rows["rung2_shape"].numbers
    assert "p = 0.9" in rows["rung3_stability"].sentence
    assert "No match in 4 catalogues" in rows["rung4_novelty"].sentence
    assert "4 of 4 seasons" in rows["rung5_pixels"].sentence and rows["rung5_pixels"].status == "pass"


def test_verdict_words_and_stages(seed_detail):
    assert explain.verdict(**_args(seed_detail("candidate"))).word == "Candidate"
    assert explain.verdict(**_args(seed_detail("candidate"))).stage == "stage 1"
    assert explain.verdict(**_args(seed_detail("stage2"))).stage == "stage 2"
    assert explain.verdict(**_args(seed_detail("undetermined"))).headline == "Undetermined: one check could not decide"
    assert explain.verdict(**_args(seed_detail("rejected"))).tone == "fail"
    assert explain.verdict(**_args(seed_detail("rejected"))).headline == 'Rejected: failed the check "Transit shape"'


def test_verdict_with_no_tests_is_not_vetted(seed_detail):
    v = explain.verdict(**_args(seed_detail("pending")))
    assert v.word == "Not vetted yet" and v.tone == "muted"


def test_verdict_puts_the_reviewer_decision_first(seed_detail):
    v = explain.verdict(**_args(seed_detail("approved")))
    assert v.reviewer == "Approved by reviewer" and v.word == "Candidate"


def test_verdict_needs_data_comes_from_the_latest_decision(seed_detail):
    d = seed_detail("candidate")
    review = {"status": "review", "note": "", "decided_at": None, "branch": "first"}
    assert explain.verdict(d["signal"], d["tests"], review).reviewer is None
    asked = [{"decision": "needs_data", "note": "more", "who": "operator", "utc": "2026-10-20T00:00:00"}]
    assert explain.verdict(d["signal"], d["tests"], review, asked).reviewer == "Needs data"


def test_verdict_for_superseded_and_pending_with_tests(seed_detail):
    d = seed_detail("candidate")
    assert explain.verdict({**d["signal"], "final_status": "superseded"}, d["tests"], None).tone == "muted"
    v = explain.verdict({**d["signal"], "final_status": "pending"}, d["tests"], None)
    assert v.word == "Candidate" and v.tone == "pass"


def test_undetermined_with_no_undetermined_row_says_a_check(seed_detail):
    d = seed_detail("candidate")
    v = explain.verdict({**d["signal"], "final_status": "undetermined"}, d["tests"], None)
    assert v.headline == "Undetermined: a check could not decide"


def test_default_limits_are_never_stated_as_the_run_limit():
    row = {"rung": "rung1_detection", "status": "fail", "reason": "duration ratio 3.40 outside range", "metrics": {}}
    assert explain.why([row])[0].sentence == ("The dip lasts 3.40 times the expected duration, outside the usual "
                                              "range of 0.3 to 3")
    assert {"MS2", "MS3", "Chases"} <= set(explain.GLOSSARY)


def test_legacy_metrics_render_without_the_new_keys(seed_detail):
    rows = explain.why(seed_detail("legacy")["tests"])
    assert len(rows) == len(explain.LADDER_ORDER) and all(w.sentence for w in rows if w.status != "skipped")


def test_no_em_dash_anywhere(seed_detail):
    for role in ROLES:
        d = seed_detail(role)
        v = explain.verdict(**_args(d))
        texts = [v.headline, explain.summary_sentence(d["signal"], d["target"], d["tests"]),
                 explain.copy_summary(d["signal"], d["target"], v, "http://x")]
        texts += [f"{w.sentence} {w.numbers} {w.missing or ''} {w.next_step or ''}" for w in explain.why(d["tests"])]
        assert all("—" not in t for t in texts), role
    glossaries = [*explain.GLOSSARY.values(), *(f"{a} {b}" for a, b in explain.STATUS_GLOSSARY)]
    assert all("—" not in t for t in glossaries) and len(explain.GLOSSARY) == 13


def test_summary_sentence_and_copy_summary(seed_detail):
    d = seed_detail("candidate")
    s = explain.summary_sentence(d["signal"], d["target"], d["tests"])
    assert "13.77" in s or "13.8" in s
    text = explain.copy_summary(d["signal"], d["target"], explain.verdict(**_args(d)), "http://127.0.0.1:8765/signals/1")
    assert "kic-005689121" in text and "http://127.0.0.1:8765/signals/1" in text and "—" not in text


def test_summary_sentence_of_the_stage2_signal(seed_detail):
    d = seed_detail("stage2")
    assert explain.summary_sentence(d["signal"], d["target"], d["tests"]) == (
        "A dip of 0.22 percent repeats every 40.3 days on a star of magnitude 16.2.")
