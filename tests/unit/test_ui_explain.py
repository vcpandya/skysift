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
    ("pixels: 2 usable transits < 3", explain.PIXELS_SAID),
    ("pixels: season 2 disagrees (4.1 sigma)", explain.PIXELS_SAID),
    ("the dip is on a neighbouring star (3 of 4 seasons)", explain.FAILED),
    ("outage:mast: HTTP 503", explain.PIXELS_RETRY), ("season-dependent depth (p=5.7e-04)", "pixel stage")])
def test_the_other_reasons_the_rungs_emit_map_too(reason, step):
    missing, nxt = explain.reason_in_words(reason)
    assert missing != reason and nxt == step and "—" not in missing + nxt


def test_reason_table_reproduces_the_spec_words():
    assert explain.reason_in_words("too few seasons")[0] == "Fewer than three seasons have data"
    assert explain.reason_in_words("outage:vizier: HTTP 503") == ("A catalogue did not answer when we asked",
                                                                  "retry the catalogue")
    assert explain.reason_in_words("systematic_alias")[1] == "pixel stage"
    assert explain.reason_in_words("pixels: no neighbour to test against")[1] == explain.PIXELS_NONE


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
    assert rows["rung1_detection"].sentence == "SDE 50.5, above the usual minimum of 12"
    assert "32 of 37 predicted transits have data" in rows["rung1_events"].sentence
    assert "0.3 sigma" in rows["rung2_shape"].sentence and "2,202" in rows["rung2_shape"].numbers
    assert "p = 0.9" in rows["rung3_stability"].sentence
    assert "No match in 4 catalogues" in rows["rung4_novelty"].sentence
    assert "3 of 3 seasons" in rows["rung5_pixels"].sentence and rows["rung5_pixels"].status == "pass"
    numbers = rows["rung5_pixels"].numbers
    assert "archive season 3: not voting (box incomplete: target)" in numbers
    assert ("archive season 0: 0.13 ± 0.05 px from the star, 2.75 from the neighbour, 40.9 σ nearer the star"
            in numbers)
    assert "quarter group 0: 2,150 ± 110 ppm" in rows["rung3_stability"].numbers           # rung 3: quarter mod 4
    assert "4 quarter groups" in rows["rung3_stability"].sentence and "season" not in rows["rung3_stability"].numbers


def _detection_row(sde):
    return [{"rung": "rung1_detection", "status": "pass", "reason": "", "metrics": {"sde": sde}}]


def test_the_detection_sentence_quotes_the_configured_minimum():
    from skysift.config import Thresholds
    explain.configure(Thresholds(sde_min=9.0))
    assert explain.why(_detection_row(9.6))[0].sentence == "SDE 9.6, above the usual minimum of 9"
    # a legacy row that passed under an older, lower threshold quotes no number it never had to clear
    explain.configure(Thresholds())
    assert explain.why(_detection_row(9.6))[0].sentence == "SDE 9.6, above the threshold that run used"
    assert explain.why(_detection_row(14.0))[0].sentence == "SDE 14.0, above the usual minimum of 12"


def test_create_app_points_explain_at_the_settings_thresholds(settings, monkeypatch):
    from skysift.config import load_settings
    from tests.unit.test_ui_pages import make_client
    monkeypatch.setenv("SKYSIFT_THRESHOLDS__SDE_MIN", "9")
    make_client(load_settings())
    assert "minimum of 9" in explain.why(_detection_row(9.6))[0].sentence


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


