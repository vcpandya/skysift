# SkySift M2 Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local web dashboard (`python -m skysift ui`) that shows the source registry with yield and cost, starts and watches a sweep, presents every candidate with its ladder verdicts, periodogram and folded light curves, records approve/reject decisions in an append-only log, and lists approved candidates; plus the engine-side periodogram summary it plots.

**Architecture:** A `ui/` layer (FastAPI + Jinja2 + HTMX + Plotly) that reads the same SQLite/Postgres store and blob the engine writes and changes nothing scientific. Reads go through `ui/queries.py` (plain dicts, short sessions); the only writes are `Store.decide` (one transaction, appends a `decisions` row) and the sweep launcher (a child `python -m skysift run`). Engine changes: a `PeriodogramSummary` (per-band SDE envelope, 2,048 log bins) produced by `BoxDip.search`, stored per target as a small `.npz` through the blob and referenced from the run row; a `migrations` module for the three schema additions; `target_id` on query rows; an artifact `kind` so stream retention keeps summaries.

**Tech Stack:** Python 3.12, FastAPI ≥0.115, uvicorn ≥0.30, Jinja2 ≥3.1, python-multipart, HTMX 2.0.x (vendored), Plotly 2.35.x (downloaded once, SHA-256 verified), SQLAlchemy 2, pydantic 2, typer, numpy, pytest + FastAPI TestClient (httpx).

**Spec:** `docs/superpowers/specs/2026-10-14-skysift-dashboard-design.md` (parent spec `docs/superpowers/specs/2026-10-13-skysift-design.md`, section 13 amendments). The M1 ledger `docs/superpowers/plans/2026-10-13-skysift-m1-core-ledger.md` records every ruling the code already embodies.

## Global Constraints

- Python 3.12, src layout, package `skysift`; new extra `ui = ["fastapi>=0.115", "uvicorn>=0.30", "jinja2>=3.1", "python-multipart>=0.0.9"]`; the container installs `.[s3,pg,ui]`.
- **Never run a console `.exe` from `.venv/Scripts`** (Norton CyberCapture froze this machine). Every command is `.venv/Scripts/python.exe -m <module>`: `python -m pytest`, `python -m ruff`, `python -m skysift`. Install packages with `uv pip install --native-tls -p .venv/Scripts/python.exe -e ".[dev,ui]"`. Never `docker build` here. The sweep launcher spawns `sys.executable -m skysift run`, never `skysift.exe`.
- The dashboard binds to loopback only (`127.0.0.1`, `localhost`, `::1`); any other `--host` is refused with a message pointing to SSH tunnelling.
- No CDN at view time: HTMX is vendored in `src/skysift/ui/static/`, Plotly is downloaded once into `<data_dir>/ui/assets` with SHA-256 verification; without it the plot panels show a plain message and the tables work.
- Nothing in the UI deletes records or files, edits thresholds, runs or tests. Decisions are appended to `decisions`; the candidate row holds only the current state.
- Every HTTP request the engine makes is logged to `queries` (now with `target_id`); periodogram envelopes are stored for every target by default (`store_periodogram: all`), `signals` or `off` by setting or per sweep.
- Strict JSON everywhere (`skysift.jsonutil.dumps`); no `NaN` tokens reach the browser.
- Lint: `ruff check src tests` clean (ruff 0.17 defaults plus `E701`, `E702`, `E731` ignored; line length 120). Tests: `python -m pytest -q -m "not golden and not slow"` must stay green (about 5 minutes; the detector tests run real BLS). Do not run `-m golden` or `slow` in a task unless the task says so.
- Commit messages are conventional (`feat(ui): ...`, `fix(store): ...`) and end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Work on branch `feat/m2-dashboard` off `main` (07901bc or later).
- Prose written into templates or docs is plain declarative English; no em-dashes.

## Review Focus

1. **A target whose raw file was released by stream retention** opens in the review page: the plots must come from a hash-verified re-fetch that pins the row, and an outage must show a message in the plot panel while the ladder table still renders. Pinned by Task 10's `test_loader_refetches_released_file_and_verifies_sha` and `test_plots_endpoint_reports_outage_without_breaking_the_page`.
2. **A dashboard opened against a milestone 1 database** (created before this plan) must migrate in place and render every page. Pinned by Task 2's `test_migrate_milestone1_database_in_place` and Task 13's `test_pages_render_on_a_migrated_m1_database`.
3. **A sweep started from the UI while the previous one is still running, or after the UI restarted** must be refused or re-attached, never doubled. Pinned by Task 6's `test_second_start_refused_while_active` and `test_stale_lock_from_dead_pid_is_removed`.
4. **A signal rejected, then approved again** must appear once in the approved list, never return to the queue as rejected, and show both decisions in its history. Pinned by Task 3's `test_revisit_appends_history_and_moves_state` and Task 12's `test_approved_page_lists_each_signal_once`.
5. **A periodogram envelope for a window where some log bins hold no trial period** (short baselines, narrow windows) must store NaN gaps, round-trip through the `.npz`, and plot without `NaN` tokens in the JSON. Pinned by Task 5's `test_envelope_has_gaps_for_empty_bins_and_round_trips` and Task 10's `test_plots_json_is_strict`.

---

## File structure

| Path | Responsibility |
|---|---|
| `src/skysift/store/migrations.py` | Ordered, idempotent schema steps with a `schema_version` table; step 001 adds `decisions`, `runs.periodogram_sha256`, `candidates.decided_at`, `queries.target_id`, `artifacts.kind`. |
| `src/skysift/store/db.py` | New `DecisionRow`; new columns on `RunRow`, `CandidateRow`, `QueryRow`, `ArtifactRow`; `Store.decide`; `record_results(..., periodogram=...)`; `init_schema` runs migrations; `unpinned_artifacts(kind=...)`. |
| `src/skysift/store/retention.py` | `release_target` deletes only `kind == "raw"` files. |
| `src/skysift/detectors/periodogram.py` | `PeriodogramSummary` model: envelope from a spectrum, `to_npz`/`from_npz`, bin centres. |
| `src/skysift/detectors/box_dip.py` | `search()` returning `SearchResult(signals, periodogram, spectrum)`; `detect()` delegates. |
| `src/skysift/config.py` | `Settings.store_periodogram` (`all`/`signals`/`off`). |
| `src/skysift/pipeline.py` | Worker payload gains `periodogram`; `_write_results(store, blob, ...)` stores the `.npz`; `store_periodogram` and `spectrum_path` plumbing. |
| `src/skysift/sources/factory.py` | `make_source(name, store, blob)` shared by CLI and UI (moved from `cli._source`). |
| `src/skysift/cli.py` | `run --periodogram`, `vet --spectrum`, `status` via `queries.summary`, new `ui` command. |
| `src/skysift/ui/app.py` | `create_app(settings, store=None, blob=None, launcher=None)`: templates, static mounts, routers, CSRF token, error page. |
| `src/skysift/ui/queries.py` | All reads: `summary`, `source_progress`, `review_queue`, `signal_detail`, `neighbours`, `approved`, `review_document`. |
| `src/skysift/ui/sweeps.py` | `SweepRequest`, `SweepLauncher` (start/active/tail/stop/history, lock file, dead-pid cleanup). |
| `src/skysift/ui/assets.py` | `ensure_plotly(data_dir, fetch)` download with SHA-256 check. |
| `src/skysift/ui/plots.py` | `LightCurveLoader` (blob or hash-verified re-fetch) and `build_plots(...)`. |
| `src/skysift/ui/routes/{sources,live,review,published,api}.py` | One router per page plus the plots JSON API. |
| `src/skysift/ui/templates/*.html`, `partials/*.html` | Jinja2 pages and HTMX fragments. |
| `src/skysift/ui/static/{htmx.min.js,app.css,app.js}` | Vendored HTMX, styles with light/dark tokens, keyboard shortcuts. |
| `tests/unit/ui_seed.py` | `seed(store, blob)` building a small realistic database shared by UI tests. |
| `tests/unit/test_{migrations,decisions,periodogram,ui_queries,sweeps,assets,plots,ui_pages,ui_review}.py` | Tests per task; `test_cli.py`, `test_pipeline.py`, `test_deploy_files.py`, `test_retention.py` gain cases. |

Conventions used in every code listing below: `utcnow()` is `skysift.store.db.utcnow`; `jsonutil` is `skysift.jsonutil`; `PY` means `.venv/Scripts/python.exe`.

---

### Task 1: UI extra, app factory, `ui` command with loopback guard

**Files:**
- Modify: `pyproject.toml` (extras), `docker/Dockerfile:8`, `docker/docker-compose.yml`
- Create: `src/skysift/ui/__init__.py`, `src/skysift/ui/app.py`, `src/skysift/ui/templates/base.html`, `src/skysift/ui/templates/error.html`, `src/skysift/ui/static/app.css`, `src/skysift/ui/static/app.js`
- Modify: `src/skysift/cli.py` (add `ui` command)
- Test: `tests/unit/test_ui_pages.py`, `tests/unit/test_cli.py`, `tests/unit/test_deploy_files.py`

**Interfaces:**
- Produces: `create_app(settings: Settings, store: Store | None = None, blob: Blob | None = None, launcher=None) -> FastAPI` with `app.state.settings`, `app.state.store`, `app.state.blob`, `app.state.launcher`, `app.state.token: str`, `app.state.templates: Jinja2Templates`, `app.state.plotly_path: Path | None`; helper `render(request, name, **ctx) -> HTMLResponse` that injects `token`, `settings`, `nav`, `plotly_url`; dependency `require_token(request)` for POST routes; `LOOPBACK = {"127.0.0.1", "localhost", "::1"}`.
- Consumes: `Settings`, `Store`, `blob_from_url`.

- [ ] **Step 1: Install the UI dependencies**

```bash
uv pip install --native-tls -p .venv/Scripts/python.exe fastapi>=0.115 "uvicorn>=0.30" "jinja2>=3.1" "python-multipart>=0.0.9" "httpx>=0.27"
```

Then edit `pyproject.toml` extras:

```toml
[project.optional-dependencies]
s3 = ["boto3>=1.34"]
pg = ["psycopg[binary]>=3.1"]
ui = ["fastapi>=0.115", "uvicorn>=0.30", "jinja2>=3.1", "python-multipart>=0.0.9"]
dev = ["pytest>=8.2", "pytest-cov>=5.0", "ruff>=0.17,<0.18", "httpx>=0.27"]
```

Add `include-package-data` so templates and static files ship in the wheel:

```toml
[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-data]
"skysift.ui" = ["templates/*.html", "templates/partials/*.html", "static/*"]
```

- [ ] **Step 2: Write the failing tests**

`tests/unit/test_ui_pages.py` (new):

```python
from fastapi.testclient import TestClient

from skysift.store.blob import blob_from_url
from skysift.store.db import Store
from skysift.ui.app import create_app


def make_client(settings):
    store = Store(settings.database_url); store.init_schema()
    app = create_app(settings, store=store, blob=blob_from_url(settings.blob_url))
    return TestClient(app), app


def test_healthz_and_token(settings):
    client, app = make_client(settings)
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert len(app.state.token) >= 32


def test_error_page_is_plain_html_without_traceback(settings):
    client, app = make_client(settings)

    @app.get("/boom")
    def boom():
        raise RuntimeError("secret internals")
    r = TestClient(app, raise_server_exceptions=False).get("/boom")
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
```

`tests/unit/test_cli.py` additions:

```python
def test_ui_refuses_non_loopback_host(settings):
    r = CliRunner().invoke(app, ["ui", "--host", "0.0.0.0", "--no-browser"])
    assert r.exit_code != 0 and "loopback" in r.output


def test_ui_starts_uvicorn_on_loopback(settings, monkeypatch):
    import uvicorn
    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda application, **kw: seen.update(kw, app=application))
    r = CliRunner().invoke(app, ["ui", "--port", "8899", "--no-browser"])
    assert r.exit_code == 0, r.output
    assert seen["host"] == "127.0.0.1" and seen["port"] == 8899 and seen["app"].title == "SkySift"
```

`tests/unit/test_deploy_files.py` additions:

```python
def test_dockerfile_installs_the_ui_extra():
    text = (ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
    assert '".[s3,pg,ui]"' in text


def test_compose_has_a_loopback_ui_service():
    import yaml
    doc = yaml.safe_load((ROOT / "docker" / "docker-compose.yml").read_text(encoding="utf-8"))
    ui = doc["services"]["ui"]
    assert ui["ports"] == ["127.0.0.1:8765:8765"] and ui["command"][:3] == ["ui", "--host", "0.0.0.0"]
```

(`ROOT` already exists in that file as the repo root; if it is named differently, use the existing name.) Note the container form binds `0.0.0.0` inside the container because compose maps it to the host's loopback only; the CLI guard therefore accepts `0.0.0.0` only when the environment variable `SKYSIFT_UI_ALLOW_CONTAINER=1` is set, which the compose service sets.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `PY -m pytest tests/unit/test_ui_pages.py tests/unit/test_cli.py tests/unit/test_deploy_files.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: skysift.ui` and `No such command 'ui'`.

- [ ] **Step 4: Write the app factory**

`src/skysift/ui/__init__.py`: empty.

`src/skysift/ui/app.py`:

```python
"""FastAPI application factory for the local dashboard. The UI reads the engine's store and blob and changes nothing
scientific; its only writes are decisions (Store.decide) and starting or stopping a sweep (SweepLauncher)."""
from __future__ import annotations

import logging
import secrets
import subprocess
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from skysift import __version__ as engine_version
from skysift.config import Settings
from skysift.store.blob import Blob, blob_from_url
from skysift.store.db import Store

log = logging.getLogger(__name__)
HERE = Path(__file__).resolve().parent
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
NAV = [("/", "Sources"), ("/live", "Live"), ("/candidates", "Candidates"), ("/approved", "Approved")]


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5,
                              cwd=HERE.parents[2], check=False).stdout.strip() or "unknown"
    except OSError:
        return "unknown"


async def require_token(request: Request) -> None:
    """CSRF guard for every state-changing POST: the per-process token must arrive in the X-SkySift-Token header
    or the _token form field. A page on another origin cannot read it, so it cannot forge the request."""
    token = request.app.state.token
    sent = request.headers.get("X-SkySift-Token")
    if sent is None and request.headers.get("content-type", "").startswith(("application/x-www-form-urlencoded",
                                                                                "multipart/form-data")):
        form = await request.form()
        sent = form.get("_token")
    if not sent or not secrets.compare_digest(str(sent), token):
        raise HTTPException(status_code=403, detail="missing or stale dashboard token; reload the page")


def render(request: Request, name: str, status_code: int = 200, **ctx) -> HTMLResponse:
    st = request.app.state
    base = {"request": request, "token": st.token, "settings": st.settings, "nav": NAV,
            "plotly_url": "/static/vendor/plotly.min.js" if st.plotly_path else None,
            "engine_version": engine_version, "git_commit": st.git_commit, "active_path": request.url.path}
    return st.templates.TemplateResponse(request, name, {**base, **ctx}, status_code=status_code)


def create_app(settings: Settings, store: Store | None = None, blob: Blob | None = None, launcher=None,
               plotly_path: Path | None = None) -> FastAPI:
    app = FastAPI(title="SkySift", docs_url=None, redoc_url=None)
    st = app.state
    st.settings = settings
    st.store = store or Store(settings.database_url)
    if store is None: st.store.init_schema()
    st.blob = blob or blob_from_url(settings.blob_url)
    st.launcher = launcher
    st.token = secrets.token_urlsafe(32)
    st.templates = Jinja2Templates(directory=str(HERE / "templates"))
    st.templates.env.filters["fmt"] = _fmt
    st.plotly_path = plotly_path
    st.git_commit = _git_commit()
    st.plot_cache = {}
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    if plotly_path is not None:
        app.mount("/static/vendor", StaticFiles(directory=str(plotly_path.parent)), name="vendor")

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("dashboard request failed: %s %s", request.method, request.url.path)
        if request.headers.get("accept", "").startswith("application/json") or request.url.path.startswith("/api/"):
            return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)
        return render(request, "error.html", status_code=500, error=f"{type(exc).__name__}: {exc}",
                      log_hint=str(settings.data_dir / "ui" / "dashboard.log"))

    from skysift.ui.routes import api, live, published, review, sources   # routers are added by Tasks 8 to 12
    for router in (sources.router, live.router, review.router, published.router, api.router):
        app.include_router(router)
    return app


def _fmt(value, digits: int = 2) -> str:
    """Template filter: numbers with fixed decimals, None as a dash, everything else as str."""
    if value is None: return "–"
    if isinstance(value, float): return f"{value:,.{digits}f}"
    if isinstance(value, int): return f"{value:,}"
    return str(value)
```

Until Tasks 8 to 12 exist, create `src/skysift/ui/routes/__init__.py` and five stub modules each containing only `from fastapi import APIRouter` and `router = APIRouter()`. `skysift/__init__.py` must export `__version__ = "0.1.0"` (add it if absent; keep it equal to `pyproject.toml`).

`src/skysift/ui/templates/base.html`:

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{% block title %}SkySift{% endblock %}</title>
<link rel="stylesheet" href="/static/app.css">
<script src="/static/htmx.min.js" defer></script>
{% if plotly_url %}<script src="{{ plotly_url }}" defer></script>{% endif %}
<script src="/static/app.js" defer></script>
</head>
<body hx-headers='{"X-SkySift-Token": "{{ token }}"}'>
<nav class="side">
  <div class="brand">SkySift</div>
  {% for href, label in nav %}
  <a href="{{ href }}" class="{{ 'active' if (href == '/' and active_path == '/') or (href != '/' and active_path.startswith(href)) else '' }}">{{ label }}</a>
  {% endfor %}
</nav>
<main>
  <header class="top">
    <span>data <code>{{ settings.data_dir }}</code></span>
    <span>db <code>{{ settings.database_url }}</code></span>
    <span>retention <code>{{ settings.retention }}</code></span>
  </header>
  <section class="page">
  {% block content %}{% endblock %}
  </section>
  <footer>SkySift {{ engine_version }} · {{ git_commit }}</footer>
</main>
</body>
</html>
```

`src/skysift/ui/templates/error.html`:

```html
{% extends "base.html" %}
{% block title %}SkySift · error{% endblock %}
{% block content %}
<h1>Something went wrong</h1>
<p class="error">{{ error }}</p>
<p>The full traceback is in the dashboard log: <code>{{ log_hint }}</code></p>
{% endblock %}
```

`src/skysift/ui/static/app.css` (tokens first; colour carries status only):

```css
:root { --bg:#ffffff; --fg:#1b1f23; --muted:#5b6470; --line:#d9dee4; --panel:#f6f8fa; --accent:#2457c5;
        --pass:#1a7f37; --fail:#c0392b; --undet:#b7791f; --running:#2457c5; font-size:14px; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0f1317; --fg:#e6e9ee; --muted:#9aa4b2; --line:#2a323c; --panel:#161c23; --accent:#6ea0ff;
          --pass:#3fb950; --fail:#ff6b5b; --undet:#e3b341; --running:#6ea0ff; }
}
* { box-sizing: border-box; }
body { margin:0; display:grid; grid-template-columns: 180px 1fr; min-height:100vh; background:var(--bg); color:var(--fg);
       font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
nav.side { border-right:1px solid var(--line); padding:16px 12px; position:sticky; top:0; height:100vh; }
nav.side .brand { font-weight:700; letter-spacing:.04em; margin-bottom:16px; }
nav.side a { display:block; padding:6px 8px; color:var(--fg); text-decoration:none; border-radius:4px; }
nav.side a.active { background:var(--panel); color:var(--accent); font-weight:600; }
main { display:flex; flex-direction:column; min-width:0; }
header.top { display:flex; gap:24px; padding:8px 20px; border-bottom:1px solid var(--line); color:var(--muted); font-size:12px; }
header.top code, td code { font-size:12px; }
section.page { padding:20px; flex:1; }
footer { padding:8px 20px; color:var(--muted); font-size:12px; border-top:1px solid var(--line); }
h1 { font-size:20px; margin:0 0 12px; } h2 { font-size:16px; margin:20px 0 8px; }
table { border-collapse:collapse; width:100%; font-variant-numeric: tabular-nums; }
th, td { padding:4px 8px; border-bottom:1px solid var(--line); text-align:left; white-space:nowrap; }
td.num, th.num { text-align:right; }
th a { color:inherit; text-decoration:none; } th a.sorted::after { content:" ▾"; } th a.sorted.asc::after { content:" ▴"; }
tr.row-link { cursor:pointer; } tr.row-link:hover { background:var(--panel); }
.cards { display:grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap:12px; margin-bottom:16px; }
.card { background:var(--panel); border:1px solid var(--line); border-radius:6px; padding:10px 12px; }
.card .label { color:var(--muted); font-size:12px; } .card .value { font-size:22px; font-weight:600; }
.status-pass { color:var(--pass); } .status-fail { color:var(--fail); } .status-undetermined { color:var(--undet); }
.status-running, .status-review { color:var(--running); } .status-approved { color:var(--pass); } .status-rejected { color:var(--fail); }
.muted { color:var(--muted); } .error { color:var(--fail); }
form.inline { display:grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap:8px 16px; align-items:end;
              background:var(--panel); border:1px solid var(--line); border-radius:6px; padding:12px; margin:8px 0 16px; }
label { display:flex; flex-direction:column; font-size:12px; color:var(--muted); gap:2px; }
input, select, textarea { font: inherit; padding:4px 6px; border:1px solid var(--line); border-radius:4px; background:var(--bg); color:var(--fg); }
button { font: inherit; padding:6px 12px; border:1px solid var(--line); border-radius:4px; background:var(--panel); color:var(--fg); cursor:pointer; }
button.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
button.danger { border-color:var(--fail); color:var(--fail); }
pre.log { background:var(--panel); border:1px solid var(--line); border-radius:6px; padding:10px; max-height:420px; overflow:auto; font-size:12px; }
.plot { height:300px; margin:8px 0 16px; } .plot.tall { height:360px; }
.decision-bar { position:sticky; bottom:0; background:var(--bg); border-top:1px solid var(--line); padding:10px 0; display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
.decision-bar textarea { flex:1; min-width:240px; min-height:36px; }
details.raw pre { font-size:12px; max-height:300px; overflow:auto; }
.htmx-indicator { opacity:0; transition:opacity .2s; } .htmx-request .htmx-indicator, .htmx-request.htmx-indicator { opacity:1; }
```

`src/skysift/ui/static/app.js` (keyboard shortcuts and row links; the plot code arrives in Task 10):

```js
document.addEventListener("click", (e) => {
  const row = e.target.closest("tr.row-link[data-href]");
  if (row && !e.target.closest("a, button, input")) window.location = row.dataset.href;
});
document.addEventListener("keydown", (e) => {
  if (e.target.matches("input, textarea, select")) return;
  const act = (id) => { const el = document.getElementById(id); if (el) el.click(); };
  if (e.key === "a") act("btn-approve");
  if (e.key === "r") act("btn-reject");
  if (e.key === "n") act("btn-needs-data");
  if (e.key === "j") act("link-next");
  if (e.key === "k") act("link-prev");
});
```

- [ ] **Step 5: Add the `ui` command**

In `src/skysift/cli.py` add:

```python
@app.command()
def ui(host: str = "127.0.0.1", port: int = 8765, no_browser: bool = typer.Option(False, "--no-browser")):
    """Start the local dashboard (loopback only) and open it in the browser."""
    import os
    import threading
    import webbrowser

    import uvicorn

    from skysift.ui.app import LOOPBACK, create_app
    from skysift.ui.assets import ensure_plotly
    from skysift.ui.sweeps import SweepLauncher

    if host not in LOOPBACK and not (host == "0.0.0.0" and os.environ.get("SKYSIFT_UI_ALLOW_CONTAINER") == "1"):
        raise typer.BadParameter(f"{host} is not a loopback address; the dashboard has no authentication. "
                                 "Reach it remotely through an SSH tunnel: ssh -L 8765:127.0.0.1:8765 <host>",
                                 param_hint="--host")
    s, store, blob = _ctx()
    (s.data_dir / "ui").mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        handlers=[logging.FileHandler(s.data_dir / "ui" / "dashboard.log", encoding="utf-8")])
    plotly = ensure_plotly(s.data_dir)
    application = create_app(s, store=store, blob=blob, launcher=SweepLauncher(s.data_dir), plotly_path=plotly)
    url = f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}/"
    _plain(_console(stderr=True), f"SkySift dashboard at {url}  (Ctrl+C to stop)")
    if not no_browser:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    uvicorn.run(application, host=host, port=port, log_level="warning")
