import json

import pytest
from sqlalchemy import text

from skysift.ui import explain
from skysift.ui.routes.api import sparkline_key, sparkline_paths
from tests.unit.test_ui_pages import make_seeded_app
from tests.unit.ui_seed import add_older_version


def test_queue_page_defaults_and_filters(settings, tmp_path):
    client, _app, ids, _launcher = make_seeded_app(settings, tmp_path)
    r = client.get("/candidates?mode=expert")                         # the table and its reason column are expert
    assert r.status_code == 200 and "kic-005689121" in r.text and "gaia-dr3-2101234567890123457" not in r.text
    assert f'data-href="/signals/{ids["candidate"]}' in r.text and "13.7695" in r.text
    r = client.get("/candidates?status=undetermined&mode=expert")
    assert "gaia-dr3-2101234567890123457" in r.text and "outage:vizier" in r.text
    r = client.get("/candidates?status=all&sort=period&dir=asc")
    first = r.text.index("gaia-dr3-2101234567890123458"); last = r.text.index("kic-005689121")
    assert first < last


def test_queue_rejects_unknown_query_parameters(settings, tmp_path):
    client, _app, ids, _launcher = make_seeded_app(settings, tmp_path)
    for qs in ("sort=evil", "status=nope", "dir=sideways"):
        r = client.get(f"/candidates?{qs}")
        assert r.status_code == 400 and "Something went wrong" in r.text
    assert client.get(f"/signals/{ids['candidate']}?sort=evil").status_code == 400


def test_signal_page_shows_ladder_metrics_links_and_plots_hook(settings, tmp_path):
    client, _app, ids, _launcher = make_seeded_app(settings, tmp_path)
    r = client.get(f"/signals/{ids['candidate']}?status=all&sort=period&dir=asc&mode=expert")   # raw rows, neighbours
    assert r.status_code == 200
    assert "rung1_detection" in r.text and "parity_sigma" in r.text and "0.20" in r.text    # rung 2 metric shown
    assert "season_depths" not in r.text or "raw" in r.text                                # dict metrics only in raw
    assert f'id="plots" data-signal="{ids["candidate"]}"' in r.text and "/static/plots.js" in r.text
    assert "exofop.ipac.caltech.edu" not in r.text and "archive.stsci.edu" in r.text and "gea.esac.esa.int" in r.text
    assert f'id="link-prev" href="/signals/{ids["approved"]}' in r.text
    assert f'id="link-next" href="/signals/{ids["stage2"]}' in r.text                      # "all" holds stage 2 too
    assert client.get("/signals/999999").status_code == 404


def test_signal_page_without_plotly_shows_notice(settings, tmp_path):
    client, app, ids, _launcher = make_seeded_app(settings, tmp_path)
    assert app.state.plotly_path is None
    r = client.get(f"/signals/{ids['candidate']}")
    assert "Plotly bundle could not be fetched" in r.text and "/static/vendor/plotly" not in r.text
    assert "/static/plots.js" in r.text
    app.state.plotly_path = tmp_path / "plotly.min.js"
    r = client.get(f"/signals/{ids['candidate']}")
    assert "Plotly bundle could not be fetched" not in r.text and 'id="plots"' in r.text
    assert r.text.index("/static/vendor/plotly.min.js") < r.text.index("/static/plots.js")


def test_decision_bar_records_and_requires_note_for_reject(settings, tmp_path):
    client, app, ids, _launcher = make_seeded_app(settings, tmp_path)
    sid = ids["candidate"]
    r = client.post(f"/signals/{sid}/decide", data={"decision": "reject", "note": "", "_token": app.state.token})
    assert r.status_code == 400 and "needs a note" in r.text
    r = client.post(f"/signals/{sid}/decide", data={"decision": "approve", "note": "clean", "_token": app.state.token})
    assert r.status_code == 200 and "approved" in r.text and "clean" in r.text and "<html" not in r.text
    assert client.post(f"/signals/{sid}/decide", data={"decision": "approve", "note": "x"}).status_code == 403
    r = client.post(f"/signals/{sid}/decide", data={"decision": "reject", "note": "changed my mind", "_token": app.state.token})
    assert r.text.count("history-row") == 2 and "rejected" in r.text
    assert "kic-005689121" not in client.get("/candidates").text                              # left the default queue
    assert "kic-005689121" in client.get("/candidates?status=rejected").text
    r = client.post("/signals/999999/decide", data={"decision": "approve", "note": "", "_token": app.state.token})
    assert r.status_code == 404
    r = client.post(f"/signals/{sid}/decide", data={"decision": "bogus", "note": "", "_token": app.state.token})
    assert r.status_code == 400


def test_approved_page_lists_each_signal_once(settings, tmp_path):
    client, app, ids, _launcher = make_seeded_app(settings, tmp_path)
    sid = ids["candidate"]
    for decision, note in (("reject", "first look"), ("approve", "second look"), ("approve", "confirmed")):
        client.post(f"/signals/{sid}/decide", data={"decision": decision, "note": note, "_token": app.state.token})
    r = client.get("/approved")
    # a card names its target once as its title (the picture's alt text and the copy text repeat it)
    assert r.status_code == 200 and r.text.count("<code>kic-005689121</code>") == 1 and "confirmed" in r.text
    assert "first look" not in r.text and "second look" not in r.text                         # superseded notes
    assert "kic-005689121" not in client.get("/approved?status=rejected").text
    assert "kic-005689121" not in client.get("/candidates?status=rejected").text
    assert f'href="/signals/{sid}/review.json"' in r.text


def test_approved_page_rejects_unknown_status(settings, tmp_path):
    client, _app, _ids, _launcher = make_seeded_app(settings, tmp_path)
    r = client.get("/approved?status=bogus")
    assert r.status_code == 400 and "Something went wrong" in r.text


def _strict(body: str):
    return json.loads(body, parse_constant=lambda c: pytest.fail(f"non-strict constant {c}"))


def test_review_json_download(settings, tmp_path):
    client, app, ids, _launcher = make_seeded_app(settings, tmp_path)
    sid = ids["candidate"]
    client.post(f"/signals/{sid}/decide", data={"decision": "approve", "note": "ok", "_token": app.state.token})
    with app.state.store.session() as s:           # the ORM sanitizes on write, so plant NaN/Infinity as raw JSON text
        rung = s.execute(text("SELECT id, rung FROM tests WHERE signal_id = :sid ORDER BY id"), {"sid": sid}).first()
        s.execute(text("UPDATE tests SET metrics = :m WHERE id = :id"),
                  {"m": '{"timing_rms_min": NaN, "wide": Infinity, "narrow": -Infinity, "ok": 1.5}', "id": rung.id})
        s.commit()
    r = client.get(f"/signals/{sid}/review.json")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    assert f'filename="kic-005689121_{sid}_review.json"' in r.headers["content-disposition"]
    doc = _strict(r.text)
    assert doc["signal"]["target_id"] == "kic-005689121" and doc["decisions"][0]["decision"] == "approve"
    assert doc["queries"] and doc["artifacts"] and len(doc["tests"]) == 4
    planted = next(t for t in doc["tests"] if t["rung"] == rung.rung)["metrics"]
    assert planted == {"timing_rms_min": None, "wide": None, "narrow": None, "ok": 1.5}
    assert client.get("/signals/999999/review.json").status_code == 404


