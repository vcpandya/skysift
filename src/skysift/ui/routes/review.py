"""Vetting pages: the candidates queue, the signal detail page and the decision bar. Reads go through
`ui.queries`; the only write is `Store.decide`, which appends to the decision history."""
from __future__ import annotations

from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from skysift import jsonutil
from skysift.ui import explain, plots, queries
from skysift.ui.app import render, require_token

router = APIRouter()

RUNG_METRICS = {
    "rung1_detection": ["sde", "snr", "n_events", "duration_ratio", "stellar_source"],
    "rung1_events": ["n_events_with_data", "n_covered", "n_good", "ses_max_over_mes", "chases_median"],
    "rung2_shape": ["stacked", "depth_ppm", "depth_err_ppm", "odd_ppm", "even_ppm", "parity_sigma", "secondary_ppm",
                    "secondary_sigma", "free_secondary_phase", "free_secondary_sigma", "box_chi2", "sine_chi2",
                    "ms2", "ms3", "sweet_snr", "sweet_amplitude_ppm", "fit_k", "fit_b", "fit_depth_ppm",
                    "occultation_albedo", "suggested_period"],
    "rung3_stability": ["stacked", "p_value", "chi2", "dof", "n_events", "sap_psf_ratio", "aperture_z", "aperture_ratio"],
    "rung4_novelty": ["period_matched", "cumulative", "q1_q17_dr25_tce", "toi", "kepler_eb", "gaia_nss_two_body_orbit",
                      "gaia_vari_eclipsing_binary", "gaia_skipped"],
}


_HIDDEN = object()     # a metric the key-metrics cell cannot show; it stays in the raw metrics


def _number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _epoch_note(r: dict) -> str:
    """Whether the catalogue epoch lines up with the signal's transits (rung 4's ``epoch_agrees``), or nothing when the
    check could not be made."""
    agrees, offset = r.get("epoch_agrees"), r.get("epoch_offset_h")
    if agrees is None or not _number(offset):
        return ""
    return f", epoch agrees (offset {offset:.1f} h)" if agrees else f", epoch differs by {offset:.1f} h"


def _period_matched(rows):
    """Rung 4's matching catalogue row as "catalogue id at N arcsec" (a row without coordinates has no separation),
    followed by the epoch check when rung 4 made one."""
    if not rows or not isinstance(rows, list) or not isinstance(rows[0], dict): return _HIDDEN
    r = rows[0]
    sep = r.get("sep_arcsec")
    if _number(sep):
        return f"{r.get('catalog')} {r.get('id')} at {sep:.1f} arcsec{_epoch_note(r)}"
    return f"{r.get('catalog')} {r.get('id')}, no position{_epoch_note(r)}"


def _display(key: str, value):
    """A key metric as the ladder shows it: flags as yes or no, the period-matched row as text, anything else as it
    is; other dicts and lists are hidden."""
    if key == "period_matched": return _period_matched(value)
    if isinstance(value, bool): return "yes" if value else "no"
    if isinstance(value, (dict, list)): return _HIDDEN
    return value
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


def _ladder_rows(tests: list[dict]) -> dict[str, dict]:
    """The expert view of each rung's latest row (tests come ordered by id): key metrics as the ladder shows them and
    the raw metrics as JSON, keyed by rung like ``explain.why``'s rows."""
    rows = {}
    for t in tests:
        metrics = t["metrics"] or {}
        keys = RUNG_METRICS.get(t["rung"], [])
        shown = [(k, v) for k in keys if k in metrics and (v := _display(k, metrics[k])) is not _HIDDEN]
        rows[t["rung"]] = {**t, "shown": shown, "raw": jsonutil.dumps(metrics, indent=1)}
    return rows


# the status glossary's words as the popup footer says them ("Stage 1 means ..."); the reviewer row is plural
GLOSSARY_SUBJECT = {"Candidate, stage 1": "Stage 1 means", "Candidate, stage 2": "Stage 2 means",
                    "Approved / Rejected by reviewer / Needs data": "Approved, Rejected by reviewer and Needs data mean"}


def _glossary(v: explain.Verdict) -> list[str]:
    """One sentence per status word, the signal's own word first."""
    own = f"{v.word}, {v.stage}" if v.stage else v.word
    lines = [(word != own, f"{GLOSSARY_SUBJECT.get(word, word + ' means')} {meaning[:1].lower()}{meaning[1:]}")
             for word, meaning in explain.STATUS_GLOSSARY]
    return [line for _later, line in sorted(lines, key=lambda x: x[0])]


