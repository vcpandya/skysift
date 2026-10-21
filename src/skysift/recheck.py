"""Stage 2: the recheck of open signals (mission control spec 4.3). It takes every undetermined signal and every
stage-1 candidate of the current search version that no reviewer has decided on, and applies, in order:

1. the ladder re-vet: a signal vetted under an older ladder is vetted again under the current one;
2. the half-period re-vet: when rung 2 found an equal secondary at phase 0.5 (``suggested_period``), the signal is
   re-measured at P/2 as a new signal (``branch = "recheck:P/2"``, ``parent_signal_id``) and vetted; the original is
   superseded unless the new one is undetermined for the same reason;
3. the catalogue retry: when rung 4 could not reach a catalogue (``outage:``), rung 4 alone runs again;
4. the pixel stage (``step_pixels``, a hook until ``rung5_pixels`` is wired in).

Every step writes one ``rechecks`` row with its outcome ("skipped" and why when it could not run) and ordinary test
rows that point back to it, so the dashboard shows rechecks in the same ladder. The status is derived again from the
latest rows after each step (``Store.refresh_status``)."""
from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import select

from skysift import net
from skysift.config import Settings
from skysift.detectors.base import get_detector
from skysift.lc.loader import TARGET_FIELDS, LightCurveLoader
from skysift.models import LightCurve, Signal, Target, TestResult, TestStatus
from skysift.pipeline import DEFAULT_LADDER_VERSION, build_ladder, halved_signal, revet, revet_flux, search_version
from skysift.store.db import (
    RECHECK_JOB_PREFIX,
    RunRow,
    SignalRow,
    Store,
    TargetRow,
    final_status_for,
)
from skysift.vetting.base import Ladder
from skysift.vetting.rung4_novelty import Rung4Novelty

logger = logging.getLogger(__name__)

# undetermined reasons only the pixels can settle (spec 4.2): the pixel stage takes these and every stage-1 candidate
PIXEL_REASON_PREFIXES = ("season-dependent depth", "aperture-dependent depth", "systematic_alias", "period matches")
OPEN_STATUSES = ("undetermined", "candidate_stage1")         # what the queue holds; a step that settles it ends it
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

    def as_dict(self) -> dict:
        return {"considered": self.considered, "steps": dict(self.steps), "outcomes": dict(self.outcomes),
                "seconds": self.seconds}


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
    """What every step of one signal's recheck shares: the store, the light curve and target, the fetch for the
    catalogues, and the signal's flux, built once (``flux``) for all the steps."""
    store: Store
    blob: object
    settings: Settings
    fetch: Callable
    lc: LightCurve
    target: Target
    with_novelty: bool = True
    tpf_loader: Callable | None = None
    say: Callable[[str], None] = lambda text: None
    _flux: tuple | None = None

    def flux(self, sig: StoredSignal) -> tuple:
        """``revet_flux`` of the signal: (primary, secondary, detrended), built on first use."""
        if self._flux is None:
            self._flux = revet_flux(self.lc, self.settings.thresholds, sig.signal)
        return self._flux

    def vet(self, sig: Signal, ladder: Ladder, flux_of: StoredSignal) -> tuple[str, list[TestResult], list[float]]:
        """``revet`` through ``ladder`` on ``flux_of``'s flux, catalogue queries logged in the store under the
        target: (outcome, results, seconds per rung)."""
        timings: list[float] = []
        outcome, results = revet(self.lc, self.target, self.settings.thresholds, sig, ladder, fetch=self.fetch,
                                 store=self.store, on_result=lambda r, dt: timings.append(dt),
                                 flux=self.flux(flux_of))
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


def _latest(store: Store, signal_id: int, rung: str) -> dict | None:
    return next((t for t in store.latest_tests(signal_id) if t["rung"] == rung), None)


def _deciding_reason(results: list[TestResult]) -> str:
    """The reason of the row that decided a ladder outcome: the failure, else the first undetermined row."""
    for status in (TestStatus.FAIL, TestStatus.UNDETERMINED):
        for r in results:
            if r.status == status:
                return f"{r.rung}: {r.reason}"
    return ""


def _add_tests(ctx: RecheckContext, signal_id: int, results: list[TestResult], timings: list[float],
               recheck_id: int) -> None:
    for r, dt in zip(results, timings, strict=True):
        ctx.store.add_test(signal_id, r, dt, recheck_id=recheck_id)


def _skip(ctx: RecheckContext, sig: StoredSignal, step: str, reason: str) -> bool:
    return _record_skip(ctx.store, ctx.say, sig, step, reason)


def _record_skip(store: Store, say, sig: StoredSignal, step: str, reason: str) -> bool:
    """Record that ``step`` could not run on the signal, and why; the same skip as the step's last row is not
    written again, so a signal that stays stuck does not add a row at every sweep. Returns False (it did not run)."""
    rows = [r for r in store.rechecks(sig.id) if r["step"] == step]
    if not (rows and rows[-1]["outcome"] == "skipped" and rows[-1]["reason"] == reason):
        store.add_recheck(sig.id, step, DEFAULT_LADDER_VERSION, "skipped", reason)
    say(f"recheck {sig.id} ({sig.signal.target_id}) {step}: skipped, {reason}")
    return False