```

`ensure_plotly` and `SweepLauncher` arrive in Tasks 7 and 6; until then add minimal stubs: `src/skysift/ui/assets.py` with `def ensure_plotly(data_dir, fetch=None): return None` and `src/skysift/ui/sweeps.py` with `class SweepLauncher:\n    def __init__(self, data_dir): self.data_dir = data_dir`. Tasks 6 and 7 replace them.

- [ ] **Step 6: Dockerfile and compose**

`docker/Dockerfile` line 8: `RUN pip install --no-cache-dir -e ".[s3,pg,ui]"`.

`docker/docker-compose.yml`:

```yaml
services:
  skysift:
    build: {context: .., dockerfile: docker/Dockerfile}
    volumes: ["../data:/data"]
    env_file: [{path: ../.env, required: false}]
    command: ["run", "--source", "kbonus_bkg", "--limit", "100"]
  ui:
    build: {context: .., dockerfile: docker/Dockerfile}
    volumes: ["../data:/data"]
    env_file: [{path: ../.env, required: false}]
    environment: {SKYSIFT_UI_ALLOW_CONTAINER: "1"}
    ports: ["127.0.0.1:8765:8765"]
    command: ["ui", "--host", "0.0.0.0", "--no-browser"]
```

- [ ] **Step 7: Run the tests**

Run: `PY -m pytest tests/unit/test_ui_pages.py tests/unit/test_cli.py tests/unit/test_deploy_files.py -q -p no:cacheprovider`
Expected: PASS. Also `PY -m ruff check src tests` clean.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml docker src/skysift/__init__.py src/skysift/ui src/skysift/cli.py tests/unit/test_ui_pages.py tests/unit/test_cli.py tests/unit/test_deploy_files.py
git commit -m "feat(ui): app factory, loopback-only ui command, token guard, base layout"
```

---

### Task 2: Schema migrations and the new rows

**Files:**
- Create: `src/skysift/store/migrations.py`
- Modify: `src/skysift/store/db.py` (DecisionRow, new columns, `init_schema`, `log_query(target_id=...)`, `record_results` writes `target_id` on queries, `unpinned_artifacts(kind=...)`, `add_artifact(kind=...)`), `src/skysift/store/retention.py` (`release_target` raw only)
- Test: `tests/unit/test_migrations.py`, `tests/unit/test_retention.py`, `tests/unit/test_store.py`

**Interfaces:**
- Produces: `DecisionRow(id, signal_id, decision, note, who, utc)`; `RunRow.periodogram_sha256: str | None`; `CandidateRow.decided_at: datetime | None`; `QueryRow.target_id: str | None`; `ArtifactRow.kind: str` (`"raw"` default, `"summary"` for periodograms); `migrations.migrate(engine) -> list[str]` (steps applied); `Store.log_query(..., target_id=None)`; `Store.add_artifact(..., kind="raw")`; `Store.unpinned_artifacts(target_id=None, exclude_targets=None, kind=None)`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_migrations.py`:

```python
from sqlalchemy import inspect, text

from skysift.store import migrations
from skysift.store.db import Base, Store


def _milestone1_database(tmp_path):
    """A database as milestone 1 left it: current tables minus everything this milestone adds."""
    url = f"sqlite:///{tmp_path.as_posix()}/m1.db"
    s = Store(url); Base.metadata.create_all(s.engine)
    with s.engine.begin() as c:
        c.execute(text("ALTER TABLE runs DROP COLUMN periodogram_sha256"))
        c.execute(text("ALTER TABLE candidates DROP COLUMN decided_at"))
        c.execute(text("ALTER TABLE queries DROP COLUMN target_id"))
        c.execute(text("ALTER TABLE artifacts DROP COLUMN kind"))
        c.execute(text("DROP TABLE decisions"))
        c.execute(text("INSERT INTO artifacts (sha256, nbytes, url, local_path, pinned, created, last_used) "
                       "VALUES ('a'*64, 1, 'u', 'p', 0, '2026-10-13 00:00:00', '2026-10-13 00:00:00')"))
    return url


def test_migrate_milestone1_database_in_place(tmp_path):
    url = _milestone1_database(tmp_path)
    s = Store(url); s.init_schema()
    insp = inspect(s.engine)
    assert "periodogram_sha256" in {c["name"] for c in insp.get_columns("runs")}
    assert "decided_at" in {c["name"] for c in insp.get_columns("candidates")}
    assert "target_id" in {c["name"] for c in insp.get_columns("queries")}
    assert "kind" in {c["name"] for c in insp.get_columns("artifacts")}
    assert "decisions" in insp.get_table_names() and "schema_version" in insp.get_table_names()
    with s.engine.connect() as c:
        assert c.execute(text("select kind from artifacts")).scalar() == "raw"       # existing rows get the default
        assert [r[0] for r in c.execute(text("select step from schema_version"))] == ["001_dashboard"]


def test_migrate_is_idempotent(tmp_path):
    s = Store(f"sqlite:///{tmp_path.as_posix()}/t.db"); s.init_schema()
    assert migrations.migrate(s.engine) == []          # nothing left to apply on a fresh schema
    assert migrations.migrate(s.engine) == []
```

`tests/unit/test_retention.py` addition:

```python
def test_stream_keeps_summary_artifacts(tmp_path):
    st, b, r = setup(tmp_path, "stream")
    _, raw = put(st, b, "t1", b"raw" * 10)
    sha, loc = b.put(b"npz" * 10, "t1_periodogram.npz")
    st.add_artifact(sha, 30, "skysift://periodogram/t1", loc, "t1", kind="summary")
    assert r.release_target("t1") == 30 and not b.exists(raw) and b.exists(loc)
    assert [a["kind"] for a in st.unpinned_artifacts("t1")] == ["summary"]
    assert st.unpinned_artifacts("t1", kind="raw") == []
```

`tests/unit/test_store.py` addition:

```python
def test_log_query_records_target(tmp_path):
    s = make_store(tmp_path)
    s.log_query("mast", "https://x", {}, 200, 5, target_id="kic-1")
    with s.session() as ses:
        from skysift.store.db import QueryRow
        assert ses.query(QueryRow).one().target_id == "kic-1"
```

- [ ] **Step 2: Run to verify failure**

Run: `PY -m pytest tests/unit/test_migrations.py tests/unit/test_retention.py tests/unit/test_store.py -q -p no:cacheprovider`
Expected: FAIL (`no such column`, unexpected keyword `kind`, module `migrations` missing).

- [ ] **Step 3: Add the rows and columns in `db.py`**

In `RunRow` add `periodogram_sha256: Mapped[str | None] = mapped_column(String(64))`.
In `CandidateRow` add `decided_at: Mapped[datetime | None] = mapped_column(DateTime)`.
In `QueryRow` add `target_id: Mapped[str | None] = mapped_column(String(64), index=True)`.
In `ArtifactRow` add `kind: Mapped[str] = mapped_column(String(16), default="raw", server_default="raw")`.
After `CandidateRow` add:

```python
class DecisionRow(Base):
    """Append-only operator decisions; the candidate row carries only the current state."""
    __tablename__ = "decisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), index=True)
    decision: Mapped[str] = mapped_column(String(16))        # approve|reject|needs_data
    note: Mapped[str] = mapped_column(Text, default="")
    who: Mapped[str] = mapped_column(String(64), default="operator")
    utc: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
```

Change `init_schema`:

```python
    def init_schema(self) -> None:
        from skysift.store import migrations
        Base.metadata.create_all(self.engine)
        migrations.migrate(self.engine)
```

`log_query` and `add_artifact` gain parameters, `record_results` writes them:

```python
    def log_query(self, service, url, params: dict, status, nbytes, sha256=None, error="", target_id=None) -> None:
        with self.session() as s:
            s.add(QueryRow(service=service, url=url, params=jsonutil.dumps(params, default=str), status=status,
                           nbytes=nbytes, sha256=sha256, error=error, target_id=target_id))

    def add_artifact(self, sha256, nbytes, url, local_path, target_id=None, pinned=False, kind="raw") -> None:
        with self.session() as s:
            self._upsert_artifact(s, sha256, nbytes, url, local_path, target_id, pinned, kind)

    def _upsert_artifact(self, s: Session, sha256, nbytes, url, local_path, target_id, pinned, kind) -> None:
        row = s.get(ArtifactRow, sha256)
        if row is None:
            s.add(ArtifactRow(sha256=sha256, nbytes=nbytes, url=url, local_path=local_path, target_id=target_id,
                              pinned=pinned, kind=kind))
            return
        if row.deleted_at is not None: row.pinned = False      # a deleted row's old pin protects nothing
        row.deleted_at, row.local_path, row.nbytes, row.last_used, row.kind = None, local_path, nbytes, utcnow(), kind
        if target_id is not None: row.target_id = target_id
        if pinned: row.pinned = True
```

In `record_results`, the `QueryRow(...)` construction gains `target_id=target_id`. `unpinned_artifacts` gains `kind: str | None = None` and `if kind: q = q.where(ArtifactRow.kind == kind)`, and each returned dict gains `"kind": a.kind`.

In `retention.py`, `release_target` becomes:

```python
    def release_target(self, target_id: str) -> int:
        """Stream mode: delete the target's unpinned raw files once its run is written. Summaries (periodogram
        envelopes, a few kB) stay: they are the null set the threshold calibration needs and are evicted only by budget."""
        if self.policy.mode != "stream":
            return 0
        return sum(self._delete(a) for a in self.store.unpinned_artifacts(target_id, kind="raw"))
```

- [ ] **Step 4: Write the migrations module**

`src/skysift/store/migrations.py`:

```python
"""Hand-rolled schema migrations. `create_all` creates missing tables but never alters existing ones, so every
column added after milestone 1 is added here, once, recorded in `schema_version`. Steps are idempotent: each checks
the catalogue before altering, so a database that already has the column (a fresh create_all) records the step
without touching it. Column types are spelled for both SQLite and Postgres (TIMESTAMP, VARCHAR)."""
from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine

from skysift.store.db import utcnow


def _columns(conn: Connection, table: str) -> set[str]:
    return {c["name"] for c in inspect(conn).get_columns(table)}


def _add_column(conn: Connection, table: str, column: str, ddl: str) -> None:
    if column not in _columns(conn, table):
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


def _step_001_dashboard(conn: Connection) -> None:
    _add_column(conn, "runs", "periodogram_sha256", "VARCHAR(64)")
    _add_column(conn, "candidates", "decided_at", "TIMESTAMP")
    _add_column(conn, "queries", "target_id", "VARCHAR(64)")
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_queries_target_id ON queries (target_id)"))
    _add_column(conn, "artifacts", "kind", "VARCHAR(16) NOT NULL DEFAULT 'raw'")
    # the decisions table itself is created by Base.metadata.create_all, which init_schema runs first


STEPS = [("001_dashboard", _step_001_dashboard)]


def migrate(engine: Engine) -> list[str]:
    """Apply every step not yet recorded; returns the names applied (empty when the schema is current)."""
    applied = []
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS schema_version (step VARCHAR(64) PRIMARY KEY, applied TIMESTAMP)"))
        done = {r[0] for r in conn.execute(text("SELECT step FROM schema_version"))}
        for name, fn in STEPS:
            if name in done: continue
            fn(conn)
            conn.execute(text("INSERT INTO schema_version (step, applied) VALUES (:s, :a)"), {"s": name, "a": utcnow()})
            applied.append(name)
    return applied
```

Note the circular import: `migrations` imports `utcnow` from `db`, and `db.init_schema` imports `migrations` inside the method, so module import order is safe.

- [ ] **Step 5: Run the tests**

Run: `PY -m pytest tests/unit/test_migrations.py tests/unit/test_retention.py tests/unit/test_store.py tests/unit/test_pipeline.py -q -p no:cacheprovider`
Expected: PASS (the pipeline tests take about 2 minutes). `test_migrate_is_idempotent` expects `[]` on a fresh schema because `init_schema` already recorded the step.

- [ ] **Step 6: Commit**

```bash
git add src/skysift/store tests/unit/test_migrations.py tests/unit/test_retention.py tests/unit/test_store.py
git commit -m "feat(store): migrations module, decisions table, periodogram and target columns, summary artifacts"
```

---

### Task 3: `Store.decide` and the decision rules

**Files:**
- Modify: `src/skysift/store/db.py`
- Test: `tests/unit/test_decisions.py`

**Interfaces:**
- Produces: `DECISION_STATUS = {"approve": "approved", "reject": "rejected", "needs_data": "review"}`; `Store.decide(signal_id: int, decision: str, note: str = "", who: str = "operator") -> dict` returning `{"signal_id", "decision", "status", "final_status", "note", "who", "decided_at"}`; `Store.decisions(signal_id) -> list[dict]` newest first with keys `decision, note, who, utc`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_decisions.py`:

```python
import pytest

from skysift.models import Signal, Target
from skysift.store.db import CandidateRow, DecisionRow, SignalRow, Store, make_job_key


def _store_with_signal(tmp_path):
    s = Store(f"sqlite:///{tmp_path.as_posix()}/t.db"); s.init_schema()
    s.upsert_target(Target(source="kbonus_bkg", target_id="kic-1", ra=0, dec=0))
    rid = s.start_run(make_job_key("kic-1", "box_dip", "1+0", "1"), "kic-1", "box_dip", "1+0", "1")
    sig = Signal(target_id="kic-1", detector="box_dip", detector_version="1+0", period=3.0, t0=1.0, duration=0.1,
                 depth_ppm=800, snr=12, sde=11, n_events=9)
    sid = s.add_signal(sig, rid); s.set_signal_status(sid, "candidate_stage1")
    return s, sid


def test_approve_creates_candidate_and_history(tmp_path):
    s, sid = _store_with_signal(tmp_path)
    out = s.decide(sid, "approve", "clean fold, no secondary")
    assert out["status"] == "approved" and out["final_status"] == "candidate_stage1" and out["decided_at"]
    with s.session() as ses:
        c = ses.query(CandidateRow).one()
        assert (c.status, c.note, c.branch) == ("approved", "clean fold, no secondary", "planet") and c.decided_at
        assert ses.query(DecisionRow).count() == 1
    assert s.decisions(sid)[0]["decision"] == "approve"


def test_reject_requires_note_and_marks_signal(tmp_path):
    s, sid = _store_with_signal(tmp_path)
    with pytest.raises(ValueError):
        s.decide(sid, "reject", "   ")
    s.decide(sid, "reject", "odd/even differ by 4 sigma")
    with s.session() as ses:
        assert ses.get(SignalRow, sid).final_status == "rejected"
        assert ses.query(CandidateRow).one().status == "rejected"


def test_revisit_appends_history_and_moves_state(tmp_path):
    s, sid = _store_with_signal(tmp_path)
    s.decide(sid, "reject", "looked like an EB")
    s.decide(sid, "approve", "secondary was a data gap artefact")
    hist = s.decisions(sid)
    assert [h["decision"] for h in hist] == ["approve", "reject"]          # newest first
    with s.session() as ses:
        assert ses.query(CandidateRow).count() == 1                          # one candidate row per signal
        assert ses.query(CandidateRow).one().status == "approved"
        assert ses.get(SignalRow, sid).final_status == "candidate_stage1"   # back in the queue's status space
    s.decide(sid, "needs_data", "wait for rung 5")
    with s.session() as ses:
        assert ses.query(CandidateRow).one().status == "review"
    assert len(s.decisions(sid)) == 3


def test_unknown_decision_or_signal(tmp_path):
    s, sid = _store_with_signal(tmp_path)
    with pytest.raises(ValueError): s.decide(sid, "delete")
    with pytest.raises(KeyError): s.decide(sid + 99, "approve")
```

- [ ] **Step 2: Run to verify failure**

Run: `PY -m pytest tests/unit/test_decisions.py -q -p no:cacheprovider`
Expected: FAIL with `AttributeError: 'Store' object has no attribute 'decide'`.

- [ ] **Step 3: Implement**

In `db.py`, after `FINAL_STATUS`, add `DECISION_STATUS = {"approve": "approved", "reject": "rejected", "needs_data": "review"}`. Add to `Store`:

```python
    def decide(self, signal_id: int, decision: str, note: str = "", who: str = "operator") -> dict:
        """Record an operator decision in one transaction: append a decisions row and move the candidate row to
        the new current state. Nothing is deleted; a later decision appends another row. A rejection needs a note
        and marks the signal rejected; approving or asking for data after a rejection returns the signal to the
        candidate status so the queue and the approved list agree."""
        if decision not in DECISION_STATUS:
            raise ValueError(f"unknown decision {decision!r}; one of {sorted(DECISION_STATUS)}")
        if decision == "reject" and not note.strip():
            raise ValueError("a rejection needs a note saying why")
        now = utcnow()
        with self.session() as s:
            sig = s.get(SignalRow, signal_id)
            if sig is None:
                raise KeyError(signal_id)
            s.add(DecisionRow(signal_id=signal_id, decision=decision, note=note, who=who, utc=now))
            cand = s.scalars(select(CandidateRow).where(CandidateRow.signal_id == signal_id)).first()
            if cand is None:
                cand = CandidateRow(signal_id=signal_id, branch=sig.branch); s.add(cand)
            cand.status, cand.note, cand.decided_at = DECISION_STATUS[decision], note, now
            if decision == "reject":
                sig.final_status = "rejected"
            elif sig.final_status == "rejected":
                sig.final_status = "candidate_stage1"
            return {"signal_id": signal_id, "decision": decision, "status": cand.status, "final_status": sig.final_status,
                    "note": note, "who": who, "decided_at": now.isoformat()}

    def decisions(self, signal_id: int) -> list[dict]:
        with self.session() as s:
            rows = s.scalars(select(DecisionRow).where(DecisionRow.signal_id == signal_id)
                             .order_by(DecisionRow.id.desc()))
            return [{"decision": d.decision, "note": d.note, "who": d.who, "utc": d.utc.isoformat()} for d in rows]
```

- [ ] **Step 4: Run the tests**

Run: `PY -m pytest tests/unit/test_decisions.py tests/unit/test_store.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/skysift/store/db.py tests/unit/test_decisions.py
git commit -m "feat(store): append-only operator decisions with current candidate state"
```

---
### Task 4: Read queries and a shared `status` summary

**Files:**
- Create: `src/skysift/ui/queries.py`, `tests/unit/ui_seed.py`
- Modify: `src/skysift/cli.py` (`status` uses `queries.summary`)
- Test: `tests/unit/test_ui_queries.py`, `tests/unit/test_cli.py`

