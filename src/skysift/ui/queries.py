"""Every read the dashboard (and `skysift status`) needs, as plain dicts from short sessions. Nothing here writes."""
from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select

from skysift import crosscheck as cc
from skysift.store import db
from skysift.store.db import (
    RECHECK_JOB_PREFIX,
    ArtifactRow,
    CandidateRow,
    DecisionRow,
    QueryRow,
    RunRow,
    SignalRow,
    Store,
    TargetRow,
    TestRow,
)
from skysift.store.retention import Retention
from skysift.ui import explain

SORT_COLUMNS = {"sde": SignalRow.sde, "snr": SignalRow.snr, "period": SignalRow.period, "depth_ppm": SignalRow.depth_ppm,
                "duration": SignalRow.duration, "n_events": SignalRow.n_events, "detected": SignalRow.id,
                "target_id": SignalRow.target_id}
# the engine's statuses (the store's list, the one source) less "pending": a pending signal is still being vetted, so it
# is in no queue; "all" is these
ENGINE_STATUSES = tuple(s for s in db.ENGINE_STATUSES if s != "pending")
# the Candidates page's filters, in chip order: the stage 1 and 2 candidates nobody has decided (Tonight's "waiting"),
# the engine's statuses, then the reviewer's decisions, then everything
QUEUE_STATUSES = ("waiting", *ENGINE_STATUSES, "approved", "needs_data", "all")
# a run the stage-2 recheck started (a P/2 signal's run) is no sweep: every sweep reader below leaves it out
SWEEP_RUN = RunRow.job_key.not_like(f"{RECHECK_JOB_PREFIX}%")


def _iso(dt) -> str | None:
    return dt.isoformat() if dt is not None else None


def run_time_totals(store: Store) -> tuple[int, float]:
    """(number of finished sweep runs, their total wall seconds) from one aggregate, so the Live page's poll never
    loads every run into Python; recheck runs are left out. SQLite has no interval type, hence the julianday
    arithmetic."""
    if store.engine.dialect.name == "sqlite":
        seconds = func.sum((func.julianday(RunRow.finished) - func.julianday(RunRow.started)) * 86400.0)
    else:
        seconds = func.sum(func.extract("epoch", RunRow.finished - RunRow.started))
    with store.session() as s:
        n, total = s.execute(select(func.count(), seconds)
                             .where(RunRow.status == "done", RunRow.finished.is_not(None), SWEEP_RUN)).one()
    return int(n), float(total or 0.0)


def mean_seconds(store: Store) -> float | None:
    """Mean wall time of a finished run (None before the first one), rounded to 2 decimals."""
    n, total = run_time_totals(store)
    return round(total / n, 2) if n else None


def _rung_counts(s) -> dict[str, dict]:
    rungs: dict[str, dict] = {}
    for rung, status, n in s.execute(select(TestRow.rung, TestRow.status, func.count())
                                     .group_by(TestRow.rung, TestRow.status)):
        rungs.setdefault(rung, {"pass": 0, "fail": 0, "undetermined": 0})[status] = n
    return rungs


def rung_counts(store: Store) -> dict[str, dict]:
    """{rung: {"pass", "fail", "undetermined"}} over every test row, for the funnel."""
    with store.session() as s:
        return _rung_counts(s)


def funnel(rungs: dict[str, dict]) -> list[dict]:
    """The rung funnel as bars, in ladder order: each rung's verdict counts, and as percent of the widest rung."""
    order = [*explain.LADDER_ORDER, *sorted(r for r in rungs if r not in explain.LADDER_ORDER)]
    rows = [{"rung": r, "name": explain.RUNG_NAMES.get(r, r), **rungs[r]} for r in order if r in rungs]
    widest = max((sum(r[k] for k in ("pass", "fail", "undetermined")) for r in rows), default=0) or 1
    for r in rows:
        r["total"] = r["pass"] + r["fail"] + r["undetermined"]
        r["pct"] = {k: round(100 * r[k] / widest, 1) for k in ("pass", "fail", "undetermined")}
    return rows


def summary(store: Store, retention: Retention, settings) -> dict:
    """The overview's numbers. Its recent errors keep failed ``recheck:`` runs on purpose: a recheck that broke is an
    error a person should see, so unlike the readers of sweep results this one does not filter them out."""
    from skysift.pipeline import MAX_ATTEMPTS, MAX_POOL_BREAKS
    counts = store.counts()
    with store.session() as s:
        states = dict(s.execute(select(TargetRow.state, func.count()).group_by(TargetRow.state)).all())
        outcomes = dict(s.execute(select(SignalRow.final_status, func.count()).group_by(SignalRow.final_status)).all())
        rungs = _rung_counts(s)
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