def _ladder(ctx: RecheckContext) -> Ladder:
    return build_ladder(DEFAULT_LADDER_VERSION, with_novelty=ctx.with_novelty)


def step_ladder(ctx: RecheckContext, sig: StoredSignal) -> bool:
    """Step 1: a signal vetted under an older ladder is vetted again under the current one (the stacked-fold tests
    settle the "too few events" class). Once per signal: a ladder recheck at the current version that ran is final."""
    if sig.ladder_version == DEFAULT_LADDER_VERSION:
        return False
    if any(r["step"] == "ladder" and r["ladder_version"] == DEFAULT_LADDER_VERSION and r["outcome"] != "skipped"
           for r in ctx.store.rechecks(sig.id)):
        return False
    outcome, results, timings = ctx.vet(sig.signal, _ladder(ctx), sig)
    reason = _deciding_reason(results)
    rid = ctx.store.add_recheck(sig.id, "ladder", DEFAULT_LADDER_VERSION, outcome, reason)
    _add_tests(ctx, sig.id, results, timings, rid)
    ctx.say(f"recheck {sig.id} ({ctx.target.target_id}) ladder {sig.ladder_version} to {DEFAULT_LADDER_VERSION}: "
            f"{outcome}{', ' + reason if reason else ''}")
    return True


def step_period(ctx: RecheckContext, sig: StoredSignal) -> bool:
    """Step 2: rung 2 suggested P/2. The signal is re-measured at half its period on the flux it was vetted on
    (``halved_signal``), stored as a new signal in its own run (job key ``recheck:P/2:<parent id>``) and vetted
    under the current ladder. The parent is superseded unless the new signal is undetermined for the same reason
    (an equal secondary at phase 0.5 again). A P/2 signal is not halved again; a parent is halved once."""
    rung2 = _latest(ctx.store, sig.id, "rung2_shape")
    suggested = ((rung2 or {}).get("metrics") or {}).get("suggested_period")
    if not (isinstance(suggested, int | float) and math.isfinite(suggested) and suggested > 0):
        return False
    if sig.branch == PERIOD_BRANCH or sig.signal.extra.get("halving"):
        return _skip(ctx, sig, "period", "already re-measured at half the period")
    if any(c["branch"] == PERIOD_BRANCH for c in ctx.store.children(sig.id)):
        return False
    fn = ctx.flux(sig)[0]
    child = halved_signal(sig.signal, ctx.lc, fn)
    if child is None:
        return _skip(ctx, sig, "period", "half period not measurable")
    outcome, results, timings = ctx.vet(child, _ladder(ctx), sig)
    same = outcome == "undetermined" and any(r.status == TestStatus.UNDETERMINED and r.reason.startswith(HALVING_REASON)
                                             for r in results)
    child_status = final_status_for(outcome, [r.model_dump() for r in results])
    store = ctx.store
    run_id = store.start_run(f"{RECHECK_JOB_PREFIX}P/2:{sig.id}", child.target_id, child.detector,
                             child.detector_version, DEFAULT_LADDER_VERSION)
    child_id = store.add_signal(child, run_id, parent_signal_id=sig.id, ladder_version=DEFAULT_LADDER_VERSION,
                                branch=PERIOD_BRANCH)
    if same:
        rec_outcome, reason = "undetermined", _deciding_reason(results)
    else:
        rec_outcome, reason = "superseded", f"re-measured at {child.period:.6g} d: {child_status}"
    rid = store.add_recheck(sig.id, "period", DEFAULT_LADDER_VERSION, rec_outcome, reason, new_signal_id=child_id)
    _add_tests(ctx, child_id, results, timings, rid)
    store.refresh_status(child_id)
    store.finish_run(run_id, "done")
    ctx.say(f"recheck {sig.id} ({ctx.target.target_id}) period {sig.signal.period:.6g} d to {child.period:.6g} d: "
            f"signal {child_id} {child_status}, parent {rec_outcome}")
    return True


def step_catalogue(ctx: RecheckContext, sig: StoredSignal) -> bool:
    """Step 3: rung 4 recorded a catalogue outage, so rung 4 alone runs again and its row is added."""
    rung4 = _latest(ctx.store, sig.id, "rung4_novelty")
    if rung4 is None or not str(rung4["reason"]).startswith("outage:"):
        return False
    _outcome, results, timings = ctx.vet(sig.signal, Ladder([Rung4Novelty()], DEFAULT_LADDER_VERSION), sig)
    r = results[0]
    rid = ctx.store.add_recheck(sig.id, "catalogue", DEFAULT_LADDER_VERSION, r.status.value, r.reason)
    _add_tests(ctx, sig.id, results, timings, rid)
    ctx.say(f"recheck {sig.id} ({ctx.target.target_id}) catalogue: {r.status.value}"
            f"{', ' + r.reason if r.reason else ''}")
    return True