def test_verdict_puts_the_reviewer_decision_first(settings, tmp_path):
    from tests.unit.test_ui_pages import make_seeded_app
    client, app, ids, _launcher = make_seeded_app(settings, tmp_path)
    v = explain.verdict(**_args(queries.signal_detail(app.state.store, ids["approved"])))
    assert v.reviewer == "Approved by reviewer" and v.word == "Candidate"
    reviewer = '<span class="pill pill-pass pill-reviewer">Approved by reviewer</span>'
    card = client.get("/approved?mode=simple").text
    card = card[card.index('<article class="card candidate'):]
    hero = client.get(f"/signals/{ids['approved']}").text
    detail = client.get(f"/partials/signals/{ids['approved']}/detail").text
    for html in (card, hero, detail):                                       # the person's word, then the engine's
        assert html.index(reviewer) < html.index(">Candidate</button>")
    popup = client.get(f"/partials/signals/{ids['approved']}/verdict").text
    assert popup.index(reviewer) < popup.index("Candidate, stage 1: every light-curve check passed")
    # a reviewer rejection is the headline itself: the popup says it once, the clipboard too
    app.state.store.decide(ids["candidate"], "reject", "a blend")
    popup = client.get(f"/partials/signals/{ids['candidate']}/verdict").text
    assert popup.split('class="verdict-foot"')[0].count("Rejected by reviewer") == 1
    copy = client.get(f"/signals/{ids['candidate']}").text.split('data-copy="')[1].split('"')[0]
    assert copy.count("Rejected by reviewer") == 1


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
    assert all("—" not in t for t in glossaries) and len(explain.GLOSSARY) == 15


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


def test_time_in_words():
    from datetime import UTC, datetime, timedelta
    now = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    assert [explain.duration_words(s) for s in (None, 30, 60, 2400, 3600, 4500, 7500, 40 * 3600, 48 * 3600, 5 * 86400)] == [
        "under a minute", "under a minute", "1 minute", "40 minutes", "1 hour", "1 hour 15 minutes", "2 hours", "40 hours", "2 days",
        "5 days"]
    assert explain.ago(now - timedelta(seconds=20), now) == "just now"
    assert explain.ago(datetime(2026, 10, 20, 10, 0), now) == "2 hours ago"            # a store time is UTC
    assert explain.ago("2026-10-20T11:15:00+00:00", now) == "45 minutes ago"
    assert explain.ago("not a time", now) == explain.ago(None, now) == "at an unknown time"
    assert explain.time_left(20) == "under a minute left" and explain.time_left(2400) == "about 40 minutes left"


# ---- the pixel stage and the recheck history in words (recheck, task 6)

@pytest.mark.parametrize("reason", ["pixels: 2 usable transits < 3",
                                    "pixels: season 2 disagrees (centroid nearer neighbour, 4.1 sigma)",
                                    "pixels: no neighbour to test against", "outage:mast: HTTP 503"])
def test_pixel_reasons_map_to_words(reason):
    missing, nxt = explain.reason_in_words(reason)
    assert missing != reason and nxt != "needs a person"


@pytest.mark.parametrize(("reason", "words", "step"), [
    ("pixels: no season holds both stars on the frame", "No season's pixel stamp holds both", "PIXELS_SAID"),
    ("pixels: no season localises the dip", "No season's difference image places the dip", "PIXELS_SAID"),
    ("pixels: season 2 ambiguous (1.4 sigma)", "too close to the line between the two stars", "PIXELS_SAID"),
    ("pixels: season 1 disagrees (centroid nearer neighbour, 4.1 sigma)", "One season does not agree",
     "PIXELS_SAID"),
    ("pixels: no pixel files for this star", "no pixel files", "PIXELS_NONE"),
    ("pixels: no neighbour to test against", "no neighbour in its pixels", "PIXELS_NONE"),
    ("pixels: no readable pixel files (3 listed)", "None of the archive's pixel files", "PIXELS_NONE"),
    ("pixels: only one season could vote (season 1: box incomplete: target; season 2: box incomplete: target)",
     "Only one observing season could vote on which star dims", "PERSON_DECIDES"),
    ("pixels: only one season could vote (no other season has data)", "and one is too few",
     "PERSON_DECIDES"),
    ("pixels: season 3 disagrees (neighbour depth 400 +/- 50 ppm)", "The neighbour's own depth vetoes",
     "PIXELS_SAID"),
    ("pixels: season 3 disagrees (neighbour depth not measured)", "The neighbour's own depth vetoes", "PIXELS_SAID"),
    ("pixels: no season localizes the dip", "No season's difference image places the dip", "PIXELS_SAID"),
    ("pixels: 2 usable transits < 3", "The pixels could not show which star the dip is on", "PIXELS_SAID"),
    ("outage:mast: 3 of 16 pixel files unreachable", "The pixel archive did not answer", "PIXELS_RETRY"),
    ("outage:mast: HTTP 503", "The pixel archive did not answer", "PIXELS_RETRY"),
    ("outage:exoarchive: HTTP 503", "A catalogue did not answer", "RETRY")])