def current_search_version(store: Store) -> str | None:
    """The detector version of the most recently started finished sweep run (recheck runs left out): the search code
    the queue should show by default (None before the first run)."""
    with store.session() as s:
        return s.scalar(select(RunRow.detector_version).where(RunRow.status == "done", SWEEP_RUN)
                        .order_by(RunRow.started.desc(), RunRow.id.desc()).limit(1))


def _latest_decision_subquery():
    """(signal_id, decision) of each signal's latest reviewer decision."""
    last_id = select(DecisionRow.signal_id, func.max(DecisionRow.id).label("did")).group_by(DecisionRow.signal_id).subquery()
    return (select(DecisionRow.signal_id, DecisionRow.decision)
            .join(last_id, DecisionRow.id == last_id.c.did)).subquery()


def _filtered(q, status: str, version: str | None, search: str, latest):
    """``q`` (selecting from SignalRow, with CandidateRow and the ``latest`` decision subquery outer-joined) kept to one
    queue status, search version and target-id substring. ``approved`` and ``needs_data`` follow the reviewer's latest
    decision, ``waiting`` is the stage 1 and 2 candidates nobody has decided; the substring is matched
    case-insensitively and literally (no LIKE wildcards)."""
    if status == "waiting":
        q = _undecided(q.where(SignalRow.final_status.in_(CANDIDATE_STATUSES)), latest)
    elif status == "approved":
        q = q.where(CandidateRow.status == "approved")
    elif status == "needs_data":
        q = q.where(CandidateRow.status == "review", latest.c.decision == "needs_data")
    else:
        q = q.where(SignalRow.final_status.in_(ENGINE_STATUSES if status == "all" else (status,)))
    if version is not None:
        q = q.join(RunRow, RunRow.id == SignalRow.run_id).where(RunRow.detector_version == version)
    if search:
        q = q.where(func.lower(SignalRow.target_id).contains(search.lower(), autoescape=True))
    return q


def _base(*cols, latest):
    return (select(*cols).select_from(SignalRow)
            .outerjoin(CandidateRow, CandidateRow.signal_id == SignalRow.id)
            .outerjoin(latest, latest.c.signal_id == SignalRow.id))


def _count(s, status: str, version: str | None) -> int:
    latest = _latest_decision_subquery()
    return s.scalar(_filtered(_base(func.count(), latest=latest), status, version, "", latest))


def status_counts(store: Store, version: str | None = None) -> dict[str, int]:
    """How many signals each chip lists, kept to ``version`` like the queue."""
    with store.session() as s:
        return {status: _count(s, status, version) for status in QUEUE_STATUSES}


def hidden_by_version(store: Store, status: str, version: str | None) -> int:
    """How many signals of this queue status a filter on ``version`` hides (0 when there is no filter)."""
    if version is None:
        return 0
    with store.session() as s:
        return _count(s, status, None) - _count(s, status, version)


def _queue_select(status: str, sort: str, desc: bool, ids_only: bool = False, version: str | None = None,
                  search: str = ""):
    last, latest = _last_test_subquery(), _latest_decision_subquery()
    col = SORT_COLUMNS.get(sort, SignalRow.sde)
    cols = ([SignalRow.id] if ids_only else
            [SignalRow, TargetRow, CandidateRow.status.label("decision"), last.c.rung, last.c.status.label("verdict"),
             last.c.reason, latest.c.decision.label("latest_decision")])
    q = (_base(*cols, latest=latest)
         .join(TargetRow, TargetRow.target_id == SignalRow.target_id)
         .outerjoin(last, last.c.signal_id == SignalRow.id))
    q = _filtered(q, status, version, search, latest)
    return q.order_by(col.desc() if desc else col.asc(), SignalRow.id.desc())


def _queue_row(sig: SignalRow, t: TargetRow, decision, rung, verdict, reason=None) -> dict:
    return {"id": sig.id, "target_id": sig.target_id, "period": sig.period, "depth_ppm": sig.depth_ppm,
            "duration_h": sig.duration * 24.0, "sde": sig.sde, "snr": sig.snr, "n_events": sig.n_events,
            "last_rung": rung, "verdict": verdict, "reason": reason or "", "final_status": sig.final_status,
            "decision": decision, "detected": sig.id, "kic": t.kic, "gaia_id": t.gaia_id, "gmag": t.gmag,
            "source": t.source}


