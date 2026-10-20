import hashlib

from fastapi.testclient import TestClient

from skysift.net import ServiceUnavailable
from skysift.store.blob import blob_from_url
from skysift.store.db import Store
from skysift.ui import assets
from skysift.ui.app import create_app
from skysift.ui.assets import ensure_plotly

HERE_STATIC = assets.STATIC


def test_htmx_is_vendored():
    js = (HERE_STATIC / "htmx.min.js").read_text(encoding="utf-8")
    assert "htmx" in js and len(js) > 20_000


def test_ensure_plotly_downloads_verifies_and_caches(tmp_path, monkeypatch):
    body = b"/* plotly stub */ window.Plotly = {};"
    monkeypatch.setattr(assets, "PLOTLY_SHA256", hashlib.sha256(body).hexdigest())
    calls = []

    def fetch(url, *, service, **kw):
        calls.append(url); return body
    path = ensure_plotly(tmp_path, fetch=fetch)
    assert path == tmp_path / "ui" / "assets" / "plotly.min.js" and path.read_bytes() == body
    assert ensure_plotly(tmp_path, fetch=fetch) == path and calls == [assets.PLOTLY_URL]      # cached, one download


def test_ensure_plotly_rejects_bad_hash_and_survives_offline(tmp_path, monkeypatch):
    monkeypatch.setattr(assets, "PLOTLY_SHA256", "0" * 64)
    calls = []

    def tampered(url, *, service, **kw):
        calls.append(url); return b"tampered"
    assert ensure_plotly(tmp_path, fetch=tampered) is None
    assert len(calls) == 1 and not (tmp_path / "ui").exists()

    def offline(url, *, service, **kw): raise ServiceUnavailable("no network")
    assert ensure_plotly(tmp_path, fetch=offline) is None
    assert not (tmp_path / "ui").exists()


def test_ensure_plotly_survives_write_failure_and_leaves_no_part_file(tmp_path, monkeypatch):
    body = b"/* plotly stub */ window.Plotly = {};"
    monkeypatch.setattr(assets, "PLOTLY_SHA256", hashlib.sha256(body).hexdigest())
    fetch = lambda url, *, service, **kw: body

    def boom(src, dst): raise PermissionError("antivirus holds the file")
    monkeypatch.setattr(assets.os, "replace", boom)
    assert ensure_plotly(tmp_path, fetch=fetch) is None
    folder = tmp_path / "ui" / "assets"
    assert not (folder / "plotly.min.js").exists() and not list(folder.glob("*.part"))
    monkeypatch.undo()
    monkeypatch.setattr(assets, "PLOTLY_SHA256", hashlib.sha256(body).hexdigest())

    def no_write(self, data): raise OSError("disk full")
    monkeypatch.setattr(assets.Path, "write_bytes", no_write)
    assert ensure_plotly(tmp_path, fetch=fetch) is None
    assert not (folder / "plotly.min.js").exists() and not list(folder.glob("*.part"))


def test_ensure_plotly_removes_stale_part_files(tmp_path, monkeypatch):
    body = b"/* plotly stub */ window.Plotly = {};"
    monkeypatch.setattr(assets, "PLOTLY_SHA256", hashlib.sha256(body).hexdigest())
    folder = tmp_path / "ui" / "assets"; folder.mkdir(parents=True)
    (folder / "plotly.min.99999.part").write_bytes(b"half a download")
    path = ensure_plotly(tmp_path, fetch=lambda url, *, service, **kw: body)
    assert path is not None and not list(folder.glob("*.part"))


def test_ensure_plotly_replaces_a_corrupt_cache(tmp_path, monkeypatch):
    body = b"/* plotly stub */ window.Plotly = {};"
    monkeypatch.setattr(assets, "PLOTLY_SHA256", hashlib.sha256(body).hexdigest())
    target = tmp_path / "ui" / "assets" / "plotly.min.js"
    target.parent.mkdir(parents=True); target.write_bytes(b"junk")
    calls = []

    def fetch(url, *, service, **kw):
        calls.append(url); return body
    assert ensure_plotly(tmp_path, fetch=fetch) == target
    assert target.read_bytes() == body and calls == [assets.PLOTLY_URL]
    # a corrupt cache with no network: discarded, never served
    target.write_bytes(b"junk")

    def offline(url, *, service, **kw): raise ServiceUnavailable("no network")
    assert ensure_plotly(tmp_path, fetch=offline) is None and not target.exists()


def test_ensure_plotly_never_raises_even_when_the_data_dir_is_unusable(tmp_path, monkeypatch):
    def no_access(self): raise PermissionError("data dir denied")
    monkeypatch.setattr(assets.Path, "exists", no_access)
    assert ensure_plotly(tmp_path, fetch=lambda url, *, service, **kw: b"x") is None
    monkeypatch.undo()
    blocker = tmp_path / "not-a-dir"; blocker.write_bytes(b"")     # ui/assets cannot be created under a file
    body = b"/* plotly stub */ window.Plotly = {};"
    monkeypatch.setattr(assets, "PLOTLY_SHA256", hashlib.sha256(body).hexdigest())
    assert ensure_plotly(blocker, fetch=lambda url, *, service, **kw: body) is None


def test_vendor_mount_serves_plotly_and_static_still_serves_htmx(settings, tmp_path):
    """Pins the mount order in create_app: /static/vendor must come before /static."""
    plotly = tmp_path / "plotly.min.js"
    plotly.write_bytes(b"/* plotly stub */ window.Plotly = {};")
    store = Store(settings.database_url); store.init_schema()
    app = create_app(settings, store=store, blob=blob_from_url(settings.blob_url), plotly_path=plotly)
    client = TestClient(app, base_url="http://127.0.0.1")
    r = client.get("/static/vendor/plotly.min.js")
    assert r.status_code == 200 and r.content == plotly.read_bytes()
    r = client.get("/static/htmx.min.js")
    assert r.status_code == 200 and r.content == (HERE_STATIC / "htmx.min.js").read_bytes()


def test_vendored_fonts_match_their_recorded_hashes():
    fonts = HERE_STATIC / "fonts"
    assert set(assets.FONT_SHA256) == {p.name for p in fonts.glob("*.woff2")}
    for name, sha in assets.FONT_SHA256.items():
        data = (fonts / name).read_bytes()
        assert data[:4] == b"wOF2" and hashlib.sha256(data).hexdigest() == sha, name
        assert sha in (fonts / "LICENSES.md").read_text(encoding="utf-8"), name
    assert sum((fonts / n).stat().st_size for n in assets.FONT_SHA256) < 600_000