def test_review_json_filename_is_sanitised(settings, tmp_path, monkeypatch):
    client, _app, ids, _launcher = make_seeded_app(settings, tmp_path)
    sid = ids["candidate"]
    evil = 'kic-1";x=y' + chr(13) + chr(10) + "z/../w"                                    # quote, ;, CR, LF, /
    monkeypatch.setattr("skysift.ui.queries.review_document",
                        lambda _store, _sid: {"signal": {"target_id": evil}})
    r = client.get(f"/signals/{sid}/review.json")
    assert r.status_code == 200
    assert r.headers["content-disposition"] == f'attachment; filename="kic-1xyz..w_{sid}_review.json"'


def test_signal_page_lists_the_removed_waves(settings, tmp_path):
    client, app, ids, _launcher = make_seeded_app(settings, tmp_path)
    comps = [{"frequency": 15.0479, "amplitude_ppm": 1100.0, "snr": 19000.0, "coef": [1.0, -0.0011, 0.0, 0.0, 0.0]},
             {"frequency": 13.4216, "amplitude_ppm": 600.0, "snr": 6800.0, "coef": [1.0, -0.0006, 0.0, 0.0, 0.0]}]
    extra = json.dumps({"baseline_days": 1400.0, "prewhitening": {"n": 2, "components": comps, "skipped": []}})
    with app.state.store.session() as s:
        s.execute(text("update signals set extra = :e where id = :i"), {"e": extra, "i": ids["candidate"]}); s.commit()
    r = client.get(f"/signals/{ids['candidate']}")
    assert r.status_code == 200 and "whitened: 2 waves removed (strongest P = 0.0665 d, 1,100 ppm)" in r.text
    assert "whitened" not in client.get(f"/signals/{ids['undetermined']}").text


def _set_metrics(app, signal_id, rung, metrics, status=None):
    with app.state.store.session() as s:
        s.execute(text("update tests set metrics = :m where signal_id = :i and rung = :r"),
                  {"m": json.dumps(metrics), "i": signal_id, "r": rung})
        if status: s.execute(text("update tests set status = :st where signal_id = :i and rung = :r"),
                             {"st": status, "i": signal_id, "r": rung})
        s.commit()


def _shown(html, rung):
    """The key metrics of one rung: its Why row up to the raw JSON (expert mode only)."""
    row = html[html.index(f'data-rung="{rung}"'):]
    return row[:row.index("<pre>")]


def test_rungs_2_and_3_show_whether_the_stacked_fold_answered(settings, tmp_path):
    client, app, ids, _launcher = make_seeded_app(settings, tmp_path)
    _set_metrics(app, ids["candidate"], "rung2_shape", {"stacked": True, "parity_sigma": 0.4, "n_events": 480})
    _set_metrics(app, ids["candidate"], "rung3_stability", {"stacked": False, "p_value": 0.9, "n_events": 99})
    r = client.get(f"/signals/{ids['candidate']}?mode=expert")
    assert r.status_code == 200
    assert '<span class="muted">stacked</span> yes' in _shown(r.text, "rung2_shape")
    assert '<span class="muted">stacked</span> no' in _shown(r.text, "rung3_stability")


def test_signal_page_shows_the_key_metrics_of_rung1_events(settings, tmp_path):
    """Ladder version 3's per-event rung has its key metrics in the cell, not only under raw (final review, finding 4)."""
    from skysift.store.db import TestRow
    client, app, ids, _launcher = make_seeded_app(settings, tmp_path)
    metrics = {"n_events_with_data": 102, "n_covered": 97, "n_good": 95, "ses_rules_applied": True,
               "ses_max_over_mes": 0.123, "chases_median": None, "coverage": None, "ses": None, "chases": None,
               "coverage_summary": {"n": 102, "min": 0.0, "median": 1.0, "below_min": 5},
               "ses_summary": {"n_finite": 99, "min": -0.5, "median": 3.1, "max": 6.2}}
    with app.state.store.session() as s:
        s.add(TestRow(signal_id=ids["candidate"], rung="rung1_events", status="pass", metrics=metrics)); s.commit()
    r = client.get(f"/signals/{ids['candidate']}?mode=expert")
    assert r.status_code == 200
    shown = _shown(r.text, "rung1_events")
    assert '<span class="muted">n_good</span> 95' in shown, shown
    assert '<span class="muted">n_covered</span> 97' in shown and "ses_max_over_mes" in shown


def test_rung4_shows_the_period_matched_object_and_its_separation(settings, tmp_path):
    client, app, ids, _launcher = make_seeded_app(settings, tmp_path)
    matched = [{"catalog": "cumulative", "id": "K77777.01", "period": 13.77, "sep_arcsec": 90.04}]
    _set_metrics(app, ids["candidate"], "rung4_novelty", {"cumulative": 1, "period_matched": matched}, status="fail")
    r = client.get(f"/signals/{ids['candidate']}?mode=expert")
    shown = _shown(r.text, "rung4_novelty")
    assert '<span class="muted">period_matched</span> cumulative K77777.01 at 90.0 arcsec' in shown
    without = [{**matched[0], "sep_arcsec": None}]
    _set_metrics(app, ids["candidate"], "rung4_novelty", {"cumulative": 1, "period_matched": without})
    shown = _shown(client.get(f"/signals/{ids['candidate']}?mode=expert").text, "rung4_novelty")
    assert '<span class="muted">period_matched</span> cumulative K77777.01, no position' in shown


def test_signal_header_says_when_the_period_was_multiplied(settings, tmp_path):
    client, app, ids, _launcher = make_seeded_app(settings, tmp_path)
    resolved = {"from_period": 3.44143, "factor": 7, "factors": [7], "class_depths_ppm": [960.0] + [5.0] * 6,
                "contrast_sigma": 40.0, "sde_original": 61.5, "sde_resolved": 58.0}
    legacy = {"from_period": 6.88475, "odd_ppm": 3600.0, "even_ppm": 10.0, "parity_sigma": 40.0, "sde_half": 21.1,
              "sde_double": 19.0}                                  # a version-1 record: always a doubling
    with app.state.store.session() as s:
        for key, ar in (("candidate", resolved), ("rejected", legacy)):
            extra = json.dumps({"baseline_days": 1400.0, "alias_resolved": ar})
            s.execute(text("update signals set extra = :e where id = :i"), {"e": extra, "i": ids[key]})
        s.commit()
    assert "period multiplied by 7 from 3.44143 d" in client.get(f"/signals/{ids['candidate']}").text
    assert "period multiplied by 2 from 6.88475 d" in client.get(f"/signals/{ids['rejected']}").text
    assert "period multiplied" not in client.get(f"/signals/{ids['undetermined']}").text


