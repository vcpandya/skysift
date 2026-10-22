"""Stage 2: the recheck of open signals (mission control spec 4.3). It takes every undetermined signal and every
stage-1 candidate of the current search version that no reviewer has decided on, and applies, in order:

1. the ladder re-vet: a signal vetted under an older ladder is vetted again under the current one;
2. the half-period re-vet: when rung 2 found an equal secondary at phase 0.5 (``suggested_period``), the signal is
   re-measured at P/2 as a new signal (``branch = "recheck:P/2"``, ``parent_signal_id``) on a flux restored at the
   half period's own harmonics, and vetted; the original is superseded unless the new one is undetermined for the
   same reason;
3. the catalogue retry: when rung 4 could not reach a catalogue (``outage:``), rung 4 alone runs again;
4. the pixel stage (``step_pixels``): ``rung5_pixels`` alone on the Kepler target pixel files, for a stage-1
   candidate or an undetermined signal whose reason only the pixels can settle; a pass promotes to stage 2, a dip on
   the neighbouring star rejects. Once per signal: a rung 5 row settles it, except an archive outage (retried), a
   first crash (retried once), and a row that localised under an older pixel rule (``floor_reopens``), which runs
   again whatever its status: those signals join the queue even from stage 2 or rejected, for this step alone.

A step computes everything first and touches the store not at all: it returns a ``StepWrite`` (its ``rechecks`` row
with the outcome, "skipped" and why when it could not run, the test rows that point back to it, and the P/2 signal
with its run) or None when it does not apply. ``Store.commit_recheck_step`` writes that in one transaction and derives
the statuses again, so the dashboard shows rechecks in the same ladder and a failure never leaves half a step.

The work list starts as the queue and grows: a P/2 signal the period step makes joins it right after its parent, so
it gets its catalogue and pixel steps in the same run. Each signal is rechecked under its own search window
(``extra["search_window"]``, the sweep's ``--min-period/--max-period``). Follow-up: a long recheck after a sweep shows
no progress on the dashboard's Live page (only the log lines); feeding ``say`` to the sweep's progress callback is
left for later."""
from __future__ import annotations

import logging
import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import func, select

from skysift import net
from skysift.config import Settings
from skysift.detectors.base import get_detector
from skysift.lc.loader import TARGET_FIELDS, LightCurveLoader
from skysift.lc.tpf import fetch_tpfs
from skysift.models import LightCurve, Signal, Target, TestResult, TestStatus
from skysift.pipeline import (
    DEFAULT_LADDER_VERSION,
    SweepFlux,
    _sweep_flux,
    build_ladder,
    halved_signal,
    restore_from,
    revet,
    revet_flux,
    search_version,
)
from skysift.store.db import (
    CHILD_EXISTS_REASON,
    PIXEL_SETTLED_PREFIXES,
    RECHECK_JOB_PREFIX,
    REVIEWER_DECIDED_REASON,
    ChildWrite,
    DecisionRow,
    RunRow,
    SignalRow,
    StepWrite,
    Store,
    TargetRow,
    TestRow,
    final_status_for,
    status_from_tests,
)
from skysift.vetting.base import Ladder
from skysift.vetting.rung4_novelty import Rung4Novelty
from skysift.vetting.rung5_pixels import PIXEL_RULE_VERSION, Rung5Pixels

logger = logging.getLogger(__name__)

# undetermined reasons only the pixels can settle (spec 4.2): the pixel stage takes these and every stage-1 candidate.
# One tuple with the status rule: a pixel pass on a signal undetermined only for these reasons promotes it to stage 2
# (spec 4.3; ``store.db.pixels_settle``, used by ``status_from_tests``)
PIXEL_REASON_PREFIXES = PIXEL_SETTLED_PREFIXES
OPEN_STATUSES = ("undetermined", "candidate_stage1")         # what the queue holds; a step that settles it ends it
SETTLED_BY_PIXELS = ("candidate_stage2", "rejected")         # where a pixel row below the rule version can stand
PERIOD_BRANCH = "recheck:P/2"
HALVING_REASON = "equal secondary at phase 0.5"              # rung 2's period-halving reason starts with this


@dataclass
class RecheckReport:
    considered: int = 0
    steps: dict[str, int] = field(default_factory=lambda: {"ladder": 0, "period": 0, "catalogue": 0, "pixels": 0})
    outcomes: dict[str, int] = field(default_factory=lambda: {"candidate_stage1": 0, "candidate_stage2": 0,
                                                              "undetermined": 0, "rejected": 0, "superseded": 0,
                                                              "skipped": 0})
    seconds: float = 0.0
    stopped: bool = False           # a stop request ended the recheck before its queue did
    decided_pixel_changed: int = 0  # decided signals whose pixel rerun disagrees with the decision

    def as_dict(self) -> dict:
        return {"considered": self.considered, "steps": dict(self.steps), "outcomes": dict(self.outcomes),
                "seconds": self.seconds, "stopped": self.stopped, "decided_pixel_changed": self.decided_pixel_changed}