def test_each_pixel_reason_has_its_own_words_and_next_step(reason, words, step):
    missing, nxt = explain.reason_in_words(reason)
    assert words in missing and nxt == getattr(explain, step) and "—" not in missing + nxt


def test_an_undetermined_pixel_row_carries_its_detail():
    row = {"rung": "rung5_pixels", "status": "undetermined", "reason": "pixels: season 2 ambiguous (1.4 sigma)",
           "metrics": {}}
    w = explain.why([row])[5]
    assert w.sentence.startswith("No verdict: ") and "(archive season 2 ambiguous (1.4 sigma))" in w.sentence
    assert w.next_step == explain.PIXELS_SAID


def test_the_pixel_pass_and_fail_sentences():
    passed = {"rung": "rung5_pixels", "status": "pass",
              "reason": "the dip is on the target in 3/3 seasons (centroid >= 38.2 sigma nearer)", "metrics": {}}
    assert explain.why([passed])[5].sentence == ("The dip is on this star in 3 of 3 seasons, the centroid at least "
                                                 "38.2 sigma nearer it than the neighbour")
    failed = {"rung": "rung5_pixels", "status": "fail",
              "reason": "the dip is on a neighbouring star (4.1 sigma, 3/4 seasons)", "metrics": {}}
    assert explain.why([failed])[5].sentence == ("The pixels put the dip on a neighbouring star in 3 of 4 seasons "
                                                 "(4.1 sigma)")
    assert explain.RUNG_NAMES["rung5_pixels"] == "On this star, not a neighbour"


def _partial_season_metrics() -> dict:
    """The seeded stage-2 pixel metrics with season 0's box cut by the stamp edge (6 of 9 pixels, this star's)."""
    from tests.unit.ui_seed import stage2_pixel_metrics
    m = stage2_pixel_metrics()
    m["seasons"][0] = {**m["seasons"][0], "box_partial": True, "box_pixels_target": 6, "box_pixels_neighbour": 9,
                       "box_missing_target": [[-1, -1], [0, -1], [1, -1]], "box_missing_neighbour": [],
                       "edge_bias_pix": 0.10, "separation_sigma_raw": 9.6}
    return m


def test_pixels_sentence_names_a_box_cut_by_the_stamp_edge():
    m = _partial_season_metrics()
    sentence, numbers = explain._pixels(m)
    assert sentence == ("The dip sits on this star in 3 of 3 seasons that could vote "
                        "(1 of 3 with a box cut by the stamp edge); 1 could not")
    assert "archive season 0: " in numbers and "this star's box cut, 6 of 9 pixels" in numbers
    assert "0.10 px allowance in the error" in numbers and "neighbour's box cut" not in numbers
    row = {"rung": "rung5_pixels", "status": "pass",
           "reason": "the dip is on the target in 3/3 seasons (centroid >= 17.5 sigma nearer)", "metrics": m}
    assert explain._row("rung5_pixels", row).sentence.endswith("than the neighbour (1 of 3 with a box cut by the "
                                                                "stamp edge)")