def _explained(d: dict, signal_id: int) -> dict:
    """What the popup and the signal page's Why section share: the verdict, one row per rung, the expert's raw rows,
    the glossary footer and where the pill opens the popup."""
    v = explain.verdict(d["signal"], d["tests"], d["candidate"], d["decisions"])
    return {"v": v, "rows": explain.why(d["tests"]), "ladder": _ladder_rows(d["tests"]), "glossary": _glossary(v),
            "recheck_history": [], "verdict_src": f"/partials/signals/{signal_id}/verdict"}


VERSIONS = ("current", "all")


class _BadView(ValueError):
    pass


def _view(request: Request) -> dict:
    """The queue view carried in the query string (filter, sort column, direction, which search versions), checked
    against the allow-lists."""
    q = request.query_params
    view = {"status": q.get("status", "candidate_stage1"), "sort": q.get("sort", "sde"), "dir": q.get("dir", "desc"),
            "versions": q.get("versions", "current")}
    if view["status"] not in queries.QUEUE_STATUSES:
        raise _BadView(f"unknown status {view['status']!r}; one of {', '.join(queries.QUEUE_STATUSES)}")
    if view["sort"] not in queries.SORT_COLUMNS:
        raise _BadView(f"unknown sort column {view['sort']!r}; one of {', '.join(queries.SORT_COLUMNS)}")
    if view["dir"] not in ("asc", "desc"):
        raise _BadView(f"unknown direction {view['dir']!r}; asc or desc")
    if view["versions"] not in VERSIONS:
        raise _BadView(f"unknown versions {view['versions']!r}; {' or '.join(VERSIONS)}")
    return view


def _version(store, view: dict) -> str | None:
    """The detector version the view keeps to: the current search version, or None (no filter) for 'all'."""
    return queries.current_search_version(store) if view["versions"] == "current" else None


def _bad_request(request: Request, e: Exception) -> HTMLResponse:
    return render(request, "error.html", status_code=400, error=str(e), log_hint="")


@router.get("/candidates", response_class=HTMLResponse)
def candidates_page(request: Request):
    st = request.app.state
    try:
        view = _view(request)
    except _BadView as e:
        return _bad_request(request, e)
    version = _version(st.store, view)
    rows = queries.review_queue(st.store, status=view["status"], sort=view["sort"], desc=view["dir"] != "asc",
                                version=version)
    return render(request, "candidates.html", rows=rows, view=view, statuses=list(queries.QUEUE_STATUSES),
                  columns=COLUMNS, hidden=queries.hidden_by_version(st.store, view["status"], version))


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
    prev_id, next_id = queries.neighbours(st.store, signal_id, view["status"], view["sort"], view["dir"] != "asc",
                                          version=_version(st.store, view))
    qs = "?" + urlencode(view)
    ex = _explained(d, signal_id)
    return render(request, "signal.html", d=d, links=_links(d["target"]), **ex,
                  summary=explain.summary_sentence(d["signal"], d["target"], d["tests"]),
                  copy_text=explain.copy_summary(d["signal"], d["target"], ex["v"],
                                                 f"{request.base_url}signals/{signal_id}"),
                  pixel_figures=None, plot_layout=plots.LAYOUT, marks=plots.MARKS, captions=plots.CAPTIONS,
                  prev_url=f"/signals/{prev_id}{qs}" if prev_id else None,
                  next_url=f"/signals/{next_id}{qs}" if next_id else None, view=view, signal_id=signal_id, error=None)


@router.get("/partials/signals/{signal_id}/verdict", response_class=HTMLResponse)
def verdict_popup(request: Request, signal_id: int):
    """The verdict popup's body: the headline, one row per check, what an undetermined check was missing and what
    happens next, and the status glossary."""
    d = queries.signal_detail(request.app.state.store, signal_id)
    if d is None:
        return HTMLResponse(f"no signal {signal_id}", status_code=404)
    return render(request, "partials/verdict.html", signal=d["signal"], target=d["target"], signal_id=signal_id,
                  **_explained(d, signal_id))


# the toast a recorded decision sends (app.js shows HX-Trigger "toast" events), in the tone of the new status
DECIDED = {"approve": ("Approved. It moves to the Approved page.", "pass"),
           "reject": ("Rejected. The note is kept.", "fail"),
           "needs_data": ("Marked as waiting for data.", "undetermined")}


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
    resp = render(request, "partials/decision_bar.html", d=d, signal_id=signal_id, error=None)
    message, tone = DECIDED[decision]
    resp.headers["HX-Trigger"] = jsonutil.dumps({"toast": {"message": message, "tone": tone}})
    return resp