@dataclass
class StoredSignal:
    """A queued signal as the steps see it: its row id, the model the rungs take, and the columns they decide on."""
    id: int
    signal: Signal
    ladder_version: str | None
    branch: str
    parent_signal_id: int | None


@dataclass
class RecheckContext:
    """What every step of one signal's recheck shares: the store (read only inside a step), the light curve and
    target, the fetch for the catalogues, and the whitened flux, built once (``base``) for all the steps."""
    store: Store
    blob: object
    settings: Settings
    fetch: Callable
    lc: LightCurve
    target: Target
    with_novelty: bool = True
    tpf_loader: Callable | None = None
    say: Callable[[str], None] = lambda text: None
    _base: SweepFlux | None = None
    _flux: tuple | None = None

    def base(self) -> SweepFlux:
        """The detrended and pre-whitened fluxes of the light curve before any restore (``_sweep_flux``)."""
        if self._base is None:
            self._base = _sweep_flux(self.lc, self.settings.thresholds)
        return self._base

    def flux(self, sig: StoredSignal) -> tuple:
        """``revet_flux`` of the signal (its recorded restore replayed): (primary, secondary, detrended)."""
        if self._flux is None:
            self._flux = revet_flux(self.lc, self.settings.thresholds, sig.signal, base=self.base())
        return self._flux

    def vet(self, sig: Signal, ladder: Ladder, flux: tuple) -> tuple[str, list[TestResult], list[float]]:
        """``revet`` through ``ladder`` on ``flux``, catalogue queries logged under the target: (outcome, results,
        seconds per rung)."""
        timings: list[float] = []
        outcome, results = revet(self.lc, self.target, self.settings.thresholds, sig, ladder, fetch=self.fetch,
                                 store=self.store, on_result=lambda r, dt: timings.append(dt), flux=flux)
        return outcome, results, timings


def latest_search_version(store: Store) -> str | None:
    """The search version of the newest finished sweep run (recheck runs left out), or None before any."""
    with store.session() as s:
        return s.scalar(select(RunRow.detector_version)
                        .where(RunRow.status == "done", RunRow.job_key.not_like(f"{RECHECK_JOB_PREFIX}%"))
                        .order_by(RunRow.id.desc()).limit(1))


def pixels_qualify(status: str, latest_tests: list[dict]) -> bool:
    """Does step 4 take this signal: a stage-1 candidate, or an undetermined signal whose latest undetermined row
    gives a reason only the pixels can settle (``PIXEL_REASON_PREFIXES``)."""
    if status == "candidate_stage1":
        return True
    return status == "undetermined" and any(
        t["status"] == "undetermined" and str(t["reason"]).startswith(PIXEL_REASON_PREFIXES) for t in latest_tests)


def _latest(tests: list[dict], rung: str) -> dict | None:
    return next((t for t in tests if t["rung"] == rung), None)


def _crashes(store: Store, signal_id: int, rung: str) -> int:
    """How many of the signal's rows of ``rung`` record a crash (reason "exception: ...")."""
    with store.session() as s:
        return s.scalar(select(func.count()).select_from(TestRow).where(
            TestRow.signal_id == signal_id, TestRow.rung == rung, TestRow.reason.like("exception:%")))


REOPEN_CRASHES_MAX = 2      # crash skips in a row after which a reopened settled signal stays for a person


def _reopen_tag(version: int | None = None) -> str:
    return f"on reopen at pixel rule {PIXEL_RULE_VERSION if version is None else version})"


def _reopen_crashes(store: Store, signal_id: int, version: int | None = None) -> int:
    """How many of the signal's latest pixel-step recheck rows, in a row, are crash skips (outcome "skipped", reason
    "exception: ...") of a reopen at pixel rule ``version``: the reopen of a settled signal writes no rung 5 row when
    it crashes, so these count its attempts, and the cap applies per rule version (a later rule reopens it again)."""
    n = 0
    for row in reversed([r for r in store.rechecks(signal_id) if r["step"] == "pixels"]):
        reason = str(row["reason"] or "")
        if row["outcome"] != "skipped" or not reason.startswith("exception:") or not reason.endswith(
                _reopen_tag(version)):
            break
        n += 1
    return n


def _reopen_crash_reason(store: Store, signal_id: int, reason: str) -> str:
    """A crash skip's reason on a reopened settled signal, numbered and naming the rule version it attempted
    ("exception: X (crash 1 of 2 on reopen at pixel rule 4)") so that the second is counted (``_reopen_crashes``)."""
    return f"{reason} (crash {_reopen_crashes(store, signal_id) + 1} of {REOPEN_CRASHES_MAX} {_reopen_tag()}"


