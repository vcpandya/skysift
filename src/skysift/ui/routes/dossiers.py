"""The Dossiers pages (dossiers spec 5.5): the tiles of the final set, one dossier page per target, the standalone
download, the dossier and pack jobs, the submission pack's step list and its zip. Reads go through ``ui.queries``;
the writes are a job start (``ui.jobs.DossierJobs``), the export file (``dossier.write_export``) and the pack steps
(``Store.set_pack_steps``)."""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from markupsafe import Markup

from skysift import dossier as ds
from skysift import jsonutil
from skysift.pack import STEP_TEXTS, STEPS
from skysift.ui import queries
from skysift.ui.app import render, require_token
from skysift.ui.jobs import DossierJobs, DossierRequest, JobBusy, valid_target_id
from skysift.ui.planet_svg import look_sentence, planet_svg
from skysift.ui.routes.review import _ladder_rows

router = APIRouter()
VERSIONS = ("current", "all")
SECTION_IDS = ("signal", "repeat", "checks", "pixels", "star", "look", "caveats", "reproduce")
# where a section_errors entry shows its notice; any other name shows in the caveats section
ERROR_SECTION = {"periodogram": "signal", "pixels": "pixels", "plots": "repeat", "star": "star", "look": "look"}
VOTE_WORDS = {True: "voting", False: "not voting", None: "vote not recorded"}
BUSY = "another dossier job is running"
STOP_POLLING = 286                # htmx swaps a 286 answer and stops the element's polling
# the submission pack's steps (spec 5.9), in the order its README gives them; pack.STEPS is the one list
PACK_STEPS = [(int(n), title, STEP_TEXTS[n]) for n, title in STEPS]
GONE_FOOT = '<p class="tile-note">This target is no longer available. Reload the page.</p>'


def _jobs(request: Request) -> DossierJobs:
    st = request.app.state
    if st.dossier_jobs is None:
        st.dossier_jobs = DossierJobs(st.settings.data_dir)
    return st.dossier_jobs


def _bad_id(request: Request, target_id: str) -> HTMLResponse | None:
    if valid_target_id(target_id):
        return None
    return render(request, "error.html", status_code=400, log_hint="",
                  error="a target id is 1 to 64 letters, digits and the characters _ . : -, starting with a letter "
                        "or a digit")


def date_words(when) -> str:
    """"21 October 2026" from a datetime or an ISO string; the text itself when it cannot be read."""
    if isinstance(when, str):
        try:
            when = datetime.fromisoformat(when)
        except ValueError:
            return when
    if not isinstance(when, datetime):
        return "an unknown date"
    return f"{when.day} {when:%B %Y}"


def _decorate(row: dict, running: str | None, last: dict[str, dict]) -> dict:
    """The tile's job state: generating while a job for it runs, and the last job's failure when it ended with an
    error after the dossier was written (or with no dossier)."""
    tid = row["target_id"]
    failed = None
    rec = last.get(tid)
    if rec and rec.get("exit_code") != 0:
        d = row.get("dossier")
        finished = rec.get("finished") or ""
        generated = d["generated"].isoformat() if d and isinstance(d.get("generated"), datetime) else ""
        if not d or finished[:19] > generated[:19]:
            failed = rec
    return {**row, "generating": running == tid, "failed_job": failed,
            "pack_zip": pack_zip_path((row.get("dossier") or {}).get("pack_path")) is not None,
            "thumb": Markup(planet_svg(row["look"])) if row.get("look") else None,
            "generated_words": date_words(row["dossier"]["generated"]) if row.get("dossier") else None}


def _tiles(request: Request, rows: list[dict]) -> list[dict]:
    jobs = _jobs(request)
    active = jobs.active()
    running = ((active or {}).get("request") or {}).get("target_id")
    kind = ((active or {}).get("request") or {}).get("kind")
    last: dict[str, dict] = {}
    for h in jobs.history():
        tid = (h.get("request") or {}).get("target_id")
        if tid and tid not in last:
            last[tid] = h
    return [{**_decorate(r, running, last), "job_kind": kind if running == r["target_id"] else None} for r in rows]