def test_queue_shows_the_current_search_version_by_default(settings, tmp_path):
    client, app, ids, _launcher = make_seeded_app(settings, tmp_path)
    old = add_older_version(app.state.store)                         # same target, earlier sweep, period 6.88475 d
    r = client.get("/candidates?mode=expert")                         # the table rows carry data-href
    assert r.status_code == 200 and "13.7695" in r.text and "6.8848" not in r.text
    assert f'data-href="/signals/{old}' not in r.text
    assert "Current search version only (1 older hidden)" in r.text
    assert 'href="/candidates?status=candidate_stage1&sort=sde&dir=desc&versions=all">show all</a>' in r.text
    assert f'data-href="/signals/{ids["candidate"]}?status=candidate_stage1&sort=sde&dir=desc&versions=current"' in r.text
    r = client.get("/candidates?versions=all&mode=expert")
    assert "13.7695" in r.text and "6.8848" in r.text and "All search versions" in r.text
    assert 'href="/candidates?status=candidate_stage1&sort=sde&dir=desc&versions=current">current only</a>' in r.text
    assert "older hidden" not in r.text
    assert f'data-href="/signals/{old}?status=candidate_stage1&sort=sde&dir=desc&versions=all"' in r.text
    assert 'href="/candidates?status=all&sort=sde&dir=desc&versions=all"' in r.text           # status links carry it
    assert client.get("/candidates?versions=some").status_code == 400


def test_signal_neighbours_stay_inside_the_search_version(settings, tmp_path):
    client, app, ids, _launcher = make_seeded_app(settings, tmp_path)
    old = add_older_version(app.state.store)                         # higher SDE: it precedes the candidate in 'all'
    r = client.get(f"/signals/{ids['candidate']}?mode=expert")
    assert f'href="/signals/{old}?' not in r.text and 'id="link-prev"' not in r.text
    assert 'href="/candidates?status=candidate_stage1&sort=sde&dir=desc&versions=current"' in r.text   # back to queue
    r = client.get(f"/signals/{ids['candidate']}?versions=all&mode=expert")
    amp = "&amp;"                                                    # the prev/next URLs are escaped expressions
    assert (f'id="link-prev" href="/signals/{old}?status=candidate_stage1{amp}sort=sde{amp}dir=desc{amp}versions=all"'
            in r.text)
    assert client.get(f"/signals/{ids['candidate']}?versions=some").status_code == 400


def test_signal_page_says_how_many_waves_were_put_back(settings, tmp_path):
    client, app, ids, _launcher = make_seeded_app(settings, tmp_path)
    comps = [{"frequency": 15.0479, "amplitude_ppm": 1100.0, "snr": 19000.0, "coef": [1.0, -0.0011, 0.0, 0.0, 0.0]}]
    restored = [{"frequency": 0.14526, "amplitude_ppm": 420.0}]
    with app.state.store.session() as s:
        for key, pw in (("candidate", {"n": 1, "components": comps, "skipped": [], "restored": restored}),
                        ("undetermined", {"n": 1, "components": comps, "skipped": []})):
            extra = json.dumps({"baseline_days": 1400.0, "prewhitening": pw})
            s.execute(text("update signals set extra = :e where id = :i"), {"e": extra, "i": ids[key]})
        s.commit()
    r = client.get(f"/signals/{ids['candidate']}")
    assert ("whitened: 1 wave removed (strongest P = 0.0665 d, 1,100 ppm) · 1 wave put back at the detection's "
            "harmonics") in r.text
    r = client.get(f"/signals/{ids['undetermined']}")
    assert "whitened: 1 wave removed" in r.text and "put back" not in r.text


def test_rung4_notes_whether_the_known_object_epoch_agrees(settings, tmp_path):
    client, app, ids, _launcher = make_seeded_app(settings, tmp_path)
    base = {"catalog": "cumulative", "id": "K77777.01", "period": 13.77, "sep_arcsec": 0.4}
    prefix = '<span class="muted">period_matched</span> cumulative K77777.01 at 0.4 arcsec'
    for extra, expected in (({"epoch_offset_h": 0.26, "epoch_agrees": True}, f"{prefix}, epoch agrees (offset 0.3 h)<"),
                            ({"epoch_offset_h": 122.04, "epoch_agrees": False}, f"{prefix}, epoch differs by 122.0 h<"),
                            ({"epoch_offset_h": None, "epoch_agrees": None}, f"{prefix}<"),
                            ({}, f"{prefix}<")):
        _set_metrics(app, ids["candidate"], "rung4_novelty", {"cumulative": 1, "period_matched": [{**base, **extra}]},
                     status="fail")
        shown = _shown(client.get(f"/signals/{ids['candidate']}?mode=expert").text, "rung4_novelty")
        assert expected in shown, (extra, shown)


def test_signal_page_says_the_true_period_may_be_half_the_detected_one(settings, tmp_path):
    client, app, ids, _launcher = make_seeded_app(settings, tmp_path)
    _set_metrics(app, ids["candidate"], "rung2_shape", {"parity_sigma": 0.2, "suggested_period": 6.88475},
                 status="undetermined")
    r = client.get(f"/signals/{ids['candidate']}")
    assert ("· the secondary matches the primary; the true period may be 6.8848 d (half the detected period)"
            in r.text)
    r = client.get(f"/signals/{ids['undetermined']}")
    assert "the true period may be" not in r.text


def test_signal_page_says_the_secondary_fits_a_planet_occultation(settings, tmp_path):
    client, app, ids, _launcher = make_seeded_app(settings, tmp_path)
    _set_metrics(app, ids["candidate"], "rung2_shape",
                 {"parity_sigma": 0.2, "occultation_albedo": 0.34, "occultation_exception": True})
    r = client.get(f"/signals/{ids['candidate']}")
    assert "· secondary consistent with a planet occultation (albedo 0.34)" in r.text
    r = client.get(f"/signals/{ids['undetermined']}")
    assert "planet occultation" not in r.text


def test_signal_page_keeps_quiet_when_the_occultation_exception_did_not_apply(settings, tmp_path):
    client, app, ids, _launcher = make_seeded_app(settings, tmp_path)
    _set_metrics(app, ids["candidate"], "rung2_shape",
                 {"parity_sigma": 0.2, "occultation_albedo": 3.4, "occultation_exception": False}, status="fail")
    r = client.get(f"/signals/{ids['candidate']}")
    assert r.status_code == 200 and "planet occultation" not in r.text


