"""Decisions page and the downloadable review document. Reads go through `ui.queries`; nothing here writes."""
from __future__ import annotations

import re

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from skysift import jsonutil
from skysift.ui import explain, queries
from skysift.ui.app import render

router = APIRouter()
STATUSES = ("approved", "review", "rejected", "published")
VERSIONS = ("current", "all")


@router.get("/approved", response_class=HTMLResponse)
def approved_page(request: Request):
    """The decided signals of one status, kept to the current search version by default as the Candidates page's
    Approved chip counts them (``?versions=all`` lifts the filter)."""
    store = request.app.state.store
    status = request.query_params.get("status", "approved")
    versions = request.query_params.get("versions", "current")
    if status not in STATUSES:
        return render(request, "error.html", status_code=400,
                      error=f"unknown status {status!r}; one of {', '.join(STATUSES)}", log_hint="")
    if versions not in VERSIONS:
        return render(request, "error.html", status_code=400,
                      error=f"unknown versions {versions!r}; {' or '.join(VERSIONS)}", log_hint="")
    version = queries.current_search_version(store) if versions == "current" else None
    rows = queries.approved(store, status=status, version=version)
    for r in rows:                     # the clipboard paragraph names the signal page the reader can open
        r["copy_text"] = explain.copy_summary(r, r, r["v"], f"{request.base_url}signals/{r['id']}")
    return render(request, "approved.html", rows=rows, status=status, statuses=STATUSES, versions=versions,
                  hidden=queries.decided_hidden_by_version(store, status, version))


@router.get("/signals/{signal_id}/review.json")
def review_json(request: Request, signal_id: int):
    doc = queries.review_document(request.app.state.store, signal_id)
    if doc is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    target = re.sub(r"[^A-Za-z0-9._-]", "", str(doc["signal"]["target_id"]))
    name = f"{target}_{signal_id}_review.json"
    return Response(jsonutil.dumps(doc, indent=1), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