def _tile(request: Request, target_id: str, status_code: int = 200) -> HTMLResponse:
    rows = queries.final_set(request.app.state.store, None, target_id=target_id)
    if not rows:
        return HTMLResponse(f"no signal for {target_id}", status_code=404)
    return render(request, "partials/dossier_tile.html", status_code=status_code, r=_tiles(request, rows)[0])


@router.get("/dossiers", response_class=HTMLResponse)
def dossiers_page(request: Request):
    """The final set as tiles (spec 5.5): stage 2 and approved candidates of the current search version (``?versions=
    all`` lifts the filter, as the queue's) and every target with a dossier."""
    st = request.app.state
    versions = request.query_params.get("versions", "current")
    if versions not in VERSIONS:
        return render(request, "error.html", status_code=400, log_hint="",
                      error=f"unknown versions {versions!r}; {' or '.join(VERSIONS)}")
    version = queries.current_search_version(st.store) if versions == "current" else None
    rows = _tiles(request, queries.final_set(st.store, version))
    return render(request, "dossiers.html", rows=rows, versions=versions,
                  any_dossier=any(r["dossier"] for r in rows))


@router.get("/partials/dossiers/{target_id}/tile", response_class=HTMLResponse)
def tile_partial(request: Request, target_id: str):
    return _bad_id(request, target_id) or _tile(request, target_id)


@router.get("/partials/dossiers/{target_id}/status", response_class=HTMLResponse)
def tile_status(request: Request, target_id: str):
    """The poll of a generating tile's foot: 204 (nothing to swap, so focus and the live region are left alone) while
    the job runs, then 286 (htmx swaps it and stops polling) with the new foot and the upper part out of band."""
    bad = _bad_id(request, target_id)
    if bad:
        return bad
    rows = queries.final_set(request.app.state.store, None, target_id=target_id)
    if not rows:                    # a 404 would keep htmx polling every 5 s: answer 286 so the poll stops
        return HTMLResponse(GONE_FOOT, status_code=STOP_POLLING)
    r = _tiles(request, rows)[0]
    if r["generating"]:
        return Response(status_code=204)
    return render(request, "partials/dossier_tile_status.html", status_code=STOP_POLLING, r=r)


def _num(x) -> float | None:
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        return None
    return float(x)


def pixel_seasons(data: dict) -> tuple[list[dict], str | None]:
    """Section 04's per-season rows from rung 5's metrics, voting seasons first, then those that did not vote, then
    those whose vote was not recorded (a row stored before seasons voted), each in season order; and the Monte Carlo
    sentence when the dossier's caveats carry it (``dossier.close_call`` decides, so the export says the same)."""
    tests = data.get("tests") or []
    m = next((t.get("metrics") or {} for t in tests if t.get("rung") == "rung5_pixels"), {})
    rows = []
    for s in m.get("seasons") or []:
        if not isinstance(s, dict):
            continue
        vote = s.get("voting") if isinstance(s.get("voting"), bool) else None
        rows.append({**s, "vote": vote, "vote_words": VOTE_WORDS[vote], "voting": vote is True,
                     "separation_sigma": _num(s.get("separation_sigma")),
                     "centroid_sigma_axis_pix": _num(s.get("centroid_sigma_axis_pix"))})
    rows.sort(key=lambda r: ({True: 0, False: 1, None: 2}[r["vote"]], not isinstance(r.get("season"), int),
                             r["season"] if isinstance(r.get("season"), int) else 0))
    close = next((c.get("text") for c in data.get("caveats") or []
                  if isinstance(c, dict) and c.get("key") == "close_call"), None)
    return rows, close


def _section_notices(data: dict) -> dict[str, list[tuple[str, str]]]:
    out: dict[str, list[tuple[str, str]]] = {}
    for name, err in (data.get("section_errors") or {}).items():
        text = err.get("error") if isinstance(err, dict) else str(err)
        out.setdefault(ERROR_SECTION.get(name, "caveats"), []).append((name, text or "unknown error"))
    return out