def floor_reopens(rung5: dict) -> bool:
    """The rung 5 row localised (its metrics hold the per-season ``seasons``) under a rule version below
    ``PIXEL_RULE_VERSION`` (1 when none is stored), whatever its status: a pass, a fail or an undetermined row. A
    new rule changes every season's error (2026-10-22 referee pack, section 5.2: the measured floor; before it, the
    partial-box rule), so such a row is rerun once, and the rerun writes the current version, so it is not reopened
    again. A row that never localised (no neighbour, no files, an outage, a crash) has no seasons the rule could
    change and keeps its own retry rules."""
    metrics = rung5.get("metrics")
    return (isinstance(metrics, dict) and isinstance(metrics.get("seasons"), list)
            and int(metrics.get("rule_version") or 1) < PIXEL_RULE_VERSION)


def wants_pixels(store: Store, sig: StoredSignal, tests: list[dict], status: str | None = None) -> bool:
    """Step 4 applies: the signal is open, or its latest rung 5 row localised under an older rule
    (``floor_reopens``: a stage 2 candidate or a signal the pixels rejected comes back for this step), it qualifies
    (``pixels_qualify``) on its rows other than rung 5's, and its pixels are not settled. A rung 5 row settles them
    unless it could not reach the pixel archive (``outage:``, retried at every recheck), it is the signal's first
    crash (``exception:``, retried once, spec 4.2; after a second crash it stays for a person) or it was made under an
    older rule (``floor_reopens``, rerun once under the current one; a reopened settled signal whose rerun crashed
    ``REOPEN_CRASHES_MAX`` times in a row stays for a person, as the ordinary path does after a second crash); a
    verdict, or a reason the pixels gave themselves ("pixels: ..."), is otherwise final for the signal, so a signal
    that stays in the queue is not fetched and localised at every recheck."""
    status = store.signal_status(sig.id) if status is None else status
    rung5 = _latest(tests, "rung5_pixels")
    reopened = rung5 is not None and floor_reopens(rung5)
    if status not in OPEN_STATUSES and not (reopened and status in SETTLED_BY_PIXELS):
        return False
    if reopened and status in SETTLED_BY_PIXELS and _reopen_crashes(store, sig.id) >= REOPEN_CRASHES_MAX:
        return False
    if rung5 is not None and not reopened:
        reason = str(rung5["reason"])
        if reason.startswith("exception:"):
            if _crashes(store, sig.id, "rung5_pixels") > 1:
                return False
        elif not reason.startswith("outage:"):
            return False
    others = [t for t in tests if t["rung"] != "rung5_pixels"]
    return pixels_qualify(final_status_for(status_from_tests(others), others), others)


def reopened_queue(store: Store, detector_version: str) -> list[dict]:
    """Signals of this search version that no reviewer decided on, settled by their pixels (``SETTLED_BY_PIXELS``)
    under an older pixel rule (``floor_reopens`` on the latest rung 5 row), by id: they join the recheck for the
    pixel step alone (``wants_pixels``)."""
    latest = (select(func.max(TestRow.id).label("id")).where(TestRow.rung == "rung5_pixels")
              .group_by(TestRow.signal_id).subquery())
    with store.session() as s:
        rows = s.execute(select(SignalRow.id, SignalRow.target_id, TestRow.metrics)
                         .join(TestRow, TestRow.signal_id == SignalRow.id).join(latest, latest.c.id == TestRow.id)
                         .outerjoin(DecisionRow, DecisionRow.signal_id == SignalRow.id)
                         .where(SignalRow.final_status.in_(SETTLED_BY_PIXELS),
                                SignalRow.detector_version == detector_version, DecisionRow.id.is_(None))
                         .order_by(SignalRow.id)).all()
    return [{"id": i, "target_id": t} for i, t, metrics in rows if floor_reopens({"metrics": metrics})]


def decided_queue(store: Store, detector_version: str) -> list[dict]:
    """Signals of this search version that a reviewer decided on (any decision) whose latest rung 5 row localised
    under an older pixel rule (``floor_reopens``), by id: the recheck reruns their pixel step alone and keeps the
    decision (``decided_reopens``), so a rule bump never strands an approved candidate behind the stale-rule gate."""
    latest = (select(func.max(TestRow.id).label("id")).where(TestRow.rung == "rung5_pixels")
              .group_by(TestRow.signal_id).subquery())
    decided = select(DecisionRow.signal_id).distinct().subquery()
    with store.session() as s:
        rows = s.execute(select(SignalRow.id, SignalRow.target_id, TestRow.metrics)
                         .join(TestRow, TestRow.signal_id == SignalRow.id).join(latest, latest.c.id == TestRow.id)
                         .join(decided, decided.c.signal_id == SignalRow.id)
                         .where(SignalRow.detector_version == detector_version)
                         .order_by(SignalRow.id)).all()
    return [{"id": i, "target_id": t} for i, t, metrics in rows if floor_reopens({"metrics": metrics})]


