"""FastAPI application factory for the local dashboard. The UI reads the engine's store and blob and changes nothing
scientific; its only writes are decisions (Store.decide) and starting or stopping a sweep (SweepLauncher)."""
from __future__ import annotations

import logging
import secrets
import subprocess
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.engine import make_url
from starlette.middleware.trustedhost import TrustedHostMiddleware

from skysift import __version__ as engine_version
from skysift.config import Settings
from skysift.store.blob import Blob, blob_from_url
from skysift.store.db import Store

log = logging.getLogger(__name__)
HERE = Path(__file__).resolve().parent
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
NAV = [("/", "Sources"), ("/live", "Live"), ("/candidates", "Candidates"), ("/approved", "Approved")]
# simple is Nova's view and the default, expert the astronomer's; the theme follows the system unless overridden
PREFS = {"mode": ("simple", "expert"), "theme": ("system", "dark", "light")}
PREF_COOKIE_AGE = 365 * 24 * 3600


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5,
                              cwd=HERE.parents[2], check=False).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


async def require_token(request: Request) -> None:
    """CSRF guard for every state-changing POST: the per-process token must arrive in the X-SkySift-Token header
    or the _token form field. Together with the trusted-host check (create_app), a page on another origin cannot
    read the token, so it cannot forge the request; the token alone would not stop a DNS-rebinding page."""
    token = request.app.state.token
    sent = request.headers.get("X-SkySift-Token")
    if sent is None and request.headers.get("content-type", "").startswith(("application/x-www-form-urlencoded",
                                                                            "multipart/form-data")):
        form = await request.form()
        sent = form.get("_token")
    if not sent or not secrets.compare_digest(str(sent), token):
        raise HTTPException(status_code=403, detail="missing or stale dashboard token; reload the page")


def _pref(request: Request, key: str) -> str:
    """A display preference: a valid ?key= query value wins for this request, then the cookie, then the default."""
    allowed = PREFS[key]
    for value in (request.query_params.get(key), request.cookies.get(f"skysift_{key}")):
        if value in allowed:
            return value
    return allowed[0]


def _active_nav(path: str) -> str | None:
    return next((href for href, _ in NAV if (href == "/" and path == "/") or (href != "/" and path.startswith(href))),
                None)


def _here(request: Request) -> str:
    """This page's path and query without the one-request mode and theme overrides: where /prefs sends you back."""
    query = [(k, v) for k, v in request.query_params.multi_items() if k not in PREFS]
    return request.url.path + ("?" + urlencode(query) if query else "")


def _same_origin_path(target: str) -> str:
    """`target` when it is a path on this dashboard, else "/". Browsers read a backslash as a slash and drop tabs and
    newlines inside a URL, so a backslash, a control character or white space could lead off the origin: refused."""
    parts = urlsplit(target)
    if (parts.scheme or parts.netloc or not target.startswith("/") or target.startswith("//") or "\\" in target
            or any(c.isspace() or ord(c) < 32 for c in target)):
        return "/"
    return target


def render(request: Request, name: str, status_code: int = 200, **ctx) -> HTMLResponse:
    st = request.app.state
    s = st.settings
    active_nav = _active_nav(request.url.path)
    # the template gets only what it prints; the database URL is shown with any password masked
    base = {"request": request, "token": st.token, "nav": NAV, "data_dir": s.data_dir, "retention": s.retention,
            "db_display": make_url(s.database_url).render_as_string(hide_password=True),
            "plotly_url": "/static/vendor/plotly.min.js" if st.plotly_path else None,
            "engine_version": engine_version, "git_commit": st.git_commit,
            "active_nav": active_nav, "page_title": dict(NAV).get(active_nav, "SkySift"),
            "mode": _pref(request, "mode"), "theme": _pref(request, "theme"), "here": _here(request)}
    return st.templates.TemplateResponse(request, name, {**base, **ctx}, status_code=status_code)


def create_app(settings: Settings, store: Store | None = None, blob: Blob | None = None, launcher=None,
               plotly_path: Path | None = None) -> FastAPI:
    app = FastAPI(title="SkySift", docs_url=None, redoc_url=None, openapi_url=None)
    # the dashboard has no authentication: refuse any Host header that is not loopback (DNS-rebinding defence).
    # Starlette strips the port before matching, so port-qualified entries would never match.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])
    st = app.state
    st.settings = settings
    st.store = store if store is not None else Store(settings.database_url)
    if store is None: st.store.init_schema()
    st.blob = blob if blob is not None else blob_from_url(settings.blob_url)
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

    @app.post("/prefs", dependencies=[Depends(require_token)])
    def prefs(mode: str = Form(""), theme: str = Form(""), next_path: str = Form("/", alias="next")):
        """Remember the mode and theme for a year. An empty field leaves that preference as it is."""
        chosen = {"mode": mode, "theme": theme}
        for key, value in chosen.items():
            if value and value not in PREFS[key]:
                raise HTTPException(status_code=400, detail=f"unknown {key} {value!r}; one of {', '.join(PREFS[key])}")
        resp = RedirectResponse(_same_origin_path(next_path), status_code=303)
        for key, value in chosen.items():
            if value:
                resp.set_cookie(f"skysift_{key}", value, max_age=PREF_COOKIE_AGE, path="/", samesite="lax", httponly=True)
        return resp

    @app.get("/about", response_class=HTMLResponse)
    def about(request: Request):
        return render(request, "about.html", page_title="About")

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
