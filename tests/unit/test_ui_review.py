import json

import pytest
from sqlalchemy import text

from tests.unit.test_ui_pages import make_seeded_client
from tests.unit.ui_seed import add_older_version


def test_queue_page_defaults_and_filters(settings, tmp_path):
    client, _app, ids, _launcher = make_seeded_client(settings, tmp_path)
    r = client.get("/candidates")
    assert r.status_code == 200 and "kic-005689121" in r.text and "gaia-dr3-2101234567890123457" not in r.text
    assert f'data-href="/signals/{ids["candidate"]}' in r.text and "13.7695" in r.text
    r = client.get("/candidates?status=undetermined")
    assert "gaia-dr3-2101234567890123457" in r.text and "outage:vizier" in r.text
    r = client.get("/candidates?status=all&sort=period&dir=asc")
    first = r.text.index("gaia-dr3-2101234567890123458"); last = r.text.index("kic-005689121")
    assert first < last


def test_queue_rejects_unknown_query_parameters(settings, tmp_path):
    client, _app, ids, _launcher = make_seeded_client(settings, tmp_path)
    for qs in ("sort=evil", "status=nope", "dir=sideways"):
        r = client.get(f"/candidates?{qs}")
        assert r.status_code == 400 and "Something went wrong" in r.text
    assert client.get(f"/signals/{ids['candidate']}?sort=evil").status_code == 400


def test_signal_page_shows_ladder_metrics_links_and_plots_hook(settings, tmp_path):
    client, _app, ids, _launcher = make_seeded_client(settings, tmp_path)
    r = client.get(f"/signals/{ids['candidate']}?status=all&sort=period&dir=asc")
    assert r.status_code == 200
    assert "rung1_detection" in r.text and "parity_sigma" in r.text and "0.20" in r.text    # rung 2 metric shown
    assert "season_depths" not in r.text or "raw" in r.text                                # dict metrics only in raw
    assert f'id="plots" data-signal="{ids["candidate"]}"' in r.text and "/static/plots.js" in r.text
    assert "exofop.ipac.caltech.edu" not in r.text and "archive.stsci.edu" in r.text and "gea.esac.esa.int" in r.text
    assert f'id="link-prev" href="/signals/{ids["approved"]}' in r.text and 'id="link-next"' not in r.text
    assert client.get("/signals/999999").status_code == 404


def test_signal_page_without_plotly_shows_notice(settings, tmp_path):
    client, app, ids, _launcher = make_seeded_client(settings, tmp_path)
    assert app.state.plotly_path is None
    r = client.get(f"/signals/{ids['candidate']}")
    assert "Plotly bundle could not be fetched" in r.text and "/static/vendor/plotly" not in r.text
    assert "/static/plots.js" in r.text
    app.state.plotly_path = tmp_path / "plotly.min.js"
    r = client.get(f"/signals/{ids['candidate']}")
    assert "Plotly bundle could not be fetched" not in r.text and 'id="plots"' in r.text
    assert r.text.index("/static/vendor/plotly.min.js") < r.text.index("/static/plots.js")


def test_decision_bar_records_and_requires_note_for_reject(settings, tmp_path):
    client, app, ids, _launcher = make_seeded_client(settings, tmp_path)
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
    client, app, ids, _launcher = make_seeded_client(settings, tmp_path)
    sid = ids["candidate"]
    for decision, note in (("reject", "first look"), ("approve", "second look"), ("approve", "confirmed")):
        client.post(f"/signals/{sid}/decide", data={"decision": decision, "note": note, "_token": app.state.token})
    r = client.get("/approved")
    assert r.status_code == 200 and r.text.count("kic-005689121") == 1 and "confirmed" in r.text
    assert "first look" not in r.text and "second look" not in r.text                         # superseded notes
    assert "kic-005689121" not in client.get("/approved?status=rejected").text
    assert "kic-005689121" not in client.get("/candidates?status=rejected").text
    assert f'href="/signals/{sid}/review.json"' in r.text


def test_approved_page_rejects_unknown_status(settings, tmp_path):
    client, _app, _ids, _launcher = make_seeded_client(settings, tmp_path)
    r = client.get("/approved?status=bogus")
    assert r.status_code == 400 and "Something went wrong" in r.text


def _strict(body: str):
    return json.loads(body, parse_constant=lambda c: pytest.fail(f"non-strict constant {c}"))


