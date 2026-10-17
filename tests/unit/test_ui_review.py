import json

import pytest
from sqlalchemy import text

from tests.unit.test_ui_pages import make_seeded_client


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
    assert f'id="link-prev" href="/signals/{ids["undetermined"]}' in r.text and 'id="link-next"' not in r.text
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


def test_signal_header_says_when_the_period_was_doubled(settings, tmp_path):
    client, app, ids, _launcher = make_seeded_client(settings, tmp_path)
    extra = json.dumps({"baseline_days": 1400.0, "alias_resolved": {"from_period": 6.88475, "odd_ppm": 3600.0,
                                                                    "even_ppm": 10.0, "parity_sigma": 40.0,
                                                                    "sde_half": 21.1, "sde_double": 19.0}})
    with app.state.store.session() as s:
        s.execute(text("update signals set extra = :e where id = :i"), {"e": extra, "i": ids["candidate"]}); s.commit()
    assert "period doubled from 6.88475 d" in client.get(f"/signals/{ids['candidate']}").text
    assert "period doubled" not in client.get(f"/signals/{ids['undetermined']}").text
