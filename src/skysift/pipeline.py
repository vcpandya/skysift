from __future__ import annotations

import json
import logging
import multiprocessing
import pickle
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed

from skysift.config import Settings, Thresholds
from skysift.detectors.base import get_detector
from skysift.lc.ops import detrend
from skysift.models import LightCurve, Signal, Target, TestResult
from skysift.store.blob import Blob
from skysift.store.db import Store, make_job_key
from skysift.store.retention import DiskBudgetExceeded, Retention, RetentionPolicy
from skysift.vetting.base import DataBundle, Ladder
from skysift.vetting.rung1_detection import Rung1Detection
from skysift.vetting.rung2_shape import Rung2Shape
from skysift.vetting.rung3_stability import Rung3Stability
from skysift.vetting.rung4_novelty import Rung4Novelty

log = logging.getLogger(__name__)
DEFAULT_LADDER_VERSION = "1"
FINAL_STATUS = {"pass": "candidate_stage1", "fail": "rejected", "undetermined": "undetermined"}


def priority_score(t: Target) -> float:
    s = 0.0
    g = t.gmag if t.gmag is not None else 17.0
    if 15 < g < 18.5: s += 2
    if g > 19: s -= 3
    if t.meta.get("psffrac", 0) >= 0.8: s += 1
    if t.meta.get("crowdsap", 0) >= 0.8: s += 1
    if not t.kic: s += 1
    return s


def build_ladder(version: str = DEFAULT_LADDER_VERSION, with_novelty: bool = True) -> Ladder:
    rungs = [Rung1Detection(), Rung2Shape(), Rung3Stability()]
    if with_novelty: rungs.append(Rung4Novelty())
    return Ladder(rungs, version)


def _cpu_work(lc_pickle: bytes, target_json: str, thresholds_json: str, detector_name: str, ladder_version: str,
              with_novelty: bool) -> str:
    """Runs in a worker process: detect + vet. Returns JSON of signals and their test results.

    The pickle comes only from this package's own main process (``_fetch``), never from outside data."""
    lc: LightCurve = pickle.loads(lc_pickle)
    target = Target.model_validate_json(target_json); th = Thresholds.model_validate_json(thresholds_json)
    lc.meta.setdefault("target_id", target.target_id)     # sources such as KBonus set it; others may not
    fn = detrend(lc); fn_alt = detrend(lc, flux=lc.flux_alt) if lc.flux_alt is not None else None
    det = get_detector(detector_name)
    signals = det.detect(lc, fn, th)
    ladder = build_ladder(ladder_version, with_novelty=with_novelty)
    bundle = DataBundle(lc=lc, fnorm=fn, fnorm_alt=fn_alt, target=target, thresholds=th)
    out = []
    for sig in signals:
        sig.target_id = target.target_id
        timings = []
        outcome, results = ladder.run(sig, bundle, on_result=lambda r, dt, ts=timings: ts.append(dt))
        out.append({"signal": sig.model_dump(), "outcome": outcome,
                    "tests": [r.model_dump() for r in results], "timings": timings})
    return json.dumps(out, default=_json_default)


def _json_default(o):
    """numpy scalars and arrays inside rung metrics must survive the trip back to the main process."""
    import numpy as np
    if isinstance(o, np.generic): return o.item()
    if isinstance(o, np.ndarray): return o.tolist()
    return str(o)


def _write_results(store: Store, run_id: int, payload: str) -> dict:
    counts = {"pass": 0, "fail": 0, "undetermined": 0}
    for item in json.loads(payload):
        sig = Signal(**item["signal"]); sid = store.add_signal(sig, run_id)
        for r, dt in zip(item["tests"], item["timings"], strict=True):
            store.add_test(sid, TestResult(**r), dt)
        store.set_signal_status(sid, FINAL_STATUS[item["outcome"]])
        counts[item["outcome"]] += 1
    return counts


