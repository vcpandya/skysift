from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError

from skysift.sources.base import load_registry
from skysift.ui import queries
from skysift.ui.app import render, require_token
from skysift.ui.routes.live import launched
from skysift.ui.sweeps import SweepActive, SweepRequest

router = APIRouter()


def _registry_rows(progress: dict) -> list[dict]:
    rows = []
    for info in load_registry().values():
        p = progress.get(info.name, {"new": 0, "done": 0, "error": 0, "total": 0})
        rows.append({"info": info, "progress": p, "remaining": max(0, info.n_lightcurves - p["done"]),
                     "gb_per_10k": info.bytes_per_star * 1e4 / 1e9, "cpu_h_per_10k": info.cpu_hours_per_10k()})
    return rows


def _steps(store, progress: dict, launcher=None) -> dict:
    """The state of the three steps: targets known (pick), the latest sweep (run), candidates waiting and the
    decisions people made (review). Waiting and the last sweep are Tonight's numbers, so the two pages agree."""
    t = queries.tonight(store, queries.current_search_version(store), launched=launched(launcher))
    return {"known": sum(p["total"] for p in progress.values()), "last_sweep": t["last_sweep"],
            "waiting": t["candidates_waiting"], "decided": queries.decision_counts(store)}


def _form_ctx(request: Request, source: str, error: str | None = None, values: dict | None = None) -> dict:
    s = request.app.state.settings
    return {"source": source, "th": s.thresholds, "settings": s, "error": error, "values": values or {}}


def _page(request: Request, form: dict | None = None, status_code: int = 200) -> HTMLResponse:
    """The Sources page; with ``form`` the sweep form is a section of it, which is what a browser without
    JavaScript gets from a Start link or after a refused post."""
    st = request.app.state
    progress = queries.source_progress(st.store)
    active = st.launcher.active() if st.launcher else None
    return render(request, "sources.html", status_code=status_code, rows=_registry_rows(progress), active=active,
                  steps=_steps(st.store, progress, st.launcher), form=form, **(form or {}))


@router.get("/sources", response_class=HTMLResponse)
def sources_page(request: Request, start: str = ""):
    info = load_registry().get(start)
    live = info is not None and info.status == "live"
    return _page(request, form=_form_ctx(request, start) if live else None)


@router.get("/partials/sweep-form", response_class=HTMLResponse)
def sweep_form(request: Request, source: str):
    return render(request, "partials/sweep_form.html", **_form_ctx(request, source))


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
    except ValidationError as e:         # the period window, checked by Thresholds: its message, not pydantic's dump
        return _refused(request, form, "; ".join(x["msg"].removeprefix("Value error, ") for x in e.errors()), 400)
    except (ValueError, TypeError, OverflowError) as e:
        return _refused(request, form, str(e), 400)
    except SweepActive as e:
        return _refused(request, form, str(e), 409)
    return _go_live(request)


def _refused(request: Request, form: dict, error: str, status_code: int) -> HTMLResponse:
    """The form again with the reason: alone for htmx, which swaps it into the drawer, else inside the whole page."""
    ctx = _form_ctx(request, form.get("source", ""), error, form)
    if request.headers.get("HX-Request"):
        return render(request, "partials/sweep_form.html", status_code=status_code, **ctx)
    return _page(request, form=ctx, status_code=status_code)


STOP_MODES = ("graceful", "now")


@router.post("/sweeps/stop", dependencies=[Depends(require_token)])
async def stop_sweep(request: Request):
    """``mode=graceful`` (the default, the Stop button) asks the engine to finish its in-flight targets and exit;
    ``mode=now`` (the Stop now button) terminates the sweep process at once."""
    st = request.app.state
    mode = dict(await request.form()).get("mode", "graceful")
    if mode not in STOP_MODES:
        return Response(f"mode must be one of {', '.join(STOP_MODES)}", status_code=400)
    if mode == "graceful":
        st.launcher.request_stop()       # False (no sweep) needs nothing: /live shows idle
        st.stop_requested = None         # "finishing" comes from the lock, not from the hard-stop "stopping" flag
        return _go_live(request)
    before = st.launcher.active()
    stopped = st.launcher.stop()
    # False with a sweep still holding the lock means the child is still shutting down: /live shows "stopping"
    still = None if stopped else st.launcher.active()
    same = still and before and (still.get("pid"), still.get("stamp")) == (before.get("pid"), before.get("stamp"))
    st.stop_requested = (still.get("pid"), still.get("stamp")) if same else None
    return _go_live(request)