def test_review_json_download(settings, tmp_path):
    client, app, ids, _launcher = make_seeded_client(settings, tmp_path)
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
    client, _app, ids, _launcher = make_seeded_client(settings, tmp_path)
    sid = ids["candidate"]
    evil = 'kic-1";x=y' + chr(13) + chr(10) + "z/../w"                                    # quote, ;, CR, LF, /
    monkeypatch.setattr("skysift.ui.queries.review_document",
                        lambda _store, _sid: {"signal": {"target_id": evil}})
    r = client.get(f"/signals/{sid}/review.json")
    assert r.status_code == 200
    assert r.headers["content-disposition"] == f'attachment; filename="kic-1xyz..w_{sid}_review.json"'


def test_signal_page_lists_the_removed_waves(settings, tmp_path):
    client, app, ids, _launcher = make_seeded_client(settings, tmp_path)
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
    """The key-metrics cell of one ladder row."""
    row = html[html.index(f"<tr><td>{rung}</td>"):]
    return row[:row.index("<details")]


def test_rungs_2_and_3_show_whether_the_stacked_fold_answered(settings, tmp_path):
    client, app, ids, _launcher = make_seeded_client(settings, tmp_path)
    _set_metrics(app, ids["candidate"], "rung2_shape", {"stacked": True, "parity_sigma": 0.4, "n_events": 480})
    _set_metrics(app, ids["candidate"], "rung3_stability", {"stacked": False, "p_value": 0.9, "n_events": 99})
    r = client.get(f"/signals/{ids['candidate']}")
    assert r.status_code == 200
    assert '<span class="muted">stacked</span> yes' in _shown(r.text, "rung2_shape")
    assert '<span class="muted">stacked</span> no' in _shown(r.text, "rung3_stability")


def test_signal_page_shows_the_key_metrics_of_rung1_events(settings, tmp_path):
    """Ladder version 3's per-event rung has its key metrics in the cell, not only under raw (final review, finding 4)."""
    from skysift.store.db import TestRow
    client, app, ids, _launcher = make_seeded_client(settings, tmp_path)
    metrics = {"n_events_with_data": 102, "n_covered": 97, "n_good": 95, "ses_rules_applied": True,
               "ses_max_over_mes": 0.123, "chases_median": None, "coverage": None, "ses": None, "chases": None,
               "coverage_summary": {"n": 102, "min": 0.0, "median": 1.0, "below_min": 5},
               "ses_summary": {"n_finite": 99, "min": -0.5, "median": 3.1, "max": 6.2}}
    with app.state.store.session() as s:
        s.add(TestRow(signal_id=ids["candidate"], rung="rung1_events", status="pass", metrics=metrics)); s.commit()
    r = client.get(f"/signals/{ids['candidate']}")
    assert r.status_code == 200
    shown = _shown(r.text, "rung1_events")
    assert '<span class="muted">n_good</span> 95' in shown, shown
    assert '<span class="muted">n_covered</span> 97' in shown and "ses_max_over_mes" in shown


def test_rung4_shows_the_period_matched_object_and_its_separation(settings, tmp_path):
    client, app, ids, _launcher = make_seeded_client(settings, tmp_path)
    matched = [{"catalog": "cumulative", "id": "K77777.01", "period": 13.77, "sep_arcsec": 90.04}]
    _set_metrics(app, ids["candidate"], "rung4_novelty", {"cumulative": 1, "period_matched": matched}, status="fail")
    r = client.get(f"/signals/{ids['candidate']}")
    shown = _shown(r.text, "rung4_novelty")
    assert '<span class="muted">period_matched</span> cumulative K77777.01 at 90.0 arcsec' in shown
    without = [{**matched[0], "sep_arcsec": None}]
    _set_metrics(app, ids["candidate"], "rung4_novelty", {"cumulative": 1, "period_matched": without})
    shown = _shown(client.get(f"/signals/{ids['candidate']}").text, "rung4_novelty")
    assert '<span class="muted">period_matched</span> cumulative K77777.01, no position' in shown


def test_signal_header_says_when_the_period_was_multiplied(settings, tmp_path):
    client, app, ids, _launcher = make_seeded_client(settings, tmp_path)
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
    client, app, ids, _launcher = make_seeded_client(settings, tmp_path)
    old = add_older_version(app.state.store)                         # same target, earlier sweep, period 6.88475 d
    r = client.get("/candidates")
    assert r.status_code == 200 and "13.7695" in r.text and "6.8848" not in r.text
    assert f'data-href="/signals/{old}' not in r.text
    assert "Current search version only (1 older hidden)" in r.text
    assert 'href="/candidates?status=candidate_stage1&sort=sde&dir=desc&versions=all">show all</a>' in r.text
    assert f'data-href="/signals/{ids["candidate"]}?status=candidate_stage1&sort=sde&dir=desc&versions=current"' in r.text
    r = client.get("/candidates?versions=all")
    assert "13.7695" in r.text and "6.8848" in r.text and "All search versions" in r.text
    assert 'href="/candidates?status=candidate_stage1&sort=sde&dir=desc&versions=current">current only</a>' in r.text
    assert "older hidden" not in r.text
    assert f'data-href="/signals/{old}?status=candidate_stage1&sort=sde&dir=desc&versions=all"' in r.text
    assert 'href="/candidates?status=all&sort=sde&dir=desc&versions=all"' in r.text           # status links carry it
    assert client.get("/candidates?versions=some").status_code == 400