# ---- the verdict popup and the signal page (mission control, task 3)

@pytest.fixture
def seeded(settings, tmp_path):
    client, _app, ids, _launcher = make_seeded_app(settings, tmp_path)
    return client, ids


def token(client) -> str:
    return client.app.state.token


def test_verdict_popup_for_an_undetermined_signal_has_the_three_blocks(seeded):
    client, ids = seeded
    html = client.get(f"/partials/signals/{ids['undetermined']}/verdict").text
    assert "Undetermined: one check could not decide" in html
    assert "What was missing" in html and "What happens next" in html and "catalogue" in html.lower()
    assert html.count('class="why-row') == len(explain.LADDER_ORDER)
    assert "Stage 1 means" in html                                   # glossary footer
    assert 'id="dialog-title"' in html and "<html" not in html
    assert html.index("Undetermined means") < html.index("Stage 1 means")   # the signal's own word comes first


def test_verdict_popup_for_a_candidate_has_no_missing_block(seeded):
    client, ids = seeded
    html = client.get(f"/partials/signals/{ids['candidate']}/verdict").text
    assert "What was missing" not in html and "Candidate" in html and "stage 1" in html
    assert f'href="/signals/{ids["candidate"]}"' in html                    # the full page


def test_verdict_popup_for_a_signal_without_tests(seeded):
    client, ids = seeded
    html = client.get(f"/partials/signals/{ids['pending']}/verdict").text
    assert "Not vetted yet" in html


def test_verdict_popup_renders_for_every_role_in_both_modes(seeded):
    client, ids = seeded
    for role in ("candidate", "undetermined", "rejected", "legacy", "stage2", "approved", "pending"):
        for mode in ("simple", "expert"):
            r = client.get(f"/partials/signals/{ids[role]}/verdict?mode={mode}")
            assert r.status_code == 200 and r.text.count('class="why-row') == len(explain.LADDER_ORDER), (role, mode)
    expert = client.get(f"/partials/signals/{ids['candidate']}/verdict?mode=expert").text
    simple = client.get(f"/partials/signals/{ids['candidate']}/verdict?mode=simple").text
    assert "raw metrics" in expert and "raw metrics" not in simple
    assert "Rejected: failed the check &#34;Transit shape&#34;" in client.get(f"/partials/signals/{ids['rejected']}/verdict").text


def test_verdict_popup_unknown_signal_is_404(seeded):
    client, _ids = seeded
    assert client.get("/partials/signals/999999/verdict").status_code == 404


def test_signal_page_has_hero_why_plots_and_decision_in_both_modes(seeded):
    client, ids = seeded
    for mode in ("simple", "expert"):
        html = client.get(f"/signals/{ids['candidate']}?mode={mode}").text
        assert "A dip of" in html and 'class="pill' in html and 'id="why"' in html and 'id="plots"' in html
        assert "Copy summary" in html and "Approve" in html
        assert f'data-dialog-src="/partials/signals/{ids["candidate"]}/verdict"' in html
        assert f'data-mode="{mode}"' in html and "data-captions=" in html
        assert html.index('class="hero') < html.index('id="fold"') < html.index('id="why"') < html.index('id="plots"')
        assert html.index('id="plots"') < html.index('id="decision"')
        assert "Goes to the Approved list for the write-up" in html
    assert "raw" in client.get(f"/signals/{ids['candidate']}?mode=expert").text
    assert "raw" not in client.get(f"/signals/{ids['candidate']}?mode=simple").text.split('id="why"')[1].split('id="plots"')[0]


def test_signal_page_renders_a_legacy_signal_in_both_modes(seeded):
    client, ids = seeded
    for mode in ("simple", "expert"):
        assert client.get(f"/signals/{ids['legacy']}?mode={mode}").status_code == 200


def test_signal_page_renders_every_role_in_both_modes_and_themes(seeded):
    client, ids = seeded
    for role in ("candidate", "undetermined", "rejected", "legacy", "stage2", "approved", "pending"):
        for mode in ("simple", "expert"):
            for theme in ("dark", "light"):
                r = client.get(f"/signals/{ids[role]}?mode={mode}&theme={theme}")
                assert r.status_code == 200 and r.text.count('class="why-row') == len(explain.LADDER_ORDER), role
                assert "—" not in r.text                                                    # no em-dashes


def test_copy_summary_carries_the_plain_text_paragraph(seeded):
    client, ids = seeded
    html = client.get(f"/signals/{ids['candidate']}").text
    assert 'data-copy="kic-005689121 (KIC 5689121). A dip of 0.18 percent (1,800 ppm) repeats every 13.8 days' in html
    assert f"Details: http://127.0.0.1/signals/{ids['candidate']}" in html


def test_reject_requires_a_note_in_simple_mode(seeded):
    client, ids = seeded
    r = client.post(f"/signals/{ids['candidate']}/decide", data={"decision": "reject", "note": "", "_token": token(client)})
    assert r.status_code == 400 and "note" in r.text.lower()


def test_a_recorded_decision_sends_a_toast(seeded):
    client, ids = seeded
    r = client.post(f"/signals/{ids['candidate']}/decide", data={"decision": "approve", "note": "", "_token": token(client)})
    assert r.status_code == 200
    assert json.loads(r.headers["HX-Trigger"]) == {"toast": {"message": "Approved. It moves to the Approved page.",
                                                             "tone": "pass"}}
    r = client.post(f"/signals/{ids['candidate']}/decide", data={"decision": "reject", "note": "", "_token": token(client)})
    assert r.status_code == 400 and "HX-Trigger" not in r.headers                       # nothing recorded, no toast


def test_simple_why_shows_the_numbers_and_hides_the_engine_notes(seeded):
    client, ids = seeded
    pw = {"n": 1, "components": [{"frequency": 15.0479, "amplitude_ppm": 1100.0}], "restored": [{"frequency": 0.1}]}
    extra = json.dumps({"baseline_days": 1400.0, "prewhitening": pw, "alias_resolved": {"from_period": 6.88475}})
    with client.app.state.store.session() as s:
        s.execute(text("update signals set extra = :e where id = :i"), {"e": extra, "i": ids["candidate"]}); s.commit()
    html = client.get(f"/signals/{ids['candidate']}?mode=simple").text
    why = html.split('id="why"')[1].split('id="plots"')[0]
    assert "SDE 14.0, SNR 18.0, 99 transits" in why and 'class="why-numbers' in why
    notes = html[html.index('<span class="only-expert"> · whitened: 1 wave removed'):]
    notes = notes[:notes.index("</span>")]          # the engine notes sit in one expert-only span
    assert "put back at the detection" in notes and "period multiplied by 2 from 6.88475 d" in notes


