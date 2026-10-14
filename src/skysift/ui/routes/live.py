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
    st = request.app.state
    launcher = st.launcher
    if launcher is None:
        return {"active": None, "stopping": False, "tail": [], "history": [], "elapsed": None, "eta_s": None}
    active = launcher.active()
    elapsed = eta = None
    stopping = False
    if active:
        stopping = getattr(st, "stop_requested_pid", None) == active["pid"]
        started = datetime.fromisoformat(active["started"])
        elapsed = (datetime.now(UTC) - started).total_seconds()
        mean = _summary(request)["mean_seconds_per_run"]
        req = active.get("request") or {}
        limit = req.get("limit")
        cpu = req.get("workers_cpu") or st.settings.workers_cpu
        if mean and limit: eta = max(0.0, limit * mean / max(1, cpu) - elapsed)
    return {"active": active, "stopping": stopping, "tail": launcher.tail(200), "history": launcher.history()[:10],
            "elapsed": elapsed, "eta_s": eta}


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
    st = request.app.state
    now_active = ctx["active"] is not None
    if getattr(st, "sweep_was_active", False) and not now_active:
        resp.headers["HX-Trigger"] = "sweep-finished"          # the cards poll stops with the sweep: refresh them once
    if not now_active:
        st.stop_requested_pid = None
    st.sweep_was_active = now_active
    return resp