**Interfaces:**
- Produces (all take a `Store` and return plain dicts/lists, no ORM objects):
  - `summary(store, retention: Retention, settings) -> dict` with keys `counts`, `targets_by_state`, `signal_outcomes`, `rung_counts` (`{rung: {pass, fail, undetermined}}`), `targets_per_hour`, `mean_seconds_per_run`, `error_capped_targets`, `cache_bytes`, `cache_budget_bytes`, `retention`, `last_errors` (list of `{target, finished, error}`), `queries`.
  - `source_progress(store) -> dict[source, {new, done, error, total}]`.
  - `review_queue(store, status="candidate_stage1", sort="sde", desc=True, limit=500) -> list[dict]` rows with `id, target_id, period, depth_ppm, duration_h, sde, snr, n_events, last_rung, verdict, detected, decision, kic, gaia_id, gmag`.
  - `signal_detail(store, signal_id) -> dict | None` with `signal, target, run, tests (list of {rung, status, reason, duration_s, metrics}), candidate (or None), decisions`.
  - `neighbours(store, signal_id, status, sort, desc) -> (prev_id | None, next_id | None)`.
  - `approved(store, status="approved") -> list[dict]`.
  - `review_document(store, signal_id) -> dict` (the `vet`-shaped record: signal, tests, queries for the target, decisions).
  - `SORT_COLUMNS = {"sde", "snr", "period", "depth_ppm", "duration", "n_events", "detected", "target_id"}`.
- `tests/unit/ui_seed.py`: `seed(store, blob) -> dict` with ids: `{"candidate": sid, "undetermined": sid2, "rejected": sid3, "targets": [...], "run_ids": {...}}`.

- [ ] **Step 1: Write the seed helper**

`tests/unit/ui_seed.py`:

```python
"""A small realistic database for dashboard tests: three KBonus-like targets, one done run each, one candidate with
four passing rungs, one undetermined signal (rung 4 outage), one failed signal, raw artifacts for two targets."""
from skysift.models import Signal, Target, TestResult, TestStatus
from skysift.store.db import Store, make_job_key

VERSION = "1+abcdef01"


def _target(tid, kic=None, gaia="2101234567890123456", gmag=16.4):
    return Target(source="kbonus_bkg", target_id=tid, ra=285.9, dec=40.9, gaia_id=gaia, kic=kic, gmag=gmag,
                  meta={"psffrac": 0.9, "crowdsap": 0.9, "file_name": f"{tid}.fits"})


def _signal(tid, period, sde, depth=1800.0):
    return Signal(target_id=tid, detector="box_dip", detector_version=VERSION, period=period, t0=136.3, duration=0.096,
                  depth_ppm=depth, snr=sde * 1.3, sde=sde, n_events=int(1400 / period),
                  extra={"baseline_days": 1400.0, "search_window": {"min_period_days": 0.5, "max_period_days": 200.0}})


def _tests(outcome):
    r1 = TestResult(rung="rung1_detection", status=TestStatus.PASS, metrics={"sde": 14.0, "snr": 18.0, "n_events": 99,
                                                                             "duration_ratio": 0.9})
    r2 = TestResult(rung="rung2_shape", status=TestStatus.PASS,
                    metrics={"depth_ppm": 1800.0, "depth_err_ppm": 90.0, "odd_ppm": 1790.0, "even_ppm": 1810.0,
                             "parity_sigma": 0.2, "secondary_ppm": 20.0, "secondary_sigma": 0.3,
                             "free_secondary_phase": 0.41, "free_secondary_ppm": 60.0, "free_secondary_sigma": 1.1,
                             "control_rms_ppm": 70.0, "timing_rms_min": None})
    r3 = TestResult(rung="rung3_stability", status=TestStatus.PASS,
                    metrics={"season_depths": {"0": 1750.0, "1": 1820.0, "2": 1790.0, "3": 1840.0},
                             "season_errs": {"0": 120.0, "1": 110.0, "2": 130.0, "3": 115.0},
                             "season_z": {"0": -0.4, "1": 0.3, "2": -0.1, "3": 0.5}, "chi2": 0.6, "dof": 3,
                             "p_value": 0.9, "n_events": 99, "sap_psf_ratio": 1.02})
    if outcome == "pass":
        r4 = TestResult(rung="rung4_novelty", status=TestStatus.PASS, metrics={"cumulative": 0, "q1_q17_dr25_tce": 0,
                                                                              "toi": 0, "kepler_eb": 0, "other_period_objects": []})
    elif outcome == "undetermined":
        r4 = TestResult(rung="rung4_novelty", status=TestStatus.UNDETERMINED, reason="outage:vizier: HTTP 503",
                        metrics={"cumulative": 0})
    else:
        r2 = TestResult(rung="rung2_shape", status=TestStatus.FAIL, reason="odd_even 4.2 sigma",
                        metrics={**r2.metrics, "parity_sigma": 4.2})
        return [r1, r2]
    return [r1, r2, r3, r4]


def seed(store: Store, blob=None) -> dict:
    targets = [_target("kic-005689121", kic=5689121), _target("gaia-dr3-2101234567890123457", gaia="2101234567890123457"),
               _target("gaia-dr3-2101234567890123458", gaia="2101234567890123458", gmag=18.9)]
    store.upsert_targets(targets)
    ids, runs = {}, {}
    for t, (period, sde, outcome, key) in zip(targets, [(13.7695, 21.1, "pass", "candidate"),
                                                          (3.21, 10.4, "undetermined", "undetermined"),
                                                          (1.07, 9.6, "fail", "rejected")], strict=True):
        rid = store.start_run(make_job_key(t.target_id, "box_dip", VERSION, "1"), t.target_id, "box_dip", VERSION, "1")
        tests = _tests(outcome)
        item = {"signal": _signal(t.target_id, period, sde).model_dump(), "outcome": outcome,
                "tests": [r.model_dump() for r in tests], "timings": [0.01] * len(tests)}
        q = [{"service": "exoarchive", "url": "https://exoarchive/TAP", "params": {"query": "select 1"}, "status": 200,
              "nbytes": 12, "sha256": "ab" * 32, "error": "", "utc": "2026-10-14T00:00:00+00:00"}]
        if blob is not None and outcome != "fail":
            data = f"FITS bytes for {t.target_id}".encode()
            sha, loc = blob.put(data, f"{t.target_id}.fits")
            store.add_artifact(sha, len(data), f"https://mast/{t.target_id}.fits", loc, t.target_id)
        store.record_results(rid, t.target_id, [item], q, pin_evidence=outcome != "fail")
        runs[key] = rid
    with store.session() as s:
        from skysift.store.db import SignalRow
        for row in s.query(SignalRow):
            for key, tid in (("candidate", targets[0].target_id), ("undetermined", targets[1].target_id),
                             ("rejected", targets[2].target_id)):
                if row.target_id == tid: ids[key] = row.id
    return {**ids, "targets": [t.target_id for t in targets], "run_ids": runs}
```

- [ ] **Step 2: Write the failing tests**

`tests/unit/test_ui_queries.py`:

```python
from skysift.store.blob import LocalBlob
from skysift.store.db import Store
from skysift.store.retention import Retention, RetentionPolicy
from skysift.ui import queries
from tests.unit.ui_seed import seed


def _env(tmp_path, settings):
    store = Store(settings.database_url); store.init_schema()
    blob = LocalBlob(tmp_path / "blobs")
    ids = seed(store, blob)
    return store, blob, ids


def test_summary_counts_states_outcomes_and_rungs(tmp_path, settings):
    store, blob, ids = _env(tmp_path, settings)
    out = queries.summary(store, Retention(RetentionPolicy.parse("stream"), store, blob), settings)
    assert out["targets_by_state"] == {"done": 3}
    assert out["signal_outcomes"] == {"candidate_stage1": 1, "undetermined": 1, "rejected": 1}
    assert out["rung_counts"]["rung2_shape"] == {"pass": 2, "fail": 1, "undetermined": 0}
    assert out["rung_counts"]["rung4_novelty"] == {"pass": 1, "fail": 0, "undetermined": 1}
    assert out["counts"]["queries"] == 3 and out["error_capped_targets"] == 0 and out["last_errors"] == []
    assert out["mean_seconds_per_run"] is not None and out["cache_bytes"] > 0


def test_source_progress(tmp_path, settings):
    store, blob, ids = _env(tmp_path, settings)
    assert queries.source_progress(store) == {"kbonus_bkg": {"new": 0, "done": 3, "error": 0, "total": 3}}


def test_review_queue_default_and_sorting(tmp_path, settings):
    store, blob, ids = _env(tmp_path, settings)
    rows = queries.review_queue(store)
    assert [r["id"] for r in rows] == [ids["candidate"]]
    r = rows[0]
    assert (r["last_rung"], r["verdict"], r["decision"]) == ("rung4_novelty", "pass", None)
    assert abs(r["duration_h"] - 0.096 * 24) < 1e-9 and r["kic"] == 5689121
    both = queries.review_queue(store, status="all", sort="period", desc=False)
    assert [round(b["period"], 2) for b in both] == [1.07, 3.21, 13.77]
    assert queries.review_queue(store, status="undetermined")[0]["verdict"] == "undetermined"


def test_signal_detail_neighbours_and_history(tmp_path, settings):
    store, blob, ids = _env(tmp_path, settings)
    d = queries.signal_detail(store, ids["candidate"])
    assert d["signal"]["period"] == 13.7695 and d["target"]["kic"] == 5689121 and d["run"]["status"] == "done"
    assert [t["rung"] for t in d["tests"]] == ["rung1_detection", "rung2_shape", "rung3_stability", "rung4_novelty"]
    assert d["candidate"] is None and d["decisions"] == []
    store.decide(ids["candidate"], "approve", "fine")
    d = queries.signal_detail(store, ids["candidate"])
    assert d["candidate"]["status"] == "approved" and d["decisions"][0]["decision"] == "approve"
    assert queries.signal_detail(store, 10**6) is None
    prev_id, next_id = queries.neighbours(store, ids["undetermined"], status="all", sort="period", desc=False)
    assert (prev_id, next_id) == (ids["rejected"], ids["candidate"])


def test_approved_and_review_document(tmp_path, settings):
    store, blob, ids = _env(tmp_path, settings)
    store.decide(ids["candidate"], "approve", "fine"); store.decide(ids["undetermined"], "reject", "noise")
    rows = queries.approved(store)
    assert [r["id"] for r in rows] == [ids["candidate"]] and rows[0]["note"] == "fine"
    assert [r["id"] for r in queries.approved(store, status="rejected")] == [ids["undetermined"]]
    doc = queries.review_document(store, ids["candidate"])
    assert doc["signal"]["target_id"] == "kic-005689121" and len(doc["tests"]) == 4
    assert doc["queries"][0]["service"] == "exoarchive" and doc["decisions"][0]["decision"] == "approve"
```

`tests/unit/test_cli.py` addition:

```python
def test_status_reports_rungs_and_states(settings, tmp_path):
    from skysift.store.blob import LocalBlob
    from skysift.store.db import Store
    from tests.unit.ui_seed import seed
    store = Store(settings.database_url); store.init_schema(); seed(store, LocalBlob(tmp_path / "blobs"))
    r = CliRunner().invoke(app, ["status"])
    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout)
    assert out["targets_by_state"] == {"done": 3} and out["rung_counts"]["rung1_detection"]["pass"] == 3
```

- [ ] **Step 3: Run to verify failure**

Run: `PY -m pytest tests/unit/test_ui_queries.py tests/unit/test_cli.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: skysift.ui.queries` and a KeyError on `targets_by_state`.

- [ ] **Step 4: Implement `queries.py`**

```python
"""Every read the dashboard (and `skysift status`) needs, as plain dicts from short sessions. Nothing here writes."""
from __future__ import annotations

import json

from sqlalchemy import case, func, select

from skysift.store.db import (
    ArtifactRow, CandidateRow, DecisionRow, QueryRow, RunRow, SignalRow, Store, TargetRow, TestRow,
)
from skysift.store.retention import Retention

SORT_COLUMNS = {"sde": SignalRow.sde, "snr": SignalRow.snr, "period": SignalRow.period, "depth_ppm": SignalRow.depth_ppm,
                "duration": SignalRow.duration, "n_events": SignalRow.n_events, "detected": SignalRow.id,
                "target_id": SignalRow.target_id}
QUEUE_STATUSES = {"candidate_stage1": ("candidate_stage1",), "undetermined": ("undetermined",),
                  "rejected": ("rejected",), "all": ("candidate_stage1", "undetermined", "rejected")}


def _iso(dt) -> str | None:
    return dt.isoformat() if dt is not None else None


def summary(store: Store, retention: Retention, settings) -> dict:
    from skysift.pipeline import MAX_ATTEMPTS
    counts = store.counts()
    with store.session() as s:
        states = dict(s.execute(select(TargetRow.state, func.count()).group_by(TargetRow.state)).all())
        outcomes = dict(s.execute(select(SignalRow.final_status, func.count()).group_by(SignalRow.final_status)).all())
        rungs: dict[str, dict] = {}
        for rung, status, n in s.execute(select(TestRow.rung, TestRow.status, func.count())
                                         .group_by(TestRow.rung, TestRow.status)):
            rungs.setdefault(rung, {"pass": 0, "fail": 0, "undetermined": 0})[status] = n
        done = s.execute(select(RunRow.started, RunRow.finished).where(RunRow.status == "done")).all()
        seconds = sum((f - st).total_seconds() for st, f in done if f is not None)
        errors = s.scalars(select(RunRow).where(RunRow.status == "error", RunRow.error != "superseded")
                           .order_by(RunRow.id.desc()).limit(5)).all()
        last_errors = [{"target": e.target_id, "finished": _iso(e.finished), "error": e.error.splitlines()[0][:200] if e.error else ""}
                       for e in errors]
    hours = max(1e-9, seconds / 3600)
    policy = retention.policy
    return {"counts": counts, "targets_by_state": states, "signal_outcomes": outcomes, "rung_counts": rungs,
            "targets_per_hour": round(len(done) / hours, 1) if done else 0.0,
            "mean_seconds_per_run": round(seconds / len(done), 2) if done else None,
            "error_capped_targets": store.error_capped_targets(MAX_ATTEMPTS),
            "cache_bytes": retention.cached_bytes(), "retention": settings.retention,
            "cache_budget_bytes": policy.budget_bytes if policy.mode == "budget" else None,
            "last_errors": last_errors, "queries": counts["queries"]}


def source_progress(store: Store) -> dict:
    out: dict[str, dict] = {}
    with store.session() as s:
        for source, state, n in s.execute(select(TargetRow.source, TargetRow.state, func.count())
                                          .group_by(TargetRow.source, TargetRow.state)):
            d = out.setdefault(source, {"new": 0, "done": 0, "error": 0, "total": 0})
            d[state if state in d else "new"] += n; d["total"] += n
    return out


def _last_test_subquery():
    """(signal_id, rung, status) of the last rung each signal reached."""
    last_id = select(TestRow.signal_id, func.max(TestRow.id).label("tid")).group_by(TestRow.signal_id).subquery()
    return (select(TestRow.signal_id, TestRow.rung, TestRow.status).join(last_id, TestRow.id == last_id.c.tid)).subquery()


def _queue_select(status: str, sort: str, desc: bool):
    last = _last_test_subquery()
    col = SORT_COLUMNS.get(sort, SignalRow.sde)
    q = (select(SignalRow, TargetRow, CandidateRow.status.label("decision"), last.c.rung, last.c.status.label("verdict"))
         .join(TargetRow, TargetRow.target_id == SignalRow.target_id)
         .outerjoin(CandidateRow, CandidateRow.signal_id == SignalRow.id)
         .outerjoin(last, last.c.signal_id == SignalRow.id)
         .where(SignalRow.final_status.in_(QUEUE_STATUSES.get(status, QUEUE_STATUSES["candidate_stage1"]))))
    return q.order_by(col.desc() if desc else col.asc(), SignalRow.id.desc())


def _queue_row(sig: SignalRow, t: TargetRow, decision, rung, verdict) -> dict:
    return {"id": sig.id, "target_id": sig.target_id, "period": sig.period, "depth_ppm": sig.depth_ppm,
            "duration_h": sig.duration * 24.0, "sde": sig.sde, "snr": sig.snr, "n_events": sig.n_events,
            "last_rung": rung, "verdict": verdict, "final_status": sig.final_status, "decision": decision,
            "detected": sig.id, "kic": t.kic, "gaia_id": t.gaia_id, "gmag": t.gmag, "source": t.source}


def review_queue(store: Store, status: str = "candidate_stage1", sort: str = "sde", desc: bool = True,
                 limit: int = 500) -> list[dict]:
    with store.session() as s:
        return [_queue_row(*row) for row in s.execute(_queue_select(status, sort, desc).limit(limit)).all()]


def neighbours(store: Store, signal_id: int, status: str, sort: str, desc: bool) -> tuple[int | None, int | None]:
    with store.session() as s:
        ids = [row[0].id for row in s.execute(_queue_select(status, sort, desc)).all()]
    if signal_id not in ids:
        return None, None
    i = ids.index(signal_id)
    return (ids[i - 1] if i > 0 else None), (ids[i + 1] if i + 1 < len(ids) else None)


def signal_detail(store: Store, signal_id: int) -> dict | None:
    with store.session() as s:
        sig = s.get(SignalRow, signal_id)
        if sig is None:
            return None
        t = s.get(TargetRow, sig.target_id); run = s.get(RunRow, sig.run_id)
        tests = s.scalars(select(TestRow).where(TestRow.signal_id == signal_id).order_by(TestRow.id)).all()
        cand = s.scalars(select(CandidateRow).where(CandidateRow.signal_id == signal_id)).first()
        signal = {c.name: getattr(sig, c.name) for c in SignalRow.__table__.columns}
        target = {c.name: getattr(t, c.name) for c in TargetRow.__table__.columns} if t else {"target_id": sig.target_id}
        run_d = {"id": run.id, "status": run.status, "started": _iso(run.started), "finished": _iso(run.finished),
                 "detector_version": run.detector_version, "ladder_version": run.ladder_version,
                 "periodogram_sha256": run.periodogram_sha256} if run else None
        cand_d = {"status": cand.status, "note": cand.note, "decided_at": _iso(cand.decided_at), "branch": cand.branch} if cand else None
        return {"signal": signal, "target": target, "run": run_d,
                "tests": [{"rung": x.rung, "status": x.status, "reason": x.reason, "duration_s": x.duration_s,
                           "metrics": x.metrics} for x in tests],
                "candidate": cand_d, "decisions": store.decisions(signal_id)}


def approved(store: Store, status: str = "approved") -> list[dict]:
    with store.session() as s:
        rows = s.execute(select(SignalRow, TargetRow, CandidateRow)
                         .join(CandidateRow, CandidateRow.signal_id == SignalRow.id)
                         .join(TargetRow, TargetRow.target_id == SignalRow.target_id)
                         .where(CandidateRow.status == status).order_by(CandidateRow.decided_at.desc())).all()
        return [{**_queue_row(sig, t, c.status, None, None), "note": c.note, "decided_at": _iso(c.decided_at),
                 "doi": c.doi} for sig, t, c in rows]


def review_document(store: Store, signal_id: int) -> dict | None:
    d = signal_detail(store, signal_id)
    if d is None:
        return None
    with store.session() as s:
        qs = s.scalars(select(QueryRow).where(QueryRow.target_id == d["signal"]["target_id"]).order_by(QueryRow.id)).all()
        arts = s.scalars(select(ArtifactRow).where(ArtifactRow.target_id == d["signal"]["target_id"])).all()
        queries_d = [{"service": q.service, "url": q.url, "params": json.loads(q.params or "{}"), "utc": _iso(q.utc),
                      "status": q.status, "nbytes": q.nbytes, "sha256": q.sha256, "error": q.error} for q in qs]
        arts_d = [{"sha256": a.sha256, "nbytes": a.nbytes, "url": a.url, "kind": a.kind, "pinned": a.pinned,
                   "deleted_at": _iso(a.deleted_at)} for a in arts]
    return {**d, "queries": queries_d, "artifacts": arts_d}
```

Then replace the body of `cli.status` with:

```python
@app.command()
def status():
    """Counts, targets per state, outcomes, rung verdicts, throughput, cache usage, recent errors."""
    from skysift.ui import queries
    s, store, blob = _ctx()
    ret = Retention(RetentionPolicy.parse(s.retention), store, blob)
    _plain(_console(), jsonutil.dumps(queries.summary(store, ret, s), indent=1, default=str))
```

and drop the now-unused `select`, `RunRow`, `SignalRow` imports from `cli.py` if nothing else uses them. `skysift.ui.queries` imports only FastAPI-free modules, so `status` works without the `ui` extra installed.

- [ ] **Step 5: Run the tests**

Run: `PY -m pytest tests/unit/test_ui_queries.py tests/unit/test_cli.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/skysift/ui/queries.py src/skysift/cli.py tests/unit/ui_seed.py tests/unit/test_ui_queries.py tests/unit/test_cli.py
git commit -m "feat(ui): read queries for every page; status shares the summary"
```

---

### Task 5: Periodogram summary from the detector to the store

**Files:**
- Create: `src/skysift/detectors/periodogram.py`
- Modify: `src/skysift/detectors/box_dip.py` (`search`), `src/skysift/detectors/base.py` (optional `search` in the protocol), `src/skysift/config.py` (`store_periodogram`), `src/skysift/pipeline.py` (`_cpu_work`, `_write_results`, `run`), `src/skysift/store/db.py` (`record_results(..., periodogram=None)`), `src/skysift/cli.py` (`run --periodogram`, `vet --spectrum`), `config/default.yaml`
- Test: `tests/unit/test_periodogram.py`, `tests/unit/test_pipeline.py`, `tests/unit/test_cli.py`