def step_pixels(ctx: RecheckContext, sig: StoredSignal) -> bool:
    """Step 4, the pixel stage: ``rung5_pixels`` through ``ctx.tpf_loader`` (Task 5 of the recheck plan). Not wired
    yet: writes nothing and returns False."""
    return False


STEPS: tuple[tuple[str, Callable[[RecheckContext, StoredSignal], bool]], ...] = (
    ("ladder", step_ladder), ("period", step_period), ("catalogue", step_catalogue))


def _stored(store: Store, signal_id: int) -> tuple[StoredSignal, dict | None]:
    """The signal row as a ``StoredSignal`` and its target row as the loader's dict (None when it is missing)."""
    with store.session() as s:
        row = s.get(SignalRow, signal_id)
        sig = StoredSignal(id=row.id, signal=Signal(**{k: getattr(row, k) for k in Signal.model_fields}),
                           ladder_version=row.ladder_version, branch=row.branch, parent_signal_id=row.parent_signal_id)
        t = s.get(TargetRow, row.target_id)
        target = None if t is None else {**{k: getattr(t, k) for k in TARGET_FIELDS}, "meta": dict(t.meta or {})}
    return sig, target


def _recheck_one(store: Store, blob, settings: Settings, loader: LightCurveLoader, signal_id: int, *, fetch,
                 pixels: bool, with_novelty: bool, tpf_loader, say, report: RecheckReport) -> str:
    """One signal through the steps; returns its outcome for the report (its status, or "skipped" when it was left
    alone: another search version, or no light curve)."""
    sig, target = _stored(store, signal_id)
    current = search_version(get_detector(sig.signal.detector), settings.thresholds)
    if sig.signal.detector_version != current:
        _record_skip(store, say, sig, "ladder", "search version differs: signal of search version "
                                                f"{sig.signal.detector_version}, not the current {current}")
        return "skipped"
    try:
        if target is None:
            raise LookupError(f"no target row for {sig.signal.target_id}")
        lc = loader.load(target)
    except Exception as e:                  # an outage, a changed archive file, a parser error: the signal waits
        logger.warning("recheck of signal %s: light curve unavailable", sig.id, exc_info=True)
        _record_skip(store, say, sig, "ladder", f"light curve unavailable: {e}")
        return "skipped"
    ctx = RecheckContext(store, blob, settings, fetch, lc,
                         Target(**{k: target[k] for k in TARGET_FIELDS if target.get(k) is not None},
                                meta=target["meta"]),
                         with_novelty=with_novelty, tpf_loader=tpf_loader, say=say)
    for name, step in STEPS:
        if store.signal_status(sig.id) not in OPEN_STATUSES:
            break
        try:
            ran = step(ctx, sig)
        except Exception as e:              # a broken step leaves the signal as it is for a person
            logger.exception("recheck step %s failed for signal %s", name, sig.id)
            _skip(ctx, sig, name, f"exception: {e!r}")
            store.refresh_status(sig.id)
            break
        if ran:
            report.steps[name] += 1
        store.refresh_status(sig.id)
    else:
        status = store.signal_status(sig.id)
        if pixels and pixels_qualify(status, store.latest_tests(sig.id)) and step_pixels(ctx, sig):
            report.steps["pixels"] += 1
            store.refresh_status(sig.id)
    return store.signal_status(sig.id)


def recheck(store: Store, blob, settings: Settings, *, fetch=net.fetch, signal_ids: list[int] | None = None,
            pixels: bool = True, detector_version: str | None = None, log=None, with_novelty: bool = True,
            tpf_loader: Callable | None = None) -> RecheckReport:
    """Recheck the open signals of ``detector_version`` (default: the search version of the newest finished sweep
    run), or only those of ``signal_ids`` among them. Each signal's light curve comes through ``LightCurveLoader``
    (the kept raw file, else a verified re-fetch); steps 1 to 3 run in order while the signal stays open, then the
    pixel stage when ``pixels`` and the signal qualifies (``pixels_qualify``). ``fetch`` serves the catalogues (the
    target id is bound into every request); ``with_novelty=False`` leaves rung 4 out of re-vets, as in a sweep run
    without it. ``log`` receives one line per signal and step, which also goes to the logger at INFO."""
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
    if signal_ids is not None:
        wanted = set(signal_ids)
        queue = [q for q in queue if q["id"] in wanted]
    loader = LightCurveLoader(store, blob)
    for item in queue:
        report.considered += 1
        outcome = _recheck_one(store, blob, settings, loader, item["id"], fetch=fetch, pixels=pixels,
                               with_novelty=with_novelty, tpf_loader=tpf_loader, say=say, report=report)
        report.outcomes[outcome] = report.outcomes.get(outcome, 0) + 1
    report.seconds = time.time() - started
    say(f"recheck: {report.considered} signals, steps {report.steps}, outcomes {report.outcomes}")
    return report

