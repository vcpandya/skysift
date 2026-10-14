from fastapi import Request
from fastapi.testclient import TestClient

from skysift.store.blob import blob_from_url
from skysift.store.db import Store
from skysift.ui.app import create_app

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