**Interfaces:**
- Produces: `PeriodogramSummary(min_period, max_period, n_bins=2048, envelope: list[float | None], peaks: list[dict], band_edges: list[float], n_periods: int)` with `from_spectrum(periods, sde, min_period, max_period, band_edges, peaks, n_bins)`, `bin_periods() -> np.ndarray`, `to_npz() -> bytes`, `from_npz(data) -> PeriodogramSummary`; `SearchResult(signals: list[Signal], periodogram: PeriodogramSummary | None, spectrum: dict | None)`; `BoxDip.search(lc, fnorm, thresholds, keep_spectrum=False) -> SearchResult`; `Settings.store_periodogram: str` in `{"all","signals","off"}`; worker payload key `"periodogram": dict | None`; `_cpu_work(..., store_periodogram="all", spectrum_path=None)`; `_write_results(store, blob, run_id, target_id, payload)`; `Store.record_results(..., periodogram: tuple[str, int, str] | None = None)` (sha256, nbytes, local_path); `PERIODOGRAM_URL = "skysift://periodogram/{target_id}"`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_periodogram.py`:

```python
import json

import numpy as np

from skysift import jsonutil
from skysift.config import Thresholds
from skysift.detectors.box_dip import BoxDip
from skysift.detectors.periodogram import PeriodogramSummary
from skysift.lc.ops import detrend
from tests.synthetic import inject_transit, make_lightcurve


def test_envelope_has_gaps_for_empty_bins_and_round_trips():
    periods = np.array([0.6, 0.61, 5.0, 5.1, 150.0]); sde = np.array([3.0, 7.5, 2.0, 9.9, 4.0])
    ps = PeriodogramSummary.from_spectrum(periods, sde, 0.5, 200.0, band_edges=[0.5, 10.0, 200.0],
                                          peaks=[{"period": 5.1, "sde": 9.9, "duration": 0.1, "rank": 0}], n_bins=64)
    env = np.array(ps.envelope, dtype=float)
    assert env.size == 64 and np.isnan(env).sum() > 50                     # most bins hold no trial period
    centres = ps.bin_periods()
    assert np.nanmax(env) == 9.9 and abs(centres[int(np.nanargmax(env))] - 5.1) / 5.1 < 0.1
    assert env[np.searchsorted(centres, 0.61) - 1] == 7.5 or env[np.searchsorted(centres, 0.61)] == 7.5   # max per bin
    back = PeriodogramSummary.from_npz(ps.to_npz())
    assert back.peaks == ps.peaks and back.n_bins == 64 and np.allclose(np.array(back.envelope, float), env, equal_nan=True)
    assert len(ps.to_npz()) < 16_000
    text = jsonutil.dumps(ps.model_dump())
    assert "NaN" not in text and json.loads(text)["envelope"][0] is None


def test_box_dip_search_returns_signals_and_envelope():
    lc = inject_transit(make_lightcurve(n_days=500, seed=5, noise_ppm=600), 12.5, 140.0, 0.12, 20000)
    th = Thresholds(min_period_days=5, max_period_days=40)
    res = BoxDip().search(lc, detrend(lc), th)
    assert res.signals and abs(res.signals[0].period - 12.5) / 12.5 < 0.01 and res.spectrum is None
    ps = res.periodogram
    assert ps is not None and ps.n_bins == 2048 and (ps.min_period, ps.max_period) == (5.0, 40.0)
    assert ps.peaks[0]["period"] == res.signals[0].period and ps.peaks[0]["sde"] == res.signals[0].sde
    env = np.array(ps.envelope, float)
    assert np.nanmax(env) >= res.signals[0].sde - 1e-6                     # the refined peak is at least the coarse max
    assert ps.n_periods == res.signals[0].extra["n_periods"] and len(ps.band_edges) == 25
    assert BoxDip().detect(lc, detrend(lc), th)[0].period == res.signals[0].period
    with_spec = BoxDip().search(lc, detrend(lc), th, keep_spectrum=True)
    assert set(with_spec.spectrum) == {"period", "power", "sde", "duration"} and len(with_spec.spectrum["period"]) == ps.n_periods


def test_search_on_too_short_curve_has_no_periodogram():
    lc = make_lightcurve(n_days=3, seed=1)
    res = BoxDip().search(lc, detrend(lc), Thresholds(min_period_days=5, max_period_days=40))
    assert res.signals == [] and res.periodogram is None
```

`tests/unit/test_pipeline.py` additions (place after `test_run_end_to_end_with_fake_source_and_resume`, reusing its helpers `_env`, `ScriptedSource`, `_tgt`; read that test first so the names match):

```python
@pytest.mark.parametrize("mode,expect_file", [("all", True), ("signals", False), ("off", False)])
def test_store_periodogram_modes(settings, monkeypatch, mode, expect_file):
    from skysift.detectors.periodogram import PeriodogramSummary
    monkeypatch.setenv("SKYSIFT_WORKERS_CPU", "1")
    monkeypatch.setenv("SKYSIFT_THRESHOLDS__MIN_PERIOD_DAYS", "5"); monkeypatch.setenv("SKYSIFT_THRESHOLDS__MAX_PERIOD_DAYS", "40")
    monkeypatch.setenv("SKYSIFT_STORE_PERIODOGRAM", mode)
    s, store, blob = _env(monkeypatch, settings)
    lc = make_lightcurve(n_days=500, seed=11, noise_ppm=600)          # pure noise: no signal reaches the ladder
    src = ScriptedSource([_tgt("quiet")], {"quiet": lc}, store, blob)
    out = run(src, store, blob, s, filter={}, with_novelty=False)
    assert out["processed"] == 1
    with store.session() as ss:
        sha = ss.query(db.RunRow).one().periodogram_sha256
        arts = [a for a in ss.query(db.ArtifactRow) if a.kind == "summary"]
    assert (sha is not None) == expect_file and (len(arts) == 1) == expect_file
    if expect_file:
        ps = PeriodogramSummary.from_npz(blob.get(arts[0].local_path))
        assert ps.n_bins == 2048 and ps.peaks == []                      # nothing cleared the detector's own floor
        assert arts[0].url == "skysift://periodogram/quiet" and not arts[0].pinned and blob.exists(arts[0].local_path)


def test_signals_mode_stores_periodogram_when_a_signal_exists(settings, monkeypatch):
    monkeypatch.setenv("SKYSIFT_WORKERS_CPU", "1")
    monkeypatch.setenv("SKYSIFT_THRESHOLDS__MIN_PERIOD_DAYS", "5"); monkeypatch.setenv("SKYSIFT_THRESHOLDS__MAX_PERIOD_DAYS", "40")
    monkeypatch.setenv("SKYSIFT_STORE_PERIODOGRAM", "signals")
    s, store, blob = _env(monkeypatch, settings)
    lc = inject_transit(make_lightcurve(n_days=500, seed=5, noise_ppm=600), 12.5, 140.0, 0.12, 20000)
    src = ScriptedSource([_tgt("planet")], {"planet": lc}, store, blob)
    run(src, store, blob, s, filter={}, with_novelty=False)
    with store.session() as ss:
        run_row = ss.query(db.RunRow).one(); arts = [a for a in ss.query(db.ArtifactRow) if a.kind == "summary"]
    assert run_row.periodogram_sha256 == arts[0].sha256 and arts[0].pinned       # pinned with the evidence
```

Note: `ScriptedSource` in the existing test file writes raw artifacts through the blob; a pure-noise 500 d curve with the 5 to 40 d window runs in about 25 s per test, so these three parametrised cases add about 100 s. If `_env` or `_tgt` have different names in the file, adapt the calls, not the assertions.

`tests/unit/test_cli.py` addition:

```python
def test_run_passes_periodogram_mode(settings, monkeypatch):
    from skysift import cli, pipeline
    seen = {}
    monkeypatch.setattr(pipeline, "run", lambda source, store, blob, settings, filter, **kw: seen.update(settings=settings) or {"processed": 0})
    monkeypatch.setattr(cli, "_source", lambda name, store, blob: object())
    r = CliRunner().invoke(app, ["run", "--source", "kbonus_bkg", "--periodogram", "off"])
    assert r.exit_code == 0, r.output
    assert seen["settings"].store_periodogram == "off"
    assert CliRunner().invoke(app, ["run", "--source", "kbonus_bkg", "--periodogram", "sometimes"]).exit_code != 0
```

- [ ] **Step 2: Run to verify failure**

Run: `PY -m pytest tests/unit/test_periodogram.py tests/unit/test_cli.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: skysift.detectors.periodogram`.

- [ ] **Step 3: Write `periodogram.py`**

```python
"""Per-target periodogram summary: the per-band normalised SDE envelope on a fixed log-period grid plus the exact top
peaks. About 8 kB per star against about 2 MB for the raw spectrum, and the envelope keeps what the eye and the
false-alarm calibration need: peak heights, their isolation, aliases and the noise floor."""
from __future__ import annotations

import io

import numpy as np
from pydantic import BaseModel, Field

N_BINS = 2048


class PeriodogramSummary(BaseModel):
    min_period: float
    max_period: float
    n_bins: int = N_BINS
    envelope: list[float | None]            # max SDE per log-period bin; None where no trial period fell
    peaks: list[dict] = Field(default_factory=list)   # {"period", "sde", "duration", "rank"}
    band_edges: list[float] = Field(default_factory=list)
    n_periods: int = 0

    def bin_periods(self) -> np.ndarray:
        edges = np.geomspace(self.min_period, self.max_period, self.n_bins + 1)
        return np.sqrt(edges[:-1] * edges[1:])

    @classmethod
    def from_spectrum(cls, periods: np.ndarray, sde: np.ndarray, min_period: float, max_period: float,
                      band_edges, peaks: list[dict], n_bins: int = N_BINS) -> PeriodogramSummary:
        periods = np.asarray(periods, float); sde = np.asarray(sde, float)
        env = np.full(n_bins, np.nan)
        if periods.size:
            frac = np.log(periods / min_period) / np.log(max_period / min_period)
            idx = np.clip(np.floor(frac * n_bins).astype(int), 0, n_bins - 1)
            np.fmax.at(env, idx, sde)              # fmax ignores the NaN fill, so each bin ends at its maximum SDE
        return cls(min_period=float(min_period), max_period=float(max_period), n_bins=n_bins,
                   envelope=[None if not np.isfinite(v) else float(v) for v in env],
                   peaks=[{k: (float(v) if isinstance(v, (int, float, np.generic)) and k != "rank" else v) for k, v in p.items()}
                          for p in peaks],
                   band_edges=[float(b) for b in band_edges], n_periods=int(periods.size))

    def to_npz(self) -> bytes:
        buf = io.BytesIO()
        env = np.array([np.nan if v is None else v for v in self.envelope], dtype=np.float32)
        peaks = np.array([(p["period"], p["sde"], p["duration"], p["rank"]) for p in self.peaks], dtype=np.float64).reshape(-1, 4)
        np.savez_compressed(buf, envelope=env, peaks=peaks, band_edges=np.asarray(self.band_edges, np.float64),
                            meta=np.array([self.min_period, self.max_period, self.n_bins, self.n_periods], np.float64))
        return buf.getvalue()

    @classmethod
    def from_npz(cls, data: bytes) -> PeriodogramSummary:
        with np.load(io.BytesIO(data)) as z:
            env, peaks, edges, meta = z["envelope"], z["peaks"], z["band_edges"], z["meta"]
        return cls(min_period=float(meta[0]), max_period=float(meta[1]), n_bins=int(meta[2]), n_periods=int(meta[3]),
                   envelope=[None if not np.isfinite(v) else float(v) for v in env],
                   peaks=[{"period": float(p), "sde": float(s), "duration": float(d), "rank": int(r)} for p, s, d, r in peaks],
                   band_edges=[float(b) for b in edges])
```

`np.load` on bytes from our own blob is safe (`allow_pickle` defaults to False).

- [ ] **Step 4: `BoxDip.search`**

In `box_dip.py` add near the top:

```python
from dataclasses import dataclass, field

from skysift.detectors.periodogram import PeriodogramSummary


@dataclass
class SearchResult:
    signals: list[Signal] = field(default_factory=list)
    periodogram: PeriodogramSummary | None = None
    spectrum: dict | None = None            # full arrays (period, power, sde, duration), only on request
```

Rename the current `detect` body to `search(self, lc, fnorm, thresholds, keep_spectrum: bool = False) -> SearchResult`; every early `return []` becomes `return SearchResult()`. After the `out` list is built, finish with:

```python
        edges = np.geomspace(th.min_period_days, pmax, self.n_bands + 1)
        peaks = [{"period": s.period, "sde": s.sde, "duration": s.duration, "rank": i} for i, s in enumerate(out)]
        summary = PeriodogramSummary.from_spectrum(periods, spec["sde"], th.min_period_days, pmax, edges, peaks)
        spectrum = ({"period": periods, "power": power, "sde": spec["sde"], "duration": spec["duration"]}
                    if keep_spectrum else None)
        return SearchResult(signals=out, periodogram=summary, spectrum=spectrum)
```

and add:

```python
    def detect(self, lc: LightCurve, fnorm: np.ndarray, thresholds: Thresholds) -> list[Signal]:
        return self.search(lc, fnorm, thresholds).signals
```

In `detectors/base.py` extend the protocol with `def search(self, lc, fnorm, thresholds, keep_spectrum: bool = False): ...` (document that detectors without it are wrapped by `pipeline._search`).

- [ ] **Step 5: Settings and pipeline**

`config.py` `Settings`: add `store_periodogram: str = "all"` and a validator:

```python
    @model_validator(mode="after")
    def _periodogram_mode(self) -> Settings:
        if self.store_periodogram not in ("all", "signals", "off"):
            raise ValueError(f"store_periodogram must be all, signals or off, got {self.store_periodogram!r}")
        return self
```

`config/default.yaml`: add `store_periodogram: all` after `retention`.

`pipeline.py`:

```python
PERIODOGRAM_URL = "skysift://periodogram/{target_id}"


def _search(det, lc, fn, th, keep_spectrum=False):
    """Detectors that implement search() give signals plus the periodogram; older ones give signals only."""
    if hasattr(det, "search"):
        return det.search(lc, fn, th, keep_spectrum=keep_spectrum)
    from skysift.detectors.box_dip import SearchResult
    return SearchResult(signals=det.detect(lc, fn, th))
```

`_cpu_work` gains `store_periodogram: str = "all", spectrum_path: str | None = None`; replace `signals = det.detect(lc, fn, th)` with:

```python
    res = _search(det, lc, fn, th, keep_spectrum=spectrum_path is not None)
    signals = res.signals
    if spectrum_path and res.spectrum is not None:
        import numpy as np
        np.savez_compressed(spectrum_path, **{k: np.asarray(v) for k, v in res.spectrum.items()})
    keep = store_periodogram == "all" or (store_periodogram == "signals" and bool(signals))
    periodogram = res.periodogram.model_dump() if (keep and res.periodogram is not None) else None
```

and return `jsonutil.dumps({"signals": out, "queries": recorder.rows, "periodogram": periodogram}, default=str)`.

`_write_results` becomes:

```python
def _write_results(store: Store, blob: Blob, run_id: int, target_id: str, payload: str) -> dict:
    """Parse a worker payload, store the periodogram summary (content addressed, so a retry reuses the same file)
    and write everything else atomically through ``Store.record_results``."""
    data = json.loads(payload)
    items = data["signals"]
    pin = any(i["outcome"] in ("pass", "undetermined") for i in items)
    periodogram = None
    if data.get("periodogram"):
        npz = PeriodogramSummary(**data["periodogram"]).to_npz()
        sha, loc = blob.put(npz, f"{target_id}_periodogram.npz")
        periodogram = (sha, len(npz), loc)
    return store.record_results(run_id, target_id, items, data["queries"], pin_evidence=pin, periodogram=periodogram)
```

(import `PeriodogramSummary` at the top of `pipeline.py`). In `run`, pass `settings.store_periodogram` into `_cpu_work` via `top_up` and `blob` into `_write_results`. Update the existing test `test_write_results_keeps_nan_and_numpy_metrics` to the new signature (`_write_results(store, LocalBlob(tmp_path/"b"), rid, "t1", payload)`; the payload has no `periodogram` key, which must be accepted).

`Store.record_results(..., periodogram: tuple[str, int, str] | None = None)`: inside the transaction, before the run row is finished:

```python
            if periodogram is not None:
                sha, nbytes, loc = periodogram
                self._upsert_artifact(s, sha, nbytes, PERIODOGRAM_URL.format(target_id=target_id), loc, target_id,
                                      pin_evidence, "summary")
                run_pg = sha
            ...
            run = s.get(RunRow, run_id); run.status, run.error, run.finished = "done", "", utcnow()
            run.periodogram_sha256 = run_pg
```

(`run_pg = None` by default; `PERIODOGRAM_URL` lives in `db.py` and `pipeline.py` imports it from there to avoid a cycle.)

- [ ] **Step 6: CLI options**

`run` gains `periodogram: str | None = typer.Option(None, "--periodogram", help="Store the SDE envelope per target: all (default), signals, off.")`; after the thresholds overrides:

```python
    if periodogram is not None:
        if periodogram not in ("all", "signals", "off"):
            raise typer.BadParameter("one of all, signals, off", param_hint="--periodogram")
        s.store_periodogram = periodogram
```

`vet` gains `spectrum: bool = typer.Option(False, "--spectrum", help="Also write the raw BLS spectrum (.npz, about 2 MB).")`; it passes `spectrum_path=str(s.data_dir / "vet" / f"{target_id}_spectrum.npz")` (after `mkdir`) when set and `store_periodogram="all"` otherwise, and prints the spectrum path. The `vet` JSON now also carries `periodogram`.

- [ ] **Step 7: Run the tests**

Run: `PY -m pytest tests/unit/test_periodogram.py tests/unit/test_pipeline.py tests/unit/test_cli.py tests/unit/test_box_dip.py tests/unit/test_store.py -q -p no:cacheprovider`
Expected: PASS (about 4 minutes).

- [ ] **Step 8: Commit**

```bash
git add src/skysift/detectors src/skysift/config.py config/default.yaml src/skysift/pipeline.py src/skysift/store/db.py src/skysift/cli.py tests/unit/test_periodogram.py tests/unit/test_pipeline.py tests/unit/test_cli.py
git commit -m "feat(detector): periodogram SDE envelope per target, stored through the blob (all/signals/off)"
```

---

### Task 6: Sweep launcher

**Files:**
- Create: `src/skysift/ui/sweeps.py` (replacing the Task 1 stub)
- Test: `tests/unit/test_sweeps.py`

**Interfaces:**
- Produces: `SweepRequest(source, limit=500, min_period=None, max_period=None, retention="stream", workers_io=None, workers_cpu=None, background_only=True, gmag_max=19.0, store_periodogram="all")` with `.validate()` (raises `ValueError`) and `.argv(python=sys.executable) -> list[str]` and `.env() -> dict`; `SweepActive(RuntimeError)`; `SweepLauncher(data_dir, command: list[str] | None = None)` with `start(req) -> dict`, `active() -> dict | None`, `tail(n=200) -> list[str]`, `stop() -> bool`, `history() -> list[dict]`. Files live in `<data_dir>/ui/sweeps/`: `active.json`, `<stamp>.log`, `<stamp>.json`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_sweeps.py`:

```python
import json
import sys
import time

import pytest

from skysift.ui.sweeps import SweepActive, SweepLauncher, SweepRequest

SLEEPER = [sys.executable, "-c", "import sys, time; print('started', flush=True); time.sleep(20)"]
QUICK = [sys.executable, "-c", "print('{\"processed\": 1}')"]


def test_request_argv_and_env():
    req = SweepRequest(source="kbonus_bkg", limit=50, min_period=1.0, max_period=30.0, retention="budget:20",
                       workers_cpu=3, store_periodogram="signals", background_only=False, gmag_max=18.0)
    argv = req.argv(python="PY")
    assert argv[:4] == ["PY", "-m", "skysift", "run"] and "--source" in argv and "kbonus_bkg" in argv
    assert argv[argv.index("--limit") + 1] == "50" and argv[argv.index("--min-period") + 1] == "1.0"
    assert "--all" in argv and argv[argv.index("--gmag-max") + 1] == "18.0" and argv[argv.index("--periodogram") + 1] == "signals"
    env = req.env()
    assert env["SKYSIFT_RETENTION"] == "budget:20" and env["SKYSIFT_WORKERS_CPU"] == "3" and "SKYSIFT_WORKERS_IO" not in env
    with pytest.raises(ValueError): SweepRequest(source="kbonus_bkg", min_period=5, max_period=2).validate()
    with pytest.raises(ValueError): SweepRequest(source="kbonus_bkg", retention="bogus").validate()
    with pytest.raises(ValueError): SweepRequest(source="kbonus_bkg", store_periodogram="maybe").validate()


def test_start_tail_stop_and_history(tmp_path):
    launcher = SweepLauncher(tmp_path, command=SLEEPER)
    info = launcher.start(SweepRequest(source="kbonus_bkg", limit=5))
    assert info["pid"] > 0 and (tmp_path / "ui" / "sweeps" / "active.json").exists()
    for _ in range(50):
        if launcher.tail() == ["started"]: break
        time.sleep(0.1)
    assert launcher.tail() == ["started"] and launcher.active()["request"]["limit"] == 5
    assert launcher.stop() is True
    assert launcher.active() is None
    hist = launcher.history()
    assert len(hist) == 1 and hist[0]["exit_code"] is not None and hist[0]["log"].endswith(".log")


def test_second_start_refused_while_active(tmp_path):
    launcher = SweepLauncher(tmp_path, command=SLEEPER)
    launcher.start(SweepRequest(source="kbonus_bkg"))
    with pytest.raises(SweepActive):
        launcher.start(SweepRequest(source="kbonus_bkg"))
    launcher.stop()


def test_finished_child_is_moved_to_history_with_summary(tmp_path):
    launcher = SweepLauncher(tmp_path, command=QUICK)
    launcher.start(SweepRequest(source="kbonus_bkg"))
    for _ in range(50):
        if launcher.active() is None: break
        time.sleep(0.1)
    hist = launcher.history()
    assert hist[0]["exit_code"] == 0 and hist[0]["summary"] == {"processed": 1}


def test_stale_lock_from_dead_pid_is_removed(tmp_path):
    d = tmp_path / "ui" / "sweeps"; d.mkdir(parents=True)
    (d / "active.json").write_text(json.dumps({"pid": 999999999, "argv": [], "started": "2026-10-14T00:00:00+00:00",
                                                "log": str(d / "x.log"), "request": {"source": "kbonus_bkg"}}), encoding="utf-8")
    launcher = SweepLauncher(tmp_path, command=SLEEPER)
    assert launcher.active() is None and not (d / "active.json").exists()
    assert launcher.history()[0]["exit_code"] is None          # recorded as unknown, not lost
    launcher.start(SweepRequest(source="kbonus_bkg")); launcher.stop()
```