def test_a_season_without_box_keys_renders_as_before():
    from tests.unit.ui_seed import stage2_pixel_metrics
    assert explain._pixels(stage2_pixel_metrics()) == (
        "The dip sits on this star in 3 of 3 seasons that could vote; 1 could not",
        ("archive season 0: 0.13 ± 0.05 px from the star, 2.75 from the neighbour, 40.9 σ nearer the star; "
         "archive season 1: 0.11 ± 0.07 px from the star, 2.83 from the neighbour, 20.5 σ nearer the star; "
         "archive season 2: 0.19 ± 0.08 px from the star, 2.71 from the neighbour, 17.5 σ nearer the star; "
         "archive season 3: not voting (box incomplete: target); 28 transits in 15 quarters; "
         "the neighbour's own light at the transits: 14 ± 31 ppm"))
    row = {"rung": "rung5_pixels", "status": "pass",
           "reason": "the dip is on the target in 3/3 seasons (centroid >= 17.5 sigma nearer)",
           "metrics": stage2_pixel_metrics()}
    assert explain._row("rung5_pixels", row).sentence == ("The dip is on this star in 3 of 3 seasons, the centroid at "
                                                          "least 17.5 sigma nearer it than the neighbour")


def test_recheck_history_in_words():
    rows = [{"id": 1, "step": "ladder", "ladder_version": "4", "outcome": "skipped", "new_signal_id": None,
             "reason": "search version differs: signal of search version 1+old, not the current 2+new",
             "utc": "2026-10-20T03:04:05"},
            {"id": 2, "step": "period", "ladder_version": "4", "outcome": "superseded", "new_signal_id": 9,
             "reason": "re-measured at 9.0975 d: rejected", "utc": "2026-10-21T01:00:00"},
            {"id": 3, "step": "catalogue", "ladder_version": "4", "outcome": "undetermined", "new_signal_id": None,
             "reason": "outage:vizier: HTTP 503", "utc": "2026-10-21T01:00:00"},
            {"id": 4, "step": "pixels", "ladder_version": "4", "outcome": "fail", "new_signal_id": None,
             "reason": "the dip is on a neighbouring star (4.1 sigma, 3/4 seasons)", "utc": "2026-10-21T01:00:00"},
            {"id": 5, "step": "ladder", "ladder_version": "4", "outcome": "fail", "new_signal_id": None,
             "reason": "rung2_shape: odd_even 4.2 sigma", "utc": "2026-10-21T01:00:00"},
            {"id": 6, "step": "pixels", "ladder_version": "4", "outcome": "skipped", "new_signal_id": None,
             "reason": "reviewer decided meanwhile", "utc": "2026-10-21T01:00:00"}]
    children = [{"id": 9, "period": 9.0975, "final_status": "rejected"}]
    h = explain.recheck_history(rows, children)
    assert [x["step"] for x in h] == ["Re-vetted under ladder 4", "Re-vetted at half the period",
                                      "Catalogue asked again", "Pixels checked", "Re-vetted under ladder 4",
                                      "Pixels checked"]
    assert h[0]["date"] == "20 Oct 2026" and h[0]["outcome"] == "skipped"
    assert h[0]["detail"] == "the signal comes from search version 1+old, not the current 2+new"
    assert (h[1]["outcome"], h[1]["link_id"], h[1]["link_period"]) == ("superseded", 9, 9.0975)
    assert h[1]["detail"] == "that signal was rejected"
    assert h[2]["outcome"] == "no verdict" and h[2]["detail"] == "a catalogue did not answer when we asked"
    assert h[3]["outcome"] == "failed" and "neighbouring star in 3 of 4 seasons" in h[3]["detail"]
    assert h[4]["detail"] == "odd and even transits differ by 4.2 sigma, above the usual limit of 3"
    assert h[5]["detail"] == "a reviewer decided on the signal while the step ran"
    assert all("—" not in f"{x['step']} {x['outcome']} {x['detail']}" for x in h)
    assert explain.recheck_history([], []) == []