def decided_reopens(store: Store, sig: StoredSignal, tests: list[dict]) -> bool:
    """The pixel step applies to a decided signal: its latest rung 5 row localised under an older pixel rule
    (``floor_reopens``) and its reruns under the current rule have not crashed ``REOPEN_CRASHES_MAX`` times in a row.
    Only the pixel step runs; the decision and the signal's status stay as they are."""
    rung5 = _latest(tests, "rung5_pixels")
    return (rung5 is not None and floor_reopens(rung5) and _decided(store, sig.id)
            and _reopen_crashes(store, sig.id) < REOPEN_CRASHES_MAX)


DISAGREEING = {"approve": ("fail", "undetermined")}     # a decision and the pixel verdicts that contradict it


def pixel_disagrees(decision: str | None, status: str | None) -> bool:
    """The rerun's pixel verdict contradicts the reviewer's decision (a fail or an undetermined row under an
    approval)."""
    return status in DISAGREEING.get(decision or "", ())


def _recheck_decided(store: Store, blob, settings: Settings, loader: LightCurveLoader, sig: StoredSignal,
                     target: dict | None, *, fetch, tpf_loader, say, report: RecheckReport, cache: dict) -> None:
    """The pixel step alone on a decided signal under an older pixel rule (``decided_reopens``): the new rung 5 row is
    stored beside the decision, which stays, as does ``final_status``. An outage or a crash writes a "skipped" row
    (crashes numbered per rule version, as on a reopened settled signal). A verdict that disagrees with the decision
    is counted in ``decided_pixel_changed``; the dossier then carries ``pixel_verdict_disagrees``."""
    if target is None:
        _commit(store, say, sig, _write(sig, "pixels", "skipped", f"no target row for {sig.signal.target_id}",
                                        decided=True))
        return
    settings = signal_settings(settings, sig)
    key = (sig.signal.target_id, settings.thresholds.model_dump_json())
    if cache.get("key") == key:
        lc = cache["lc"]
    else:
        try:
            lc = loader.load(target)
        except Exception as e:              # an outage, a changed archive file, a parser error: the signal waits
            logger.warning("recheck of decided signal %s: light curve unavailable", sig.id, exc_info=True)
            _commit(store, say, sig, _write(sig, "pixels", "skipped", f"light curve unavailable: {e}", decided=True))
            return
        cache.clear(); cache.update(key=key, lc=lc, base=None)
    ctx = RecheckContext(store, blob, settings, fetch, lc,
                         Target(**{k: target[k] for k in TARGET_FIELDS if target.get(k) is not None},
                                meta=target["meta"]),
                         with_novelty=False, tpf_loader=tpf_loader, say=say, _base=cache.get("base"))
    try:
        rung = Rung5Pixels(tpf_loader=ctx.tpf_loader or fetch_tpfs, blob=ctx.blob, signal_id=sig.id)
        _outcome, results, timings = ctx.vet(sig.signal, Ladder([rung], DEFAULT_LADDER_VERSION), ctx.flux(sig))
        r = results[0]
        if r.reason.startswith("outage:"):
            write = _write(sig, "pixels", "skipped", r.reason, decided=True)
        elif r.reason.startswith("exception:"):
            write = _write(sig, "pixels", "skipped", _reopen_crash_reason(store, sig.id, r.reason), decided=True)
        else:
            write = _write(sig, "pixels", r.status.value, r.reason, results, timings, decided=True)
    except Exception as e:                  # a broken step leaves the signal as it is for a person
        logger.exception("recheck step pixels failed for decided signal %s", sig.id)
        write = _write(sig, "pixels", "skipped", _reopen_crash_reason(store, sig.id, f"exception: {type(e).__name__}"),
                       decided=True)
    cache["base"] = ctx._base
    if _commit(store, say, sig, write).ran:
        report.steps["pixels"] += 1
        decisions = store.decisions(sig.id)
        if pixel_disagrees(decisions[0]["decision"] if decisions else None, write.outcome):
            report.decided_pixel_changed += 1
            say(f"recheck {sig.id} ({sig.signal.target_id}) pixels: {write.outcome} under the decision "
                f"{decisions[0]['decision']}; the dossier flags it")


def _deciding_reason(results: list[TestResult]) -> str:
    """The reason of the row that decided a ladder outcome: the failure, else the first undetermined row."""
    for status in (TestStatus.FAIL, TestStatus.UNDETERMINED):
        for r in results:
            if r.status == status:
                return f"{r.rung}: {r.reason}"
    return ""