def _tests_by_signal(s, ids: list[int]) -> dict[int, list[dict]]:
    """Every test row of these signals in one query, grouped by signal and ordered by id as ``explain`` expects."""
    out: dict[int, list[dict]] = {i: [] for i in ids}
    if ids:
        for t in s.scalars(select(TestRow).where(TestRow.signal_id.in_(ids)).order_by(TestRow.id)):
            out[t.signal_id].append({"rung": t.rung, "status": t.status, "reason": t.reason, "metrics": t.metrics})
    return out


def _explained(row: dict, tests: list[dict], latest_decision: str | None) -> dict:
    """The queue row with the verdict in words (``explain.verdict``, kept whole as ``v``) and the summary
    sentence. The row itself carries the signal and target fields both need."""
    decision = {"status": row["decision"]} if row["decision"] else None
    v = explain.verdict(row, tests, decision, [{"decision": latest_decision}] if latest_decision else None)
    return {**row, "stage": v.stage, "verdict_word": v.word, "verdict_tone": v.tone, "reviewer": v.reviewer,
            "v": v, "summary": explain.summary_sentence(row, row, tests)}


def review_queue(store: Store, status: str = "candidate_stage1", sort: str = "sde", desc: bool = True,
                 limit: int = 500, version: str | None = None, search: str = "") -> list[dict]:
    """The queue rows with their verdict in words; with ``version``, only signals found by runs of that detector
    version; with ``search``, only targets whose id contains it."""
    with store.session() as s:
        found = s.execute(_queue_select(status, sort, desc, version=version, search=search).limit(limit)).all()
        tests = _tests_by_signal(s, [row[0].id for row in found])
        return [_explained(_queue_row(*row[:6]), tests[row[0].id], row[6]) for row in found]


CANDIDATE_STATUSES = ("candidate_stage1", "candidate_stage2")


def _undecided(q, latest):
    """``q`` kept to signals no person has decided: no decision row, and no candidate row past review."""
    return q.where(latest.c.decision.is_(None),
                   (CandidateRow.status.is_(None)) | (CandidateRow.status == "review"))


def recent_candidates(store: Store, version: str | None, limit: int = 6) -> list[dict]:
    """The newest stage 1 and stage 2 candidates of ``version`` nobody has decided yet, as cards (newest first)."""
    last, latest = _last_test_subquery(), _latest_decision_subquery()
    q = (_base(SignalRow, TargetRow, CandidateRow.status, last.c.rung, last.c.status, last.c.reason, latest=latest)
         .join(TargetRow, TargetRow.target_id == SignalRow.target_id)
         .outerjoin(last, last.c.signal_id == SignalRow.id)
         .where(SignalRow.final_status.in_(CANDIDATE_STATUSES)))
    if version is not None:
        q = q.join(RunRow, RunRow.id == SignalRow.run_id).where(RunRow.detector_version == version)
    with store.session() as s:
        found = s.execute(_undecided(q, latest).order_by(SignalRow.id.desc()).limit(limit)).all()
        tests = _tests_by_signal(s, [row[0].id for row in found])
        return [_explained(_queue_row(*row), tests[row[0].id], None) for row in found]


def _naive_utc(dt: datetime) -> datetime:
    """The store's columns hold UTC without a zone: an aware time is converted, a naive one is taken as UTC."""
    return dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo else dt


SWEEP_GAP = timedelta(minutes=30)     # more than this with no run finishing, and the next run starts a new sweep


def _latest_sweep(s, launched: tuple[datetime, datetime] | None) -> tuple[datetime | None, datetime | None]:
    """(start, end) of the latest sweep; end None means it runs to now, start None means no finished run yet.
    ``launched`` is the dashboard's last sweep (its history record's started and finished), used when the newest done
    run belongs to it. Otherwise the done runs are grouped by gaps: walking back from the newest, a run that finished
    more than SWEEP_GAP before the group's first start belongs to an earlier sweep. Only the latest sweep's rows are
    read, not every run. Recheck runs are no sweep and are left out."""
    latest = s.scalar(select(func.max(RunRow.started)).where(RunRow.status == "done", SWEEP_RUN))
    if latest is None:
        return None, None
    if launched is not None:
        start, end = (_naive_utc(t) for t in launched)
        if start <= latest <= end:
            return start, end
    start = latest
    rows = s.execute(select(RunRow.started, RunRow.finished).where(RunRow.status == "done", SWEEP_RUN)
                     .order_by(RunRow.started.desc()).execution_options(yield_per=500))
    for started, finished in rows:
        if start - (finished or started) > SWEEP_GAP:
            break
        start = min(start, started)
    return start, None


