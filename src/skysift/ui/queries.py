"""Every read the dashboard (and `skysift status`) needs, as plain dicts from short sessions. Nothing here writes."""
from __future__ import annotations

import json

from sqlalchemy import func, select

from skysift.store.db import (
    ArtifactRow,
    CandidateRow,
    QueryRow,
    RunRow,
    SignalRow,
    Store,
    TargetRow,
    TestRow,
)
from skysift.store.retention import Retention

SORT_COLUMNS = {"sde": SignalRow.sde, "snr": SignalRow.snr, "period": SignalRow.period, "depth_ppm": SignalRow.depth_ppm,
                "duration": SignalRow.duration, "n_events": SignalRow.n_events, "detected": SignalRow.id,
                "target_id": SignalRow.target_id}
QUEUE_STATUSES = {"candidate_stage1": ("candidate_stage1",), "undetermined": ("undetermined",),
                  "rejected": ("rejected",), "all": ("candidate_stage1", "undetermined", "rejected")}


def _iso(dt) -> str | None:
    return dt.isoformat() if dt is not None else None


def run_time_totals(store: Store) -> tuple[int, float]:
    """(number of finished runs, their total wall seconds) from one aggregate, so the Live page's poll never loads
    every run into Python. SQLite has no interval type, hence the julianday arithmetic."""
    if store.engine.dialect.name == "sqlite":
        seconds = func.sum((func.julianday(RunRow.finished) - func.julianday(RunRow.started)) * 86400.0)
    else:
        seconds = func.sum(func.extract("epoch", RunRow.finished - RunRow.started))
    with store.session() as s:
        n, total = s.execute(select(func.count(), seconds)
                             .where(RunRow.status == "done", RunRow.finished.is_not(None))).one()
    return int(n), float(total or 0.0)


def mean_seconds(store: Store) -> float | None:
    """Mean wall time of a finished run (None before the first one), rounded to 2 decimals."""
    n, total = run_time_totals(store)
    return round(total / n, 2) if n else None


def summary(store: Store, retention: Retention, settings) -> dict:
    from skysift.pipeline import MAX_ATTEMPTS, MAX_POOL_BREAKS
    counts = store.counts()
    with store.session() as s:
        states = dict(s.execute(select(TargetRow.state, func.count()).group_by(TargetRow.state)).all())
        outcomes = dict(s.execute(select(SignalRow.final_status, func.count()).group_by(SignalRow.final_status)).all())
        rungs: dict[str, dict] = {}
        for rung, status, n in s.execute(select(TestRow.rung, TestRow.status, func.count())
                                         .group_by(TestRow.rung, TestRow.status)):
            rungs.setdefault(rung, {"pass": 0, "fail": 0, "undetermined": 0})[status] = n
        errors = s.scalars(select(RunRow).where(RunRow.status == "error", RunRow.error != "superseded")
                           .order_by(RunRow.id.desc()).limit(5)).all()
        last_errors = [{"target": e.target_id, "finished": _iso(e.finished),
                        "error": e.error.splitlines()[0][:200] if e.error else ""} for e in errors]
    n_done, seconds = run_time_totals(store)
    hours = max(1e-9, seconds / 3600)
    policy = retention.policy
    return {"counts": counts, "targets_by_state": states, "signal_outcomes": outcomes, "rung_counts": rungs,
            "targets_per_hour": round(n_done / hours, 1) if n_done else 0.0,
            "mean_seconds_per_run": round(seconds / n_done, 2) if n_done else None,
            "error_capped_targets": store.error_capped_targets(MAX_ATTEMPTS, MAX_POOL_BREAKS),
            "cache_bytes": retention.cached_bytes(), "retention": settings.retention,
            "cache_budget_bytes": policy.budget_bytes if policy.mode == "budget" else None,
            "last_errors": last_errors, "queries": counts["queries"]}


def source_progress(store: Store) -> dict:
    out: dict[str, dict] = {}
    with store.session() as s:
        for source, state, n in s.execute(select(TargetRow.source, TargetRow.state, func.count())
                                          .group_by(TargetRow.source, TargetRow.state)):
            d = out.setdefault(source, {"new": 0, "done": 0, "error": 0, "total": 0})
            d[state if state in d else "new"] += n; d["total"] += n
    return out


def _last_test_subquery():
    """(signal_id, rung, status, reason) of the last rung each signal reached."""
    last_id = select(TestRow.signal_id, func.max(TestRow.id).label("tid")).group_by(TestRow.signal_id).subquery()
    return (select(TestRow.signal_id, TestRow.rung, TestRow.status, TestRow.reason)
            .join(last_id, TestRow.id == last_id.c.tid)).subquery()