- [ ] **Step 2: Run to verify failure**

Run: `PY -m pytest tests/unit/test_sweeps.py -q -p no:cacheprovider`
Expected: FAIL with `ImportError: cannot import name 'SweepRequest'`.

- [ ] **Step 3: Implement**

`src/skysift/ui/sweeps.py`:

```python
"""Start, watch and stop one engine sweep from the dashboard. The sweep is a child `python -m skysift run` (never a
console .exe), its output goes to a log file, and a lock file allows one sweep per data directory. Interrupted
runs need no cleanup: the engine's start_run marks them superseded on the next sweep."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from skysift.config import Thresholds
from skysift.store.retention import RetentionPolicy

PERIODOGRAM_MODES = ("all", "signals", "off")


class SweepActive(RuntimeError):
    pass


@dataclass
class SweepRequest:
    source: str
    limit: int = 500
    min_period: float | None = None
    max_period: float | None = None
    retention: str = "stream"
    workers_io: int | None = None
    workers_cpu: int | None = None
    background_only: bool = True
    gmag_max: float = 19.0
    store_periodogram: str = "all"

    def validate(self) -> None:
        if not self.source or not self.source.replace("_", "").isalnum():
            raise ValueError("source name is required")
        if self.limit is not None and self.limit <= 0:
            raise ValueError("limit must be positive")
        RetentionPolicy.parse(self.retention)                      # raises ValueError
        if self.store_periodogram not in PERIODOGRAM_MODES:
            raise ValueError(f"store_periodogram must be one of {PERIODOGRAM_MODES}")
        if self.min_period is not None or self.max_period is not None:
            base = Thresholds()
            Thresholds(min_period_days=self.min_period if self.min_period is not None else base.min_period_days,
                       max_period_days=self.max_period if self.max_period is not None else base.max_period_days)
        for w in (self.workers_io, self.workers_cpu):
            if w is not None and w <= 0: raise ValueError("worker counts must be positive")

    def argv(self, python: str = sys.executable) -> list[str]:
        a = [python, "-m", "skysift", "run", "--source", self.source, "--gmag-max", str(self.gmag_max),
             "--periodogram", self.store_periodogram, "--background-only" if self.background_only else "--all"]
        if self.limit is not None: a += ["--limit", str(self.limit)]
        if self.min_period is not None: a += ["--min-period", str(self.min_period)]
        if self.max_period is not None: a += ["--max-period", str(self.max_period)]
        return a

    def env(self) -> dict:
        env = {"SKYSIFT_RETENTION": self.retention}
        if self.workers_io is not None: env["SKYSIFT_WORKERS_IO"] = str(self.workers_io)
        if self.workers_cpu is not None: env["SKYSIFT_WORKERS_CPU"] = str(self.workers_cpu)
        return env


def _pid_alive(pid: int) -> bool:
    if pid <= 0: return False
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)              # PROCESS_QUERY_LIMITED_INFORMATION
        if not h: return False
        try:
            code = ctypes.c_ulong()
            ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
            return bool(ok) and code.value == 259            # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0); return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class SweepLauncher:
    def __init__(self, data_dir: Path, command: list[str] | None = None):
        self.dir = Path(data_dir) / "ui" / "sweeps"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.command = command                     # tests replace the real engine command with a small script
        self._proc: subprocess.Popen | None = None
        self._log_fh = None

    @property
    def lock(self) -> Path:
        return self.dir / "active.json"

    def start(self, req: SweepRequest) -> dict:
        req.validate()
        if self.active() is not None:
            raise SweepActive("a sweep is already running in this data directory")
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        log = self.dir / f"{stamp}.log"
        argv = self.command or req.argv()
        env = {**os.environ, **req.env(), "PYTHONUNBUFFERED": "1"}
        self._log_fh = open(log, "w", encoding="utf-8")       # noqa: SIM115  (handed to the child; closed on finish)
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        self._proc = subprocess.Popen(argv, stdout=self._log_fh, stderr=subprocess.STDOUT, env=env, creationflags=flags)
        info = {"pid": self._proc.pid, "argv": argv, "started": datetime.now(UTC).isoformat(), "log": str(log),
                "request": asdict(req), "stamp": stamp}
        self.lock.write_text(json.dumps(info), encoding="utf-8")
        return info

    def _finish(self, info: dict, exit_code: int | None) -> None:
        """Move the lock to a history record with the exit code and the engine's summary (the last JSON on stdout)."""
        summary = None
        try:
            lines = Path(info["log"]).read_text(encoding="utf-8", errors="replace").splitlines()
            for i in range(len(lines) - 1, -1, -1):
                if lines[i].strip() == "{":
                    try: summary = json.loads("\n".join(lines[i:])); break
                    except json.JSONDecodeError: continue
                if lines[i].startswith("{"):
                    try: summary = json.loads(lines[i]); break
                    except json.JSONDecodeError: continue
        except OSError:
            pass
        record = {**info, "exit_code": exit_code, "finished": datetime.now(UTC).isoformat(), "summary": summary}
        (self.dir / f"{info.get('stamp') or 'unknown'}.json").write_text(json.dumps(record), encoding="utf-8")
        self.lock.unlink(missing_ok=True)
        if self._log_fh: self._log_fh.close(); self._log_fh = None
        self._proc = None

    def active(self) -> dict | None:
        if not self.lock.exists():
            return None
        info = json.loads(self.lock.read_text(encoding="utf-8"))
        if self._proc is not None and self._proc.pid == info["pid"]:
            code = self._proc.poll()
            if code is None: return info
            self._finish(info, code); return None
        if _pid_alive(info["pid"]):                 # started by an earlier dashboard process: re-attach read-only
            return info
        self._finish(info, None); return None

    def tail(self, n: int = 200) -> list[str]:
        info = self.active() or (self.history()[0] if self.history() else None)
        if info is None: return []
        try:
            lines = Path(info["log"]).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
        return [ln.rstrip("\r") for ln in lines[-n:] if ln.strip()]

    def stop(self) -> bool:
        info = self.active()
        if info is None: return False
        if self._proc is not None and self._proc.pid == info["pid"]:
            self._proc.terminate()
            try: code = self._proc.wait(timeout=30)
            except subprocess.TimeoutExpired: self._proc.kill(); code = self._proc.wait(timeout=10)
        else:
            try: os.kill(info["pid"], signal.SIGTERM)
            except OSError: pass
            code = None
        self._finish(info, code)
        return True

    def history(self) -> list[dict]:
        out = []
        for p in sorted(self.dir.glob("*.json"), reverse=True):
            if p.name == "active.json": continue
            try: out.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError): continue
        return out
```

On Windows `terminate()` is `TerminateProcess`; the engine's spawn workers die with their parent's process group when the console closes, and any that outlive it exit on their own at the end of the current target (at most about 250 s). The next sweep's `start_run` marks interrupted runs superseded.

- [ ] **Step 4: Run the tests**

Run: `PY -m pytest tests/unit/test_sweeps.py -q -p no:cacheprovider`
Expected: PASS (about 5 s).

- [ ] **Step 5: Commit**

```bash
git add src/skysift/ui/sweeps.py tests/unit/test_sweeps.py
git commit -m "feat(ui): sweep launcher with lock file, log tail, stop and history"
```

---
### Task 7: Static assets: vendored HTMX and the Plotly downloader

**Files:**
- Create: `src/skysift/ui/static/htmx.min.js` (downloaded once, committed), `src/skysift/ui/assets.py` (replacing the Task 1 stub)
- Test: `tests/unit/test_assets.py`

**Interfaces:**
- Produces: `PLOTLY_VERSION = "2.35.2"`, `PLOTLY_URL`, `PLOTLY_SHA256` (hex), `ensure_plotly(data_dir: Path, fetch=net.fetch) -> Path | None` returning `<data_dir>/ui/assets/plotly.min.js` when present or downloaded and verified, `None` when offline or the hash fails (never raises); `HTMX_VERSION = "2.0.3"`.

- [ ] **Step 1: Fetch the two assets once and record the hashes**

Run this snippet (network required; truststore handles the TLS interception):

```bash
PY -c "import truststore; truststore.inject_into_ssl(); import requests, hashlib, pathlib; h=requests.get('https://unpkg.com/htmx.org@2.0.3/dist/htmx.min.js', timeout=60); h.raise_for_status(); pathlib.Path('src/skysift/ui/static/htmx.min.js').write_bytes(h.content); print('htmx', len(h.content), hashlib.sha256(h.content).hexdigest()); p=requests.get('https://cdn.plot.ly/plotly-2.35.2.min.js', timeout=120); p.raise_for_status(); print('plotly', len(p.content), hashlib.sha256(p.content).hexdigest())"
```

Copy the printed Plotly hash into `PLOTLY_SHA256` below. The HTMX file (about 50 kB) is committed; Plotly (about 3.6 MB) is not committed and is not written anywhere by this snippet. Note in the PR description that both hashes were taken from the vendor CDN on the day of the task.

- [ ] **Step 2: Write the failing tests**

`tests/unit/test_assets.py`:

```python
import hashlib

from skysift.net import ServiceUnavailable
from skysift.ui import assets
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
    assert ensure_plotly(tmp_path, fetch=lambda url, *, service, **kw: b"tampered") is None
    assert not (tmp_path / "ui" / "assets" / "plotly.min.js").exists()

    def offline(url, *, service, **kw): raise ServiceUnavailable("no network")
    assert ensure_plotly(tmp_path, fetch=offline) is None
```

- [ ] **Step 3: Run to verify failure**

Run: `PY -m pytest tests/unit/test_assets.py -q -p no:cacheprovider`
Expected: FAIL (`STATIC` and `PLOTLY_URL` missing).

- [ ] **Step 4: Implement `assets.py`**

```python
"""Front-end assets without a CDN at view time. HTMX (about 50 kB) is vendored in the package; Plotly (about 3.6 MB)
is downloaded once into the data directory, SHA-256 verified against the hash recorded here, and served locally."""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

from skysift import net

log = logging.getLogger(__name__)
STATIC = Path(__file__).resolve().parent / "static"
HTMX_VERSION = "2.0.3"
PLOTLY_VERSION = "2.35.2"
PLOTLY_URL = f"https://cdn.plot.ly/plotly-{PLOTLY_VERSION}.min.js"
PLOTLY_SHA256 = "<paste the hash printed in Step 1>"


def ensure_plotly(data_dir: Path, fetch=net.fetch) -> Path | None:
    """Return the local Plotly file, downloading it on first use. Returns None (and logs why) when the network is
    unavailable or the download does not match the recorded hash; the dashboard then runs without plots."""
    target = Path(data_dir) / "ui" / "assets" / "plotly.min.js"
    if target.exists():
        return target
    try:
        data = fetch(PLOTLY_URL, service="plotly-cdn", timeout=120)
    except Exception as e:  # noqa: BLE001  (offline is a normal condition for the dashboard)
        log.warning("Plotly %s not available (%s); plots are disabled until the next start", PLOTLY_VERSION, e)
        return None
    digest = hashlib.sha256(data).hexdigest()
    if digest != PLOTLY_SHA256:
        log.error("Plotly download hash %s does not match the recorded %s; refusing to serve it", digest, PLOTLY_SHA256)
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(f".{os.getpid()}.part")
    tmp.write_bytes(data); os.replace(tmp, target)
    return target
```

`net.fetch` uses `attempts=5` with backoff; offline the first call fails fast on connection errors but retries up to five times (about 20 s). Acceptable for a start-up step; `ui` prints "checking Plotly" before it.

- [ ] **Step 5: Run the tests**

Run: `PY -m pytest tests/unit/test_assets.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/skysift/ui/static/htmx.min.js src/skysift/ui/assets.py tests/unit/test_assets.py
git commit -m "feat(ui): vendor HTMX, download and verify Plotly once"
```

---

### Task 8: Sources page and the Start sweep form

**Files:**
- Create: `src/skysift/sources/factory.py`, `src/skysift/ui/routes/sources.py`, `src/skysift/ui/templates/sources.html`, `src/skysift/ui/templates/partials/sweep_form.html`
- Modify: `src/skysift/cli.py` (`_source` delegates to the factory)
- Test: `tests/unit/test_ui_pages.py`

**Interfaces:**
- Produces: `make_source(name, store, blob)` raising `ValueError` for a non-live source; routes `GET /` (sources page), `GET /partials/sweep-form?source=...`, `POST /sweeps` (form fields: `source, limit, min_period, max_period, retention_mode (keep|stream|budget), budget_gib, workers_io, workers_cpu, background_only (checkbox), gmag_max, store_periodogram, _token`) redirecting `303` to `/live`, `POST /sweeps/stop`.
- Consumes: `load_registry`, `queries.source_progress`, `SweepLauncher`, `SweepRequest`, `render`, `require_token`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_ui_pages.py`:

```python
import sys

from skysift.ui.sweeps import SweepLauncher
from tests.unit.ui_seed import seed

SLEEPER = [sys.executable, "-c", "import time; print('started', flush=True); time.sleep(20)"]


def make_seeded_client(settings, tmp_path, command=SLEEPER):
    store = Store(settings.database_url); store.init_schema()
    blob = blob_from_url(settings.blob_url)
    ids = seed(store, blob)
    launcher = SweepLauncher(tmp_path, command=command)
    app = create_app(settings, store=store, blob=blob, launcher=launcher)
    return TestClient(app), app, ids, launcher


def test_sources_page_lists_registry_and_progress(settings, tmp_path):
    client, app, ids, launcher = make_seeded_client(settings, tmp_path)
    r = client.get("/")
    assert r.status_code == 200
    assert "kbonus_bkg" in r.text and "CPU h/10k" in r.text and "694" in r.text      # 250 s/star -> 694 CPU h per 10k
    assert "3 done" in r.text and "planned" in r.text and "Start sweep" in r.text


def test_start_sweep_form_launches_child_and_redirects(settings, tmp_path):
    client, app, ids, launcher = make_seeded_client(settings, tmp_path)
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


def test_start_sweep_rejects_bad_window(settings, tmp_path):
    client, app, ids, launcher = make_seeded_client(settings, tmp_path)
    r = client.post("/sweeps", data={"source": "kbonus_bkg", "min_period": "40", "max_period": "5", "_token": app.state.token})
    assert r.status_code == 400 and "period window" in r.text and launcher.active() is None
```

- [ ] **Step 2: Run to verify failure**

Run: `PY -m pytest tests/unit/test_ui_pages.py -q -p no:cacheprovider`
Expected: FAIL (404 on `/`).

- [ ] **Step 3: The source factory**

`src/skysift/sources/factory.py`:

```python
"""One place that knows which adapters are live; the CLI and the dashboard both use it."""
from __future__ import annotations

from skysift.sources.base import load_registry


def make_source(name: str, store, blob):
    if name == "kbonus_bkg":
        from skysift.sources.kbonus_bkg import KBonusBkg
        return KBonusBkg(blob, store)
    status = load_registry().get(name)
    raise ValueError(f"source {name} is {'not live yet' if status else 'unknown'}; see `skysift sources`")
```

In `cli.py`, `_source` becomes:

```python
def _source(name: str, store, blob):
    from skysift.sources.factory import make_source
    try:
        return make_source(name, store, blob)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
```

(the existing test asserting "not live" in the output keeps passing).

- [ ] **Step 4: The route and templates**

`src/skysift/ui/routes/sources.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from skysift.sources.base import load_registry
from skysift.ui import queries
from skysift.ui.app import render, require_token
from skysift.ui.sweeps import SweepActive, SweepRequest

router = APIRouter()


def _registry_rows(progress: dict) -> list[dict]:
    rows = []
    for info in load_registry().values():
        p = progress.get(info.name, {"new": 0, "done": 0, "error": 0, "total": 0})
        rows.append({"info": info, "progress": p, "remaining": max(0, info.n_lightcurves - p["done"]),
                     "gb_per_10k": info.bytes_per_star * 1e4 / 1e9, "cpu_h_per_10k": info.cpu_hours_per_10k()})
    return rows


@router.get("/", response_class=HTMLResponse)
def sources_page(request: Request):
    st = request.app.state
    active = st.launcher.active() if st.launcher else None
    return render(request, "sources.html", rows=_registry_rows(queries.source_progress(st.store)), active=active)


@router.get("/partials/sweep-form", response_class=HTMLResponse)
def sweep_form(request: Request, source: str):
    s = request.app.state.settings
    return render(request, "partials/sweep_form.html", source=source, th=s.thresholds, settings=s, error=None, values={})


def _request_from_form(form: dict) -> SweepRequest:
    def num(key, cast, default=None):
        v = form.get(key, "")
        return cast(v) if str(v).strip() != "" else default
    mode = form.get("retention_mode", "stream")
    retention = f"budget:{num('budget_gib', float, 50)}" if mode == "budget" else mode
    return SweepRequest(source=form.get("source", ""), limit=num("limit", int, 500), min_period=num("min_period", float),
                        max_period=num("max_period", float), retention=retention, workers_io=num("workers_io", int),
                        workers_cpu=num("workers_cpu", int), background_only=form.get("background_only") in ("on", "true", "1"),
                        gmag_max=num("gmag_max", float, 19.0), store_periodogram=form.get("store_periodogram", "all"))


@router.post("/sweeps", dependencies=[Depends(require_token)])
async def start_sweep(request: Request):
    st = request.app.state
    form = dict(await request.form())
    try:
        req = _request_from_form(form)
        req.validate()
        st.launcher.start(req)
    except (ValueError, TypeError) as e:
        return render(request, "partials/sweep_form.html", status_code=400, source=form.get("source", ""),
                      th=st.settings.thresholds, settings=st.settings, error=str(e), values=form)
    except SweepActive as e:
        return render(request, "partials/sweep_form.html", status_code=409, source=form.get("source", ""),
                      th=st.settings.thresholds, settings=st.settings, error=str(e), values=form)
    return RedirectResponse("/live", status_code=303)


@router.post("/sweeps/stop", dependencies=[Depends(require_token)])
def stop_sweep(request: Request, _token: str = Form(None)):
    request.app.state.launcher.stop()
    return RedirectResponse("/live", status_code=303)
```

`src/skysift/ui/templates/sources.html`:

```html
{% extends "base.html" %}
{% block title %}SkySift · Sources{% endblock %}
{% block content %}
<h1>Data sources</h1>
{% if active %}<p class="status-running">A sweep of <strong>{{ active.request.source }}</strong> is running (pid {{ active.pid }}). <a href="/live">Watch it</a>.</p>{% endif %}
<table>
<thead><tr><th>source</th><th>status</th><th class="num">light curves</th><th class="num">baseline d</th><th class="num">cadence min</th>
<th class="num">depth ppm</th><th class="num">searched</th><th class="num">yield/10k</th><th class="num">CPU h/10k</th><th class="num">GB/10k</th>
<th>progress</th><th></th></tr></thead>
<tbody>
{% for r in rows %}
<tr class="{{ 'muted' if r.info.status != 'live' else '' }}">
  <td><strong>{{ r.info.name }}</strong><div class="muted">{{ r.info.agency }}</div></td>
  <td>{{ r.info.status }}</td>
  <td class="num">{{ r.info.n_lightcurves | fmt }}</td><td class="num">{{ r.info.baseline_days | fmt(0) }}</td>
  <td class="num">{{ r.info.cadence_min | fmt(1) }}</td><td class="num">{{ r.info.depth_limit_ppm | fmt(0) }}</td>
  <td class="num">{{ (r.info.fraction_searched * 100) | fmt(0) }}%</td><td class="num">{{ r.info.est_yield_per_10k | fmt(1) }}</td>
  <td class="num">{{ r.cpu_h_per_10k | fmt(0) }}</td><td class="num">{{ r.gb_per_10k | fmt(1) }}</td>
  <td>{% if r.info.status == 'live' %}{{ r.progress.done | fmt }} done · {{ r.progress.error | fmt }} error · {{ r.remaining | fmt }} remaining{% else %}<span class="muted">{{ r.info.notes }}</span>{% endif %}</td>
  <td>{% if r.info.status == 'live' and not active %}<button hx-get="/partials/sweep-form?source={{ r.info.name }}" hx-target="#sweep-form" hx-swap="innerHTML">Start sweep</button>{% endif %}</td>