def _payload(data: dict) -> Markup:
    """The JSON for ``#dossier-data``: strict, with every ``<`` escaped so no ``</script>`` can end the block."""
    return Markup(jsonutil.dumps(data, separators=(",", ":")).replace("<", "\\u003c"))


def _missing(request: Request, target_id: str) -> HTMLResponse:
    rows = queries.final_set(request.app.state.store, None, target_id=target_id)
    tile = _tiles(request, rows)[0] if rows else None
    return render(request, "dossier.html", status_code=404, dz=None, target_id=target_id, r=tile,
                  page_title="Dossier")


@router.get("/dossiers/{target_id}", response_class=HTMLResponse)
def dossier_page(request: Request, target_id: str):
    """The eight sections of the dossier at the dashboard's mode (spec 5.1, 5.2), from the stored JSON."""
    bad = _bad_id(request, target_id)
    if bad:
        return bad
    st = request.app.state
    dz = queries.dossier_for(st.store, target_id)
    if dz is None:
        return _missing(request, target_id)
    data = dz["data"] or {}
    seasons, close = pixel_seasons(data)
    rows = _tiles(request, queries.final_set(st.store, None, target_id=target_id))
    look = data.get("look")
    return render(request, "dossier.html", dz=dz, data=data, target_id=target_id, r=rows[0] if rows else None,
                  page_title=(data.get("target") or {}).get("name") or target_id,
                  why_rows=data.get("why") or [], ladder=_ladder_rows(data.get("tests") or []),
                  seasons=seasons, close_call=close, notices=_section_notices(data),
                  payload=_payload(data) if data else None, sections=SECTION_IDS,
                  look_svg=Markup(planet_svg(look, 180)) if look else None,
                  look_words=look_sentence(look, look) if look else None,
                  generated_words=date_words(dz["generated"]), pack_steps=PACK_STEPS,
                  pack_zip=pack_zip_path(dz.get("pack_path")) is not None,
                  steps_done=dz.get("pack_steps") or {}, d=dz["detail"], signal_id=dz["signal_id"], error=None,
                  rung5=next((t for t in data.get("tests") or [] if t.get("rung") == "rung5_pixels"), None))