def _window(s, since: datetime | None, launched: tuple[datetime, datetime] | None = None) -> dict:
    """The sweep window: from ``since`` (a running sweep's start) on, or else the latest sweep (``_latest_sweep``).
    The targets with a finished run started in it, their signals by status, and the window's first start and last
    finish (None before the first run). Recheck runs are left out: the signals a recheck derived are not the sweep's."""
    if since is not None:
        start, end = _naive_utc(since), None
    else:
        start, end = _latest_sweep(s, launched)
    out = {"start": start, "scanned": 0, "first": None, "last": None, "found": {}}
    if start is not None:
        window = [RunRow.status == "done", SWEEP_RUN, RunRow.started >= start,
                  *([RunRow.started <= end] if end else [])]
        out["scanned"], out["first"], out["last"] = s.execute(
            select(func.count(func.distinct(RunRow.target_id)), func.min(RunRow.started), func.max(RunRow.finished))
            .where(*window)).one()
        out["found"] = dict(s.execute(select(SignalRow.final_status, func.count())
                                      .join(RunRow, RunRow.id == SignalRow.run_id)
                                      .where(*window).group_by(SignalRow.final_status)).all())
    return out


def _engine_rejected(s, version: str | None) -> int:
    """Signals of ``version`` a check rejected: the "rejected" queue less those whose latest decision is a reject."""
    latest = _latest_decision_subquery()
    q = _filtered(_base(func.count(), latest=latest), "rejected", version, "", latest)
    return s.scalar(q.where(latest.c.decision.is_(None) | (latest.c.decision != "reject")))


def _last_sweep(w: dict) -> dict | None:
    if w["first"] is None:
        return None
    first, last = w["first"], w["last"] or w["first"]
    return {"started": first, "finished": last, "n_targets": w["scanned"],
            "seconds": max(0.0, (last - first).total_seconds()),
            "candidates": sum(w["found"].get(k, 0) for k in CANDIDATE_STATUSES)}


def last_sweep(store: Store, launched: tuple[datetime, datetime] | None = None) -> dict | None:
    """The latest sweep as Live's idle line says it: started, finished, targets, seconds and candidates found.
    ``launched``: the dashboard's last sweep, (started, finished), as ``_latest_sweep`` takes it."""
    with store.session() as s:
        return _last_sweep(_window(s, None, launched))


def done_since(store: Store, since: datetime) -> int:
    """How many targets a running sweep has finished: targets with a done run started at or after ``since``."""
    with store.session() as s:
        return _window(s, since)["scanned"]


def tonight(store: Store, version: str | None, since: datetime | None = None,
            launched: tuple[datetime, datetime] | None = None) -> dict:
    """The Tonight page's numbers. In the sweep window (``_window``, the running sweep from ``since`` or else the
    latest sweep, ``launched`` being the dashboard's last one): ``scanned`` targets and the ``signals`` their
    runs found. ``candidates_waiting`` (stage 1 or 2, nobody has decided), ``undetermined`` (how many signals each
    next step holds, from the latest undetermined test row of each) and ``rejected`` count every signal of
    ``version``. ``last_sweep`` describes the window, None before the first run."""
    with store.session() as s:
        w = _window(s, since, launched)
        und_row = (select(TestRow.signal_id, func.max(TestRow.id).label("tid"))
                   .where(TestRow.status == "undetermined").group_by(TestRow.signal_id).subquery())
        und = (select(TestRow.reason).select_from(SignalRow)
               .outerjoin(und_row, und_row.c.signal_id == SignalRow.id).outerjoin(TestRow, TestRow.id == und_row.c.tid)
               .where(SignalRow.final_status == "undetermined"))
        if version is not None:
            und = und.join(RunRow, RunRow.id == SignalRow.run_id).where(RunRow.detector_version == version)
        groups = Counter(explain.reason_in_words(reason or "")[1] for reason in s.scalars(und))
        # the counts agree with the Candidates chips: signals as "all" (a pending one is still being vetted), waiting
        # as "waiting", and rejected as the engine's own rejections (a reviewer's reject is a decision, not a failure)
        out = {"scanned": w["scanned"], "signals": sum(w["found"].get(k, 0) for k in ENGINE_STATUSES),
               "candidates_waiting": _count(s, "waiting", version), "undetermined": dict(groups.most_common()),
               "rejected": _engine_rejected(s, version)}
    start = w["start"]
    today = since is not None or (start is not None and start.date() == datetime.now(UTC).date())   # a running sweep is tonight's
    out.update(today=today, label="Tonight" if today or start is None else f"Last run ({start.day} {start:%B %Y})",
               date=start.date() if start else None, last_sweep=_last_sweep(w))
    return out