def _write(sig: StoredSignal, step: str, outcome: str, reason: str = "", results=(), timings=(),
           child: ChildWrite | None = None, decided: bool = False) -> StepWrite:
    return StepWrite(signal_id=sig.id, step=step, ladder_version=DEFAULT_LADDER_VERSION, outcome=outcome,
                     reason=reason, tests=list(zip(results, timings, strict=True)), child=child,
                     decided_pixels=decided)


def _ladder(ctx: RecheckContext) -> Ladder:
    return build_ladder(DEFAULT_LADDER_VERSION, with_novelty=ctx.with_novelty)


# The triggers read the database only, so a signal no step applies to never has its light curve loaded.

def wants_ladder(store: Store, sig: StoredSignal, tests: list[dict], with_novelty: bool = True) -> bool:
    """Step 1 applies: vetted under an older ladder, and no ladder recheck at the current version ran yet."""
    return sig.ladder_version != DEFAULT_LADDER_VERSION and not any(
        r["step"] == "ladder" and r["ladder_version"] == DEFAULT_LADDER_VERSION and r["outcome"] != "skipped"
        for r in store.rechecks(sig.id))


def wants_period(store: Store, sig: StoredSignal, tests: list[dict], with_novelty: bool = True) -> bool:
    """Step 2 applies: rung 2 suggested a period, the signal is not itself a P/2 signal (the parent's period row
    already records its outcome) and it has no P/2 signal yet."""
    suggested = ((_latest(tests, "rung2_shape") or {}).get("metrics") or {}).get("suggested_period")
    if not (isinstance(suggested, int | float) and math.isfinite(suggested) and suggested > 0):
        return False
    if sig.branch == PERIOD_BRANCH or sig.signal.extra.get("halving"):
        return False
    return not any(c["branch"] == PERIOD_BRANCH for c in store.children(sig.id))


def wants_catalogue(store: Store, sig: StoredSignal, tests: list[dict], with_novelty: bool = True) -> bool:
    """Step 3 applies: rung 4 recorded a catalogue outage, and catalogues are queried in this run at all."""
    rung4 = _latest(tests, "rung4_novelty")
    return with_novelty and rung4 is not None and str(rung4["reason"]).startswith("outage:")


def step_ladder(ctx: RecheckContext, sig: StoredSignal) -> StepWrite | None:
    """Step 1: a signal vetted under an older ladder is vetted again under the current one (the stacked-fold tests
    settle the "too few events" class). Once per signal: a ladder recheck at the current version that ran is final."""
    if not wants_ladder(ctx.store, sig, ctx.store.latest_tests(sig.id), ctx.with_novelty):
        return None
    outcome, results, timings = ctx.vet(sig.signal, _ladder(ctx), ctx.flux(sig))
    return _write(sig, "ladder", outcome, _deciding_reason(results), results, timings)


def step_period(ctx: RecheckContext, sig: StoredSignal) -> StepWrite | None:
    """Step 2: rung 2 suggested P/2. The signal is re-measured at half its period (``halved_signal``) on the
    whitened flux restored at the half period's own integer harmonics (``restore_from``, the sweep's selector, with
    the child's frequency and the parent's depth; the parent's recorded restore would put back its odd harmonics,
    half-integer harmonics of the child that fail rung 2 as odd_even), vetted under the current ladder and returned
    as a new signal in its own run (job key ``recheck:P/2:<parent id>``). The parent is superseded unless the new
    signal is undetermined for the same reason (an equal secondary at phase 0.5 again)."""
    if not wants_period(ctx.store, sig, ctx.store.latest_tests(sig.id), ctx.with_novelty):
        return None
    parent = sig.signal
    restore = [(2.0 / parent.period, float(parent.depth_ppm))]
    base = ctx.base()
    fn, alt, record = restore_from(ctx.lc.time, base, restore)
    child = halved_signal(parent, ctx.lc, fn)
    if child is None:
        return _write(sig, "period", "skipped", "half period not measurable")
    child = child.model_copy(update={"extra": {**child.extra, "prewhitening": record}})
    outcome, results, timings = ctx.vet(child, _ladder(ctx), (fn, alt, base.detrended))
    same = outcome == "undetermined" and any(r.status == TestStatus.UNDETERMINED and r.reason.startswith(HALVING_REASON)
                                             for r in results)
    child_status = final_status_for(outcome, [r.model_dump() for r in results])
    if same:
        rec_outcome, reason = "undetermined", _deciding_reason(results)
    else:
        rec_outcome, reason = "superseded", f"re-measured at {child.period:.6g} d: {child_status}"
    derived = ChildWrite(job_key=f"{RECHECK_JOB_PREFIX}P/2:{sig.id}", signal=child,
                         ladder_version=DEFAULT_LADDER_VERSION, branch=PERIOD_BRANCH,
                         tests=list(zip(results, timings, strict=True)))
    return _write(sig, "period", rec_outcome, reason, child=derived)