@router.get("/dossiers/{target_id}/export.html")
def export(request: Request, target_id: str):
    """The standalone page as an attachment, rendered on first request and kept at the row's ``export_path`` (a new
    generation clears it, so a kept file always matches the stored JSON)."""
    bad = _bad_id(request, target_id)
    if bad:
        return bad
    st = request.app.state
    row = st.store.dossier(target_id)
    if row is None or not row.get("data_path") or not Path(row["data_path"]).is_file():
        return render(request, "error.html", status_code=404, error=f"no dossier for {target_id}", log_hint="")
    kept = Path(row["export_path"]) if row.get("export_path") else None
    if kept is not None and kept.is_file():
        text = kept.read_text(encoding="utf-8")
    else:
        path = Path(row["data_path"]).parent / f"{target_id}_dossier.html"
        ds.write_export(st.store, SimpleNamespace(data_path=Path(row["data_path"]), target_id=target_id), path)
        text = path.read_text(encoding="utf-8")
    return Response(text, media_type="text/html; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{target_id}_dossier.html"'})


def pack_zip_path(pack_path: str | None) -> Path | None:
    """The zip ``skysift pack`` wrote inside the pack folder, when the folder is recorded and the zip is on disk:
    the name the pack's ``provenance/versions.json`` gives (``<target>_<yyyymmdd>_skysift_pack.zip``)."""
    if not pack_path:
        return None
    folder = Path(pack_path)
    try:
        name = json.loads((folder / "provenance" / "versions.json").read_text(encoding="utf-8")).get("zip_name")
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(name, str) or Path(name).name != name or not name.endswith(".zip"):
        return None
    path = folder / name
    return path if path.is_file() else None


@router.get("/dossiers/{target_id}/pack.zip")
def pack_download(request: Request, target_id: str):
    """The submission pack's zip as an attachment, from the folder the dossiers row records."""
    bad = _bad_id(request, target_id)
    if bad:
        return bad
    row = request.app.state.store.dossier(target_id)
    path = pack_zip_path((row or {}).get("pack_path"))
    if path is None:
        return render(request, "error.html", status_code=404, log_hint="",
                      error=f"no submission pack for {target_id}; build it from the Dossiers page or with "
                            f"skysift pack --target {target_id}")
    return FileResponse(path, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{path.name}"'})


def _start(request: Request, target_id: str, kind: str, force: bool) -> HTMLResponse:
    bad = _bad_id(request, target_id)
    if bad:
        return bad
    st = request.app.state
    try:
        ds.pick_signal(st.store, target_id)
    except ds.NoSignal:
        return render(request, "error.html", status_code=404, error=f"no signal for {target_id}", log_hint="")
    rows = queries.final_set(st.store, None, target_id=target_id) if kind == "pack" else []
    if kind == "pack" and (not rows or rows[0]["group"] != "approved"):
        resp = HTMLResponse("not approved", status_code=409)
        toast = {"message": "Not started: a submission pack is built only for an approved candidate.",
                 "tone": "undetermined"}
        resp.headers["HX-Trigger"] = jsonutil.dumps({"toast": toast})
        return resp
    if kind == "pack" and st.store.dossier(target_id) is None:
        resp = HTMLResponse("generate the dossier first", status_code=400)
        resp.headers["HX-Trigger"] = jsonutil.dumps({"toast": {"message": "Generate the dossier first; the pack is "
                                                               "built from it.", "tone": "fail"}})
        return resp
    try:
        _jobs(request).start(DossierRequest(kind, target_id, force=force))
    except JobBusy:
        resp = HTMLResponse(BUSY, status_code=409)
        resp.headers["HX-Trigger"] = jsonutil.dumps({"toast": {"message": f"Not started: {BUSY}. Try again when it "
                                                               "ends.", "tone": "undetermined"}})
        return resp
    resp = _tile(request, target_id)
    what = "the dossier" if kind == "dossier" else "the submission pack"
    resp.headers["HX-Trigger"] = jsonutil.dumps({"toast": {"message": f"Building {what} for {target_id}. It takes "
                                                           "one to three minutes.", "tone": "running"}})
    return resp


@router.post("/dossiers/{target_id}/generate", dependencies=[Depends(require_token)], response_class=HTMLResponse)
def generate(request: Request, target_id: str, force: str = Form("0")):
    return _start(request, target_id, "dossier", force == "1")


@router.post("/dossiers/{target_id}/pack", dependencies=[Depends(require_token)], response_class=HTMLResponse)
def pack(request: Request, target_id: str):
    return _start(request, target_id, "pack", False)


@router.post("/dossiers/{target_id}/steps", dependencies=[Depends(require_token)], response_class=HTMLResponse)
def pack_step(request: Request, target_id: str, step: str = Form(...), done: str = Form(...)):
    """Tick or untick one of the pack's five steps; answers with the step list."""
    bad = _bad_id(request, target_id)
    if bad:
        return bad
    st = request.app.state
    row = st.store.dossier(target_id)
    if row is None:
        return render(request, "error.html", status_code=404, error=f"no dossier for {target_id}", log_hint="")
    numbers = {str(n) for n, *_rest in PACK_STEPS}
    if step not in numbers or done not in ("0", "1"):
        return render(request, "error.html", status_code=400, log_hint="",
                      error="step is a number from 1 to 5 and done is 0 or 1")
    steps = {**(row.get("pack_steps") or {}), step: done == "1"}
    st.store.set_pack_steps(target_id, steps)
    return render(request, "partials/pack_steps.html", target_id=target_id, pack_steps=PACK_STEPS, steps_done=steps)