def test_lineage_words():
    parent = {"id": 3, "period": 18.195, "final_status": "superseded"}
    child = {"id": 9, "period": 9.0975, "final_status": "rejected", "branch": "recheck:P/2"}
    assert explain.lineage({"final_status": "superseded"}, None, [child]) == [
        {"words": "Superseded by", "id": 9, "period": 9.0975, "status": "rejected", "status_words": "rejected"}]
    assert explain.lineage({"final_status": "rejected"}, parent, []) == [
        {"words": "Supersedes", "id": 3, "period": 18.195, "status": "superseded", "status_words": "superseded"}]
    # a P/2 child that did not settle it: the parent stays open, so the words say measured again, not superseded
    assert explain.lineage({"final_status": "undetermined"}, None, [{**child, "final_status": "undetermined"}]) == [
        {"words": "Measured again at half the period as", "id": 9, "period": 9.0975, "status": "undetermined",
         "status_words": "undetermined"}]
    assert explain.lineage({"final_status": "undetermined"}, {**parent, "final_status": "undetermined"}, []) == [
        {"words": "Measured at half the period of", "id": 3, "period": 18.195, "status": "undetermined",
         "status_words": "undetermined"}]
    assert explain.lineage({"final_status": "candidate_stage1"}, None, []) == []
    assert explain.lineage({"final_status": "superseded"}, None, [{**child, "final_status": "candidate_stage2"}])[0][
        "status_words"] == "a stage 2 candidate"


def test_next_step_says_when_the_recheck_already_took_it():
    row = {"rung": "rung2_shape", "status": "undetermined", "metrics": {},
           "reason": "equal secondary at phase 0.5: true period may be P / 2 (9.0975 d)"}
    rows = explain.why([row])
    period = {"id": 2, "step": "period", "ladder_version": "4", "outcome": "superseded", "new_signal_id": 9,
              "reason": "re-measured at 9.0975 d: rejected", "utc": "2026-10-21T01:00:00"}
    done = explain.with_recheck_state(rows, [period], [{"id": 9, "period": 9.0975, "final_status": "rejected"}])
    assert done[2].next_step == "rechecked on 21 Oct 2026: superseded by the signal at 9.0975 d, now rejected"
    assert done[2].missing == rows[2].missing and done[0] == rows[0]
    assert explain.with_recheck_state(rows, [], []) == rows                         # not yet: the plain next step


def test_vetted_statuses_are_the_engine_statuses_less_pending():
    from skysift.store.db import ENGINE_STATUSES
    assert explain.VETTED_STATUSES == tuple(s for s in ENGINE_STATUSES if s != "pending")


def _step(id_, step, outcome, reason="", utc="2026-10-21T01:00:00"):
    return {"id": id_, "step": step, "ladder_version": "4", "outcome": outcome, "new_signal_id": None,
            "reason": reason, "utc": utc}


def test_a_catalogue_retry_that_met_the_outage_again_keeps_the_retry_wording():
    """The catalogue step wrote the outage row it explains, so it has not taken that row's next step."""
    rows = explain.why([{"rung": "rung4_novelty", "status": "undetermined", "metrics": {}, "recheck_id": 7,
                         "reason": "outage:exoarchive: HTTP 503"}])
    done = explain.with_recheck_state(rows, [_step(7, "catalogue", "undetermined", "outage:exoarchive: HTTP 503")])
    assert done[4].next_step == "retried on 21 Oct 2026, still no answer; " + explain.RETRY
    assert rows[4].next_step == explain.RETRY and explain.with_recheck_state(rows, []) == rows   # a sweep row


def test_a_pixel_retry_that_met_the_outage_again_keeps_the_retry_wording():
    rows = explain.why([{"rung": "rung5_pixels", "status": "undetermined", "metrics": {}, "recheck_id": 8,
                         "reason": "outage:mast: HTTP 503"}])
    done = explain.with_recheck_state(rows, [_step(8, "pixels", "undetermined", "outage:mast: HTTP 503")])
    assert done[5].next_step == "retried on 21 Oct 2026, still no answer; ask the pixel archive again"


