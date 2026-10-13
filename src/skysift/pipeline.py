from __future__ import annotations

import hashlib
import importlib
import json
import logging
import multiprocessing
import pickle
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, ThreadPoolExecutor, wait
from itertools import islice

from skysift import jsonutil, net
from skysift.config import Settings, Thresholds
from skysift.detectors.base import get_detector
from skysift.lc.ops import detrend
from skysift.models import LightCurve, Target
from skysift.store.blob import Blob
from skysift.store.db import Store, make_job_key, utcnow
from skysift.store.retention import Retention, RetentionPolicy
from skysift.vetting.base import DataBundle, Ladder
from skysift.vetting.rung1_detection import Rung1Detection
from skysift.vetting.rung2_shape import Rung2Shape
from skysift.vetting.rung3_stability import Rung3Stability
from skysift.vetting.rung4_novelty import Rung4Novelty

log = logging.getLogger(__name__)
DEFAULT_LADDER_VERSION = "1"
UPSERT_BATCH = 1000
MAX_ATTEMPTS = 3            # spec section 9: a target that errors three times is left for a human
SEARCH_CONFIG_KEYS = ("oversample", "n_bands", "n_refine", "refine_oversample", "two_stage")


def priority_score(t: Target) -> float:
    s = 0.0
    g = t.gmag if t.gmag is not None else 17.0
    if 15 < g < 18.5: s += 2
    if g > 19: s -= 3
    if t.meta.get("psffrac", 0) >= 0.8: s += 1
    if t.meta.get("crowdsap", 0) >= 0.8: s += 1
    if not t.kic: s += 1
    return s


def search_version(det, thresholds: Thresholds) -> str:
    """Detector version plus an 8-hex hash of the search configuration (period window and the detector's grid
    knobs), so a run with a different window or grid is a different job: a narrow-window run never marks a target
    done for a later full-window run. Used in the job key, the run row and every signal's detector_version."""
    cfg = {"min_period_days": thresholds.min_period_days, "max_period_days": thresholds.max_period_days,
           **{k: getattr(det, k, None) for k in SEARCH_CONFIG_KEYS}}
    h = hashlib.sha1(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:8]
    return f"{det.version}+{h}"


def build_ladder(version: str = DEFAULT_LADDER_VERSION, with_novelty: bool = True) -> Ladder:
    rungs = [Rung1Detection(), Rung2Shape(), Rung3Stability()]
    if with_novelty: rungs.append(Rung4Novelty())
    return Ladder(rungs, version)


class _QueryRecorder:
    """Stands in for the Store inside a worker process: ``net.fetch`` calls ``log_query`` on it, and the recorded rows
    travel back with the results so the main process writes them in the target's results transaction."""

    def __init__(self):
        self.rows: list[dict] = []

    def log_query(self, service, url, params, status, nbytes, sha256=None, error="") -> None:
        self.rows.append({"service": service, "url": url, "params": dict(params or {}), "status": status,
                          "nbytes": nbytes, "sha256": sha256, "error": error, "utc": utcnow().isoformat()})


