from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from skysift import jsonutil
from skysift.detectors.periodogram import PeriodogramSummary
from skysift.net import ServiceUnavailable
from skysift.store.db import ArtifactRow
from skysift.ui import queries
from skysift.ui.plots import IntegrityError, LightCurveLoader, build_plots, whitened_fnorm

log = logging.getLogger(__name__)
router = APIRouter()
CACHE_LIMIT = 64


def _periodogram(request: Request, run: dict | None) -> tuple[PeriodogramSummary | None, str]:
    """The run's stored periodogram summary and a status telling the panel why it may be absent: "ok", "off" (never
    stored: store_periodogram was off or kept signals only), "evicted" (released by budget retention), "missing" (the
    blob file is gone) or "unreadable" (it cannot be parsed). The signal page shows a matching note instead of failing."""
    st = request.app.state
    sha = (run or {}).get("periodogram_sha256")
    if not sha: return None, "off"
    with st.store.session() as s:
        row = s.get(ArtifactRow, sha)
        if row is not None and row.deleted_at is not None: return None, "evicted"
        path = row.local_path if row else None
    if not path: return None, "missing"
    try:
        if not st.blob.exists(path): return None, "missing"
        return PeriodogramSummary.from_npz(st.blob.get(path)), "ok"
    except Exception:
        log.warning("periodogram summary %s is unreadable; plotting without it", sha[:12], exc_info=True)
        return None, "unreadable"


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
    except IntegrityError as e:
        return JSONResponse({"error": "integrity", "detail": str(e)}, status_code=409)
    summary, status = _periodogram(request, d["run"])
    out = build_plots(lc, whitened_fnorm(lc, d["signal"]), d["signal"], d["tests"], summary, st.settings.thresholds)
    out["periodogram_status"] = status
    text = jsonutil.dumps(out)
    while len(st.plot_cache) >= CACHE_LIMIT:
        st.plot_cache.pop(next(iter(st.plot_cache)), None)      # oldest first; payloads are a few hundred kB each
    st.plot_cache[signal_id] = text
    return Response(text, media_type="application/json")