def test_a_catalogue_retry_whose_newer_row_passed_leaves_no_retry_wording():
    rows = explain.why([{"rung": "rung4_novelty", "status": "undetermined", "metrics": {}, "id": 1,
                         "reason": "outage:exoarchive: HTTP 503", "created": "2026-10-20T01:00:00"},
                        {"rung": "rung4_novelty", "status": "pass", "metrics": {"cumulative": 0, "toi": 0}, "id": 2,
                         "recheck_id": 7, "created": "2026-10-21T01:00:00"}])
    done = explain.with_recheck_state(rows, [_step(7, "catalogue", "pass")])
    assert done[4].status == "pass" and done[4].next_step is None and "retr" not in done[4].sentence.lower()


def test_a_pixel_step_takes_the_next_step_a_rung3_row_asked_for():
    """The pixel stage a stability row asks for is taken by a step that wrote a different rung's row."""
    rows = explain.why([{"rung": "rung3_stability", "status": "undetermined", "metrics": {},
                         "reason": "aperture-dependent depth (3.9 sigma)"}])
    assert rows[3].next_step == explain.PIXELS
    done = explain.with_recheck_state(rows, [_step(9, "pixels", "pass")])
    assert done[3].next_step == "rechecked on 21 Oct 2026: passed"
    skipped = [_step(9, "pixels", "skipped", "reviewer decided meanwhile")]
    assert explain.with_recheck_state(rows, skipped)[3].next_step == explain.PIXELS      # a skip takes no step


def test_a_superseded_signal_unexamined_pixel_row_says_so():
    rows = explain.why([{"rung": "rung1_detection", "status": "pass", "metrics": {}, "reason": ""},
                        {"rung": "rung2_shape", "status": "undetermined", "metrics": {},
                         "reason": "equal secondary at phase 0.5: true period may be P / 2 (9.0975 d)"}])
    assert rows[5].status == "skipped"
    assert explain.with_recheck_state(rows, [], [], "superseded")[5].sentence == "Not examined: superseded"
    assert explain.with_recheck_state(rows, [], [], "undetermined")[5].sentence == rows[5].sentence


def test_season_names():
    assert explain.season_name({"season": 3}) == "archive season 3"
    assert explain.season_name({}) == "archive season (unlabelled)"


def _undetermined(rung, reason):
    return {"rung": rung, "status": "undetermined", "metrics": {}, "reason": reason}


def test_a_revet_the_recheck_will_not_make_reads_a_person_decides():
    """recheck.wants_ladder re-vets once per signal, and only a signal vetted under an older ladder."""
    rows = explain.why([_undetermined("rung2_shape", "too few odd/even events")])
    assert rows[2].next_step == explain.REVET
    old = {"ladder_version": "3", "branch": "planet", "extra": {}}
    assert explain.with_recheck_state(rows, [], signal=old)[2].next_step == explain.REVET       # it will come
    current = {**old, "ladder_version": "4"}
    assert explain.with_recheck_state(rows, [], signal=current)[2].next_step == explain.PERSON
    ran = [_step(3, "ladder", "undetermined", "rung2_shape: too few odd/even events")]
    assert (explain.with_recheck_state(rows, ran, signal=old)[2].next_step
            == "re-vetted on 21 Oct 2026; a person decides")
    skipped = [_step(3, "ladder", "skipped", "light curve unavailable: HTTP 503")]
    assert explain.with_recheck_state(rows, skipped, signal=old)[2].next_step == explain.REVET
    assert explain.with_recheck_state(rows, []) == rows                     # no signal given: the plain next step