def _cpu_work(lc_pickle: bytes, target_json: str, thresholds_json: str, detector_name: str, ladder_version: str,
              with_novelty: bool, fetch_module: str | None = None) -> str:
    """Runs in a worker process: detect + vet. Returns strict JSON {"signals": [...], "queries": [...]}: each signal
    with its outcome, test results and timings, and every catalogue query the rungs made.

    ``fetch_module`` names a module whose ``fetch`` replaces ``net.fetch`` for the rungs (tests use it to keep rung 4
    off the network; a monkeypatch in the parent never reaches a spawned worker). The pickle comes only from this
    package's own main process (``_fetch``), never from outside data."""
    lc: LightCurve = pickle.loads(lc_pickle)
    target = Target.model_validate_json(target_json); th = Thresholds.model_validate_json(thresholds_json)
    # KBonus and other adapters set target_id themselves; their value wins on purpose (setdefault), and sources
    # that leave it out get the pipeline's target id so detector output names the right star
    lc.meta.setdefault("target_id", target.target_id)
    fn = detrend(lc); fn_alt = detrend(lc, flux=lc.flux_alt) if lc.flux_alt is not None else None
    det = get_detector(detector_name)
    signals = det.detect(lc, fn, th)
    version = search_version(det, th)
    ladder = build_ladder(ladder_version, with_novelty=with_novelty)
    recorder = _QueryRecorder()
    fetch = importlib.import_module(fetch_module).fetch if fetch_module else net.fetch
    bundle = DataBundle(lc=lc, fnorm=fn, fnorm_alt=fn_alt, target=target, thresholds=th, store=recorder, fetch=fetch)
    out = []
    for sig in signals:
        sig.target_id, sig.detector_version = target.target_id, version
        sig.extra["search_window"] = {"min_period_days": th.min_period_days, "max_period_days": th.max_period_days}
        timings = []
        outcome, results = ladder.run(sig, bundle, on_result=lambda r, dt, ts=timings: ts.append(dt))
        out.append({"signal": sig.model_dump(), "outcome": outcome,
                    "tests": [r.model_dump() for r in results], "timings": timings})
    return jsonutil.dumps({"signals": out, "queries": recorder.rows})


def _write_results(store: Store, run_id: int, target_id: str, payload: str) -> dict:
    """Parse a worker payload and write it atomically (``Store.record_results``); evidence is pinned when any
    signal passed or is undetermined. Returns counts per outcome."""
    data = json.loads(payload)
    items = data["signals"]
    pin = any(i["outcome"] in ("pass", "undetermined") for i in items)
    return store.record_results(run_id, target_id, items, data["queries"], pin_evidence=pin)


def _select_targets(source, store: Store, filter: dict, det, version: str, ladder_version: str,
                    max_targets: int | None) -> tuple[list[tuple[Target, str]], int, int]:
    """Enumerate the whole source, record every target (in batches), skip done job keys and keys that already
    errored MAX_ATTEMPTS times, and return (the top ``max_targets`` remaining targets by priority, highest first;
    skipped count; gave-up count)."""
    todo, skipped, gave_up = [], 0, 0
    targets = iter(source.list_targets(filter))
    while batch := list(islice(targets, UPSERT_BATCH)):
        store.upsert_targets(batch)
        for t in batch:
            key = make_job_key(t.target_id, det.name, version, ladder_version)
            if store.job_done(key): skipped += 1
            elif store.attempts(key) >= MAX_ATTEMPTS: gave_up += 1
            else: todo.append((t, key))
    todo.sort(key=lambda x: priority_score(x[0]), reverse=True)     # stable: ties keep source order
    return (todo if max_targets is None else todo[:max_targets]), skipped, gave_up


def _fetch(source, t: Target) -> bytes:
    lc, _sha, _url = source.fetch_lightcurve(t)       # the adapter records the artifact itself
    return pickle.dumps(lc)


