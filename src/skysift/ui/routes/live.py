from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from skysift.store.db import RunRow
from skysift.store.retention import Retention, RetentionPolicy
from skysift.ui import queries
from skysift.ui.app import render

router = APIRouter()


def _summary(request: Request) -> dict:
    st = request.app.state
    ret = Retention(RetentionPolicy.parse(st.settings.retention), st.store, st.blob)
    return queries.summary(st.store, ret, st.settings)


def _mean_seconds(store) -> float | None:
    """Mean wall time of a finished run: the one number the ETA needs, without the group-bys of queries.summary
    (same definition and rounding as summary()["mean_seconds_per_run"])."""
    with store.session() as s:
        done = s.execute(select(RunRow.started, RunRow.finished).where(RunRow.status == "done")).all()
    seconds = sum((f - st).total_seconds() for st, f in done if f is not None)
    return round(seconds / len(done), 2) if done else None


def _elapsed_s(started) -> float | None:
    """Seconds since the sweep started; None for a lock whose `started` is missing, malformed or not a string."""
    try:
        t = datetime.fromisoformat(started)
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None: t = t.replace(tzinfo=UTC)
    return (datetime.now(UTC) - t).total_seconds()


def _as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _positive_int(value, default: int) -> int:
    """A worker count from the sweep request (absent or null means the engine's setting)."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else default


def _sweep_ctx(request: Request, mean=...) -> dict:
    """Template context for the sweep block. `mean` (seconds per run) is passed in when the caller already has the
    summary; otherwise it is computed only when a sweep is active."""
    st = request.app.state
    launcher = st.launcher
    idle = {"active": None, "stopping": False, "finishing": None, "in_flight_max": None, "pid": None, "req": {},
            "tail": [], "history": [], "elapsed": None, "eta_s": None}
    if launcher is None:
        return idle
    active = launcher.active()
    history = [{**h, "req": _as_dict(h.get("request"))} for h in launcher.history()[:10]]
    if not active:
        return {**idle, "history": history}
    pid = active.get("pid")
    req = _as_dict(active.get("request"))
    stopping = pid is not None and getattr(st, "stop_requested", None) == (pid, active.get("stamp"))
    # a graceful stop was asked for: the engine finishes what it had started (at most 2*io + 2*cpu targets) and exits
    finishing = active.get("stop_requested_at") if isinstance(active.get("stop_requested_at"), str) else None
    in_flight_max = (2 * _positive_int(req.get("workers_io"), st.settings.workers_io)
                     + 2 * _positive_int(req.get("workers_cpu"), st.settings.workers_cpu))
    elapsed = _elapsed_s(active.get("started"))
    eta = None
    if elapsed is not None:
        if mean is ...: mean = _mean_seconds(st.store)
        limit = req.get("limit")
        cpu = req.get("workers_cpu") or st.settings.workers_cpu
        if (mean and isinstance(limit, int) and not isinstance(limit, bool) and isinstance(cpu, int)
                and not isinstance(cpu, bool)):
            eta = max(0.0, limit * mean / max(1, cpu) - elapsed)
    return {"active": active, "stopping": stopping, "finishing": finishing, "in_flight_max": in_flight_max, "pid": pid,
            "req": req, "tail": launcher.tail(200), "history": history, "elapsed": elapsed, "eta_s": eta}


@router.get("/live", response_class=HTMLResponse)
def live_page(request: Request):
    summary = _summary(request)
    ctx = _sweep_ctx(request, mean=summary["mean_seconds_per_run"])
    st = request.app.state
    if ctx["active"] is None: st.stop_requested = None
    st.sweep_was_active = ctx["active"] is not None
    return render(request, "live.html", summary=summary, **ctx)


@router.get("/partials/live/cards", response_class=HTMLResponse)
def live_cards(request: Request):
    return render(request, "partials/live_cards.html", summary=_summary(request))


@router.get("/partials/live/sweep", response_class=HTMLResponse)
def live_sweep(request: Request):
    ctx = _sweep_ctx(request)
    resp = render(request, "partials/live_sweep.html", **ctx)
    st = request.app.state
    now_active = ctx["active"] is not None
    # sweep-finished refreshes the cards once. Each client says whether it last saw the sweep running (?was=1), so
    # every open tab gets the signal; the process-wide flag is only the fallback for a client that sends no `was`.
    was = request.query_params.get("was")
    if was is not None:
        finished = was == "1" and not now_active
    else:
        finished = getattr(st, "sweep_was_active", False) and not now_active
    if finished:
        resp.headers["HX-Trigger"] = "sweep-finished"
    if not now_active:
        st.stop_requested = None
    st.sweep_was_active = now_active
    return resp
