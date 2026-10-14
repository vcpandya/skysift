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
        assert client.get("/").text.count("Start sweep") == 0 or "running" in client.get("/").text
        r2 = client.post("/sweeps", data={"source": "kbonus_bkg", "_token": app.state.token}, follow_redirects=False)
        assert r2.status_code == 409 and "already running" in r2.text
        assert client.post("/sweeps/stop", data={"_token": app.state.token}, follow_redirects=False).status_code == 303
        assert launcher.active() is None
    finally:
        launcher.stop()                                   # never leave the SLEEPER child behind if an assertion fails


def test_start_sweep_rejects_bad_window(settings, tmp_path):
    client, app, _ids, launcher = make_seeded_client(settings, tmp_path)
    r = client.post("/sweeps", data={"source": "kbonus_bkg", "min_period": "40", "max_period": "5", "_token": app.state.token})
    assert r.status_code == 400 and "period window" in r.text and launcher.active() is None