# ---- candidate cards, sparklines and the Candidates page (mission control, task 4)

APPROVED = "gaia-dr3-2101234567890123461"


def _synthetic_curve(monkeypatch, period=13.7695, t0=136.3, duration=0.096, depth=1800):
    """The seed's raw files are fake bytes, not FITS: the parser hands back a synthetic curve with the transit
    injected at the seeded candidate's period and epoch, as the plots.json tests do."""
    from skysift.ui import plots as plots_mod
    from tests.synthetic import inject_transit, make_lightcurve
    lc = inject_transit(make_lightcurve(n_days=400, seed=3, noise_ppm=500), period, t0, duration, depth)
    monkeypatch.setitem(plots_mod.PARSERS, "kbonus_bkg", lambda b: lc)
    return lc


def _sparkline_file(client, settings, signal_id: int):
    """Where the route caches this signal's picture: its id and the key over its fold and rung 2 row."""
    from sqlalchemy import func, select

    from skysift.store.db import SignalRow, TestRow
    with client.app.state.store.session() as s:
        sig = s.get(SignalRow, signal_id)
        rung2 = s.scalar(select(func.max(TestRow.id)).where(TestRow.signal_id == signal_id,
                                                             TestRow.rung == "rung2_shape"))
        key = sparkline_key(sig.period, sig.t0, sig.duration, rung2)
    return settings.data_dir / "ui" / "sparklines" / f"{signal_id}-{key}.svg"


def _card(html: str, signal_id: int) -> str:
    """The card of one signal, from its article tag to its end."""
    img = html.index(f'src="/api/signals/{signal_id}/sparkline.svg"')
    start = html.rindex('<article class="card candidate', 0, img)
    return html[start:html.index("</article>", img)]


def test_sparkline_is_svg_and_cached(seeded, settings, monkeypatch):
    client, ids = seeded
    _synthetic_curve(monkeypatch)
    r = client.get(f"/api/signals/{ids['candidate']}/sparkline.svg")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml") and "<svg" in r.text
    assert r.headers["cache-control"] == "max-age=86400"
    assert 'viewBox="0 0 240 72"' in r.text and "<circle" in r.text and "#4fd1e8" in r.text and "#ffb454" in r.text
    assert "light curve not cached" not in r.text
    [cached] = sparkline_paths(settings.data_dir, ids["candidate"])
    assert cached.read_text(encoding="utf-8") == r.text


def test_sparkline_cache_hit_serves_the_file_without_loading(seeded, settings, monkeypatch):
    from skysift.ui import plots as plots_mod
    client, ids = seeded
    cached = _sparkline_file(client, settings, ids["candidate"])
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 72"><title>from disk</title></svg>',
                      encoding="utf-8")
    monkeypatch.setattr(plots_mod.LightCurveLoader, "load", lambda *a, **k: pytest.fail("the cache was not used"))
    r = client.get(f"/api/signals/{ids['candidate']}/sparkline.svg")
    assert r.status_code == 200 and "from disk" in r.text and r.headers["cache-control"] == "max-age=86400"
    assert r.headers["content-type"].startswith("image/svg+xml")


def test_sparkline_without_a_cached_curve_returns_a_placeholder(seeded, settings):
    client, ids = seeded
    r = client.get(f"/api/signals/{ids['rejected']}/sparkline.svg")        # the seed stores no raw file for it
    assert r.status_code == 200 and "light curve not cached" in r.text
    assert r.headers["content-type"].startswith("image/svg+xml") and r.headers["cache-control"] == "no-store"
    assert sparkline_paths(settings.data_dir, ids["rejected"]) == []                         # never cached


def test_sparkline_of_an_unreadable_curve_is_a_placeholder_and_unknown_ids_404(seeded, settings):
    client, ids = seeded
    r = client.get(f"/api/signals/{ids['candidate']}/sparkline.svg")      # fake bytes: the loader's IntegrityError
    assert r.status_code == 200 and "light curve not cached" in r.text and r.headers["cache-control"] == "no-store"
    assert sparkline_paths(settings.data_dir, ids["candidate"]) == []
    assert client.get("/api/signals/999999/sparkline.svg").status_code == 404


def test_sparkline_never_pins_or_fetches(seeded, monkeypatch):
    client, ids = seeded
    store = client.app.state.store
    monkeypatch.setattr(store, "pin_target_artifacts", lambda tid: pytest.fail("a list view pinned evidence"))
    client.app.state.source_factory = lambda *a, **k: pytest.fail("a list view fetched a light curve")
    _synthetic_curve(monkeypatch)
    assert client.get(f"/api/signals/{ids['candidate']}/sparkline.svg").status_code == 200
    assert "light curve not cached" in client.get(f"/api/signals/{ids['rejected']}/sparkline.svg").text


def test_candidates_simple_mode_renders_cards_with_pills_and_sparklines(seeded):
    client, ids = seeded
    html = client.get("/candidates?mode=simple").text
    assert 'class="card candidate' in html and "sparkline.svg" in html and 'data-dialog-src="/partials/signals/' in html
    assert "stage 1" in html
    card = _card(html, ids["candidate"])
    assert 'loading="lazy"' in card and 'alt="Folded transit of kic-005689121"' in card
    assert "A dip of 0.18 percent repeats every 13.8 days" in card                          # the summary sentence
    assert f'href="/signals/{ids["candidate"]}?status=candidate_stage1&sort=sde&dir=desc&versions=current"' in card
    assert f'data-dialog-src="/partials/signals/{ids["candidate"]}/verdict"' in card and ">Candidate</button>" in card
    assert "13.77" in card and "1,800" in card and "stage 1" in card and "<table" not in html


def test_candidates_expert_mode_renders_the_table_and_the_detail_slot(seeded):
    client, ids = seeded
    html = client.get("/candidates?mode=expert").text
    assert "<table" in html and 'id="detail"' in html and "hx-get=\"/partials/signals/" in html
    assert f'hx-get="/partials/signals/{ids["candidate"]}/detail"' in html and 'class="card candidate' not in html
    assert f'data-dialog-src="/partials/signals/{ids["candidate"]}/verdict"' in html          # the same pill


def test_candidates_chips_carry_counts_and_search_filters(seeded):
    client, _ids = seeded
    html = client.get("/candidates").text
    assert "undetermined" in html and "rejected" in html
    assert 'class="chip-count">1<' in html                              # every chip carries its count
    assert "kic-005689121" in client.get("/candidates?q=5689").text
    assert "kic-005689121" not in client.get("/candidates?q=zzz").text


