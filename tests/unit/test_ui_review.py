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
    assert "kic-005689121" not in client.get("/approved?status=rejected").text
    assert "kic-005689121" not in client.get("/candidates?status=rejected").text
    assert f'href="/signals/{sid}/review.json"' in r.text


def test_approved_page_rejects_unknown_status(settings, tmp_path):
    client, _app, _ids, _launcher = make_seeded_client(settings, tmp_path)
    r = client.get("/approved?status=bogus")
    assert r.status_code == 400 and "Something went wrong" in r.text


def test_review_json_download(settings, tmp_path):
    client, app, ids, _launcher = make_seeded_client(settings, tmp_path)
    sid = ids["candidate"]
    client.post(f"/signals/{sid}/decide", data={"decision": "approve", "note": "ok", "_token": app.state.token})
    r = client.get(f"/signals/{sid}/review.json")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"] and "NaN" not in r.text
    assert f'filename="kic-005689121_{sid}_review.json"' in r.headers["content-disposition"]
    doc = r.json()
    assert doc["signal"]["target_id"] == "kic-005689121" and doc["decisions"][0]["decision"] == "approve"
    assert doc["queries"] and doc["artifacts"] and len(doc["tests"]) == 4
    assert client.get("/signals/999999/review.json").status_code == 404