</tr>
{% endfor %}
</tbody>
</table>
<div id="sweep-form"></div>
{% endblock %}
```

`src/skysift/ui/templates/partials/sweep_form.html`:

```html
<form class="inline" hx-post="/sweeps" hx-target="#sweep-form" hx-swap="innerHTML">
  <input type="hidden" name="_token" value="{{ token }}">
  <input type="hidden" name="source" value="{{ source }}">
  <h2 style="grid-column:1/-1;margin:0">Start sweep of {{ source }}</h2>
  {% if error %}<p class="error" style="grid-column:1/-1;margin:0">{{ error }}</p>{% endif %}
  <label>limit (targets)<input name="limit" type="number" min="1" value="{{ values.get('limit', 500) }}"></label>
  <label>min period (d)<input name="min_period" type="number" step="any" value="{{ values.get('min_period', th.min_period_days) }}"></label>
  <label>max period (d)<input name="max_period" type="number" step="any" value="{{ values.get('max_period', th.max_period_days) }}"></label>
  <label>retention<select name="retention_mode">
    {% for m in ['stream', 'keep', 'budget'] %}<option value="{{ m }}" {{ 'selected' if values.get('retention_mode', settings.retention.split(':')[0]) == m else '' }}>{{ m }}</option>{% endfor %}
  </select></label>
  <label>budget (GiB, budget mode)<input name="budget_gib" type="number" step="any" value="{{ values.get('budget_gib', 50) }}"></label>
  <label>workers io<input name="workers_io" type="number" min="1" value="{{ values.get('workers_io', settings.workers_io) }}"></label>
  <label>workers cpu<input name="workers_cpu" type="number" min="1" value="{{ values.get('workers_cpu', settings.workers_cpu) }}"></label>
  <label>G mag max<input name="gmag_max" type="number" step="any" value="{{ values.get('gmag_max', 19.0) }}"></label>
  <label>store periodogram summary (~8 kB/star)<select name="store_periodogram">
    {% for m, lbl in [('all', 'all targets'), ('signals', 'only targets with a signal'), ('off', 'off')] %}<option value="{{ m }}" {{ 'selected' if values.get('store_periodogram', settings.store_periodogram) == m else '' }}>{{ lbl }}</option>{% endfor %}
  </select></label>
  <label style="flex-direction:row;align-items:center;gap:6px"><input type="checkbox" name="background_only" {{ 'checked' if values.get('background_only', 'on') else '' }}> background sources only</label>
  <div><button class="primary" type="submit">Start</button></div>
</form>
```

HTMX posts the form and swaps the response into `#sweep-form`; a 303 redirect from an HTMX request is followed by the browser via the `HX-Redirect` header, so in `start_sweep` return `RedirectResponse` with an extra header when the request came from HTMX:

```python
    resp = RedirectResponse("/live", status_code=303)
    if request.headers.get("HX-Request"): resp.headers["HX-Redirect"] = "/live"
    return resp
```

- [ ] **Step 5: Run the tests**

Run: `PY -m pytest tests/unit/test_ui_pages.py tests/unit/test_cli.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/skysift/sources/factory.py src/skysift/cli.py src/skysift/ui/routes/sources.py src/skysift/ui/templates tests/unit/test_ui_pages.py
git commit -m "feat(ui): sources page with registry, progress and the start-sweep form"
```

---

### Task 9: Live page with polling cards and log tail

**Files:**
- Create: `src/skysift/ui/routes/live.py`, `src/skysift/ui/templates/live.html`, `src/skysift/ui/templates/partials/live_cards.html`, `src/skysift/ui/templates/partials/live_sweep.html`
- Test: `tests/unit/test_ui_pages.py`

**Interfaces:**
- Produces: `GET /live`, `GET /partials/live/cards`, `GET /partials/live/sweep` (returns the active sweep block, or the idle block with history; sets `HX-Trigger: sweep-finished` header on the transition to idle).
- Consumes: `queries.summary`, `SweepLauncher.active/tail/history`, `Retention`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_ui_pages.py`:

```python
def test_live_page_cards_and_idle_state(settings, tmp_path):
    client, app, ids, launcher = make_seeded_client(settings, tmp_path)
    r = client.get("/live")
    assert r.status_code == 200 and "rung4_novelty" in r.text and "idle" in r.text
    cards = client.get("/partials/live/cards")
    assert cards.status_code == 200 and "candidate_stage1" in cards.text and "<html" not in cards.text
    assert 'hx-trigger="every 5s"' not in client.get("/partials/live/sweep").text      # no polling while idle


def test_live_page_shows_active_sweep_and_tail(settings, tmp_path):
    import time
    client, app, ids, launcher = make_seeded_client(settings, tmp_path)
    client.post("/sweeps", data={"source": "kbonus_bkg", "_token": app.state.token})
    for _ in range(50):
        if launcher.tail(): break
        time.sleep(0.1)
    r = client.get("/partials/live/sweep")
    assert "started" in r.text and "Stop" in r.text and 'hx-trigger="every 5s"' in r.text
    launcher.stop()
    r = client.get("/partials/live/sweep")
    assert "idle" in r.text and r.headers.get("HX-Trigger") == "sweep-finished"
```

- [ ] **Step 2: Run to verify failure**

Run: `PY -m pytest tests/unit/test_ui_pages.py -q -p no:cacheprovider -k live`
Expected: FAIL (404).

- [ ] **Step 3: Route and templates**

`src/skysift/ui/routes/live.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from skysift.store.retention import Retention, RetentionPolicy
from skysift.ui import queries
from skysift.ui.app import render

router = APIRouter()


def _summary(request: Request) -> dict:
    st = request.app.state
    ret = Retention(RetentionPolicy.parse(st.settings.retention), st.store, st.blob)
    return queries.summary(st.store, ret, st.settings)


def _sweep_ctx(request: Request) -> dict:
    launcher = request.app.state.launcher
    if launcher is None:
        return {"active": None, "tail": [], "history": [], "elapsed": None, "eta_s": None}
    active = launcher.active()
    elapsed = eta = None
    if active:
        started = datetime.fromisoformat(active["started"])
        elapsed = (datetime.now(UTC) - started).total_seconds()
        mean = _summary(request)["mean_seconds_per_run"]
        limit = (active.get("request") or {}).get("limit")
        cpu = (active.get("request") or {}).get("workers_cpu") or request.app.state.settings.workers_cpu
        if mean and limit: eta = max(0.0, limit * mean / max(1, cpu) - elapsed)
    return {"active": active, "tail": launcher.tail(200), "history": launcher.history()[:10], "elapsed": elapsed, "eta_s": eta}


@router.get("/live", response_class=HTMLResponse)
def live_page(request: Request):
    return render(request, "live.html", summary=_summary(request), **_sweep_ctx(request))


@router.get("/partials/live/cards", response_class=HTMLResponse)
def live_cards(request: Request):
    return render(request, "partials/live_cards.html", summary=_summary(request))


@router.get("/partials/live/sweep", response_class=HTMLResponse)
def live_sweep(request: Request):
    ctx = _sweep_ctx(request)
    resp = render(request, "partials/live_sweep.html", **ctx)
    was_active = request.query_params.get("was") == "1"
    if was_active and ctx["active"] is None:
        resp.headers["HX-Trigger"] = "sweep-finished"
    return resp
```

The test calls `/partials/live/sweep` without `was=1` after stopping and still expects the header: make the transition detection stateful instead of query based. Replace the last four lines with:

```python
    st = request.app.state
    now_active = ctx["active"] is not None
    if getattr(st, "sweep_was_active", False) and not now_active:
        resp.headers["HX-Trigger"] = "sweep-finished"
    st.sweep_was_active = now_active
    return resp
```

`src/skysift/ui/templates/live.html`:

```html
{% extends "base.html" %}
{% block title %}SkySift · Live{% endblock %}
{% block content %}
<h1>Live</h1>
<div id="cards" hx-get="/partials/live/cards" hx-trigger="every 5s [document.querySelector('#sweep [data-active]')], sweep-finished from:body" hx-swap="innerHTML">
{% include "partials/live_cards.html" %}
</div>
<div id="sweep">
{% include "partials/live_sweep.html" %}
</div>
{% endblock %}
```

`partials/live_cards.html`:

```html
<div class="cards">
  {% for state, n in summary.targets_by_state.items() %}<div class="card"><div class="label">targets {{ state }}</div><div class="value">{{ n | fmt }}</div></div>{% endfor %}
  {% for status, n in summary.signal_outcomes.items() %}<div class="card"><div class="label">signals {{ status }}</div><div class="value">{{ n | fmt }}</div></div>{% endfor %}
  <div class="card"><div class="label">targets / hour</div><div class="value">{{ summary.targets_per_hour | fmt(1) }}</div></div>
  <div class="card"><div class="label">mean s / run</div><div class="value">{{ summary.mean_seconds_per_run | fmt(1) }}</div></div>
  <div class="card"><div class="label">cache</div><div class="value">{{ (summary.cache_bytes / 1e9) | fmt(2) }} GB{% if summary.cache_budget_bytes %} / {{ (summary.cache_budget_bytes / 1e9) | fmt(0) }}{% endif %}</div></div>
  <div class="card"><div class="label">error-capped targets</div><div class="value">{{ summary.error_capped_targets | fmt }}</div></div>
  <div class="card"><div class="label">queries logged</div><div class="value">{{ summary.queries | fmt }}</div></div>
</div>
<h2>Rung verdicts</h2>
<table><thead><tr><th>rung</th><th class="num">pass</th><th class="num">fail</th><th class="num">undetermined</th></tr></thead><tbody>
{% for rung, c in summary.rung_counts.items() %}<tr><td>{{ rung }}</td><td class="num status-pass">{{ c.pass | fmt }}</td><td class="num status-fail">{{ c.fail | fmt }}</td><td class="num status-undetermined">{{ c.undetermined | fmt }}</td></tr>{% endfor %}
</tbody></table>
{% if summary.last_errors %}<h2>Last errors</h2>
<table><thead><tr><th>target</th><th>when</th><th>error</th></tr></thead><tbody>
{% for e in summary.last_errors %}<tr><td><code>{{ e.target }}</code></td><td class="muted">{{ e.finished }}</td><td>{{ e.error }}</td></tr>{% endfor %}
</tbody></table>{% endif %}
```

`partials/live_sweep.html`:

```html
{% if active %}
<div data-active="1" hx-get="/partials/live/sweep" hx-trigger="every 5s" hx-target="#sweep" hx-swap="innerHTML">
  <h2 class="status-running">Sweep running · {{ active.request.source }} · pid {{ active.pid }}</h2>
  <p class="muted">started {{ active.started }} · elapsed {{ (elapsed or 0) | fmt(0) }} s{% if eta_s is not none %} · about {{ (eta_s / 60) | fmt(0) }} min remaining{% endif %}
  · limit {{ active.request.limit }} · window {{ active.request.min_period or 'default' }}–{{ active.request.max_period or 'default' }} d · retention {{ active.request.retention }}</p>
  <form method="post" action="/sweeps/stop" onsubmit="return confirm('Stop the running sweep? Interrupted targets are redone next time.')">
    <input type="hidden" name="_token" value="{{ token }}"><button class="danger">Stop</button></form>
  <pre class="log">{{ tail | join('\n') }}</pre>
</div>
{% else %}
<div>
  <h2>idle</h2>
  {% if history %}<p class="muted">last sweep finished {{ history[0].finished }} with exit code {{ history[0].exit_code }}{% if history[0].summary %} · processed {{ history[0].summary.processed }} · signals {{ history[0].summary.signals }}{% endif %}</p>
  <h2>Past sweeps</h2>
  <table><thead><tr><th>started</th><th>source</th><th class="num">limit</th><th class="num">exit</th><th>log</th></tr></thead><tbody>
  {% for h in history %}<tr><td>{{ h.started }}</td><td>{{ h.request.source }}</td><td class="num">{{ h.request.limit }}</td><td class="num">{{ h.exit_code }}</td><td><code>{{ h.log }}</code></td></tr>{% endfor %}
  </tbody></table>{% else %}<p class="muted">No sweep has run from this dashboard yet. Start one from <a href="/">Sources</a>.</p>{% endif %}
</div>
{% endif %}
```

- [ ] **Step 4: Run the tests**

Run: `PY -m pytest tests/unit/test_ui_pages.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/skysift/ui/routes/live.py src/skysift/ui/templates tests/unit/test_ui_pages.py
git commit -m "feat(ui): live page with status cards, rung verdicts, sweep tail and polling"
```

---

### Task 10: Light-curve loader, plot payloads and the plots API

**Files:**
- Create: `src/skysift/ui/plots.py`, `src/skysift/ui/routes/api.py`, `src/skysift/ui/static/plots.js`
- Test: `tests/unit/test_plots.py`, `tests/unit/test_ui_pages.py`

**Interfaces:**
- Produces: `PARSERS = {"kbonus_bkg": parse_kbonus_fits}`; `LightCurveLoader(store, blob, source_factory=make_source)` with `load(target: dict) -> LightCurve` (raises `ServiceUnavailable` on outage, `ValueError` on hash mismatch); `build_plots(lc, fnorm, signal: dict, tests: list[dict], periodogram: PeriodogramSummary | None, thresholds) -> dict` with keys `periodogram, full, fold, oddeven, secondary, seasons`, each a dict of plain lists (strict JSON); `GET /api/signals/{id}/plots.json` returning that dict or `{"error": "...", "detail": "..."}` with status 503 (outage) or 404.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_plots.py`:

```python
import json

import numpy as np
import pytest

from skysift import jsonutil
from skysift.config import Thresholds
from skysift.detectors.periodogram import PeriodogramSummary
from skysift.lc.ops import detrend
from skysift.net import ServiceUnavailable
from skysift.store.blob import LocalBlob
from skysift.store.db import Store
from skysift.ui.plots import LightCurveLoader, build_plots
from tests.synthetic import inject_transit, make_lightcurve


def _signal(period=7.3, t0=135.0, duration=0.1, depth=3000.0):
    return {"id": 1, "target_id": "w1", "period": period, "t0": t0, "duration": duration, "depth_ppm": depth, "sde": 12.0,
            "extra": {"search_window": {"min_period_days": 0.5, "max_period_days": 200.0}}}


def _tests():
    return [{"rung": "rung3_stability", "status": "pass", "metrics": {"season_depths": {"0": 2900.0, "1": 3100.0},
                                                                     "season_errs": {"0": 150.0, "1": 160.0}}}]


def test_build_plots_shapes_and_fold_centres_the_dip():
    lc = inject_transit(make_lightcurve(n_days=400, seed=3, noise_ppm=500), 7.3, 135.0, 0.1, 3000)
    ps = PeriodogramSummary.from_spectrum(np.array([7.3, 3.65]), np.array([12.0, 6.0]), 0.5, 133.0, [0.5, 133.0],
                                          [{"period": 7.3, "sde": 12.0, "duration": 0.1, "rank": 0}], n_bins=256)
    out = build_plots(lc, detrend(lc), _signal(), _tests(), ps, Thresholds())
    assert set(out) == {"periodogram", "full", "fold", "oddeven", "secondary", "seasons"}
    assert len(out["full"]["time"]) <= 20000 and len(out["full"]["transit_times"]) == len(np.unique(np.round((lc.time - 135.0) / 7.3)))
    fold = out["fold"]
    assert min(fold["phase_h"]) >= -0.5 * 7.3 * 24 - 1e-6
    bins = np.array(fold["bin_phase_h"]); means = np.array(fold["bin_flux"], float)
    assert abs(bins[np.nanargmin(means)]) < 1.0 and 1 - np.nanmin(means) > 2000e-6     # dip within 1 h of phase 0
    assert fold["model_depth_ppm"] == 3000.0 and fold["model_duration_h"] == pytest.approx(2.4)
    assert set(out["oddeven"]) == {"odd_phase_h", "odd_flux", "even_phase_h", "even_flux", "bin_phase_h", "odd_bin", "even_bin"}
    assert abs(np.nanmean(out["secondary"]["bin_flux"]) - 1) < 500e-6                      # no secondary injected
    assert out["seasons"] == {"season": [0, 1], "depth_ppm": [2900.0, 3100.0], "err_ppm": [150.0, 160.0],
                              "mean_ppm": pytest.approx(2994.6, abs=1.0)}
    pg = out["periodogram"]
    assert pg["sde_min"] == 9.0 and pg["detected_period"] == 7.3 and pg["aliases"] == [3.65, 14.6, 21.9]
    assert len(pg["period"]) == 256 and pg["systematic_periods"] == [93.0, 46.5, 372.5, 186.25]
    text = jsonutil.dumps(out)
    assert "NaN" not in text and json.loads(text)["periodogram"]["sde"][0] is None


def test_build_plots_without_periodogram_or_rung3():
    lc = inject_transit(make_lightcurve(n_days=120, seed=3, noise_ppm=500), 7.3, 135.0, 0.1, 3000)
    out = build_plots(lc, detrend(lc), _signal(), [], None, Thresholds())
    assert out["periodogram"] is None and out["seasons"] is None and out["fold"]["bin_flux"]


class _Source:
    def __init__(self, lc, data, store, blob, fail=False):
        self.lc, self.data, self.store, self.blob, self.fail, self.calls = lc, data, store, blob, fail, 0

    def fetch_lightcurve(self, target):
        self.calls += 1
        if self.fail: raise ServiceUnavailable("mast down")
        import hashlib
        sha = hashlib.sha256(self.data).hexdigest()
        _, loc = self.blob.put(self.data, "w1.fits")
        self.store.add_artifact(sha, len(self.data), "https://mast/w1.fits", loc, target["target_id"])
        return self.lc, sha, "https://mast/w1.fits"


def test_loader_refetches_released_file_and_verifies_sha(tmp_path):
    store = Store(f"sqlite:///{tmp_path.as_posix()}/t.db"); store.init_schema()
    blob = LocalBlob(tmp_path / "blobs")
    lc = make_lightcurve(n_days=30, seed=1)
    data = b"FITS-like bytes"
    import hashlib
    sha = hashlib.sha256(data).hexdigest()
    _, loc = blob.put(data, "w1.fits"); store.add_artifact(sha, len(data), "https://mast/w1.fits", loc, "w1")
    blob.delete(loc); store.mark_artifact_deleted(sha)                       # stream retention released it
    src = _Source(lc, data, store, blob)
    loader = LightCurveLoader(store, blob, source_factory=lambda name, st, bl: src, parsers={"fake": lambda b: lc})
    target = {"target_id": "w1", "source": "fake"}
    assert loader.load(target) is lc and src.calls == 1
    arts = store.unpinned_artifacts("w1") + [a for a in [] ]
    with store.session() as s:
        from skysift.store.db import ArtifactRow
        row = s.get(ArtifactRow, sha)
        assert row.deleted_at is None and row.pinned                          # resurrected and pinned as evidence
    assert loader.load(target) is lc and src.calls == 1                      # now served from the blob
    bad = _Source(lc, b"different bytes", store, blob)
    blob.delete(loc); store.mark_artifact_deleted(sha)
    loader2 = LightCurveLoader(store, blob, source_factory=lambda name, st, bl: bad, parsers={"fake": lambda b: lc})
    with pytest.raises(ValueError, match="sha256"):
        loader2.load(target)


def test_loader_reports_outage(tmp_path):
    store = Store(f"sqlite:///{tmp_path.as_posix()}/t.db"); store.init_schema()
    blob = LocalBlob(tmp_path / "blobs")
    src = _Source(None, b"", store, blob, fail=True)
    loader = LightCurveLoader(store, blob, source_factory=lambda name, st, bl: src)
    with pytest.raises(ServiceUnavailable):
        loader.load({"target_id": "w1", "source": "kbonus_bkg"})
```

Append to `tests/unit/test_ui_pages.py`:

```python
def test_plots_endpoint_reports_outage_without_breaking_the_page(settings, tmp_path, monkeypatch):
    from skysift.net import ServiceUnavailable
    from skysift.ui import plots as plots_mod
    client, app, ids, launcher = make_seeded_client(settings, tmp_path)
    # the seeded artifacts hold fake bytes, not FITS: make the parser raise an outage-like error to exercise the path
    monkeypatch.setitem(plots_mod.PARSERS, "kbonus_bkg", lambda b: (_ for _ in ()).throw(ServiceUnavailable("mast down")))
    r = client.get(f"/api/signals/{ids['candidate']}/plots.json")
    assert r.status_code == 503 and "mast down" in r.json()["detail"]
    assert client.get(f"/signals/{ids['candidate']}").status_code in (200, 404)   # the page route arrives in Task 11
    assert client.get("/api/signals/999999/plots.json").status_code == 404


def test_plots_json_is_strict(settings, tmp_path, monkeypatch):
    from skysift.ui import plots as plots_mod
    from tests.synthetic import inject_transit, make_lightcurve
    client, app, ids, launcher = make_seeded_client(settings, tmp_path)
    lc = inject_transit(make_lightcurve(n_days=400, seed=3, noise_ppm=500), 13.7695, 136.3, 0.096, 1800)
    monkeypatch.setitem(plots_mod.PARSERS, "kbonus_bkg", lambda b: lc)
    r = client.get(f"/api/signals/{ids['candidate']}/plots.json")
    assert r.status_code == 200 and "NaN" not in r.text
    body = r.json()
    assert body["periodogram"] is None and body["seasons"]["season"] == [0, 1, 2, 3] and len(body["fold"]["bin_flux"]) > 10
    assert client.get(f"/api/signals/{ids['candidate']}/plots.json").json() == body     # cached per signal id
```

- [ ] **Step 2: Run to verify failure**

Run: `PY -m pytest tests/unit/test_plots.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: skysift.ui.plots`.

- [ ] **Step 3: Implement `plots.py`**