def test_candidates_chips_are_every_status_in_order_with_their_counts(seeded):
    client, _ids = seeded
    html = client.get("/candidates?status=all&versions=all").text
    chips = html[html.index('class="chips"'):]
    chips = chips[:chips.index("</nav>")]
    counts = {"waiting": 3, "candidate_stage1": 3, "candidate_stage2": 1, "undetermined": 1, "rejected": 1,
              "superseded": 0, "approved": 1, "needs_data": 0, "all": 6}
    positions = []
    for status, n in counts.items():
        at = chips.index(f'href="/candidates?status={status}&sort=sde&dir=desc&versions=all"')
        positions.append(at)
        assert f'<span class="chip-count">{n}</span>' in chips[at:chips.index("</a>", at)], status
    assert positions == sorted(positions)


def test_candidates_search_is_case_insensitive_kept_in_links_and_literal(seeded):
    client, _ids = seeded
    html = client.get("/candidates?q=KIC-0056&status=all").text
    assert "kic-005689121" in html and "gaia-dr3-2101234567890123457" not in html
    assert 'name="q" value="KIC-0056"' in html and "&q=KIC-0056" in html                   # chips keep the search
    assert "kic-005689121" not in client.get("/candidates?q=kic_005&status=all").text      # _ is not a wildcard


def test_candidates_search_is_validated(seeded):
    client, _ids = seeded
    for q in ("<script>", "a" * 65, "x%", "kic'005"):
        r = client.get("/candidates", params={"q": q})
        assert r.status_code == 400 and "Something went wrong" in r.text and "<script>" not in r.text, q
    assert client.get("/candidates", params={"q": "a" * 64}).status_code == 200
    assert client.get("/candidates", params={"q": "kic 005.1:2_3-4"}).status_code == 200


def test_candidates_approved_chip_lists_only_decided_signals(seeded):
    client, _ids = seeded
    html = client.get("/candidates?status=approved")
    assert html.status_code == 200 and "Approved by reviewer" in html.text
    assert APPROVED in html.text and "kic-005689121" not in html.text


def test_candidates_needs_data_chip_follows_the_latest_decision(seeded):
    client, ids = seeded
    assert "kic-005689121" not in client.get("/candidates?status=needs_data").text
    client.post(f"/signals/{ids['candidate']}/decide", data={"decision": "needs_data", "note": "", "_token": token(client)})
    html = client.get("/candidates?status=needs_data").text
    assert "kic-005689121" in html and "Needs data" in html and APPROVED not in html
    client.post(f"/signals/{ids['candidate']}/decide", data={"decision": "approve", "note": "", "_token": token(client)})
    assert "kic-005689121" not in client.get("/candidates?status=needs_data").text
    assert "kic-005689121" in client.get("/candidates?status=approved").text


def test_candidates_render_every_status_in_both_modes_and_themes(seeded):
    from skysift.ui import queries
    client, ids = seeded
    client.post(f"/signals/{ids['undetermined']}/decide", data={"decision": "needs_data", "note": "", "_token": token(client)})
    for status in queries.QUEUE_STATUSES:
        for mode in ("simple", "expert"):
            for theme in ("dark", "light"):
                r = client.get(f"/candidates?status={status}&mode={mode}&theme={theme}&versions=all")
                assert r.status_code == 200, (status, mode, theme)
                assert "—" not in r.text and "style=" not in r.text and "discover" not in r.text.lower()


def test_candidates_empty_list_says_so(seeded):
    client, _ids = seeded
    for mode in ("simple", "expert"):
        html = client.get(f"/candidates?status=superseded&mode={mode}").text
        assert 'class="empty"' in html and "Nothing in this list" in html


def test_signal_detail_partial_has_the_hero_why_and_decision_bar(seeded):
    client, ids = seeded
    r = client.get(f"/partials/signals/{ids['undetermined']}/detail?mode=expert")
    assert r.status_code == 200 and "<html" not in r.text
    html = r.text
    assert "gaia-dr3-2101234567890123457" in html and "Undetermined" in html and "A dip of" in html
    assert html.count('class="why-row') == len(explain.LADDER_ORDER) and "What was missing" in html
    assert 'id="decision"' in html and 'id="btn-approve"' in html
    assert f'hx-post="/signals/{ids["undetermined"]}/decide"' in html
    assert f'href="/signals/{ids["undetermined"]}"' in html and 'id="plots"' not in html and "plots.js" not in html
    assert client.get("/partials/signals/999999/detail").status_code == 404


def test_queue_rows_link_to_the_signal_page_and_load_the_detail(seeded):
    client, ids = seeded
    html = client.get("/candidates?mode=expert&q=5689").text
    row = html[html.index('<tr class="row-link'):]
    row = row[:row.index("</tr>")]
    qs = "status=candidate_stage1&sort=sde&dir=desc&versions=current&q=5689"
    assert f'data-href="/signals/{ids["candidate"]}?{qs}"' in row and f'data-id="{ids["candidate"]}"' in row
    assert f'hx-get="/partials/signals/{ids["candidate"]}/detail"' in row and 'hx-target="#detail"' in row
    page = client.get(f"/signals/{ids['candidate']}?{qs}").text                     # the search survives the page
    assert f'href="/candidates?{qs}"' in page


def test_a_leftover_sparkline_file_never_answers_for_an_unknown_signal(seeded, settings):
    client, _ids = seeded
    stray = settings.data_dir / "ui" / "sparklines" / "999999-deadbeef0000.svg"     # the shape the route reads
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_text("<svg/>", encoding="utf-8")
    assert client.get("/api/signals/999999/sparkline.svg").status_code == 404