def test_signal_neighbours_stay_inside_the_search_version(settings, tmp_path):
    client, app, ids, _launcher = make_seeded_client(settings, tmp_path)
    old = add_older_version(app.state.store)                         # higher SDE: it precedes the candidate in 'all'
    r = client.get(f"/signals/{ids['candidate']}")
    assert f'href="/signals/{old}?' not in r.text and 'id="link-prev"' not in r.text
    assert 'href="/candidates?status=candidate_stage1&sort=sde&dir=desc&versions=current"' in r.text   # back to queue
    r = client.get(f"/signals/{ids['candidate']}?versions=all")
    amp = "&amp;"                                                    # the prev/next URLs are escaped expressions
    assert (f'id="link-prev" href="/signals/{old}?status=candidate_stage1{amp}sort=sde{amp}dir=desc{amp}versions=all"'
            in r.text)
    assert client.get(f"/signals/{ids['candidate']}?versions=some").status_code == 400


def test_signal_page_says_how_many_waves_were_put_back(settings, tmp_path):
    client, app, ids, _launcher = make_seeded_client(settings, tmp_path)
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
    client, app, ids, _launcher = make_seeded_client(settings, tmp_path)
    base = {"catalog": "cumulative", "id": "K77777.01", "period": 13.77, "sep_arcsec": 0.4}
    prefix = '<span class="muted">period_matched</span> cumulative K77777.01 at 0.4 arcsec'
    for extra, expected in (({"epoch_offset_h": 0.26, "epoch_agrees": True}, f"{prefix}, epoch agrees (offset 0.3 h)<"),
                            ({"epoch_offset_h": 122.04, "epoch_agrees": False}, f"{prefix}, epoch differs by 122.0 h<"),
                            ({"epoch_offset_h": None, "epoch_agrees": None}, f"{prefix}<"),
                            ({}, f"{prefix}<")):
        _set_metrics(app, ids["candidate"], "rung4_novelty", {"cumulative": 1, "period_matched": [{**base, **extra}]},
                     status="fail")
        shown = _shown(client.get(f"/signals/{ids['candidate']}").text, "rung4_novelty")
        assert expected in shown, (extra, shown)


def test_signal_page_says_the_true_period_may_be_half_the_detected_one(settings, tmp_path):
    client, app, ids, _launcher = make_seeded_client(settings, tmp_path)
    _set_metrics(app, ids["candidate"], "rung2_shape", {"parity_sigma": 0.2, "suggested_period": 6.88475},
                 status="undetermined")
    r = client.get(f"/signals/{ids['candidate']}")
    assert ("· the secondary matches the primary; the true period may be 6.8848 d (half the detected period)"
            in r.text)
    r = client.get(f"/signals/{ids['undetermined']}")
    assert "the true period may be" not in r.text


def test_signal_page_says_the_secondary_fits_a_planet_occultation(settings, tmp_path):
    client, app, ids, _launcher = make_seeded_client(settings, tmp_path)
    _set_metrics(app, ids["candidate"], "rung2_shape",
                 {"parity_sigma": 0.2, "occultation_albedo": 0.34, "occultation_exception": True})
    r = client.get(f"/signals/{ids['candidate']}")
    assert "· secondary consistent with a planet occultation (albedo 0.34)" in r.text
    r = client.get(f"/signals/{ids['undetermined']}")
    assert "planet occultation" not in r.text


def test_signal_page_keeps_quiet_when_the_occultation_exception_did_not_apply(settings, tmp_path):
    client, app, ids, _launcher = make_seeded_client(settings, tmp_path)
    _set_metrics(app, ids["candidate"], "rung2_shape",
                 {"parity_sigma": 0.2, "occultation_albedo": 3.4, "occultation_exception": False}, status="fail")
    r = client.get(f"/signals/{ids['candidate']}")
    assert r.status_code == 200 and "planet occultation" not in r.text