def step_catalogue(ctx: RecheckContext, sig: StoredSignal) -> StepWrite | None:
    """Step 3: rung 4 recorded a catalogue outage, so rung 4 alone runs again and its row is added."""
    if not wants_catalogue(ctx.store, sig, ctx.store.latest_tests(sig.id), ctx.with_novelty):
        return None
    _outcome, results, timings = ctx.vet(sig.signal, Ladder([Rung4Novelty()], DEFAULT_LADDER_VERSION), ctx.flux(sig))
    r = results[0]
    return _write(sig, "catalogue", r.status.value, r.reason, results, timings)


def step_pixels(ctx: RecheckContext, sig: StoredSignal) -> StepWrite | None:
    """Step 4, the pixel stage (``wants_pixels``): ``rung5_pixels`` alone, its pixel files through ``ctx.tpf_loader``
    (``fetch_tpfs`` by default, kept in the blob), in the bundle ``revet`` builds; it stores the season arrays as the
    signal's ``pixels.npz``. The fetch and the localization happen here, before the step's one write: the rung 5 row
    pointing at the rechecks row (outcome the rung's status, reason its reason); the status is derived again. A
    signal its pixels settled (``SETTLED_BY_PIXELS``, reopened by ``floor_reopens``) keeps its verdict when the rerun
    measures nothing (an archive outage or a crash): the step is "skipped" with the rung's reason and writes no rung 5
    row, so the old row stands and the next recheck tries again."""
    tests = ctx.store.latest_tests(sig.id)
    if not wants_pixels(ctx.store, sig, tests):
        return None
    settled = ctx.store.signal_status(sig.id) in SETTLED_BY_PIXELS
    rung = Rung5Pixels(tpf_loader=ctx.tpf_loader or fetch_tpfs, blob=ctx.blob, signal_id=sig.id)
    _outcome, results, timings = ctx.vet(sig.signal, Ladder([rung], DEFAULT_LADDER_VERSION), ctx.flux(sig))
    r = results[0]
    if settled and r.reason.startswith("outage:"):
        return _write(sig, "pixels", "skipped", r.reason)
    if settled and r.reason.startswith("exception:"):
        return _write(sig, "pixels", "skipped", _reopen_crash_reason(ctx.store, sig.id, r.reason))
    return _write(sig, "pixels", r.status.value, r.reason, results, timings)


STEPS: tuple[tuple[str, Callable[[RecheckContext, StoredSignal], StepWrite | None],
                   Callable[..., bool]], ...] = (
    ("ladder", step_ladder, wants_ladder), ("period", step_period, wants_period),
    ("catalogue", step_catalogue, wants_catalogue))


@dataclass
class Committed:
    """What one commit did: whether the step ran (wrote a row other than a skip) and the P/2 signal it made."""
    ran: bool
    child_id: int | None = None


def _commit(store: Store, say, sig: StoredSignal, write: StepWrite) -> Committed:
    """Write a step through ``Store.commit_recheck_step``; a skip identical to the step's last row is not written
    again, so a signal that stays stuck does not add a row at every sweep."""
    if write.outcome == "skipped":
        rows = [r for r in store.rechecks(sig.id) if r["step"] == write.step]
        if rows and rows[-1]["outcome"] == "skipped" and rows[-1]["reason"] == write.reason:
            return Committed(False)
    done = store.commit_recheck_step(write)
    if not done["written"]:
        why = CHILD_EXISTS_REASON if write.child is not None and not _decided(store, sig.id) else REVIEWER_DECIDED_REASON
        say(f"recheck {sig.id} ({sig.signal.target_id}) {write.step}: skipped, {why}")
        return Committed(False)
    note = f", {write.reason}" if write.reason else ""
    child = f", signal {done['child_id']}" if done["child_id"] is not None else ""
    say(f"recheck {sig.id} ({sig.signal.target_id}) {write.step}: {write.outcome}{note}{child}")
    return Committed(write.outcome != "skipped", done["child_id"])


def _stored(store: Store, signal_id: int) -> tuple[StoredSignal, dict | None]:
    """The signal row as a ``StoredSignal`` and its target row as the loader's dict (None when it is missing)."""
    with store.session() as s:
        row = s.get(SignalRow, signal_id)
        sig = StoredSignal(id=row.id, signal=Signal(**{k: getattr(row, k) for k in Signal.model_fields}),
                           ladder_version=row.ladder_version, branch=row.branch, parent_signal_id=row.parent_signal_id)
        t = s.get(TargetRow, row.target_id)
        target = None if t is None else {**{k: getattr(t, k) for k in TARGET_FIELDS}, "meta": dict(t.meta or {})}
    return sig, target