EXTERNAL_FRESH = timedelta(minutes=3)      # a done run this recent means a sweep is still writing results
RUNNING_STALE = timedelta(hours=1)         # a run marked running for longer than this was interrupted, not live


def external_sweep(store: Store, after: datetime | None = None) -> dict | None:
    """A sweep the dashboard did not start (``skysift run`` on the command line), seen in the store: a run marked
    running that started in the last hour, or a done run that finished in the last three minutes. ``after`` (when the
    dashboard's own last sweep ended) discounts that sweep's rows. Its stars done in the last hour, or None. A run the
    recheck started is no sweep and is left out."""
    now = datetime.now(UTC).replace(tzinfo=None)
    floor = _naive_utc(after) if after is not None else None
    since_running, since_fresh = now - RUNNING_STALE, now - EXTERNAL_FRESH
    with store.session() as s:
        running = s.scalar(select(func.count()).select_from(RunRow).where(
            RunRow.status == "running", SWEEP_RUN, RunRow.started >= max(since_running, floor or since_running)))
        fresh = s.scalar(select(func.max(RunRow.finished)).where(RunRow.status == "done", SWEEP_RUN))
        if not running and (fresh is None or fresh < max(since_fresh, floor or since_fresh)):
            return None
        done = s.scalar(select(func.count(func.distinct(RunRow.target_id)))
                        .where(RunRow.status == "done", SWEEP_RUN, RunRow.finished >= now - timedelta(hours=1)))
    return {"running": running, "done_last_hour": done}


def searched(store: Store) -> tuple[int, int]:
    """(targets searched, targets listed) over every source, for the idle ring."""
    with store.session() as s:
        states = dict(s.execute(select(TargetRow.state, func.count()).group_by(TargetRow.state)).all())
    return states.get("done", 0), sum(states.values())


# a finished target's outcome in the ticker: (word, pill tone), the best verdict among its signals first
OUTCOMES = (("candidate", "pass"), ("undetermined", "undetermined"), ("rejected", "fail"))


def recent_runs(store: Store, limit: int = 10) -> list[dict]:
    """The last ``limit`` finished targets, newest first, each with its outcome in one word: candidate, undetermined,
    rejected, no signal, or error. A run superseded by a later one is not an outcome and is left out, and so is a run
    the recheck started (no target was swept)."""
    with store.session() as s:
        runs = s.execute(select(RunRow.id, RunRow.target_id, RunRow.finished, RunRow.status)
                         .where(RunRow.finished.is_not(None), RunRow.status.in_(("done", "error")),
                                RunRow.error != "superseded", SWEEP_RUN)
                         .order_by(RunRow.finished.desc(), RunRow.id.desc()).limit(limit)).all()
        statuses: dict[int, set] = {}
        if runs:
            for run_id, status in s.execute(select(SignalRow.run_id, SignalRow.final_status)
                                            .where(SignalRow.run_id.in_([r.id for r in runs])).distinct()):
                statuses.setdefault(run_id, set()).add(status)
    out = []
    for r in runs:
        found = statuses.get(r.id, set())
        if r.status == "error":
            word, tone = "error", "fail"
        else:
            kinds = {"candidate" if f in CANDIDATE_STATUSES else f for f in found}
            word, tone = next(((w, t) for w, t in OUTCOMES if w in kinds),
                              ("no signal", "muted") if not found else ("not vetted", "muted"))
        out.append({"run_id": r.id, "target_id": r.target_id, "finished": r.finished, "outcome": word, "tone": tone})
    return out


def neighbours(store: Store, signal_id: int, status: str, sort: str, desc: bool,
               version: str | None = None, search: str = "") -> tuple[int | None, int | None]:
    with store.session() as s:
        ids = list(s.scalars(_queue_select(status, sort, desc, ids_only=True, version=version, search=search)))
    if signal_id not in ids:
        return None, None
    i = ids.index(signal_id)
    return (ids[i - 1] if i > 0 else None), (ids[i + 1] if i + 1 < len(ids) else None)


