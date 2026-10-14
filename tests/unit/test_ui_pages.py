import sys

from fastapi import Request
from fastapi.testclient import TestClient

from skysift.store.blob import blob_from_url
from skysift.store.db import Store
from skysift.ui.app import create_app
from skysift.ui.sweeps import SweepLauncher
from tests.unit.ui_seed import seed

BASE = "http://127.0.0.1"   # the trusted-host check refuses TestClient's default host, "testserver"


def make_client(settings):
    store = Store(settings.database_url); store.init_schema()
    app = create_app(settings, store=store, blob=blob_from_url(settings.blob_url))
    return TestClient(app, base_url=BASE), app


def test_healthz_and_token(settings):
    client, app = make_client(settings)
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert len(app.state.token) >= 32


def test_error_page_is_plain_html_without_traceback(settings):
    _client, app = make_client(settings)

    @app.get("/boom")
    def boom():
        raise RuntimeError("secret internals")
    r = TestClient(app, base_url=BASE, raise_server_exceptions=False).get("/boom")
    assert r.status_code == 500 and "RuntimeError" in r.text and "Traceback" not in r.text


def test_post_without_token_is_refused(settings):
    client, app = make_client(settings)

    from fastapi import Depends

    from skysift.ui.app import require_token

    @app.post("/guarded", dependencies=[Depends(require_token)])
    def guarded():
        return {"ok": True}
    assert client.post("/guarded").status_code == 403
    assert client.post("/guarded", headers={"X-SkySift-Token": app.state.token}).status_code == 200
    assert client.post("/guarded", data={"_token": app.state.token}).status_code == 200