def _decided(store: Store, signal_id: int) -> bool:
    with store.session() as s:
        return s.scalar(select(DecisionRow.id).where(DecisionRow.signal_id == signal_id).limit(1)) is not None


def signal_settings(settings: Settings, sig: StoredSignal) -> Settings:
    """``settings`` with the signal's own search window (``extra["search_window"]``, stored by the sweep that found
    it), so a standalone recheck after a ``--min-period/--max-period`` sweep compares and re-vets each signal under
    the search version it was found with; a signal without one (none should be) keeps ``settings`` and says so."""
    window = sig.signal.extra.get("search_window") or {}
    lo, hi = window.get("min_period_days"), window.get("max_period_days")
    if lo is None or hi is None:
        logger.info("recheck of signal %s: no stored search window; using the configured %g to %g d", sig.id,
                    settings.thresholds.min_period_days, settings.thresholds.max_period_days)
        return settings
    th = settings.thresholds
    if (float(lo), float(hi)) == (th.min_period_days, th.max_period_days):
        return settings
    th = th.model_copy(update={"min_period_days": float(lo), "max_period_days": float(hi)})
    return settings.model_copy(update={"thresholds": th})


@dataclass
class SignalDone:
    """One signal's recheck: its outcome for the report, the P/2 signals it made (they join the work list), and
    whether its pixel step settled (its pixel files can go)."""
    outcome: str
    children: list[int] = field(default_factory=list)
    pixels_settled: bool = False


def _recheck_one(store: Store, blob, settings: Settings, loader: LightCurveLoader, sig: StoredSignal,
                 target: dict | None, *, fetch, pixels: bool, with_novelty: bool, tpf_loader, say,
                 report: RecheckReport, cache: dict) -> SignalDone:
    """One signal through the steps. ``cache`` keeps the last target's light curve and whitened flux, so a P/2
    signal checked right after its parent loads and whitens nothing again."""
    settings = signal_settings(settings, sig)
    current = search_version(get_detector(sig.signal.detector), settings.thresholds)
    if sig.signal.detector_version != current:
        _commit(store, say, sig, _write(sig, "ladder", "skipped", "search version differs: signal of search version "
                                        f"{sig.signal.detector_version}, not the current {current}"))
        return SignalDone("skipped")
    tests, status = store.latest_tests(sig.id), store.signal_status(sig.id)
    if not (any(wants(store, sig, tests, with_novelty) for _name, _step, wants in STEPS)
            or (pixels and wants_pixels(store, sig, tests, status))):
        return SignalDone(status)           # nothing applies: the light curve is not loaded
    key = (sig.signal.target_id, settings.thresholds.model_dump_json())
    if cache.get("key") == key:
        lc = cache["lc"]
    else:
        try:
            if target is None:
                raise LookupError(f"no target row for {sig.signal.target_id}")
            lc = loader.load(target)
        except Exception as e:              # an outage, a changed archive file, a parser error: the signal waits
            logger.warning("recheck of signal %s: light curve unavailable", sig.id, exc_info=True)
            _commit(store, say, sig, _write(sig, "ladder", "skipped", f"light curve unavailable: {e}"))
            return SignalDone("skipped")
        cache.clear(); cache.update(key=key, lc=lc, base=None)
    ctx = RecheckContext(store, blob, settings, fetch, lc,
                         Target(**{k: target[k] for k in TARGET_FIELDS if target.get(k) is not None},
                                meta=target["meta"]),
                         with_novelty=with_novelty, tpf_loader=tpf_loader, say=say, _base=cache.get("base"))
    done = SignalDone(status)
    crashed = False
    for name, step, _wants in STEPS:
        if store.signal_status(sig.id) not in OPEN_STATUSES:
            break
        try:
            write = step(ctx, sig)
            if write is None:
                continue
            result = _commit(store, say, sig, write)
            if result.ran:
                report.steps[name] += 1
            if result.child_id is not None:
                done.children.append(result.child_id)
        except Exception as e:              # a broken step leaves the signal as it is for a person
            logger.exception("recheck step %s failed for signal %s", name, sig.id)
            _commit(store, say, sig, _write(sig, name, "skipped", f"exception: {type(e).__name__}"))
            crashed = True
            break
    # a signal a step closed is left alone; one the pixels settled under an older rule takes this step only
    if not crashed and pixels and wants_pixels(store, sig, store.latest_tests(sig.id)):
        try:
            write = step_pixels(ctx, sig)
            if write is not None and _commit(store, say, sig, write).ran:
                report.steps["pixels"] += 1
                done.pixels_settled = not write.reason.startswith(("outage:", "exception:"))
        except Exception as e:              # a broken step leaves the signal as it is for a person
            logger.exception("recheck step pixels failed for signal %s", sig.id)
            reason = f"exception: {type(e).__name__}"
            if store.signal_status(sig.id) in SETTLED_BY_PIXELS:
                reason = _reopen_crash_reason(store, sig.id, reason)
            _commit(store, say, sig, _write(sig, "pixels", "skipped", reason))
    cache["base"] = ctx._base
    done.outcome = store.signal_status(sig.id)
    return done