def signal_detail(store: Store, signal_id: int) -> dict | None:
    """The signal with its target, run, every test row (in id order, for the plots), the latest row of each rung
    (``latest_tests``, what the verdict and the Why rows read), the candidate row and decisions, its recheck history
    (``rechecks``, oldest first), the signals a recheck derived from it (``children``) and the one it was derived
    from (``parent``: id, period, final status and branch), or None."""
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
        up = s.get(SignalRow, sig.parent_signal_id) if sig.parent_signal_id is not None else None
        parent = ({"id": up.id, "period": up.period, "final_status": up.final_status, "branch": up.branch}
                  if up is not None else None)
        all_tests = [{"rung": x.rung, "status": x.status, "reason": x.reason, "duration_s": x.duration_s,
                      "metrics": x.metrics} for x in tests]
    latest = [{**t, "created": _iso(t["created"])} for t in store.latest_tests(signal_id)]   # strict JSON for review
    return {"signal": signal, "target": target, "run": run_d, "tests": all_tests, "latest_tests": latest,
            "candidate": cand_d, "decisions": store.decisions(signal_id), "rechecks": store.rechecks(signal_id),
            "children": store.children(signal_id), "parent": parent}


def _decided_select(cols, status: str, version: str | None):
    latest = _latest_decision_subquery()
    q = (select(*cols(latest)).select_from(SignalRow)
         .join(CandidateRow, CandidateRow.signal_id == SignalRow.id)
         .join(TargetRow, TargetRow.target_id == SignalRow.target_id)
         .outerjoin(latest, latest.c.signal_id == SignalRow.id)
         .where(CandidateRow.status == status))
    if version is not None:
        q = q.join(RunRow, RunRow.id == SignalRow.run_id).where(RunRow.detector_version == version)
    return q


def approved(store: Store, status: str = "approved", version: str | None = None) -> list[dict]:
    """The signals whose candidate row has ``status``, newest decision first, as explained cards with the note; with
    ``version``, only signals found by runs of that detector version (as the Candidates chips count them)."""
    q = _decided_select(lambda latest: (SignalRow, TargetRow, CandidateRow, latest.c.decision), status, version)
    with store.session() as s:
        rows = s.execute(q.order_by(CandidateRow.decided_at.desc())).all()
        tests = _tests_by_signal(s, [sig.id for sig, *_rest in rows])
        return [_explained({**_queue_row(sig, t, c.status, None, None), "note": c.note,
                            "decided_at": _iso(c.decided_at), "doi": c.doi}, tests[sig.id], dec)
                for sig, t, c, dec in rows]


def decided_hidden_by_version(store: Store, status: str, version: str | None) -> int:
    """How many signals of this decision status a filter on ``version`` hides on the Approved page."""
    if version is None:
        return 0
    with store.session() as s:
        n = [s.scalar(_decided_select(lambda _latest: (func.count(),), status, v)) for v in (None, version)]
    return n[0] - n[1]


def decision_counts(store: Store) -> dict[str, int]:
    """How many signals a person has decided, by their latest decision (approve, reject, needs_data)."""
    latest = _latest_decision_subquery()
    with store.session() as s:
        return dict(s.execute(select(latest.c.decision, func.count()).group_by(latest.c.decision)).all())


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


# ---- the Dossiers page (dossiers spec 5.5) ------------------------------------------------------------------------

FINAL_GROUPS = ("approved", "stage2", "stage1", "pixels")      # "pixels": the pixel stage could not decide
_JSON_CACHE: dict[str, tuple[int, dict | None, str | None]] = {}      # path -> (mtime_ns, data, error)
_JSON_CACHE_MAX = 64


def dossier_json(path: str) -> tuple[dict | None, str | None]:
    """(the parsed ``dossier_data.json``, None) or (None, why it could not be read), cached by modification time so
    a page of tiles does not parse every file on every request."""
    p = Path(path) if path else None
    try:
        mtime = p.stat().st_mtime_ns if p is not None else None
    except OSError:
        mtime = None
    if p is None or mtime is None:
        return None, "the dossier's data file is missing"
    hit = _JSON_CACHE.get(str(p))
    if hit is not None and hit[0] == mtime:
        return hit[1], hit[2]
    try:
        data, error = json.loads(p.read_text(encoding="utf-8")), None
        if not isinstance(data, dict):
            data, error = None, "the dossier's data file does not hold a dossier"
    except (OSError, ValueError) as e:
        data, error = None, f"the dossier's data file could not be read ({type(e).__name__})"
    if len(_JSON_CACHE) >= _JSON_CACHE_MAX:
        _JSON_CACHE.clear()
    _JSON_CACHE[str(p)] = (mtime, data, error)
    return data, error


def _group(final_status: str, decision: str | None, candidate_status: str | None) -> str:
    if decision == "approve" or candidate_status in ("approved", "published"):
        return "approved"
    if final_status == "undetermined":
        return "pixels"
    return "stage2" if final_status == "candidate_stage2" else "stage1"


