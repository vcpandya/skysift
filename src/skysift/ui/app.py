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
    # the vendor mount goes first: Starlette tries mounts in order and "/static" would swallow "/static/vendor/..."
    if plotly_path is not None:
        app.mount("/static/vendor", StaticFiles(directory=str(plotly_path.parent)), name="vendor")
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

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

    from skysift.ui.routes import api, live, published, review, sources  # routers are added by Tasks 8 to 12
    for router in (sources.router, live.router, review.router, published.router, api.router):
        app.include_router(router)
    return app


def _fmt(value, digits: int = 2) -> str:
    """Template filter: numbers with fixed decimals, None as a dash, everything else as str."""
    if value is None: return "–"
    if isinstance(value, float): return f"{value:,.{digits}f}"
    if isinstance(value, int): return f"{value:,}"
    return str(value)
