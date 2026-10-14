"""Vetting pages: the candidates queue, the signal detail page and the decision bar. Reads go through
`ui.queries`; the only write is `Store.decide`, which appends to the decision history."""
from __future__ import annotations

from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from skysift import jsonutil
from skysift.ui import queries
from skysift.ui.app import render, require_token

router = APIRouter()

RUNG_METRICS = {
    "rung1_detection": ["sde", "snr", "n_events", "duration_ratio", "stellar_source"],
    "rung2_shape": ["depth_ppm", "depth_err_ppm", "odd_ppm", "even_ppm", "parity_sigma", "secondary_ppm",
                    "secondary_sigma", "free_secondary_phase", "free_secondary_sigma"],
    "rung3_stability": ["p_value", "chi2", "dof", "n_events", "sap_psf_ratio"],
    "rung4_novelty": ["cumulative", "q1_q17_dr25_tce", "toi", "kepler_eb", "gaia_nss_two_body_orbit",
                      "gaia_vari_eclipsing_binary", "gaia_skipped"],
}
COLUMNS = [("target_id", "target"), ("period", "period d"), ("depth_ppm", "depth ppm"), ("duration", "duration h"),
           ("sde", "SDE"), ("snr", "SNR"), ("n_events", "events"), ("detected", "detected")]


def _links(target: dict) -> list[tuple[str, str]]:
    out = []
    if target.get("kic"):
        out.append((f"KIC {target['kic']}",
                    f"https://archive.stsci.edu/kepler/data_search/search.php?kic_kepler_id={int(target['kic'])}"))
    if target.get("gaia_id"):
        out.append((f"Gaia DR3 {target['gaia_id']}", "https://gea.esac.esa.int/archive/"))
    if target.get("ra") is not None and target.get("dec") is not None:
        coords = quote(f"{target['ra']} {target['dec']}")
        out.append(("MAST portal", f"https://mast.stsci.edu/portal/Mashup/Clients/Mast/Portal.html?searchQuery={coords}"))
    return out


def _ladder_rows(tests: list[dict]) -> list[dict]:
    rows = []
    for t in tests:
        keys = RUNG_METRICS.get(t["rung"], [])
        shown = [(k, t["metrics"].get(k)) for k in keys
                 if k in t["metrics"] and not isinstance(t["metrics"][k], (dict, list))]
        rows.append({**t, "shown": shown, "raw": jsonutil.dumps(t["metrics"], indent=1)})
    return rows


class _BadView(ValueError):
    pass


def _view(request: Request) -> dict:
    """The queue view carried in the query string (filter, sort column, direction), checked against the allow-lists."""
    q = request.query_params
    view = {"status": q.get("status", "candidate_stage1"), "sort": q.get("sort", "sde"), "dir": q.get("dir", "desc")}
    if view["status"] not in queries.QUEUE_STATUSES:
        raise _BadView(f"unknown status {view['status']!r}; one of {', '.join(queries.QUEUE_STATUSES)}")
    if view["sort"] not in queries.SORT_COLUMNS:
        raise _BadView(f"unknown sort column {view['sort']!r}; one of {', '.join(queries.SORT_COLUMNS)}")
    if view["dir"] not in ("asc", "desc"):
        raise _BadView(f"unknown direction {view['dir']!r}; asc or desc")
    return view


def _bad_request(request: Request, e: Exception) -> HTMLResponse:
    return render(request, "error.html", status_code=400, error=str(e), log_hint="")


@router.get("/candidates", response_class=HTMLResponse)
def candidates_page(request: Request):
    st = request.app.state
    try:
        view = _view(request)
    except _BadView as e:
        return _bad_request(request, e)
    rows = queries.review_queue(st.store, status=view["status"], sort=view["sort"], desc=view["dir"] != "asc")
    return render(request, "candidates.html", rows=rows, view=view, statuses=list(queries.QUEUE_STATUSES),
                  columns=COLUMNS)


@router.get("/signals/{signal_id}", response_class=HTMLResponse)
def signal_page(request: Request, signal_id: int):
    st = request.app.state
    try:
        view = _view(request)
    except _BadView as e:
        return _bad_request(request, e)
    d = queries.signal_detail(st.store, signal_id)
    if d is None:
        return render(request, "error.html", status_code=404, error=f"no signal {signal_id}", log_hint="")
    prev_id, next_id = queries.neighbours(st.store, signal_id, view["status"], view["sort"], view["dir"] != "asc")
    qs = "?" + urlencode(view)
    return render(request, "signal.html", d=d, ladder=_ladder_rows(d["tests"]), links=_links(d["target"]),
                  prev_url=f"/signals/{prev_id}{qs}" if prev_id else None,
                  next_url=f"/signals/{next_id}{qs}" if next_id else None, view=view, signal_id=signal_id, error=None)


@router.post("/signals/{signal_id}/decide", dependencies=[Depends(require_token)], response_class=HTMLResponse)
def decide(request: Request, signal_id: int, decision: str = Form(...), note: str = Form("")):
    st = request.app.state
    try:
        st.store.decide(signal_id, decision, note.strip())
    except KeyError:
        return render(request, "error.html", status_code=404, error=f"no signal {signal_id}", log_hint="")
    except ValueError as e:
        d = queries.signal_detail(st.store, signal_id)
        return render(request, "partials/decision_bar.html", status_code=400, d=d, signal_id=signal_id, error=str(e))
    d = queries.signal_detail(st.store, signal_id)
    return render(request, "partials/decision_bar.html", d=d, signal_id=signal_id, error=None)