def pixels_undecided(tests: list[dict]) -> bool:
    """An undetermined signal belongs on the Dossiers page when every light-curve rung passed and only the pixel
    stage could not decide: among the latest row of each rung, no failure, at least one undetermined row, and every
    undetermined row is rung 5's (its reason starts with "pixels:": one voting season, an ambiguous season, no
    pixel files). A signal undetermined at rung 2, 3 or 4, or whose pixel step never ran, stays off the page."""
    latest: dict[str, dict] = {}
    for t in tests:                        # ordered by id, so the last row of a rung wins
        latest[t["rung"]] = t
    rows = list(latest.values())
    if any(t["status"] == "fail" for t in rows):
        return False
    open_rows = [t for t in rows if t["status"] == "undetermined"]
    return bool(open_rows) and all(t["rung"] == "rung5_pixels" and str(t.get("reason") or "").startswith("pixels:")
                                   for t in open_rows)


def stale_reason(store: Store, row: dict, best_signal_id: int | None = None) -> str | None:
    """Why the dossier ``row`` no longer describes its target (spec 5.4), or None when it is current: its data file
    is gone, the dossier format changed, another signal is now the target's best, or the fingerprint of the signal's
    latest test rows, status and decision differs from the one it was built from."""
    from skysift import dossier as ds
    if not row.get("data_path") or not Path(row["data_path"]).is_file():
        return "its data file is missing"
    if row.get("dossier_version") != ds.DOSSIER_VERSION:
        return "the dossier format changed"
    if best_signal_id is None:
        try:
            best_signal_id = ds.pick_signal(store, row["target_id"])["id"]
        except ds.NoSignal:
            best_signal_id = row["signal_id"]
    if best_signal_id != row["signal_id"]:
        return "another signal is now the target's best"
    sid = row["signal_id"]
    with store.session() as s:
        sig = s.get(SignalRow, sid)
        status = sig.final_status if sig is not None else None
        search = {"detector": sig.detector, "detector_version": sig.detector_version} if sig is not None else {}
    decisions = store.decisions(sid)
    source = ds._target_dict(store, row["target_id"]).get("source")
    fp = ds.fingerprint(store.latest_tests(sid), status, decisions[0]["decision"] if decisions else None,
                        ds.calibration_rows(ds.data_dir_of(row["data_path"]), source),
                        ds.sweep_size_of(store, search, source))
    if fp == row.get("tests_fingerprint"):
        return None
    if status != row.get("status_at_generation"):
        return "the signal's status changed"
    return "the checks, the reviewer's decision, the null calibration or the sweep size changed"


def _tile_facts(data: dict | None) -> dict:
    """What a tile prints from the dossier's JSON: the look block, the star line and the summary."""
    if not data:
        return {"look": None, "star_line": None, "summary": None}
    star = data.get("star") or {}
    dist = star.get("distance_pc")
    dist = dist.get("median") if isinstance(dist, dict) else None
    parts = [star.get("spectral_type"), f"{dist:,.0f} pc" if isinstance(dist, (int, float)) else None]
    return {"look": data.get("look"), "star_line": " · ".join(p for p in parts if p) or None,
            "summary": data.get("summary")}