def recheck(store: Store, blob, settings: Settings, *, fetch=net.fetch, signal_ids: list[int] | None = None,
            pixels: bool = True, detector_version: str | None = None, log=None, with_novelty: bool = True,
            tpf_loader: Callable | None = fetch_tpfs, stop_requested: Callable[[], bool] | None = None,
            retention=None) -> RecheckReport:
    """Recheck the open signals of ``detector_version`` (default: the search version of the newest finished sweep
    run), or only those of ``signal_ids`` among them. A signal one of the steps applies to has its light curve
    loaded through ``LightCurveLoader`` (the kept raw file, else a verified re-fetch); steps 1 to 3 run in order
    while the signal stays open, then the pixel stage when ``pixels`` and the signal qualifies (``wants_pixels``),
    its pixel files through ``tpf_loader`` (``fetch_tpfs``; None means the same). A P/2 signal made by the period
    step is rechecked right after its parent, in the same run. Each signal is compared and re-vetted under its own
    search window (``signal_settings``).
    ``fetch`` serves the catalogues (the target id is bound into every request); ``with_novelty=False`` leaves rung 4
    out, as in a sweep run without it. ``retention`` (a ``Retention``; None keeps every file) releases a target's
    pixel files in stream mode once its pixel step is written and no signal of that target is left in the work
    list. ``stop_requested`` is polled before each signal: once it answers True the recheck returns the report so far
    with ``stopped`` set (every finished step is committed). A signal a reviewer decided on is passed over, except
    for the pixel step alone when its rung 5 row predates the pixel rule (``decided_queue``, ``decided_reopens``):
    the decision stays, and a disagreeing verdict is counted in ``decided_pixel_changed``. ``log`` receives one
    line per signal and step, which also goes to the logger at INFO."""
    started = time.time()
    report = RecheckReport()

    def say(text: str) -> None:
        logger.info(text)
        if log is not None:
            log(text)

    version = detector_version or latest_search_version(store)
    if version is None:
        report.seconds = time.time() - started
        return report
    queue = store.recheck_queue(version)
    if pixels:
        queued = {q["id"] for q in queue}
        extra = [*reopened_queue(store, version), *decided_queue(store, version)]
        queue = sorted([*queue, *{q["id"]: q for q in extra if q["id"] not in queued}.values()],
                       key=lambda q: q["id"])
    if signal_ids is not None:
        wanted = set(signal_ids)
        queue = [q for q in queue if q["id"] in wanted]
    work: deque[tuple[int, str]] = deque((q["id"], q["target_id"]) for q in queue)
    loader = LightCurveLoader(store, blob)
    cache: dict = {}
    settled: set[str] = set()               # targets whose pixel step settled, waiting for their files to go

    def release() -> None:
        """Stream retention: the pixel files of every settled target no signal left in the work list needs."""
        if retention is None:
            return
        pending = {t for _i, t in work}
        for tid in sorted(settled - pending):
            freed = retention.release_pixel_files(tid)
            if freed:
                say(f"recheck: released {freed} bytes of pixel files of {tid}")
            settled.discard(tid)

    while work:
        if stop_requested is not None and stop_requested():
            report.stopped = True
            say(f"recheck: stop requested, {len(work)} signals left for the next recheck")
            break
        signal_id, target_id = work.popleft()
        if _decided(store, signal_id):
            sig, target = _stored(store, signal_id)
            if pixels and sig.signal.detector_version == version and decided_reopens(
                    store, sig, store.latest_tests(signal_id)):
                report.considered += 1
                _recheck_decided(store, blob, settings, loader, sig, target, fetch=fetch, tpf_loader=tpf_loader,
                                 say=say, report=report, cache=cache)
                settled.add(target_id)
                release()
            continue
        report.considered += 1
        sig, target = _stored(store, signal_id)
        done = _recheck_one(store, blob, settings, loader, sig, target, fetch=fetch, pixels=pixels,
                            with_novelty=with_novelty, tpf_loader=tpf_loader, say=say, report=report, cache=cache)
        report.outcomes[done.outcome] = report.outcomes.get(done.outcome, 0) + 1
        for child_id in reversed(done.children):            # right after the parent, in the order they were made
            if store.signal_status(child_id) in OPEN_STATUSES:
                work.appendleft((child_id, target_id))
        if done.pixels_settled:
            settled.add(target_id)
        release()
    report.seconds = time.time() - started
    say(f"recheck: {report.considered} signals, steps {report.steps}, outcomes {report.outcomes}, "
        f"decided pixel verdicts changed {report.decided_pixel_changed}")
    return report
