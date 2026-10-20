"""Tonight, the landing page: what the latest sweep searched and found, the candidates waiting for a look and the
rechecks in progress. The numbers come from `ui.queries`, the sweep state from the Live page's `_sweep_ctx`."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from skysift.ui import queries
from skysift.ui.app import _pref, render
from skysift.ui.routes.live import _sweep_ctx

router = APIRouter()


def _hero_ctx(request: Request) -> dict:
    """The hero's numbers: the sweep window is the running sweep when there is one, else the latest run's day."""
    store = request.app.state.store
    sweep = _sweep_ctx(request)
    since = sweep["progress"]["since"] if sweep["progress"] else None
    version = queries.current_search_version(store)
    return {"t": queries.tonight(store, version, since=since), "sweep": sweep, "version": version}


@router.get("/", response_class=HTMLResponse)
def tonight_page(request: Request):
    ctx = _hero_ctx(request)
    store = request.app.state.store
    expert = {}
    if _pref(request, "mode") == "expert":
        n, seconds = queries.run_time_totals(store)
        expert = {"funnel_rows": queries.funnel(queries.rung_counts(store)),
                  "per_hour": n / (seconds / 3600) if seconds > 0 else None, "mean_s": seconds / n if n else None}
    return render(request, "tonight.html", feed=queries.recent_candidates(store, ctx["version"], limit=6),
                  polled=False, **ctx, **expert)


@router.get("/partials/tonight/hero", response_class=HTMLResponse)
def tonight_hero(request: Request):
    """The hero alone, which the page polls every 10 s while a sweep runs."""
    return render(request, "partials/tonight_hero.html", polled=True, **_hero_ctx(request))