def _queue_select(status: str, sort: str, desc: bool, ids_only: bool = False):
    last = _last_test_subquery()
    col = SORT_COLUMNS.get(sort, SignalRow.sde)
    cols = ([SignalRow.id] if ids_only else
            [SignalRow, TargetRow, CandidateRow.status.label("decision"), last.c.rung, last.c.status.label("verdict"),
             last.c.reason])
    q = (select(*cols).select_from(SignalRow)
         .join(TargetRow, TargetRow.target_id == SignalRow.target_id)
         .outerjoin(CandidateRow, CandidateRow.signal_id == SignalRow.id)
         .outerjoin(last, last.c.signal_id == SignalRow.id)
         .where(SignalRow.final_status.in_(QUEUE_STATUSES.get(status, QUEUE_STATUSES["candidate_stage1"]))))
    return q.order_by(col.desc() if desc else col.asc(), SignalRow.id.desc())


def _queue_row(sig: SignalRow, t: TargetRow, decision, rung, verdict, reason=None) -> dict:
    return {"id": sig.id, "target_id": sig.target_id, "period": sig.period, "depth_ppm": sig.depth_ppm,
            "duration_h": sig.duration * 24.0, "sde": sig.sde, "snr": sig.snr, "n_events": sig.n_events,
            "last_rung": rung, "verdict": verdict, "reason": reason or "", "final_status": sig.final_status,
            "decision": decision, "detected": sig.id, "kic": t.kic, "gaia_id": t.gaia_id, "gmag": t.gmag,
            "source": t.source}


def review_queue(store: Store, status: str = "candidate_stage1", sort: str = "sde", desc: bool = True,
                 limit: int = 500) -> list[dict]:
    with store.session() as s:
        return [_queue_row(*row) for row in s.execute(_queue_select(status, sort, desc).limit(limit)).all()]


def neighbours(store: Store, signal_id: int, status: str, sort: str, desc: bool) -> tuple[int | None, int | None]:
    with store.session() as s:
        ids = list(s.scalars(_queue_select(status, sort, desc, ids_only=True)))
    if signal_id not in ids:
        return None, None
    i = ids.index(signal_id)
    return (ids[i - 1] if i > 0 else None), (ids[i + 1] if i + 1 < len(ids) else None)


def signal_detail(store: Store, signal_id: int) -> dict | None:
    with store.session() as s:
        sig = s.get(SignalRow, signal_id)
        if sig is None:
            return None
        t = s.get(TargetRow, sig.target_id); run = s.get(RunRow, sig.run_id)
        tests = s.scalars(select(TestRow).where(TestRow.signal_id == signal_id).order_by(TestRow.id)).all()
        cand = s.scalars(select(CandidateRow).where(CandidateRow.signal_id == signal_id)).first()
        signal = {c.name: getattr(sig, c.name) for c in SignalRow.__table__.columns}
        target = ({c.name: getattr(t, c.name) for c in TargetRow.__table__.columns} if t
                  else {"target_id": sig.target_id})
        run_d = {"id": run.id, "status": run.status, "started": _iso(run.started), "finished": _iso(run.finished),
                 "detector_version": run.detector_version, "ladder_version": run.ladder_version,
                 "periodogram_sha256": run.periodogram_sha256} if run else None
        cand_d = {"status": cand.status, "note": cand.note, "decided_at": _iso(cand.decided_at),
                  "branch": cand.branch} if cand else None
        return {"signal": signal, "target": target, "run": run_d,
                "tests": [{"rung": x.rung, "status": x.status, "reason": x.reason, "duration_s": x.duration_s,
                           "metrics": x.metrics} for x in tests],
                "candidate": cand_d, "decisions": store.decisions(signal_id)}


def approved(store: Store, status: str = "approved") -> list[dict]:
    with store.session() as s:
        rows = s.execute(select(SignalRow, TargetRow, CandidateRow)
                         .join(CandidateRow, CandidateRow.signal_id == SignalRow.id)
                         .join(TargetRow, TargetRow.target_id == SignalRow.target_id)
                         .where(CandidateRow.status == status).order_by(CandidateRow.decided_at.desc())).all()
        return [{**_queue_row(sig, t, c.status, None, None), "note": c.note, "decided_at": _iso(c.decided_at),
                 "doi": c.doi} for sig, t, c in rows]


def review_document(store: Store, signal_id: int) -> dict | None:
    d = signal_detail(store, signal_id)
    if d is None:
        return None
    with store.session() as s:
        qs = s.scalars(select(QueryRow).where(QueryRow.target_id == d["signal"]["target_id"])
                       .order_by(QueryRow.id)).all()
        arts = s.scalars(select(ArtifactRow).where(ArtifactRow.target_id == d["signal"]["target_id"])).all()
        queries_d = [{"service": q.service, "url": q.url, "params": json.loads(q.params or "{}"), "utc": _iso(q.utc),
                      "status": q.status, "nbytes": q.nbytes, "sha256": q.sha256, "error": q.error} for q in qs]
        arts_d = [{"sha256": a.sha256, "nbytes": a.nbytes, "url": a.url, "kind": a.kind, "pinned": a.pinned,
                   "deleted_at": _iso(a.deleted_at)} for a in arts]
    return {**d, "queries": queries_d, "artifacts": arts_d}