def test_foreign_host_header_is_refused(settings):
    client, _app = make_client(settings)
    assert client.get("/healthz", headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/healthz", headers={"Host": "evil.example:8765"}).status_code == 400
    for host in ("[::1]:8765", "localhost:8765", "127.0.0.1:8765"):
        assert client.get("/healthz", headers={"Host": host}).status_code == 200


def test_openapi_schema_is_not_served(settings):
    client, _app = make_client(settings)
    assert client.get("/openapi.json").status_code == 404


def test_rendered_page_masks_the_database_password(settings, tmp_path, monkeypatch):
    from skysift.config import load_settings
    monkeypatch.setenv("SKYSIFT_DATABASE_URL", "postgresql://u:secret@h/db")
    pg_settings = load_settings()
    store = Store(settings.database_url); store.init_schema()      # a real sqlite store: nothing connects to h
    app = create_app(pg_settings, store=store, blob=blob_from_url(settings.blob_url))
    from skysift.ui.app import render

    @app.get("/page")
    def page(request: Request):
        return render(request, "error.html", error="x", log_hint="y")
    r = TestClient(app, base_url=BASE).get("/page")
    assert r.status_code == 200
    assert "secret" not in r.text and "postgresql://u:***@h/db" in r.text


SLEEPER = [sys.executable, "-c", "import time; print('started', flush=True); time.sleep(20)"]


def make_seeded_client(settings, tmp_path, command=SLEEPER):
    store = Store(settings.database_url); store.init_schema()
    blob = blob_from_url(settings.blob_url)
    ids = seed(store, blob)
    launcher = SweepLauncher(tmp_path, command=command)
    app = create_app(settings, store=store, blob=blob, launcher=launcher)
    return TestClient(app, base_url=BASE), app, ids, launcher


def test_sources_page_lists_registry_and_progress(settings, tmp_path):
    client, _app, _ids, _launcher = make_seeded_client(settings, tmp_path)
    r = client.get("/")
    assert r.status_code == 200
    assert "kbonus_bkg" in r.text and "CPU h/10k" in r.text and "694" in r.text      # 250 s/star -> 694 CPU h per 10k
    assert "3 done" in r.text and "planned" in r.text and "Start sweep" in r.text


def test_start_sweep_form_launches_child_and_redirects(settings, tmp_path):
    client, app, _ids, launcher = make_seeded_client(settings, tmp_path)
    try:
        form = client.get("/partials/sweep-form?source=kbonus_bkg")
        assert form.status_code == 200 and 'name="store_periodogram"' in form.text and 'value="all" selected' in form.text
        r = client.post("/sweeps", data={"source": "kbonus_bkg", "limit": "25", "min_period": "0.5", "max_period": "200",
                                        "retention_mode": "budget", "budget_gib": "20", "workers_io": "4", "workers_cpu": "2",
                                        "background_only": "on", "gmag_max": "19", "store_periodogram": "all",
                                        "_token": app.state.token}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/live"
        active = launcher.active()
        assert active["request"]["retention"] == "budget:20" and active["request"]["limit"] == 25
        page = client.get("/").text
        assert page.count("Start sweep") == 0 and "is running" in page and "kbonus_bkg" in page
        r2 = client.post("/sweeps", data={"source": "kbonus_bkg", "_token": app.state.token}, follow_redirects=False)
        assert r2.status_code == 409 and "already running" in r2.text
        assert client.post("/sweeps/stop", data={"_token": app.state.token}, follow_redirects=False).status_code == 303
        assert launcher.active() is None
    finally:
        launcher.stop()                                   # never leave the SLEEPER child behind if an assertion fails


def test_start_sweep_rejects_bad_window(settings, tmp_path):
    client, app, _ids, launcher = make_seeded_client(settings, tmp_path)
    try:
        r = client.post("/sweeps", data={"source": "kbonus_bkg", "min_period": "40", "max_period": "5",
                                        "_token": app.state.token})
        assert r.status_code == 400 and "period window" in r.text and launcher.active() is None
    finally:
        launcher.stop()


def test_htmx_posts_get_hx_redirect_not_a_303(settings, tmp_path):
    client, app, _ids, launcher = make_seeded_client(settings, tmp_path)
    hx = {"HX-Request": "true", "X-SkySift-Token": app.state.token}
    try:
        r = client.post("/sweeps", data={"source": "kbonus_bkg", "limit": "5"}, headers=hx, follow_redirects=False)
        assert r.status_code == 200 and r.headers["HX-Redirect"] == "/live" and launcher.active() is not None
        r = client.post("/sweeps/stop", headers=hx, follow_redirects=False)
        assert r.status_code == 200 and r.headers["HX-Redirect"] == "/live" and launcher.active() is None
    finally:
        launcher.stop()


def test_start_sweep_rejects_malformed_values(settings, tmp_path):
    client, app, _ids, launcher = make_seeded_client(settings, tmp_path)
    try:
        for field, value in (("limit", "abc"), ("limit", "-4"), ("min_period", "x"), ("gmag_max", "nan"),
                             ("workers_io", "0"), ("budget_gib", "1e999"), ("budget_gib", "-3"), ("budget_gib", "zz")):
            data = {"source": "kbonus_bkg", "retention_mode": "budget", "_token": app.state.token, field: value}
            r = client.post("/sweeps", data=data)
            assert r.status_code == 400, (field, value, r.status_code)
            assert field in r.text, (field, value)
            assert launcher.active() is None, (field, value)
    finally:
        launcher.stop()


def test_start_sweep_refuses_sources_that_are_not_live(settings, tmp_path):
    client, app, _ids, launcher = make_seeded_client(settings, tmp_path)
    try:
        for name in ("tess_spoc", "nonexistent"):
            r = client.post("/sweeps", data={"source": name, "_token": app.state.token})
            assert r.status_code == 400 and f"source {name} is not live" in r.text and launcher.active() is None
    finally:
        launcher.stop()


def test_sweep_posts_need_the_token(settings, tmp_path):
    client, _app, _ids, launcher = make_seeded_client(settings, tmp_path)
    try:
        assert client.post("/sweeps", data={"source": "kbonus_bkg"}).status_code == 403
        assert client.post("/sweeps/stop").status_code == 403
        assert launcher.active() is None
    finally:
        launcher.stop()


def test_form_echo_is_html_escaped(settings, tmp_path):
    client, app, _ids, launcher = make_seeded_client(settings, tmp_path)
    try:
        evil = '"><script>alert(1)</script>'
        r = client.post("/sweeps", data={"source": evil, "limit": evil, "_token": app.state.token})
        assert r.status_code == 400 and "<script>alert" not in r.text and "&lt;script&gt;" in r.text
    finally:
        launcher.stop()


def test_live_page_cards_and_idle_state(settings, tmp_path):
    client, _app, _ids, _launcher = make_seeded_client(settings, tmp_path)
    r = client.get("/live")
    assert r.status_code == 200 and "rung4_novelty" in r.text and "idle" in r.text
    cards = client.get("/partials/live/cards")
    assert cards.status_code == 200 and "candidate_stage1" in cards.text and "<html" not in cards.text
    assert 'hx-trigger="every 5s"' not in client.get("/partials/live/sweep").text      # no polling while idle


def test_live_page_shows_active_sweep_and_tail(settings, tmp_path):
    import time
    client, app, _ids, launcher = make_seeded_client(settings, tmp_path)
    try:
        r = client.post("/sweeps", data={"source": "kbonus_bkg", "_token": app.state.token}, follow_redirects=False)
        assert r.status_code == 303
        for _ in range(50):
            if launcher.tail(): break
            time.sleep(0.1)
        r = client.get("/partials/live/sweep")
        assert "started" in r.text and "Stop" in r.text and 'hx-trigger="every 5s"' in r.text
        assert "stopping" not in r.text
        launcher.stop()
        r = client.get("/partials/live/sweep")
        assert "idle" in r.text and r.headers.get("HX-Trigger") == "sweep-finished"
        assert "HX-Trigger" not in client.get("/partials/live/sweep").headers        # only on the transition
    finally:
        launcher.stop()


def test_live_sweep_shows_stopping_state_without_second_stop_button(settings, tmp_path, monkeypatch):
    client, app, _ids, launcher = make_seeded_client(settings, tmp_path)
    try:
        client.post("/sweeps", data={"source": "kbonus_bkg", "_token": app.state.token}, follow_redirects=False)
        assert launcher.active() is not None
        monkeypatch.setattr(launcher, "stop", lambda: False)                  # the child is slow to exit
        r = client.post("/sweeps/stop", data={"_token": app.state.token}, follow_redirects=False)
        assert r.status_code == 303 and app.state.stop_requested == (launcher.active()["pid"], launcher.active()["stamp"])
        r = client.get("/partials/live/sweep")
        assert "stopping, waiting for the sweep to exit" in r.text and "<button" not in r.text
        assert 'hx-trigger="every 5s"' in r.text                              # keep polling until it is gone
        monkeypatch.setattr(launcher, "stop", lambda: True)
        client.post("/sweeps/stop", data={"_token": app.state.token}, follow_redirects=False)
        assert app.state.stop_requested is None
        assert "Stop" in client.get("/partials/live/sweep").text
    finally:
        monkeypatch.undo()
        launcher.stop()


def test_live_idle_shows_past_sweeps_with_engine_summary(settings, tmp_path):
    client, app, _ids, launcher = make_seeded_client(
        settings, tmp_path, command=[sys.executable, "-c", "import json; print(json.dumps({'processed': 7, 'signals': 3}))"])
    client.post("/sweeps", data={"source": "kbonus_bkg", "_token": app.state.token}, follow_redirects=False)
    import time
    for _ in range(100):
        if launcher.active() is None: break
        time.sleep(0.1)
    r = client.get("/partials/live/sweep")
    assert "Past sweeps" in r.text and "processed 7" in r.text and "signals 3" in r.text


def test_live_survives_a_reattached_lock_without_started_or_request(settings, tmp_path):
    import json
    import os

    from skysift.ui.sweeps import _process_created
    client, _app, _ids, launcher = make_seeded_client(settings, tmp_path)
    try:
        launcher.lock.write_text(json.dumps({"pid": os.getpid(), "created": _process_created(os.getpid()),
                                             "log": str(tmp_path / "x.log"), "stamp": "20260101T000000000000Z"}),
                                 encoding="utf-8")                      # a live pid (this test process), nothing else
        for url in ("/live", "/partials/live/sweep"):
            r = client.get(url)
            assert r.status_code == 200 and "running" in r.text, url
        for started in (None, "not a date", 12, "2026-01-01T00:00:00"):     # null, malformed, wrong type, naive
            launcher.lock.write_text(json.dumps({"pid": os.getpid(), "created": _process_created(os.getpid()),
                                                 "log": str(tmp_path / "x.log"), "stamp": "20260101T000000000000Z",
                                                 "started": started, "request": "garbage"}), encoding="utf-8")
            r = client.get("/partials/live/sweep")
            assert r.status_code == 200 and "running" in r.text, started
    finally:
        launcher.lock.unlink(missing_ok=True)


def test_sweep_finished_header_is_sent_to_every_client_that_saw_it_running(settings, tmp_path):
    client, app, _ids, launcher = make_seeded_client(settings, tmp_path)
    try:
        client.post("/sweeps", data={"source": "kbonus_bkg", "_token": app.state.token}, follow_redirects=False)
        assert 'hx-vals=\'{"was": 1}\'' in client.get("/partials/live/sweep").text
        launcher.stop()
        for _ in range(2):                                   # two tabs: neither may lose the signal to the other
            r = client.get("/partials/live/sweep?was=1")
            assert "idle" in r.text and r.headers.get("HX-Trigger") == "sweep-finished"
        assert "HX-Trigger" not in client.get("/partials/live/sweep").headers      # idle -> idle, no was
        assert "HX-Trigger" not in client.get("/partials/live/sweep?was=0").headers
    finally:
        launcher.stop()


def test_provisional_lock_is_not_shown_as_stopping(settings, tmp_path):
    import json
    from datetime import UTC, datetime
    client, app, _ids, launcher = make_seeded_client(settings, tmp_path)
    try:
        launcher.lock.write_text(json.dumps({"pid": None, "provisional": True, "stamp": "20260101T000000000000Z",
                                             "log": str(tmp_path / "x.log"), "started": datetime.now(UTC).isoformat(),
                                             "request": {"source": "kbonus_bkg", "limit": 5}}), encoding="utf-8")
        app.state.stop_requested = (None, "20260101T000000000000Z")
        r = client.get("/partials/live/sweep")
        assert r.status_code == 200 and "stopping" not in r.text and "running" in r.text
    finally:
        launcher.lock.unlink(missing_ok=True)


def test_live_tail_is_entity_escaped(settings, tmp_path):
    import time
    cmd = [sys.executable, "-c", "import time; print('<script>alert(1)</script>', flush=True); time.sleep(20)"]
    client, app, _ids, launcher = make_seeded_client(settings, tmp_path, command=cmd)
    try:
        client.post("/sweeps", data={"source": "kbonus_bkg", "_token": app.state.token}, follow_redirects=False)
        for _ in range(50):
            if launcher.tail(): break
            time.sleep(0.1)
        r = client.get("/partials/live/sweep")
        assert "&lt;script&gt;alert(1)" in r.text and "<script>alert" not in r.text
    finally:
        launcher.stop()


def test_live_idle_renders_a_corrupt_lock_history_record(settings, tmp_path):
    import json
    client, _app, _ids, launcher = make_seeded_client(settings, tmp_path)
    (launcher.dir / "20260101T000000000000Z.json").write_text(json.dumps(
        {"stamp": "20260101T000000000000Z", "log": "", "request": {}, "reason": "corrupt-lock", "exit_code": None,
         "finished": "2026-01-01T00:00:01+00:00", "summary": None}), encoding="utf-8")
    (launcher.dir / "20260102T000000000000Z.json").write_text(json.dumps(
        {"stamp": "20260102T000000000000Z", "log": "", "reason": "corrupt-lock"}), encoding="utf-8")   # no request at all
    for url in ("/live", "/partials/live/sweep"):
        r = client.get(url)
        assert r.status_code == 200 and "Past sweeps" in r.text, url