def run(source, store: Store, blob: Blob, settings: Settings, filter: dict, detector_name: str = "box_dip",
        max_targets: int | None = None, progress=None, with_novelty: bool = True, fetch_module: str | None = None) -> dict:
    """Bounded two-pool scheduler: at most 2*workers_io fetches (fetched light curves waiting for a CPU slot
    included) and 2*workers_cpu CPU jobs are in flight, so memory and disk stay flat however long the sweep."""
    det = get_detector(detector_name)
    retention = Retention(RetentionPolicy.parse(settings.retention), store, blob)
    ladder_version = DEFAULT_LADDER_VERSION
    th_json = settings.thresholds.model_dump_json()
    version = search_version(det, settings.thresholds)
    todo, skipped, gave_up = _select_targets(source, store, filter, det, version, ladder_version, max_targets)
    summary = {"processed": 0, "skipped": skipped, "gave_up": gave_up, "errors": 0, "signals": 0,
               "outcomes": {"pass": 0, "fail": 0, "undetermined": 0}}
    start = time.time()
    max_fetch, max_cpu = 2 * settings.workers_io, 2 * settings.workers_cpu
    pending = iter(todo)
    fetches: dict[Future, tuple[Target, str]] = {}
    ready: deque[tuple[Target, int, bytes]] = deque()      # fetched, run started, waiting for a CPU slot
    cpu_jobs: dict[Future, tuple[Target, int]] = {}

    def fail(t: Target, rid: int | None, e: Exception) -> None:
        if rid is not None: store.finish_run(rid, "error", repr(e))
        store.set_target_state(t.target_id, "error")
        retention.release_target(t.target_id)            # nothing to pin: free whatever the fetch left behind
        summary["errors"] += 1
        if progress: progress(summary)

    def on_fetched(fut: Future) -> None:
        t, key = fetches.pop(fut)
        rid = None
        try:
            rid = store.start_run(key, t.target_id, det.name, version, ladder_version)
            ready.append((t, rid, fut.result()))
        except Exception as e:
            log.warning("fetch or run start failed for %s", t.target_id, exc_info=True)
            fail(t, rid, e)

    def on_vetted(fut: Future) -> None:
        t, rid = cpu_jobs.pop(fut)
        try:
            # one transaction: signals, tests, queries, evidence pins, finished run, target state
            counts = _write_results(store, rid, t.target_id, fut.result())
        except Exception as e:
            log.exception("processing failed for %s", t.target_id)
            fail(t, rid, e); return
        try:
            retention.release_target(t.target_id)            # pinned evidence stays; everything else goes
        except Exception:                                    # the results are committed: the target stays done
            log.warning("releasing files of %s failed; a later sweep retries", t.target_id, exc_info=True)
        for k, v in counts.items(): summary["outcomes"][k] += v
        summary["signals"] += sum(counts.values()); summary["processed"] += 1
        if progress: progress(summary)
        # after a vetted target only, and never touching in-flight targets: they have not pinned their evidence yet,
        # so evicting their files could delete what a live signal is about to claim
        in_flight = ({x[0].target_id for x in fetches.values()} | {x[0].target_id for x in ready}
                     | {x[0].target_id for x in cpu_jobs.values()})
        retention.enforce_budget(exclude_targets=in_flight)   # raises DiskBudgetExceeded -> stops the run

    def top_up() -> None:
        while ready and len(cpu_jobs) < max_cpu:
            t, rid, lc_pickle = ready.popleft()
            cpu_jobs[cpu_pool.submit(_cpu_work, lc_pickle, t.model_dump_json(), th_json, detector_name,
                                     ladder_version, with_novelty, fetch_module)] = (t, rid)
        while len(fetches) + len(ready) < max_fetch and (nxt := next(pending, None)) is not None:
            fetches[io_pool.submit(_fetch, source, nxt[0])] = nxt

    # spawn everywhere (the Windows default): forking while the I/O threads run is unsafe on POSIX
    io_pool = ThreadPoolExecutor(settings.workers_io)
    cpu_pool = ProcessPoolExecutor(settings.workers_cpu, mp_context=multiprocessing.get_context("spawn"))
    try:
        top_up()
        while fetches or cpu_jobs:
            done, _ = wait([*fetches, *cpu_jobs], return_when=FIRST_COMPLETED)
            for fut in done:
                if fut in fetches: on_fetched(fut)
                else: on_vetted(fut)
            done = fut = None                              # drop the last references to payloads and results
            top_up()
    except BaseException:
        # stop now: queued fetches and CPU jobs are dropped; their runs stay 'running' and are redone next time
        io_pool.shutdown(wait=True, cancel_futures=True); cpu_pool.shutdown(wait=True, cancel_futures=True)
        raise
    io_pool.shutdown(); cpu_pool.shutdown()
    summary["seconds"] = time.time() - start
    return summary