def _select_targets(source, store: Store, filter: dict, det, ladder_version: str,
                    max_targets: int | None) -> tuple[list[tuple[Target, str]], int]:
    """Enumerate the source, record every target, skip done job keys; return (todo by priority, skipped count)."""
    todo, skipped = [], 0
    for t in source.list_targets(filter):
        key = make_job_key(t.target_id, det.name, det.version, ladder_version)
        store.upsert_target(t)
        if store.job_done(key):
            skipped += 1; continue
        todo.append((t, key))
        if max_targets and len(todo) >= max_targets: break
    todo.sort(key=lambda x: -priority_score(x[0]))
    return todo, skipped


def _fetch(source, t: Target) -> tuple[Target, bytes]:
    lc, _sha, _url = source.fetch_lightcurve(t)       # the adapter records the artifact itself
    return t, pickle.dumps(lc)


def run(source, store: Store, blob: Blob, settings: Settings, filter: dict, detector_name: str = "box_dip",
        max_targets: int | None = None, progress=None, with_novelty: bool = True) -> dict:
    det = get_detector(detector_name)
    retention = Retention(RetentionPolicy.parse(settings.retention), store, blob)
    ladder_version = DEFAULT_LADDER_VERSION
    th_json = settings.thresholds.model_dump_json()
    todo, skipped = _select_targets(source, store, filter, det, ladder_version, max_targets)
    summary = {"processed": 0, "skipped": skipped, "errors": 0, "signals": 0,
               "outcomes": {"pass": 0, "fail": 0, "undetermined": 0}}
    start = time.time()

    def fail(t: Target, rid: int, e: Exception) -> None:
        store.finish_run(rid, "error", repr(e)); store.set_target_state(t.target_id, "error")
        summary["errors"] += 1

    # spawn everywhere (the Windows default): forking while the I/O threads run is unsafe on POSIX
    io_pool = ThreadPoolExecutor(settings.workers_io)
    cpu_pool = ProcessPoolExecutor(settings.workers_cpu, mp_context=multiprocessing.get_context("spawn"))
    try:
        fetches = {io_pool.submit(_fetch, source, t): (t, key) for t, key in todo}
        cpu_jobs = {}
        for fut in as_completed(fetches):
            t, key = fetches[fut]
            rid = store.start_run(key, t.target_id, det.name, det.version, ladder_version)
            try:
                t, lc_pickle = fut.result()
            except Exception as e:
                log.warning("fetch failed for %s", t.target_id, exc_info=True)
                fail(t, rid, e); continue
            cpu_jobs[cpu_pool.submit(_cpu_work, lc_pickle, t.model_dump_json(), th_json, detector_name,
                                     ladder_version, with_novelty)] = (t, rid)
        for fut in as_completed(cpu_jobs):
            t, rid = cpu_jobs[fut]
            try:
                counts = _write_results(store, rid, fut.result())
                for k, v in counts.items(): summary["outcomes"][k] += v
                summary["signals"] += sum(counts.values())
                store.finish_run(rid, "done"); store.set_target_state(t.target_id, "done")
                if counts["pass"] or counts["undetermined"]:
                    store.pin_target_artifacts(t.target_id)      # keep evidence for anything still alive
                retention.release_target(t.target_id)
                retention.enforce_budget()                      # raises DiskBudgetExceeded -> stops the run
                summary["processed"] += 1
            except DiskBudgetExceeded:
                raise
            except Exception as e:
                log.exception("processing failed for %s", t.target_id)
                fail(t, rid, e)
            if progress: progress(summary)
    except BaseException:
        # stop now: queued fetches and CPU jobs are dropped; their runs stay 'running' and are redone next time
        io_pool.shutdown(wait=True, cancel_futures=True); cpu_pool.shutdown(wait=True, cancel_futures=True)
        raise
    io_pool.shutdown(); cpu_pool.shutdown()
    summary["seconds"] = time.time() - start
    return summary
