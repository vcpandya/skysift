from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

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
        if str(v).strip() == "": return default
        try:
            return cast(v)
        except (ValueError, TypeError):
            raise ValueError(f"{key}: not a valid number") from None
    mode = form.get("retention_mode", "stream")
    retention = f"budget:{num('budget_gib', float, 50):g}" if mode == "budget" else mode
    return SweepRequest(source=form.get("source", ""), limit=num("limit", int, 500), min_period=num("min_period", float),
                        max_period=num("max_period", float), retention=retention, workers_io=num("workers_io", int),
                        workers_cpu=num("workers_cpu", int), background_only=form.get("background_only") in ("on", "true", "1"),
                        gmag_max=num("gmag_max", float, 19.0), store_periodogram=form.get("store_periodogram", "all"))


def _go_live(request: Request) -> Response:
    """Redirect to /live. An htmx request follows a 303 transparently inside XHR and would swap the whole /live page
    into the form target, so it gets a 200 with HX-Redirect instead; a plain form post gets the 303."""
    if request.headers.get("HX-Request"):
        return Response(status_code=200, headers={"HX-Redirect": "/live"})
    return RedirectResponse("/live", status_code=303)


def _require_live(source: str) -> None:
    info = load_registry().get(source)
    if info is None or info.status != "live":
        raise ValueError(f"source {source} is not live")


@router.post("/sweeps", dependencies=[Depends(require_token)])
async def start_sweep(request: Request):
    st = request.app.state
    form = dict(await request.form())
    try:
        req = _request_from_form(form)
        req.validate(st.settings.thresholds)
        _require_live(req.source)
        st.launcher.start(req, st.settings.thresholds)
    except (ValueError, TypeError, OverflowError) as e:
        return render(request, "partials/sweep_form.html", status_code=400, source=form.get("source", ""),
                      th=st.settings.thresholds, settings=st.settings, error=str(e), values=form)
    except SweepActive as e:
        return render(request, "partials/sweep_form.html", status_code=409, source=form.get("source", ""),
                      th=st.settings.thresholds, settings=st.settings, error=str(e), values=form)
    return _go_live(request)


@router.post("/sweeps/stop", dependencies=[Depends(require_token)])
def stop_sweep(request: Request):
    st = request.app.state
    before = st.launcher.active()
    stopped = st.launcher.stop()
    # False with a sweep still holding the lock means the child is still shutting down: /live shows "stopping"
    still = None if stopped else st.launcher.active()
    same = still and before and (still.get("pid"), still.get("stamp")) == (before.get("pid"), before.get("stamp"))
    st.stop_requested = (still.get("pid"), still.get("stamp")) if same else None
    return _go_live(request)