```python
"""Plot payloads for the signal page: plain lists, strict JSON, computed from the engine's own detrend and fold so the
plots show exactly what the rungs measured. The loader serves the pinned raw file from the blob, or re-fetches it
through the adapter with a SHA-256 check when retention released it (parent spec, data flow step 5)."""
from __future__ import annotations

import logging

import numpy as np
from sqlalchemy import select

from skysift.detectors.periodogram import PeriodogramSummary
from skysift.lc.kbonus import parse_kbonus_fits
from skysift.lc.ops import fold_phase
from skysift.models import LightCurve
from skysift.sources.factory import make_source
from skysift.store.db import ArtifactRow, Store
from skysift.vetting.rung1_detection import SYSTEMATIC_PERIODS

log = logging.getLogger(__name__)
PARSERS = {"kbonus_bkg": parse_kbonus_fits}
MAX_POINTS = 20_000
BIN_MINUTES = 15.0
ALIAS_FACTORS = (0.5, 2.0, 3.0)


class LightCurveLoader:
    def __init__(self, store: Store, blob, source_factory=make_source, parsers: dict | None = None):
        self.store, self.blob, self.source_factory = store, blob, source_factory
        self.parsers = parsers if parsers is not None else PARSERS

    def _raw_rows(self, target_id: str) -> list[ArtifactRow]:
        with self.store.session() as s:
            rows = s.scalars(select(ArtifactRow).where(ArtifactRow.target_id == target_id, ArtifactRow.kind == "raw")
                             .order_by(ArtifactRow.created.desc())).all()
            s.expunge_all()
            return rows

    def load(self, target: dict) -> LightCurve:
        tid, source = target["target_id"], target.get("source", "kbonus_bkg")
        parse = self.parsers.get(source, parse_kbonus_fits)
        rows = self._raw_rows(tid)
        for row in rows:
            if row.deleted_at is None and self.blob.exists(row.local_path):
                lc = parse(self.blob.get(row.local_path)); lc.meta.setdefault("target_id", tid)
                return lc
        # released by retention (or never fetched): re-fetch through the adapter, which records the artifact row again
        src = self.source_factory(source, self.store, self.blob)
        from skysift.models import Target
        tgt = Target(**{k: target.get(k) for k in ("source", "target_id", "ra", "dec", "gaia_id", "kic", "gmag")
                        if target.get(k) is not None} | {"meta": target.get("meta") or {}}) if "ra" in target else target
        lc, sha, _url = src.fetch_lightcurve(tgt)
        known = {r.sha256 for r in rows}
        if known and sha not in known:
            raise ValueError(f"re-fetched file sha256 {sha[:12]} differs from the recorded {sorted(known)[0][:12]}; "
                             "the archive file changed since the sweep")
        self.store.pin_target_artifacts(tid)              # evidence the operator is looking at stays
        lc.meta.setdefault("target_id", tid)
        return lc


def _downsample(t: np.ndarray, f: np.ndarray, max_points: int = MAX_POINTS):
    if t.size <= max_points: return t, f
    step = int(np.ceil(t.size / max_points))
    return t[::step], f[::step]


def _bin(x: np.ndarray, y: np.ndarray, width: float):
    if x.size == 0: return np.array([]), np.array([])
    edges = np.arange(x.min(), x.max() + width, width)
    idx = np.clip(np.digitize(x, edges) - 1, 0, edges.size - 2)
    sums = np.bincount(idx, weights=y, minlength=edges.size - 1); counts = np.bincount(idx, minlength=edges.size - 1)
    with np.errstate(invalid="ignore", divide="ignore"):
        means = sums / counts
    centres = 0.5 * (edges[:-1] + edges[1:])
    return centres, means


def _fold_hours(t, period, t0):
    return fold_phase(t, period, t0) * 24.0


def build_plots(lc: LightCurve, fnorm: np.ndarray, signal: dict, tests: list[dict],
                periodogram: PeriodogramSummary | None, thresholds) -> dict:
    period, t0, dur = float(signal["period"]), float(signal["t0"]), float(signal["duration"])
    good = np.isfinite(fnorm) & np.isfinite(lc.time)
    t, f = lc.time[good], fnorm[good]
    bin_h = BIN_MINUTES / 60.0
    # full curve with transit epochs
    epochs = np.unique(np.round((t - t0) / period)) if t.size else np.array([])
    td, fd = _downsample(t, f)
    full = {"time": td.tolist(), "flux": fd.tolist(), "transit_times": (t0 + epochs * period).tolist()}
    # primary fold with bins and the box model
    ph = _fold_hours(t, period, t0)
    window = max(6 * dur * 24.0, 6.0)
    k = np.abs(ph) < window
    bc, bm = _bin(ph[k], f[k], bin_h)
    fold = {"phase_h": ph[k].tolist(), "flux": f[k].tolist(), "bin_phase_h": bc.tolist(), "bin_flux": bm.tolist(),
            "model_depth_ppm": float(signal["depth_ppm"]), "model_duration_h": dur * 24.0, "window_h": window}
    # odd and even
    ep = np.round((t - t0) / period).astype(int)
    odd, even = (ep % 2 == 1) & k, (ep % 2 == 0) & k
    oc, om = _bin(ph[odd], f[odd], bin_h); ec, em = _bin(ph[even], f[even], bin_h)
    oddeven = {"odd_phase_h": ph[odd].tolist(), "odd_flux": f[odd].tolist(), "even_phase_h": ph[even].tolist(),
               "even_flux": f[even].tolist(), "bin_phase_h": oc.tolist(), "odd_bin": om.tolist(), "even_bin": em.tolist()}
    # phase 0.5 window at the same scale
    ph2 = _fold_hours(t, period, t0 + 0.5 * period); k2 = np.abs(ph2) < window
    sc, sm = _bin(ph2[k2], f[k2], bin_h)
    secondary = {"phase_h": ph2[k2].tolist(), "flux": f[k2].tolist(), "bin_phase_h": sc.tolist(), "bin_flux": sm.tolist(),
                 "window_h": window}
    # per-season depths from rung 3
    seasons = None
    r3 = next((x for x in tests if x["rung"] == "rung3_stability" and x.get("metrics", {}).get("season_depths")), None)
    if r3:
        d = r3["metrics"]["season_depths"]; e = r3["metrics"].get("season_errs", {})
        keys = sorted(d, key=int)
        depths = np.array([d[k] for k in keys], float); errs = np.array([e.get(k, np.nan) for k in keys], float)
        w = 1 / np.where(np.isfinite(errs) & (errs > 0), errs, np.nan) ** 2
        mean = float(np.nansum(w * depths) / np.nansum(w)) if np.isfinite(w).any() else float(np.mean(depths))
        seasons = {"season": [int(k) for k in keys], "depth_ppm": depths.tolist(), "err_ppm": errs.tolist(), "mean_ppm": mean}
    # periodogram envelope
    pg = None
    if periodogram is not None:
        pg = {"period": periodogram.bin_periods().tolist(), "sde": periodogram.envelope, "peaks": periodogram.peaks,
              "sde_min": float(thresholds.sde_min), "detected_period": period,
              "aliases": [round(period * a, 6) for a in ALIAS_FACTORS], "systematic_periods": list(SYSTEMATIC_PERIODS),
              "band_edges": periodogram.band_edges}
    return {"periodogram": pg, "full": full, "fold": fold, "oddeven": oddeven, "secondary": secondary, "seasons": seasons}
```