def final_set(store: Store, version: str | None, target_id: str | None = None) -> list[dict]:
    """One row per target for the Dossiers page: every ``candidate_stage2`` and ``candidate_stage1`` signal of
    ``version`` (None: every version), every ``undetermined`` signal of ``version`` that only the pixel stage could
    not decide (``pixels_undecided``), every signal of ``version`` whose latest decision is an approval, and every
    target with a dossier row whatever its version. A target shows its best signal by ``dossier.rank_key``, the ranking ``pick_signal`` builds
    the dossier from; a target with a dossier ranks every signal it has, of any version, so its tile and its dossier
    always name the same signal. ``group`` only orders the tiles. Rows carry ``target_id``,
    ``signal_id``, ``final_status``, ``decision``, ``dossier`` (the row or None), ``stale`` and ``stale_reason``,
    ``group`` ("approved", "stage2", "stage1", "pixels"), ``fpp`` and ``fap``, plus what a tile prints; sorted
    approved, stage 2, stage 1, pixels undecided, then FPP ascending with None last. With ``target_id``, the one row of that target (any of its
    signals qualifies, so a stage-1 candidate's tile can be drawn while its first dossier is generated)."""
    from skysift import dossier as ds
    latest = _latest_decision_subquery()
    base = (select(SignalRow, TargetRow, latest.c.decision, CandidateRow.status).select_from(SignalRow)
            .join(TargetRow, TargetRow.target_id == SignalRow.target_id)
            .outerjoin(CandidateRow, CandidateRow.signal_id == SignalRow.id)
            .outerjoin(latest, latest.c.signal_id == SignalRow.id))
    if target_id is not None:
        base = base.where(SignalRow.target_id == target_id)
        q = base
    else:
        q = base.where(SignalRow.final_status.in_(("candidate_stage2", "candidate_stage1", "undetermined"))
                       | (latest.c.decision == "approve"))
    if version is not None:
        q = q.join(RunRow, RunRow.id == SignalRow.run_id).where(RunRow.detector_version == version)
    dossiers = {d["target_id"]: d for d in store.dossiers() if target_id is None or d["target_id"] == target_id}
    with store.session() as s:
        found = list(s.execute(q))
        if dossiers:
            found += list(s.execute(base.where(SignalRow.target_id.in_(list(dossiers)))))
        rows, have = [], set()
        for sig, t, decision, cand in found:
            if sig.id in have:
                continue
            have.add(sig.id)
            target = {c.name: getattr(t, c.name) for c in TargetRow.__table__.columns}
            signal = {c.name: getattr(sig, c.name) for c in SignalRow.__table__.columns}
            rows.append({"target_id": sig.target_id, "signal_id": sig.id, "final_status": sig.final_status,
                         "decision": decision, "candidate_status": cand, "sde": sig.sde, "signal": signal,
                         "target": {**target, "name": ds.target_name(target)},
                         "group": _group(sig.final_status, decision, cand)})
        tests = _tests_by_signal(s, [r["signal_id"] for r in rows])
    # an undetermined signal qualifies on its own only when the pixel stage alone could not decide; one of a target
    # already on the page (a dossier, or a qualifying sibling) is kept so the tile and the dossier rank the same set
    qualifying = {r["target_id"] for r in rows
                  if r["group"] != "pixels" or pixels_undecided(tests[r["signal_id"]])} | set(dossiers)
    rows = [r for r in rows if r["target_id"] in qualifying]
    by_target: dict[str, list[dict]] = {}
    for r in rows:
        by_target.setdefault(r["target_id"], []).append(r)
    out = []
    for tid, found_rows in by_target.items():
        d = dossiers.get(tid)
        row = min(found_rows, key=lambda r: ds.rank_key({**r["signal"], "candidate_status": r["candidate_status"]},
                                                        r["decision"]))
        data, error = dossier_json(d["data_path"]) if d else (None, None)
        reason = stale_reason(store, d, row["signal_id"]) if d else None
        facts = _tile_facts(data)
        cand = {"status": row["candidate_status"]} if row["candidate_status"] else None
        t = tests[row["signal_id"]]
        v = explain.verdict(row["signal"], t, cand, [{"decision": row["decision"]}] if row["decision"] else None)
        out.append({**row, "dossier": d, "stale": reason is not None, "stale_reason": reason,
                    "fpp": d["fpp"] if d else None, "fap": d["fap"] if d else None,
                    "fap_detail": (d or {}).get("fap_detail") or {}, "fpp_detail": (d or {}).get("fpp_detail") or {},
                    "data_error": error, "v": v, "look": facts["look"], "star_line": facts["star_line"],
                    "summary": facts["summary"] or explain.summary_sentence(row["signal"], row["target"], t)})
    return sorted(out, key=lambda r: (FINAL_GROUPS.index(r["group"]), r["fpp"] is None,
                                      r["fpp"] if r["fpp"] is not None else 0.0, r["target_id"]))


def dossier_for(store: Store, target_id: str, with_data: bool = True) -> dict | None:
    """The target's dossier row with ``stale`` and ``stale_reason``, its signal's ``detail`` (as ``signal_detail``),
    ``signal`` and latest ``decision``, and with ``with_data`` the parsed JSON as ``data`` (None, with
    ``data_error``, when it cannot be read), its ``cross_check`` the row's block with ``current`` recomputed.
    None when the target has no dossier."""
    row = store.dossier(target_id)
    if row is None:
        return None
    detail = signal_detail(store, row["signal_id"])
    decisions = (detail or {}).get("decisions") or []
    data, error = dossier_json(row["data_path"]) if with_data else (None, None)
    if data is not None:                # a copy: the parsed JSON is cached and shared
        data = {**data, "cross_check": cc.with_current(row.get("cross_check"), data)}
    reason = stale_reason(store, row)
    return {**row, "row": row, "stale": reason is not None, "stale_reason": reason, "data": data,
            "data_error": error, "detail": detail, "signal": (detail or {}).get("signal"),
            "decision": decisions[0]["decision"] if decisions else None}
