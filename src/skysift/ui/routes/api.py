from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, select

from skysift import jsonutil
from skysift.detectors.periodogram import PeriodogramSummary
from skysift.net import ServiceUnavailable
from skysift.store.db import ArtifactRow, SignalRow, TestRow
from skysift.ui import pixel_figure, queries, sparkline
from skysift.ui.plots import IntegrityError, LightCurveLoader, build_plots, whitened_fnorm

log = logging.getLogger(__name__)
router = APIRouter()
CACHE_LIMIT = 64
SVG = "image/svg+xml"
SPARKLINE_MAX_AGE = "max-age=86400"


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
    key = plot_cache_key(st.store, signal_id)
    cached = st.plot_cache.get(key)
    if cached is not None:
        return Response(cached, media_type="application/json")
    d = queries.signal_detail(st.store, signal_id)
    if d is None:
        return JSONResponse({"error": "not found", "detail": f"no signal {signal_id}"}, status_code=404)
    try:
        lc = LightCurveLoader(st.store, st.blob, source_factory=st.source_factory).load(d["target"])
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
    st.plot_cache[key] = text
    return Response(text, media_type="application/json")


def plot_cache_key(store, signal_id: int) -> tuple[int, int]:
    """The plots cache key: the signal and its newest test row, so a recheck that wrote a row (in this process or a
    separate ``skysift recheck``) is drawn afresh."""
    return signal_id, max((t["id"] for t in store.latest_tests(signal_id)), default=0)


def _not_cached(source: str, store, blob):
    """The sparkline's source factory: a card never downloads a light curve, so a raw file that is not on disk is
    reported as not cached (the signal page's plots fetch it, after which the card can draw it)."""
    raise ServiceUnavailable(f"the {source} light curve is not cached; sparklines never fetch")


def sparkline_dir(data_dir) -> Path:
    return Path(data_dir) / "ui" / "sparklines"


def sparkline_key(period, t0, duration, rung2_id: int | None) -> str:
    """The first 12 hex digits of SHA-256 over what the picture is drawn from: the fold (period, t0, duration) and the
    rung 2 row whose fit draws the model. A swapped database or a recheck that changes any of them misses the cache,
    so a file never shows another signal's picture."""
    return hashlib.sha256(f"{period!r}|{t0!r}|{duration!r}|{rung2_id!r}".encode()).hexdigest()[:12]


def sparkline_paths(data_dir, signal_id: int) -> list[Path]:
    """Every cached picture of this signal, whatever its key: what a recheck deletes, and what tests look for."""
    return sorted(sparkline_dir(data_dir).glob(f"{signal_id}-*.svg"))


@router.get("/api/signals/{signal_id}/sparkline.svg")
def sparkline_svg(request: Request, signal_id: int):
    """The card's folded-transit picture, cached on disk per signal and key (``sparkline_key``). Without a readable
    raw file on disk the answer is a placeholder that is never cached, so the real picture appears once the curve is."""
    st = request.app.state
    with st.store.session() as s:                            # a leftover file never answers for an unknown signal
        sig = s.get(SignalRow, signal_id)
        if sig is None:
            return JSONResponse({"error": "not found", "detail": f"no signal {signal_id}"}, status_code=404)
        rung2 = s.scalar(select(func.max(TestRow.id)).where(TestRow.signal_id == signal_id,
                                                             TestRow.rung == "rung2_shape"))
        key = sparkline_key(sig.period, sig.t0, sig.duration, rung2)
    path = sparkline_dir(st.settings.data_dir) / f"{signal_id}-{key}.svg"
    if path.is_file():
        return Response(path.read_bytes(), media_type=SVG, headers={"Cache-Control": SPARKLINE_MAX_AGE})
    d = queries.signal_detail(st.store, signal_id)
    try:
        lc = LightCurveLoader(st.store, st.blob, source_factory=_not_cached).load(d["target"], pin=False)
    except (ServiceUnavailable, IntegrityError) as e:
        log.info("sparkline %s: placeholder (%s)", signal_id, e)
        return Response(sparkline.placeholder_svg(), media_type=SVG, headers={"Cache-Control": "no-store"})
    svg = sparkline.render_svg(sparkline.fold_points(lc, d["signal"], d["tests"]), d["signal"]["depth_ppm"])
    _cache(path, svg)
    return Response(svg, media_type=SVG, headers={"Cache-Control": SPARKLINE_MAX_AGE})


@router.get("/api/signals/{signal_id}/pixels.svg")
def pixels_svg(request: Request, signal_id: int, theme: str = "light"):
    """The pixel stage's season panels as a standalone file (``pixel_figure.render_svg``) in the token values of
    ``theme`` (light or dark), drawn from the latest ``rung5_pixels`` row's metrics alone; 404 when the signal is
    unknown, has no such row or the row's metrics are not a mapping (logged). Not cached: a recheck may write a newer
    row. The signal page draws the same figure inline on the theme tokens."""
    if theme not in pixel_figure.THEMES:
        return JSONResponse({"error": "bad request", "detail": f"theme is one of {', '.join(pixel_figure.THEMES)}"},
                            status_code=400)
    store = request.app.state.store
    row = next((t for t in store.latest_tests(signal_id) if t["rung"] == "rung5_pixels"), None)
    metrics = None if row is None else (row["metrics"] or {})
    if metrics is not None and not isinstance(metrics, dict):
        log.warning("pixels.svg %s: rung 5 row %s holds %s metrics, not a mapping", signal_id, row["id"],
                    type(metrics).__name__)
        metrics = None
    if metrics is None:
        return JSONResponse({"error": "not found", "detail": f"no pixel check for signal {signal_id}"},
                            status_code=404)
    svg = pixel_figure.render_svg(metrics, signal_id, row["reason"] or "", theme=theme)
    return Response(svg, media_type=SVG, headers={"Cache-Control": "no-store"})


def _cache(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` through a uniquely named temp file beside it (requests run on a thread pool, so two
    first requests for one signal may race), then rename: a reader sees no file or a whole one. The temp file never
    outlives a failure, and losing the rename to a concurrent writer of the same picture is not an error."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False) as f:
            tmp = Path(f.name)
            f.write(text)
        os.replace(tmp, path)
    except BaseException as e:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
        if not (isinstance(e, OSError) and path.is_file()):
            raise