Check `SYSTEMATIC_PERIODS` in `rung1_detection.py` is a tuple or list `(93.0, 46.5, 372.5, 186.25)`; if it is a set, convert in the test expectation or make it a tuple in the module (a tuple is the right type; change it and keep rung 1's `any(...)` working).

`src/skysift/ui/routes/api.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from skysift import jsonutil
from skysift.detectors.periodogram import PeriodogramSummary
from skysift.lc.ops import detrend
from skysift.net import ServiceUnavailable
from skysift.ui import queries
from skysift.ui.plots import LightCurveLoader, build_plots

router = APIRouter()


def _periodogram(request: Request, run: dict | None) -> PeriodogramSummary | None:
    st = request.app.state
    sha = (run or {}).get("periodogram_sha256")
    if not sha: return None
    from sqlalchemy import select
    from skysift.store.db import ArtifactRow
    with st.store.session() as s:
        row = s.get(ArtifactRow, sha)
        path = row.local_path if row and row.deleted_at is None else None
    if not path or not st.blob.exists(path): return None
    return PeriodogramSummary.from_npz(st.blob.get(path))


@router.get("/api/signals/{signal_id}/plots.json")
def plots_json(request: Request, signal_id: int):
    st = request.app.state
    cached = st.plot_cache.get(signal_id)
    if cached is not None:
        return Response(cached, media_type="application/json")
    d = queries.signal_detail(st.store, signal_id)
    if d is None:
        return JSONResponse({"error": "not found", "detail": f"no signal {signal_id}"}, status_code=404)
    try:
        lc = LightCurveLoader(st.store, st.blob).load(d["target"])
    except ServiceUnavailable as e:
        return JSONResponse({"error": "outage", "detail": str(e)}, status_code=503)
    except ValueError as e:
        return JSONResponse({"error": "integrity", "detail": str(e)}, status_code=409)
    out = build_plots(lc, detrend(lc), d["signal"], d["tests"], _periodogram(request, d["run"]), st.settings.thresholds)
    text = jsonutil.dumps(out)
    st.plot_cache[signal_id] = text
    return Response(text, media_type="application/json")
```

`src/skysift/ui/static/plots.js` (used by the signal page in Task 11; write it now so the API has its consumer):

```js
// Renders the six panels from /api/signals/{id}/plots.json. Colour carries status only; traces use the theme text colour.
async function skysiftPlots(signalId) {
  const root = document.getElementById("plots");
  if (!root) return;
  const msg = (text) => { root.innerHTML = `<p class="muted">${text}</p>`; };
  if (!window.Plotly) { msg("Plotly is not available offline yet; start the dashboard once with network access to download it."); return; }
  let data;
  try {
    const r = await fetch(`/api/signals/${signalId}/plots.json`);
    data = await r.json();
    if (!r.ok) { msg(`Light curve unavailable (${data.error}): ${data.detail} <a href="">retry</a>`); return; }
  } catch (e) { msg(`Could not load plot data: ${e}`); return; }
  const fg = getComputedStyle(document.documentElement).getPropertyValue("--fg").trim();
  const muted = getComputedStyle(document.documentElement).getPropertyValue("--muted").trim();
  const base = { paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)", font: { color: fg, size: 11 },
                 margin: { l: 56, r: 12, t: 28, b: 40 }, showlegend: false };
  const cfg = { displaylogo: false, responsive: true };
  const panel = (id) => { const d = document.createElement("div"); d.id = id; d.className = "plot"; root.appendChild(d); return d; };
  if (data.periodogram) {
    const p = data.periodogram;
    const shapes = [{ type: "line", x0: p.period[0], x1: p.period[p.period.length - 1], y0: p.sde_min, y1: p.sde_min, line: { color: muted, dash: "dot" } }];
    p.aliases.concat(p.systematic_periods).forEach((x) => shapes.push({ type: "line", x0: x, x1: x, yref: "paper", y0: 0, y1: 1, line: { color: muted, width: 1, dash: "dash" } }));
    shapes.push({ type: "line", x0: p.detected_period, x1: p.detected_period, yref: "paper", y0: 0, y1: 1, line: { color: fg, width: 1 } });
    Plotly.newPlot(panel("plot-periodogram"), [
      { x: p.period, y: p.sde, mode: "lines", line: { color: fg, width: 1 }, connectgaps: false, name: "SDE" },
      { x: p.peaks.map((k) => k.period), y: p.peaks.map((k) => k.sde), mode: "markers", marker: { color: fg, size: 7, symbol: "triangle-down" }, name: "peaks" }],
      { ...base, title: "Periodogram (per-band SDE envelope)", xaxis: { type: "log", title: "period (d)" }, yaxis: { title: "SDE" }, shapes }, cfg);
  } else {
    const d = panel("plot-periodogram"); d.className = "muted"; d.textContent = "No periodogram stored for this run (store_periodogram was off).";
  }
  const f = data.full;
  Plotly.newPlot(panel("plot-full"), [
    { x: f.time, y: f.flux, mode: "markers", marker: { color: fg, size: 2, opacity: 0.6 }, name: "flux" },
    { x: f.transit_times, y: f.transit_times.map(() => 1.0), mode: "markers", marker: { color: fg, size: 6, symbol: "line-ns-open" }, name: "transits" }],
    { ...base, title: "Detrended light curve", xaxis: { title: "time (BKJD d)" }, yaxis: { title: "relative flux" } }, cfg);
  const box = (fold) => {
    const d = fold.model_depth_ppm * 1e-6, h = fold.model_duration_h / 2;
    return { x: [-fold.window_h, -h, -h, h, h, fold.window_h], y: [1, 1, 1 - d, 1 - d, 1, 1], mode: "lines", line: { color: muted, width: 1.5 }, name: "box model" };
  };
  const fo = data.fold;
  Plotly.newPlot(panel("plot-fold"), [
    { x: fo.phase_h, y: fo.flux, mode: "markers", marker: { color: muted, size: 2, opacity: 0.4 }, name: "points" },
    { x: fo.bin_phase_h, y: fo.bin_flux, mode: "markers", marker: { color: fg, size: 6 }, name: "15 min bins" }, box(fo)],
    { ...base, title: "Phase fold", xaxis: { title: "hours from mid-transit" }, yaxis: { title: "relative flux" } }, cfg);
  const oe = data.oddeven;
  Plotly.newPlot(panel("plot-oddeven"), [
    { x: oe.bin_phase_h, y: oe.odd_bin, mode: "lines+markers", line: { color: fg }, name: "odd" },
    { x: oe.bin_phase_h, y: oe.even_bin, mode: "lines+markers", line: { color: muted, dash: "dot" }, name: "even" }],
    { ...base, showlegend: true, title: "Odd vs even transits (binned)", xaxis: { title: "hours from mid-transit" }, yaxis: { title: "relative flux" } }, cfg);
  const se = data.secondary;
  const yr = [1 - 1.5 * fo.model_depth_ppm * 1e-6, 1 + 0.5 * fo.model_depth_ppm * 1e-6];
  Plotly.newPlot(panel("plot-secondary"), [
    { x: se.phase_h, y: se.flux, mode: "markers", marker: { color: muted, size: 2, opacity: 0.4 }, name: "points" },
    { x: se.bin_phase_h, y: se.bin_flux, mode: "markers", marker: { color: fg, size: 6 }, name: "bins" }],
    { ...base, title: "Phase 0.5 window (same vertical scale as the primary)", xaxis: { title: "hours from phase 0.5" }, yaxis: { title: "relative flux", range: yr } }, cfg);
  if (data.seasons) {
    const s = data.seasons;
    Plotly.newPlot(panel("plot-seasons"), [
      { x: s.season, y: s.depth_ppm, error_y: { type: "data", array: s.err_ppm, visible: true }, mode: "markers", marker: { color: fg, size: 8 }, name: "season depth" },
      { x: [Math.min(...s.season) - 0.5, Math.max(...s.season) + 0.5], y: [s.mean_ppm, s.mean_ppm], mode: "lines", line: { color: muted, dash: "dot" }, name: "weighted mean" }],
      { ...base, title: "Depth per season", xaxis: { title: "season (quarter mod 4)", dtick: 1 }, yaxis: { title: "depth (ppm)" } }, cfg);
  }
}
document.addEventListener("DOMContentLoaded", () => { const el = document.getElementById("plots"); if (el) skysiftPlots(el.dataset.signal); });
```

- [ ] **Step 4: Run the tests**

Run: `PY -m pytest tests/unit/test_plots.py tests/unit/test_ui_pages.py tests/unit/test_rungs_1_3.py -q -p no:cacheprovider`
Expected: PASS (the rung tests confirm `SYSTEMATIC_PERIODS` still works if its type changed).

- [ ] **Step 5: Commit**

```bash
git add src/skysift/ui/plots.py src/skysift/ui/routes/api.py src/skysift/ui/static/plots.js src/skysift/vetting/rung1_detection.py tests/unit/test_plots.py tests/unit/test_ui_pages.py
git commit -m "feat(ui): light-curve loader with hash-verified re-fetch, plot payloads and plots API"
```

---
### Task 11: Candidates queue, signal page and the decision bar

**Files:**
- Create: `src/skysift/ui/routes/review.py`, `src/skysift/ui/templates/candidates.html`, `src/skysift/ui/templates/signal.html`, `src/skysift/ui/templates/partials/decision_bar.html`
- Test: `tests/unit/test_ui_review.py`

**Interfaces:**
- Produces: `GET /candidates?status=candidate_stage1&sort=sde&dir=desc`, `GET /signals/{id}?status=&sort=&dir=` (detail page with `#plots[data-signal]`), `POST /signals/{id}/decide` (form: `decision`, `note`, `_token`; returns the decision bar fragment, 400 on a missing reject note), `RUNG_METRICS` (which metrics each rung shows).
- Consumes: `queries.review_queue/signal_detail/neighbours`, `Store.decide`, `render`, `require_token`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_ui_review.py`:

```python
from tests.unit.test_ui_pages import make_seeded_client


def test_queue_page_defaults_and_filters(settings, tmp_path):
    client, app, ids, launcher = make_seeded_client(settings, tmp_path)
    r = client.get("/candidates")
    assert r.status_code == 200 and "kic-005689121" in r.text and "gaia-dr3-2101234567890123457" not in r.text
    assert f'data-href="/signals/{ids["candidate"]}' in r.text and "13.7695" in r.text
    r = client.get("/candidates?status=undetermined")
    assert "gaia-dr3-2101234567890123457" in r.text and "outage:vizier" in r.text
    r = client.get("/candidates?status=all&sort=period&dir=asc")
    first = r.text.index("gaia-dr3-2101234567890123458"); last = r.text.index("kic-005689121")
    assert first < last


def test_signal_page_shows_ladder_metrics_links_and_plots_hook(settings, tmp_path):
    client, app, ids, launcher = make_seeded_client(settings, tmp_path)
    r = client.get(f"/signals/{ids['candidate']}?status=all&sort=period&dir=asc")
    assert r.status_code == 200
    assert "rung1_detection" in r.text and "parity_sigma" in r.text and "0.20" in r.text    # rung 2 metric shown
    assert "season_depths" not in r.text or "raw" in r.text                                # dict metrics only in raw
    assert f'id="plots" data-signal="{ids["candidate"]}"' in r.text and "/static/plots.js" in r.text
    assert "exofop.ipac.caltech.edu" not in r.text and "archive.stsci.edu" in r.text and "gea.esac.esa.int" in r.text
    assert f'id="link-prev" href="/signals/{ids["undetermined"]}' in r.text and 'id="link-next"' not in r.text
    assert client.get("/signals/999999").status_code == 404


def test_decision_bar_records_and_requires_note_for_reject(settings, tmp_path):
    client, app, ids, launcher = make_seeded_client(settings, tmp_path)
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
```

- [ ] **Step 2: Run to verify failure**

Run: `PY -m pytest tests/unit/test_ui_review.py -q -p no:cacheprovider`
Expected: FAIL (404).

- [ ] **Step 3: Implement the router**

`src/skysift/ui/routes/review.py`:

```python
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from skysift import jsonutil
from skysift.ui import queries
from skysift.ui.app import render, require_token

router = APIRouter()

RUNG_METRICS = {
    "rung1_detection": ["sde", "snr", "n_events", "duration_ratio", "stellar_source"],
    "rung2_shape": ["odd_ppm", "even_ppm", "parity_sigma", "secondary_ppm", "secondary_sigma", "free_secondary_phase",
                    "free_secondary_sigma"],
    "rung3_stability": ["p_value", "chi2", "dof", "n_events", "sap_psf_ratio"],
    "rung4_novelty": ["cumulative", "q1_q17_dr25_tce", "toi", "kepler_eb", "gaia_nss_two_body_orbit",
                      "gaia_vari_eclipsing_binary", "gaia_skipped"],
}


def _links(target: dict) -> list[tuple[str, str]]:
    out = []
    if target.get("kic"):
        out.append((f"KIC {target['kic']}", f"https://archive.stsci.edu/kepler/data_search/search.php?kic_kepler_id={int(target['kic'])}"))
    if target.get("gaia_id"):
        out.append((f"Gaia DR3 {target['gaia_id']}", "https://gea.esac.esa.int/archive/"))
    out.append(("MAST portal", f"https://mast.stsci.edu/portal/Mashup/Clients/Mast/Portal.html?searchQuery={quote(f'{target.get('ra')} {target.get('dec')}')}"))
    return out


def _ladder_rows(tests: list[dict]) -> list[dict]:
    rows = []
    for t in tests:
        keys = RUNG_METRICS.get(t["rung"], [])
        shown = [(k, t["metrics"].get(k)) for k in keys if k in t["metrics"] and not isinstance(t["metrics"][k], (dict, list))]
        rows.append({**t, "shown": shown, "raw": jsonutil.dumps(t["metrics"], indent=1)})
    return rows


def _nav(request: Request) -> dict:
    q = request.query_params
    return {"status": q.get("status", "candidate_stage1"), "sort": q.get("sort", "sde"), "dir": q.get("dir", "desc")}


@router.get("/candidates", response_class=HTMLResponse)
def candidates_page(request: Request):
    st = request.app.state; nav = _nav(request)
    rows = queries.review_queue(st.store, status=nav["status"], sort=nav["sort"], desc=nav["dir"] != "asc")
    return render(request, "candidates.html", rows=rows, nav=nav, statuses=list(queries.QUEUE_STATUSES),
                  columns=[("target_id", "target"), ("period", "period d"), ("depth_ppm", "depth ppm"), ("duration", "duration h"),
                           ("sde", "SDE"), ("snr", "SNR"), ("n_events", "events"), ("detected", "detected")])


@router.get("/signals/{signal_id}", response_class=HTMLResponse)
def signal_page(request: Request, signal_id: int):
    st = request.app.state; nav = _nav(request)
    d = queries.signal_detail(st.store, signal_id)
    if d is None:
        return render(request, "error.html", status_code=404, error=f"no signal {signal_id}", log_hint="")
    prev_id, next_id = queries.neighbours(st.store, signal_id, nav["status"], nav["sort"], nav["dir"] != "asc")
    qs = f"?status={nav['status']}&sort={nav['sort']}&dir={nav['dir']}"
    return render(request, "signal.html", d=d, ladder=_ladder_rows(d["tests"]), links=_links(d["target"]),
                  prev_url=f"/signals/{prev_id}{qs}" if prev_id else None, next_url=f"/signals/{next_id}{qs}" if next_id else None,
                  nav=nav, signal_id=signal_id)


@router.post("/signals/{signal_id}/decide", dependencies=[Depends(require_token)], response_class=HTMLResponse)
def decide(request: Request, signal_id: int, decision: str = Form(...), note: str = Form(""), _token: str = Form(None)):
    st = request.app.state
    try:
        st.store.decide(signal_id, decision, note.strip())
    except KeyError:
        return render(request, "error.html", status_code=404, error=f"no signal {signal_id}", log_hint="")
    except ValueError as e:
        d = queries.signal_detail(st.store, signal_id)
        return render(request, "partials/decision_bar.html", status_code=400, d=d, signal_id=signal_id, error=str(e))
    d = queries.signal_detail(st.store, signal_id)
    return render(request, "partials/decision_bar.html", d=d, signal_id=signal_id, error=None)
```

Jinja2 cannot be told about `prev_url`/`next_url` inside the decision bar partial when it is re-rendered from the POST, so the bar partial takes them as optional (`{% if prev_url %}`).

- [ ] **Step 4: Templates**

`templates/candidates.html`:

```html
{% extends "base.html" %}
{% block title %}SkySift · Candidates{% endblock %}
{% block content %}
<h1>Candidates</h1>
<p>
{% for s in statuses %}<a href="/candidates?status={{ s }}&sort={{ nav.sort }}&dir={{ nav.dir }}" class="{{ 'status-running' if s == nav.status else 'muted' }}">{{ s }}</a>{% if not loop.last %} · {% endif %}{% endfor %}
</p>
<table>
<thead><tr>
{% for key, label in columns %}{% set is_sorted = nav.sort == key %}
<th class="{{ 'num' if key not in ('target_id',) else '' }}"><a class="{{ 'sorted' if is_sorted else '' }} {{ 'asc' if is_sorted and nav.dir == 'asc' else '' }}" href="/candidates?status={{ nav.status }}&sort={{ key }}&dir={{ 'asc' if is_sorted and nav.dir == 'desc' else 'desc' }}">{{ label }}</a></th>
{% endfor %}
<th>last rung</th><th>verdict</th><th>decision</th></tr></thead>
<tbody>
{% for r in rows %}
<tr class="row-link" data-href="/signals/{{ r.id }}?status={{ nav.status }}&sort={{ nav.sort }}&dir={{ nav.dir }}">
  <td><code>{{ r.target_id }}</code>{% if r.kic %} <span class="muted">KIC {{ r.kic }}</span>{% endif %}</td>
  <td class="num">{{ r.period | fmt(4) }}</td><td class="num">{{ r.depth_ppm | fmt(0) }}</td><td class="num">{{ r.duration_h | fmt(2) }}</td>
  <td class="num">{{ r.sde | fmt(1) }}</td><td class="num">{{ r.snr | fmt(1) }}</td><td class="num">{{ r.n_events | fmt }}</td><td class="num muted">#{{ r.id }}</td>
  <td>{{ r.last_rung or '–' }}</td><td class="status-{{ r.verdict }}">{{ r.verdict or '–' }}</td><td class="status-{{ r.decision }}">{{ r.decision or '' }}</td>
</tr>
{% else %}<tr><td colspan="11" class="muted">Nothing in this queue.</td></tr>{% endfor %}
</tbody></table>
{% endblock %}
```

(The undetermined filter test expects the rung 4 reason text to appear; add a `reason` column: extend `_queue_select` in Task 4's `queries.py` to also select `last.c.reason` by adding `TestRow.reason` to `_last_test_subquery` and `"reason": reason` to `_queue_row`, then show `<td class="muted">{{ r.reason }}</td>` after the verdict. Update Task 4's unpacking accordingly: `_queue_row(sig, t, decision, rung, verdict, reason)`.)

`templates/signal.html`:

```html
{% extends "base.html" %}
{% block title %}SkySift · {{ d.signal.target_id }}{% endblock %}
{% block content %}
<h1><code>{{ d.signal.target_id }}</code> <span class="muted">P = {{ d.signal.period | fmt(5) }} d · depth {{ d.signal.depth_ppm | fmt(0) }} ppm · SDE {{ d.signal.sde | fmt(1) }}</span></h1>
<p class="muted">
  {% for label, href in links %}<a href="{{ href }}" target="_blank" rel="noopener">{{ label }}</a> · {% endfor %}
  G {{ d.target.gmag | fmt(2) }} · RA {{ d.target.ra | fmt(5) }} Dec {{ d.target.dec | fmt(5) }} · source {{ d.target.source }}
  · detector {{ d.signal.detector }} {{ d.signal.detector_version }} · run #{{ d.run.id }} {{ d.run.finished }}
</p>
<p><a href="/candidates?status={{ nav.status }}&sort={{ nav.sort }}&dir={{ nav.dir }}">← queue</a>
{% if prev_url %} · <a id="link-prev" href="{{ prev_url }}">previous (k)</a>{% endif %}{% if next_url %} · <a id="link-next" href="{{ next_url }}">next (j)</a>{% endif %}</p>

<h2>Ladder</h2>
<table><thead><tr><th>rung</th><th>status</th><th>reason</th><th class="num">s</th><th>metrics</th></tr></thead><tbody>
{% for t in ladder %}
<tr><td>{{ t.rung }}</td><td class="status-{{ t.status }}">{{ t.status }}</td><td>{{ t.reason }}</td><td class="num">{{ t.duration_s | fmt(2) }}</td>
<td>{% for k, v in t.shown %}<span class="muted">{{ k }}</span> {{ v | fmt(2) }}&nbsp;&nbsp;{% endfor %}
<details class="raw"><summary class="muted">raw</summary><pre>{{ t.raw }}</pre></details></td></tr>
{% endfor %}
</tbody></table>

<h2>Plots</h2>
<div id="plots" data-signal="{{ signal_id }}"><p class="muted">Loading light curve…</p></div>
<script src="/static/plots.js" defer></script>

<div id="decision">{% include "partials/decision_bar.html" %}</div>
{% endblock %}
```

`templates/partials/decision_bar.html`:

```html
<div class="decision-bar">
  <form hx-post="/signals/{{ signal_id }}/decide" hx-target="#decision" hx-swap="innerHTML" style="display:contents">
    <input type="hidden" name="_token" value="{{ token }}">
    <textarea name="note" placeholder="note (required for reject)">{{ d.candidate.note if d.candidate else '' }}</textarea>
    <button id="btn-approve" class="primary" name="decision" value="approve">Approve (a)</button>
    <button id="btn-reject" class="danger" name="decision" value="reject">Reject (r)</button>
    <button id="btn-needs-data" name="decision" value="needs_data">Needs data (n)</button>
  </form>
  {% if error %}<span class="error">{{ error }}</span>{% endif %}
  {% if d.candidate %}<span>current: <strong class="status-{{ d.candidate.status }}">{{ d.candidate.status }}</strong> <span class="muted">{{ d.candidate.decided_at }}</span></span>{% else %}<span class="muted">no decision yet</span>{% endif %}
</div>
{% if d.decisions %}
<table><thead><tr><th>when</th><th>decision</th><th>who</th><th>note</th></tr></thead><tbody>
{% for h in d.decisions %}<tr class="history-row"><td class="muted">{{ h.utc }}</td><td class="status-{{ 'approved' if h.decision == 'approve' else ('rejected' if h.decision == 'reject' else 'review') }}">{{ h.decision }}</td><td>{{ h.who }}</td><td>{{ h.note }}</td></tr>{% endfor %}
</tbody></table>
{% endif %}
```

- [ ] **Step 5: Run the tests**

Run: `PY -m pytest tests/unit/test_ui_review.py tests/unit/test_ui_queries.py tests/unit/test_ui_pages.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/skysift/ui/routes/review.py src/skysift/ui/queries.py src/skysift/ui/templates tests/unit/test_ui_review.py tests/unit/test_ui_queries.py
git commit -m "feat(ui): candidates queue, signal page with ladder and plots, decision bar with history"
```

---

### Task 12: Approved page and the review JSON

**Files:**
- Create: `src/skysift/ui/routes/published.py`, `src/skysift/ui/templates/approved.html`
- Test: `tests/unit/test_ui_review.py`

**Interfaces:**
- Produces: `GET /approved?status=approved|rejected|review`, `GET /signals/{id}/review.json` (the `review_document`, strict JSON, `Content-Disposition: attachment; filename=<target>_<id>_review.json`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_ui_review.py`:

```python
def test_approved_page_lists_each_signal_once(settings, tmp_path):
    client, app, ids, launcher = make_seeded_client(settings, tmp_path)
    sid = ids["candidate"]
    for decision, note in (("reject", "first look"), ("approve", "second look"), ("approve", "confirmed")):
        client.post(f"/signals/{sid}/decide", data={"decision": decision, "note": note, "_token": app.state.token})
    r = client.get("/approved")
    assert r.status_code == 200 and r.text.count("kic-005689121") == 1 and "confirmed" in r.text
    assert "kic-005689121" not in client.get("/approved?status=rejected").text
    assert "kic-005689121" not in client.get("/candidates?status=rejected").text
    assert f'href="/signals/{sid}/review.json"' in r.text


def test_review_json_download(settings, tmp_path):
    client, app, ids, launcher = make_seeded_client(settings, tmp_path)
    sid = ids["candidate"]
    client.post(f"/signals/{sid}/decide", data={"decision": "approve", "note": "ok", "_token": app.state.token})
    r = client.get(f"/signals/{sid}/review.json")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"] and "NaN" not in r.text
    doc = r.json()
    assert doc["signal"]["target_id"] == "kic-005689121" and doc["decisions"][0]["decision"] == "approve"
    assert doc["queries"] and doc["artifacts"] and len(doc["tests"]) == 4
    assert client.get("/signals/999999/review.json").status_code == 404
```

- [ ] **Step 2: Run to verify failure**

Run: `PY -m pytest tests/unit/test_ui_review.py -q -p no:cacheprovider -k "approved or review_json"`
Expected: FAIL (404).

- [ ] **Step 3: Implement**

`src/skysift/ui/routes/published.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from skysift import jsonutil
from skysift.ui import queries
from skysift.ui.app import render

router = APIRouter()
STATUSES = ("approved", "review", "rejected", "published")


@router.get("/approved", response_class=HTMLResponse)
def approved_page(request: Request):
    st = request.app.state
    status = request.query_params.get("status", "approved")
    if status not in STATUSES: status = "approved"
    return render(request, "approved.html", rows=queries.approved(st.store, status=status), status=status, statuses=STATUSES)


@router.get("/signals/{signal_id}/review.json")
def review_json(request: Request, signal_id: int):
    doc = queries.review_document(request.app.state.store, signal_id)
    if doc is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    name = f"{doc['signal']['target_id']}_{signal_id}_review.json"
    return Response(jsonutil.dumps(doc, indent=1, default=str), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
```

`templates/approved.html`:

```html
{% extends "base.html" %}
{% block title %}SkySift · Approved{% endblock %}
{% block content %}
<h1>Decisions</h1>
<p>{% for s in statuses %}<a href="/approved?status={{ s }}" class="{{ 'status-running' if s == status else 'muted' }}">{{ s }}</a>{% if not loop.last %} · {% endif %}{% endfor %}</p>
<table><thead><tr><th>target</th><th class="num">period d</th><th class="num">depth ppm</th><th class="num">SDE</th><th>decided</th><th>note</th><th>DOI</th><th></th></tr></thead><tbody>
{% for r in rows %}
<tr class="row-link" data-href="/signals/{{ r.id }}?status=all">
  <td><code>{{ r.target_id }}</code></td><td class="num">{{ r.period | fmt(4) }}</td><td class="num">{{ r.depth_ppm | fmt(0) }}</td><td class="num">{{ r.sde | fmt(1) }}</td>
  <td class="muted">{{ r.decided_at }}</td><td>{{ r.note }}</td><td>{{ r.doi or '' }}</td>
  <td><a href="/signals/{{ r.id }}/review.json">review JSON</a></td>
</tr>
{% else %}<tr><td colspan="8" class="muted">No candidate has this status.</td></tr>{% endfor %}
</tbody></table>
{% endblock %}
```

- [ ] **Step 4: Run the tests**

Run: `PY -m pytest tests/unit/test_ui_review.py tests/unit/test_ui_pages.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/skysift/ui/routes/published.py src/skysift/ui/templates/approved.html tests/unit/test_ui_review.py
git commit -m "feat(ui): approved list with decision filters and review JSON download"
```

---

### Task 13: Finish: migrated-database smoke test, README, visual check

**Files:**
- Modify: `README.md` (new "Dashboard" section after "Running the CLI"; mention `--periodogram`, `vet --spectrum`, the decisions table), `.env.example` (`SKYSIFT_STORE_PERIODOGRAM=all`), `deploy/aws/README.md` (one line: the dashboard is a local tool, run it against RDS through an SSH tunnel or a bastion)
- Test: `tests/unit/test_ui_pages.py`, `tests/unit/test_deploy_files.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_ui_pages.py`:

```python
def test_pages_render_on_a_migrated_m1_database(settings, tmp_path):
    from sqlalchemy import text
    from skysift.store.db import Base
    store = Store(settings.database_url); Base.metadata.create_all(store.engine)
    with store.engine.begin() as c:                      # strip what milestone 2 added, as test_migrations does
        c.execute(text("ALTER TABLE runs DROP COLUMN periodogram_sha256")); c.execute(text("ALTER TABLE candidates DROP COLUMN decided_at"))
        c.execute(text("ALTER TABLE queries DROP COLUMN target_id")); c.execute(text("ALTER TABLE artifacts DROP COLUMN kind"))
        c.execute(text("DROP TABLE decisions"))
    store = Store(settings.database_url); store.init_schema()
    seed(store, blob_from_url(settings.blob_url))
    client = TestClient(create_app(settings, store=store, blob=blob_from_url(settings.blob_url), launcher=SweepLauncher(tmp_path, command=SLEEPER)))
    for path in ("/", "/live", "/candidates", "/candidates?status=all", "/approved"):
        assert client.get(path).status_code == 200, path
```

Append to `tests/unit/test_deploy_files.py`:

```python
def test_readme_documents_the_dashboard():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "python -m skysift ui" in text and "127.0.0.1:8765" in text and "--periodogram" in text
    assert "SKYSIFT_STORE_PERIODOGRAM" in (ROOT / ".env.example").read_text(encoding="utf-8")
```

- [ ] **Step 2: Run to verify failure**

Run: `PY -m pytest tests/unit/test_ui_pages.py tests/unit/test_deploy_files.py -q -p no:cacheprovider -k "migrated or readme"`
Expected: the README test FAILS; the migrated-database test should PASS already (if it fails, fix the cause before moving on: it is Review Focus item 2).

- [ ] **Step 3: README section**

Insert after the "Running the CLI" section:

```markdown
## Dashboard

```bash
.venv\Scripts\python.exe -m skysift ui
```

Opens http://127.0.0.1:8765/ in the browser. The dashboard binds to the loopback interface only and has no
authentication; reach it from another machine through an SSH tunnel (`ssh -L 8765:127.0.0.1:8765 <host>`). Pages:

* **Sources**: the registry with yield, CPU hours and GB per 10,000 stars, progress per live source, and a Start sweep
  form (limit, period window, retention, workers, periodogram storage). The sweep runs as a child
  `python -m skysift run` process; one sweep per data directory.
* **Live**: targets per state, signal outcomes, rung verdicts, throughput, cache against budget, the running sweep's log
  (refreshes every 5 s while a sweep runs) and a Stop button.
* **Candidates**: the review queue sorted by SDE. A signal's page shows the ladder with the metrics that matter per rung,
  the periodogram (per-band SDE envelope with threshold, aliases and systematic periods), the detrended curve, the phase
  fold with 15-minute bins and the box model, odd against even, the phase 0.5 window and per-season depths. Approve,
  Reject (note required) and Needs data append to the `decisions` table; nothing is ever deleted. Keys: `a`, `r`, `n`,
  `j` next, `k` previous.
* **Approved**: current decisions by status, with a review JSON per signal (signal, every test, every query, artifacts,
  decision history).

Plotly is downloaded once on the first start (SHA-256 verified) into `<data_dir>/ui/assets`; afterwards the dashboard
works offline. HTMX is shipped with the package.

Every sweep stores a periodogram summary per target (the SDE envelope over 2,048 log-period bins, about 8 kB) unless
`--periodogram signals` or `--periodogram off` is given; `SKYSIFT_STORE_PERIODOGRAM` sets the default. The envelopes
of targets without a signal form the null set for calibrating `sde_min`. `skysift vet --spectrum` additionally writes
the raw BLS spectrum for one target.
```

Add `SKYSIFT_STORE_PERIODOGRAM=all` to `.env.example` with a comment, and the AWS README line.

- [ ] **Step 4: Visual check in the browser pane**

Start the dashboard against a seeded temporary data directory and look at every page at desktop width in light and dark colour schemes. Create the seed with:

```bash
PY -c "import os, tempfile, pathlib; d=pathlib.Path(tempfile.mkdtemp(prefix='skysift-ui-')); os.environ.update(SKYSIFT_DATA_DIR=str(d), SKYSIFT_BLOB_URL=f'file://{d.as_posix()}/blobs', SKYSIFT_DATABASE_URL=f'sqlite:///{d.as_posix()}/engine.db'); from skysift.config import load_settings; from skysift.store.db import Store; from skysift.store.blob import blob_from_url; import sys; sys.path.insert(0, '.'); from tests.unit.ui_seed import seed; s=load_settings(); st=Store(s.database_url); st.init_schema(); seed(st, blob_from_url(s.blob_url)); print(d)"
```

Then, with the three `SKYSIFT_*` variables set to that directory, start `PY -m skysift ui --no-browser --port 8765` through the desktop app's preview tool (a `.claude/launch.json` entry named `skysift-ui` with `runtimeExecutable` `.venv/Scripts/python.exe`, `runtimeArgs` `["-m", "skysift", "ui", "--no-browser", "--port", "8765"]`, `port` 8765), open `/`, `/live`, `/candidates`, `/signals/1`, `/approved`, switch the pane to dark, and fix anything that reads wrong (overflowing tables, unreadable status colours, a plot panel that does not render; the seeded artifacts are not FITS files, so the plots panel is expected to show the "Light curve unavailable" message with the parser error, which is itself a check of Review Focus item 1's error path). Record what was checked in the commit message.

- [ ] **Step 5: Full suite, lint, commit**

Run: `PY -m ruff check src tests` then `PY -m pytest -q -p no:cacheprovider -m "not golden and not slow"` (about 6 minutes).
Expected: all pass.

```bash
git add README.md .env.example deploy/aws/README.md .claude/launch.json tests/unit/test_ui_pages.py tests/unit/test_deploy_files.py
git commit -m "docs(ui): dashboard section, periodogram setting, migrated-database smoke test, visual check"
```

---

## Self-review notes

- **Spec coverage.** Section 3 bullets map to Tasks 1 (app, CLI), 2 and 3 (migrations, decisions, `Store.decide`), 4 (queries, status), 5 (periodogram summary, `store_periodogram`, `vet --spectrum`), 6 (sweeps), 7 (assets), 10 (plots, loader). Section 4 pages map to Tasks 8, 9, 11, 12. Section 5 data flow: step 1 is Task 1's `ui` command, 2 is Task 4, 3 is Task 8, 4 is Tasks 6 and 9, 5 is Task 10, 6 is Tasks 3 and 11. Section 6 error handling: outage panel (Task 10), stale lock (Task 6), error page (Task 1). Section 7: loopback guard and token (Task 1), hash check (Task 7), autoescape is Jinja2's default for `.html`. Section 8 tests are spread across tasks as listed. The spec's `SameSite=Strict` cookie is replaced by a per-process token embedded in each page and sent back in a header or form field; a foreign origin cannot read the page, so the protection is the same and no cookie is needed (record this as a ruling).
- **Type consistency.** `Store.record_results(run_id, target_id, items, queries, pin_evidence, periodogram=None)`; `_write_results(store, blob, run_id, target_id, payload)`; `_cpu_work(..., with_novelty, fetch_module=None, store_periodogram="all", spectrum_path=None)`; `queries._queue_row(sig, t, decision, rung, verdict, reason)` after Task 11's amendment; `SweepLauncher(data_dir, command=None)`; `render(request, name, status_code=200, **ctx)`.
- **Known approximations the implementer should not "fix".** The plots' box model uses the signal's fitted depth and duration, not a re-fit. `neighbours` recomputes the ordered id list per request; with a few thousand signals this is milliseconds. `summary` loads (started, finished) pairs for every done run; at 600k runs that is a few MB once per 5 s poll, acceptable for M2 (a materialised throughput table is M3 work if it shows).