def test_sparkline_cache_writes_race_safely_and_leave_no_temp_files(tmp_path, monkeypatch):
    import threading

    from skysift.ui.routes import api
    path = tmp_path / "ui" / "sparklines" / "7.svg"
    errors = []

    def write(i):
        try:
            for _ in range(20):
                api._cache(path, f"<svg>{i}</svg>")
        except Exception as e:          # noqa: BLE001 - any failure is the finding
            errors.append(e)
    threads = [threading.Thread(target=write, args=(i,)) for i in range(4)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert errors == [] and path.read_text(encoding="utf-8").startswith("<svg>")
    assert [f.name for f in path.parent.iterdir()] == ["7.svg"]
    other = path.with_name("8.svg")

    def refuse(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(api.os, "replace", refuse)
    with pytest.raises(OSError, match="disk full"):
        api._cache(other, "<svg/>")
    assert [f.name for f in path.parent.iterdir()] == ["7.svg"]


def test_a_non_finite_fit_model_falls_back_to_the_box(monkeypatch):
    import numpy as np

    from skysift.ui import sparkline
    from tests.synthetic import inject_transit, make_lightcurve
    lc = inject_transit(make_lightcurve(n_days=400, seed=3, noise_ppm=500), 13.7695, 136.3, 0.096, 1800)
    sig = {"period": 13.7695, "t0": 136.3, "duration": 0.096, "depth_ppm": 1800.0, "extra": {}}
    tests = [{"rung": "rung2_shape", "metrics": {"fit_k": 0.042, "fit_b": 0.3}}]
    assert sparkline.fold_points(lc, sig, tests)["model_kind"] == "fit"
    monkeypatch.setattr(sparkline, "uniform_disc_flux", lambda dt, *a: np.full(np.size(dt), np.nan))
    pts = sparkline.fold_points(lc, sig, tests)
    assert pts["model_kind"] == "box" and "nan" not in sparkline.render_svg(pts, 1800.0).lower()


# ---- accessibility: the dialog, the status words and the page structure (mission control, task 7)

def test_dialog_and_controls_are_labelled(seeded):
    client, ids = seeded
    html = client.get(f"/partials/signals/{ids['undetermined']}/verdict").text
    # the shared <dialog> lives in base.html and the partial fills it, so the shell and the partial are read together
    shell = client.get(f"/signals/{ids['undetermined']}").text
    dialog = shell.split("<dialog")[1].split("</dialog>")[0]
    both = dialog + html
    assert 'role="dialog"' in both and 'aria-labelledby=' in both and 'aria-label="Close"' in both
    assert 'aria-labelledby="dialog-title"' in dialog and 'id="dialog-title"' in html
    home = client.get("/").text
    # the Tonight status is a polite live region; decorative SVG icons are hidden (the starfield is a ::before layer,
    # outside the accessibility tree, and needs no aria-hidden)
    assert 'aria-live="polite"' in home and 'aria-hidden="true"' in home
    # the drawer names the dialog by its own heading: app.js points aria-labelledby at the loaded [data-dialog-focus]
    form = client.get("/partials/sweep-form?source=kbonus_bkg").text
    assert 'id="sweep-title"' in form and "data-dialog-focus" in form
    js = client.get("/static/app.js").text
    assert 'setAttribute("aria-labelledby"' in js


def test_status_is_never_colour_alone(seeded):
    client, _ids = seeded
    html = client.get("/candidates?mode=expert").text
    import re
    found = list(re.finditer(r'class="pill pill-(pass|fail|undetermined|muted)[^>]*>(.*?)</', html))
    assert found
    for m in found:
        assert m.group(2).strip()
    for status in ("all", "rejected", "undetermined"):
        simple = client.get(f"/candidates?status={status}&mode=simple").text
        for m in re.finditer(r'class="pill pill-(?:pass|fail|undetermined|running|muted)[^>]*>(.*?)</', simple):
            assert m.group(1).strip(), status


class _Structure:
    """The facts the structure test needs from one page, read with the standard library's HTML parser."""

    def __init__(self, html: str):
        from html.parser import HTMLParser

        self.h1 = 0
        self.imgs_without_alt: list[str] = []
        self.controls: list[dict] = []          # every visible form control and how it is labelled
        self.label_for: set[str] = set()
        self.skip_to_main = False
        self.main_id = False
        facts = self
        label_depth = [0]

        class Parser(HTMLParser):
            def handle_starttag(self, tag, attrs):
                a = dict(attrs)
                if tag == "h1":
                    facts.h1 += 1
                elif tag == "img" and "alt" not in a:
                    facts.imgs_without_alt.append(a.get("src", "?"))
                elif tag == "label":
                    label_depth[0] += 1
                    if a.get("for"):
                        facts.label_for.add(a["for"])
                elif tag in ("input", "select", "textarea") and a.get("type") not in ("hidden", "submit", "button"):
                    facts.controls.append({"tag": tag, "name": a.get("name"), "id": a.get("id"),
                                           "wrapped": label_depth[0] > 0,
                                           "aria": bool(a.get("aria-label") or a.get("aria-labelledby"))})
                elif tag == "a" and a.get("href") == "#main" and "skip" in (a.get("class") or ""):
                    facts.skip_to_main = True
                elif tag == "main" and a.get("id") == "main":
                    facts.main_id = True

            def handle_endtag(self, tag):
                if tag == "label":
                    label_depth[0] -= 1

        Parser().feed(html)

    def unlabelled(self) -> list:
        return [c for c in self.controls
                if not (c["wrapped"] or c["aria"] or (c["id"] and c["id"] in self.label_for))]


def test_every_page_has_one_h1_alt_text_labelled_controls_and_the_skip_link(seeded):
    client, ids = seeded
    pages = ["/", "/candidates", "/candidates?status=all", f"/signals/{ids['candidate']}",
             f"/signals/{ids['undetermined']}", f"/signals/{ids['stage2']}", "/live", "/sources",
             "/sources?start=kbonus_bkg", "/approved", "/about"]
    for path in pages:
        for mode in ("simple", "expert"):
            sep = "&" if "?" in path else "?"
            r = client.get(f"{path}{sep}mode={mode}")
            assert r.status_code == 200, (path, mode)
            page = _Structure(r.text)
            assert page.h1 == 1, (path, mode, page.h1)
            assert page.imgs_without_alt == [], (path, mode)
            assert page.unlabelled() == [], (path, mode, page.unlabelled())
            assert page.skip_to_main and page.main_id, (path, mode)
    for partial in (f"/partials/signals/{ids['undetermined']}/verdict", "/partials/sweep-form?source=kbonus_bkg",
                    f"/partials/signals/{ids['candidate']}/detail"):
        part = _Structure(client.get(partial).text)
        assert part.h1 == 0 and part.imgs_without_alt == [] and part.unlabelled() == [], partial


def test_keyboard_reaches_every_queue_row_and_a_refused_reject_lands_in_the_note(seeded):
    client, ids = seeded
    html = client.get("/candidates?mode=expert&status=all").text
    for role in ("candidate", "undetermined", "rejected"):                 # each row has a real link, not only j/k
        assert f'<a class="row-name" href="/signals/{ids[role]}?' in html, role
    assert 'aria-sort="descending"' in html and html.count("aria-sort=") == 1
    r = client.post(f"/signals/{ids['candidate']}/decide",
                    data={"decision": "reject", "note": "", "_token": token(client)}, headers={"HX-Request": "true"})
    assert r.status_code == 400
    assert 'aria-describedby="decision-error" aria-invalid="true"' in r.text and 'id="decision-error"' in r.text
    assert 'aria-invalid' not in client.get(f"/signals/{ids['candidate']}").text
    js = client.get("/static/app.js").text
    after = js[js.index('"htmx:afterSwap"'):]
    assert 'xhr.status !== 400' in after and 'textarea[name=note]' in after and "note.focus()" in after
    assert "'a[href], button, summary, input" in js                           # the trap knows disclosures
    # a plain click on the row's link at split-view width loads the detail instead of leaving the page
    handler = js[js.index('const row = e.target.closest("tr.row-link[data-href]")'):]
    handler = handler[:handler.index("});")]
    assert "e.button === 0" in handler and "e.ctrlKey || e.metaKey || e.shiftKey || e.altKey" in handler
    assert 'e.target.closest("a.row-name") && detail && wide.matches' in handler
    assert handler.index("e.preventDefault(); openRow(row)") < handler.index('!e.target.closest("a, button, input")')
    css = client.get("/static/app.css").text
    assert ".row-name { color: inherit; text-decoration: none; }" in css
    assert ".row-name:hover, .row-name:focus-visible { text-decoration: underline; }" in css


def test_the_sky_never_sits_behind_text_and_wide_tables_show_their_edge(settings):
    from tests.unit.test_ui_pages import make_client
    client, _app = make_client(settings)
    css = client.get("/static/app.css").text
    sky = css[css.index("body::before {"):]
    assert "z-index: -1" in sky.split("}")[0] and "pointer-events: none" in sky.split("}")[0]
    backing = css[css.index(":where(.page, .footer) :where("):]
    assert "{ background-color: var(--bg); }" in backing.split("}")[0] + "}"
    assert "background-color: transparent" in backing[:backing.index("h1, h2, h3 { margin")]
    wrap = css[css.index(".table-wrap {"):].split("}")[0]
    assert "local" in wrap and "scroll" in wrap and "overflow-x: auto" in wrap
    # the bottom sheet and the side drawer are sized by the viewport box, never 100vw (which runs under a scrollbar)
    assert ".dialog:has(.drawer) { width: 100%" in css and "min(480px, 100%)" in css and "100vw; max-width: 100vw" not in css
    assert "box-shadow: inset 0 2px 0 var(--ion)" not in css                 # hairlines on fractional nav cells


def test_split_view_keys_act_on_the_selected_row_only(seeded):
    """j and k carry the detail pane with them, and a, r and n click nothing until #detail shows the selected row: "j,
    then a" can never approve the signal that was open before (served app.js, read as text: there is no JS harness)."""
    client, ids = seeded
    js = client.get("/static/app.js").text
    step = js[js.index("const step = (by) =>"):]
    step = step[:step.index("};")]
    assert "select(row);" in step and "if (detail && wide.matches) openRow(row);" in step
    assert step.index("select(row);") < step.index("openRow(row)")
    guard = js[js.index("const detailMatchesSelection = () =>"):]
    guard = guard[:guard.index("};")]
    assert 'detail.querySelector("[data-signal]")' in guard and "shown.dataset.signal === row.dataset.id" in guard
    keys = js[js.index('document.addEventListener("keydown"'):]
    keys = keys[:keys.index("});")]
    assert 'if (queue && ["a", "r", "n"].includes(e.key) && !detailMatchesSelection()) return;' in keys
    assert keys.index("detailMatchesSelection()") < keys.index("el.click()")
    # the detail partial names its signal, and a newer row load replaces one still in flight
    detail = client.get(f"/partials/signals/{ids['candidate']}/detail").text
    assert f'<article class="detail" data-signal="{ids["candidate"]}">' in detail
    queue = client.get("/candidates?mode=expert").text
    row = queue[queue.index('<tr class="row-link'):]
    assert 'hx-sync="#queue:replace"' in row[:row.index(">")]
    # Enter on a focused row link follows the link (a keyboard click reports e.detail 0); only a mouse click splits
    handler = js[js.index('const row = e.target.closest("tr.row-link[data-href]")'):]
    assert "plain && e.detail > 0 && e.target.closest(\"a.row-name\")" in handler[:handler.index("});")]


def test_a_recorded_decision_moves_focus_to_its_new_state(seeded):
    client, ids = seeded
    r = client.post(f"/signals/{ids['candidate']}/decide",
                    data={"decision": "approve", "note": "", "_token": token(client)}, headers={"HX-Request": "true"})
    assert '<div class="decision-state" role="status" tabindex="-1">' in r.text
    js = client.get("/static/app.js").text
    after = js[js.index('"htmx:afterSwap"'):]
    after = after[:after.index("});")]
    assert 'querySelector(".decision-state")' in after and "state.focus()" in after


def test_plot_marks_follow_the_spec_colours(seeded):
    """Spec 3, "Marks": the data in ion, the faint points in the secondary text colour, the model in solar, as the
    card's sparkline draws them."""
    from skysift.ui import plots
    assert plots.MARKS == {"data": "--ion", "faint": "--fg-2", "model": "--solar", "grid": "--line", "text": "--fg-2"}
    client, ids = seeded
    html = client.get(f"/signals/{ids['candidate']}").text
    marks = html.split("data-marks='")[1].split("'")[0]
    assert '"data": "--ion"' in marks and '"faint": "--fg-2"' in marks and '"model": "--solar"' in marks


def test_a_cached_sparkline_is_keyed_by_what_it_draws(seeded, settings, monkeypatch):
    """A picture cached for one fold never answers for another (a swapped database, a recheck at a new period)."""
    from skysift.store.db import SignalRow
    client, ids = seeded
    cached = _sparkline_file(client, settings, ids["candidate"])
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text('<svg xmlns="http://www.w3.org/2000/svg"><title>old fold</title></svg>', encoding="utf-8")
    assert "old fold" in client.get(f"/api/signals/{ids['candidate']}/sparkline.svg").text
    assert sparkline_paths(settings.data_dir, ids["candidate"]) == [cached]
    with client.app.state.store.session() as s:
        s.get(SignalRow, ids["candidate"]).period = 6.88475
    r = client.get(f"/api/signals/{ids['candidate']}/sparkline.svg")
    # the seed's raw bytes are fake, so a miss answers with the placeholder
    assert "old fold" not in r.text and r.headers["cache-control"] == "no-store"


def test_an_aborted_detail_load_is_no_failure(seeded):
    """hx-sync aborts a detail load a newer j, k or click replaced, and htmx 2 reports the abort as an unsuccessful
    afterRequest: only the latest load may toast, select its row or change the URL."""
    client, _ids = seeded
    js = client.get("/static/app.js").text
    queue = js[js.index("let latestLoad = null;"):]
    queue = queue[:queue.index("const step = (by) =>")]
    assert 'addEventListener("htmx:beforeRequest"' in queue and "latestLoad = e.detail.xhr" in queue
    after = queue[queue.index('"htmx:afterRequest"'):]
    assert "if (e.detail.xhr !== latestLoad) return;" in after
    assert after.index("e.detail.xhr !== latestLoad") < after.index("Could not load the details")
    assert after.index("e.detail.xhr !== latestLoad") < after.index("select(row)")


def test_chart_titles_use_the_text_colour(seeded):
    client, _ids = seeded
    js = client.get("/static/plots.js").text
    title = js[js.index("title: { ...template.title"):].split("\n")[0]
    assert "color: c.text" in title and "c.data" not in title