def test_a_crash_under_the_current_ladder_reads_a_person_decides_except_on_rung5():
    rows = explain.why([_undetermined("rung2_shape", "exception: ValueError('x')"),
                        _undetermined("rung5_pixels", "exception: ValueError('y')")])
    current = {"ladder_version": "4", "branch": "planet", "extra": {}}
    done = explain.with_recheck_state(rows, [], signal=current)
    assert done[2].next_step == explain.PERSON
    assert done[5].next_step == explain.REVET_CRASH                         # the pixel step retries a crash once


def test_a_p2_signal_asked_to_halve_again_reads_a_person_decides():
    rows = explain.why([_undetermined("rung2_shape",
                                      "equal secondary at phase 0.5: true period may be P / 2 (4.5 d)")])
    assert rows[2].next_step == explain.HALVE
    child = {"ladder_version": "4", "branch": "recheck:P/2", "extra": {"halving": {"from_period": 18.195}}}
    assert explain.with_recheck_state(rows, [], signal=child)[2].next_step == explain.PERSON
    parent = {"ladder_version": "4", "branch": "planet", "extra": {}}
    assert explain.with_recheck_state(rows, [], signal=parent)[2].next_step == explain.HALVE
    from skysift.recheck import PERIOD_BRANCH
    assert explain.PERIOD_BRANCH == PERIOD_BRANCH


def test_the_pixel_localize_reason_reads_in_both_spellings():
    for reason in ("pixels: no season localises the dip", "pixels: no season localizes the dip"):
        missing, nxt = explain.reason_in_words(reason)
        assert missing.startswith("No season's difference image places the dip") and nxt == explain.PIXELS_SAID


def test_the_new_pixel_reasons_carry_their_detail_in_archive_seasons():
    one = {"rung": "rung5_pixels", "status": "undetermined", "metrics": {},
           "reason": "pixels: only one season could vote (season 1: box incomplete: target)"}
    w = explain.why([one])[5]
    assert w.sentence == ("No verdict: only one observing season could vote on which star dims, and one is too few "
                          "(archive season 1: box incomplete: target)")
    assert w.next_step == "a person decides"
    veto = {**one, "reason": "pixels: season 3 disagrees (neighbour depth 400 +/- 50 ppm)"}
    w = explain.why([veto])[5]
    assert w.sentence == ("No verdict: the neighbour's own depth vetoes one season's vote (archive season 3 disagrees "
                          "(neighbour depth 400 +/- 50 ppm))")
    centroid = {**one, "reason": "pixels: season 1 disagrees (centroid nearer neighbour, 4.1 sigma)"}
    assert explain.why([centroid])[5].sentence.startswith("No verdict: one season does not agree")


def test_the_one_season_headline_names_the_vote_not_the_frame():
    # the other seasons may have had both stars on the frame and abstained with a cut box
    row = {"rung": "rung5_pixels", "status": "undetermined", "metrics": {},
           "reason": "pixels: only one season could vote (season 3: box cut: ambiguous (1.8 sigma))"}
    w = explain.why([row])[5]
    assert "only one observing season could vote on which star dims, and one is too few" in w.sentence
    assert "(archive season 3: box cut: ambiguous (1.8 sigma))" in w.sentence
    assert "both stars on the frame" not in w.sentence
    assert w.next_step == explain.PERSON_DECIDES


def test_the_stability_line_gives_the_aperture_ratio_not_agreement():
    rows = explain.why([{"rung": "rung3_stability", "status": "pass", "reason": None, "created": None,
                         "metrics": {"season_depths": {"0": 2295.0, "1": 1962.0, "2": 1864.0, "3": 1912.0},
                                     "season_errs": {}, "p_value": 0.32, "chi2": 3.5, "dof": 3,
                                     "aperture_ratio": 0.63, "aperture_z": -4.7}}])
    line = next(w for w in rows if w.rung == "rung3_stability")      # why() gives one line per rung, ladder order
    assert "the SAP depth is 0.63 of the PSF depth" in line.sentence and "apertures agree" not in line.sentence
