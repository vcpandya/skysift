from __future__ import annotations

import hashlib
import importlib
import json
import logging
import multiprocessing
import os
import pickle
import signal
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, ThreadPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from dataclasses import replace
from itertools import islice

from skysift import jsonutil, net
from skysift.config import Settings, Thresholds, stop_request_path
from skysift.detectors.base import get_detector
from skysift.detectors.periodogram import PeriodogramSummary
from skysift.lc.ops import (
    DETREND_VERSION,
    GAP_EDGE_MASK_DAYS,
    GAP_EDGE_TRIGGER_DAYS,
    MEDIAN_PAD_MODE,
    apply_mask,
    detrend,
)
from skysift.lc.prewhiten import GUARD_EXCLUSION, PREWHITEN_VERSION, prewhiten, prewhiten_model
from skysift.models import LightCurve, Signal, Target
from skysift.net import ServiceUnavailable
from skysift.store.blob import Blob
from skysift.store.db import (
    PERIODOGRAM_URL,  # noqa: F401  re-exported: the summary artifact url
    POOL_BROKE_ERROR,  # several runs held at a pool break: culprit unknown, none charged
    UNAVAILABLE_PREFIX,
    WORKER_DIED_ERROR,  # one run held: its target is charged an attempt
    Store,
    make_job_key,
    utcnow,
)
from skysift.store.retention import DiskBudgetExceeded, Retention, RetentionPolicy
from skysift.vetting.base import DataBundle, Ladder
from skysift.vetting.rung1_detection import Rung1Detection
from skysift.vetting.rung1_events import Rung1Events
from skysift.vetting.rung2_shape import Rung2Shape
from skysift.vetting.rung3_stability import Rung3Stability
from skysift.vetting.rung4_novelty import Rung4Novelty

log = logging.getLogger(__name__)
# Ladder version 3 (18 Oct 2026, vetting best practice; spec 2026-10-18-skysift-vetting-best-practice-design.md):
#   rung 1 events (coverage, gapped events, single-event dominance, Chases, Zuma),
#   rung 2 SWEET, MS2/MS3 uniqueness, transit fit, V shape, period-doubling and occultation exceptions,
#   rung 3 seasonal and aperture depth disagreement routed to review,
#   rung 4 epoch mismatch with a known object routed to review.
# 2: rung 4 integer-ratio and epoch rules, second-pass signals vetted on the masked flux (18 Oct 2026)
DEFAULT_LADDER_VERSION = "3"
UPSERT_BATCH = 1000
MAX_ATTEMPTS = 3            # spec section 9: a target that errors three times is left for a human
MAX_POOL_RESTARTS = 2       # a CPU pool that breaks a third time in one sweep ends the sweep
MAX_POOL_BREAKS = 3         # a job held by three broken pools (charged or not) is given up: it may be the killer
SEARCH_CONFIG_KEYS = ("oversample", "n_bands", "n_refine", "refine_oversample", "two_stage", "mask_iterations",
                     "alias_resolution")
RESTORE_HARMONICS = 8       # a detection gets back removed components at m * f / n for m in 1 .. 8 ...
# ... and n in RESTORE_DIVISORS: its integer harmonics only. A transit has no power at half-integer harmonics, so a
# component there is a stellar wave that, put back, alternates between transits and fails rung 2 as odd_even; the P/2
# alias is resolved by the detector (BoxDip._resolve_alias) before the restore runs
RESTORE_DIVISORS = (1,)


def priority_score(t: Target) -> float:
    s = 0.0
    g = t.gmag if t.gmag is not None else 17.0
    if 15 < g < 18.5: s += 2
    if g > 19: s -= 3
    if t.meta.get("psffrac", 0) >= 0.8: s += 1
    if t.meta.get("crowdsap", 0) >= 0.8: s += 1
    if not t.kic: s += 1
    # Data span decides whether a search is possible at all: fewer than two quarters cannot reach MIN_POINTS and
    # a long baseline is what makes periods out to 200 d and the season test meaningful.
    nq = t.meta.get("n_quarters")
    if nq is not None:
        if nq < 2: s -= 10          # below every searchable target, whatever its other scores
        elif nq >= 8: s += 2
    return s


def detrend_settings() -> dict:
    """The detrend the sweep applies, as stored with every signal (``extra["detrend"]``) and hashed into
    ``search_version``: strict JSON, and enough for the dashboard to redo exactly this detrend later."""
    return {"version": DETREND_VERSION, "edge_mask_days": GAP_EDGE_MASK_DAYS, "edge_gap_days": GAP_EDGE_TRIGGER_DAYS,
            "median_mode": MEDIAN_PAD_MODE}


def search_version(det, thresholds: Thresholds) -> str:
    """Detector version plus an 8-hex hash of the search configuration (period window and the detector's grid
    knobs), so a run with a different window or grid is a different job: a narrow-window run never marks a target
    done for a later full-window run. Used in the job key, the run row and every signal's detector_version."""
    cfg = {"min_period_days": thresholds.min_period_days, "max_period_days": thresholds.max_period_days,
           **{k: getattr(det, k, None) for k in SEARCH_CONFIG_KEYS},
           # the gap-edge mask and the median padding change the flux every rung sees: earlier runs are searched again
           "detrend": detrend_settings()}
    if thresholds.prewhiten_max_components:         # whitening changes what the detector sees: part of the version
        cfg["prewhiten"] = {"version": PREWHITEN_VERSION, "max_components": thresholds.prewhiten_max_components,
                            "fap": thresholds.prewhiten_fap}
    h = hashlib.sha1(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:8]
    return f"{det.version}+{h}"


def build_ladder(version: str = DEFAULT_LADDER_VERSION, with_novelty: bool = True) -> Ladder:
    rungs = [Rung1Detection(), Rung1Events(), Rung2Shape(), Rung3Stability()]
    if with_novelty: rungs.append(Rung4Novelty())
    return Ladder(rungs, version)


class _QueryRecorder:
    """Stands in for the Store inside a worker process: ``net.fetch`` calls ``log_query`` on it, and the recorded rows
    travel back with the results so the main process writes them in the target's results transaction."""

    def __init__(self):
        self.rows: list[dict] = []

    def log_query(self, service, url, params, status, nbytes, sha256=None, error="", target_id=None) -> None:
        # target_id is accepted for net.fetch's sake; record_results stamps every row with the run's own target
        self.rows.append({"service": service, "url": url, "params": dict(params or {}), "status": status,
                          "nbytes": nbytes, "sha256": sha256, "error": error, "utc": utcnow().isoformat()})


def _search(det, lc, fn, th, keep_spectrum=False):
    """Detectors that implement search() give signals plus the periodogram; older ones give signals only."""
    if hasattr(det, "search"):
        return det.search(lc, fn, th, keep_spectrum=keep_spectrum)
    from skysift.detectors.box_dip import SearchResult
    return SearchResult(signals=det.detect(lc, fn, th))


def whitened_flux(lc: LightCurve, th: Thresholds, flux=None) -> tuple:
    """The flux the detector and every rung see: ``detrend`` (of ``flux``, default the primary flux), then harmonic
    pre-whitening, which removes the star's significant sinusoids (``prewhiten_max_components`` 0 leaves the detrended
    flux as it is). Returns (whitened flux, the record stored in each signal's ``extra["prewhitening"]``: the removed
    components, the refused frequencies and ``capped`` (the loop stopped at ``prewhiten_max_components``, not on its
    own), so the dashboard subtracts the same model; the detrend settings stored in
    ``extra["detrend"]``, so the dashboard redoes the same detrend; the detrended flux before pre-whitening, which
    rung 2's SWEET fits, since the whitening removes exactly the waves SWEET looks for). The sweep and the calibration
    search the same flux through ``search_with_restore``."""
    settings = detrend_settings()
    fn = detrend(lc, flux=flux, edge_mask_days=settings["edge_mask_days"], edge_gap_days=settings["edge_gap_days"],
                 pad_mode=settings["median_mode"])
    white = prewhiten(lc.time, fn, max_components=th.prewhiten_max_components, fap_max=th.prewhiten_fap)
    record = {"n": len(white.components), "components": [c.as_dict() for c in white.components],
              "skipped": [float(f) for f in white.skipped], "capped": bool(white.capped)}
    return white.residual, record, settings, fn


def restore_harmonics(time, flux, record: dict, detections) -> tuple | None:
    """Put back the removed components that sit at a detection's harmonics: within ``GUARD_EXCLUSION`` resolution
    elements (1/baseline of the finite flux) of m * f / n for every ``(f, depth_ppm)`` in ``detections``, m in 1 ..
    ``RESTORE_HARMONICS`` and n in ``RESTORE_DIVISORS`` (1 only: the integer harmonics; a transit has no power at
    half-integer harmonics, so a component at 1.5 f is a stellar wave and stays removed), and no larger than that
    detection's depth: a box of depth d has every Fourier amplitude below d (2 d times its duty cycle, at most), so a
    component deeper than the detection is a stellar wave that happens to sit on the comb, not a piece of the transit,
    and stays removed. A detection must never be measured on flux that had its own harmonics removed, whatever the
    shape guard decided. Returns None when
    there is nothing to restore, else (the flux plus their model, the record without them and with ``restored``:
    [{frequency, amplitude_ppm}, ...]), so ``detrend - prewhiten_model(record components)`` is still exactly the
    returned flux."""
    import numpy as np
    time, flux = np.asarray(time, float), np.asarray(flux, float)
    t = time[np.isfinite(flux) & np.isfinite(time)]
    comps = record.get("components") or []
    if t.size < 2 or not comps:
        return None
    tolerance = GUARD_EXCLUSION / float(t.max() - t.min())
    combs = [(m * f / n, depth) for f, depth in detections if f > 0 and depth > 0
             for n in RESTORE_DIVISORS for m in range(1, RESTORE_HARMONICS + 1)]
    hit = [any(abs(float(c["frequency"]) - h) < tolerance and float(c["amplitude_ppm"]) <= depth for h, depth in combs)
           for c in comps]
    if not any(hit):
        return None
    back = [c for c, h in zip(comps, hit) if h]
    kept = [c for c, h in zip(comps, hit) if not h]
    new = {**record, "n": len(kept), "components": kept,
           "restored": [{"frequency": float(c["frequency"]), "amplitude_ppm": float(c["amplitude_ppm"])} for c in back]}
    return flux + prewhiten_model(time, back), new


def search_with_restore(det, lc: LightCurve, fn, th: Thresholds, record: dict, *, keep_spectrum: bool = False,
                        detections: list | None = None) -> tuple:
    """Search ``fn`` (whitened flux with its pre-whitening ``record``); when a detection at ``sde >= th.sde_min``
    had its own harmonics whitened away, put them back (``restore_harmonics``) and search once more, at most once.
    Returns (the search result, the flux it ran on, that flux's record: after a restore it lists the restored
    components under ``restored`` instead of ``components``), so ``detrend - prewhiten_model(record components)`` is
    the searched flux. The sweep and the null calibration both search through this, so the calibration measures the
    statistic the sweep thresholds. ``detections``, when given, receives the ``(frequency, depth_ppm)`` pairs whose
    harmonics were put back (nothing when no restore happened), so the sweep restores its secondary flux from them."""
    res = _search(det, lc, fn, th, keep_spectrum=keep_spectrum)
    detected = [(1.0 / s.period, float(s.depth_ppm)) for s in res.signals if s.sde >= th.sde_min and s.period > 0]
    restored = restore_harmonics(lc.time, fn, record, detected) if detected else None
    if restored is None:
        return res, fn, record
    if detections is not None: detections.extend(detected)
    fn, record = restored
    return _search(det, lc, fn, th, keep_spectrum=keep_spectrum), fn, record


def _vetting_bundle(bundle: DataBundle, sig: Signal) -> DataBundle:
    """The bundle a signal is vetted on. A second-pass signal (``extra["mask"]``) was found with the first-pass
    signals masked, so its rungs see the same flux: every masked point NaN in ``fnorm`` and ``fnorm_alt``. On the
    unmasked flux a binary's eclipses, folded at the companion's period, built a spurious secondary and rung 2
    rejected the companion (K06356.01, 18 October 2026). The copy shares the catalogue cache; other signals get
    ``bundle`` itself. The detrended flux of SWEET (``flux_detrended``) is masked the same way. ``fnorm_unmasked`` keeps
    the flux before the mask: a masked cadence was observed, and rung 1 events must not count it as missing data."""
    mask = sig.extra.get("mask")
    if not mask:
        return bundle
    t = bundle.lc.time
    alt = None if bundle.fnorm_alt is None else apply_mask(t, bundle.fnorm_alt, mask)
    detrended = None if bundle.flux_detrended is None else apply_mask(t, bundle.flux_detrended, mask)
    return replace(bundle, fnorm=apply_mask(t, bundle.fnorm, mask), fnorm_alt=alt, flux_detrended=detrended,
                   fnorm_unmasked=bundle.fnorm)


def _cpu_work(lc_pickle: bytes, target_json: str, thresholds_json: str, detector_name: str, ladder_version: str,
              with_novelty: bool, fetch_module: str | None = None, store_periodogram: str = "all",
              spectrum_path: str | None = None) -> str:
    """Runs in a worker process: detect + vet. Returns strict JSON {"signals": [...], "queries": [...],
    "periodogram": {...} | null}: each signal with its outcome, test results and timings, every catalogue query the
    rungs made, and the periodogram summary when ``store_periodogram`` keeps it ("all"; "signals" only when a peak
    survived rung 1; never for "off"). ``spectrum_path`` also writes the full spectrum arrays there (.npz).

    ``fetch_module`` names a module whose ``fetch`` replaces ``net.fetch`` for the rungs (tests use it to keep rung 4
    off the network; a monkeypatch in the parent never reaches a spawned worker). The pickle comes only from this
    package's own main process (``_fetch``), never from outside data.

    Test-only hook: a light curve whose ``meta["__crash_worker__"]`` is truthy makes the worker process die at once
    (``os._exit(3)``), as an out-of-memory kill would, so the tests can break the pool for real."""
    lc: LightCurve = pickle.loads(lc_pickle)
    if lc.meta.get("__crash_worker__"): os._exit(3)
    target = Target.model_validate_json(target_json); th = Thresholds.model_validate_json(thresholds_json)
    # KBonus and other adapters set target_id themselves; their value wins on purpose (setdefault), and sources
    # that leave it out get the pipeline's target id so detector output names the right star
    lc.meta.setdefault("target_id", target.target_id)
    fn, prewhitening, detrend_record, fn_detrended = whitened_flux(lc, th)
    fn_alt, alt_record = whitened_flux(lc, th, flux=lc.flux_alt)[:2] if lc.flux_alt is not None else (None, None)
    det = get_detector(detector_name)
    # a detection whose own harmonics were whitened away is searched once more on flux with them put back; that
    # second search (and its record, without the restored components) is the one stored. At most once per target.
    detected: list = []
    res, fn, prewhitening = search_with_restore(det, lc, fn, th, prewhitening, keep_spectrum=spectrum_path is not None,
                                                detections=detected)
    if detected and fn_alt is not None:     # the rungs compare the secondary flux with the primary: same treatment
        alt = restore_harmonics(lc.time, fn_alt, alt_record, detected)
        if alt is not None: fn_alt = alt[0]
    signals = res.signals
    if spectrum_path and res.spectrum is not None:
        import numpy as np
        np.savez_compressed(spectrum_path, **{k: np.asarray(v) for k, v in res.spectrum.items()})
    version = search_version(det, th)
    ladder = build_ladder(ladder_version, with_novelty=with_novelty)
    recorder = _QueryRecorder()
    fetch = importlib.import_module(fetch_module).fetch if fetch_module else net.fetch
    bundle = DataBundle(lc=lc, fnorm=fn, fnorm_alt=fn_alt, target=target, thresholds=th, store=recorder, fetch=fetch,
                        flux_detrended=fn_detrended)
    out = []
    for sig in signals:
        sig.target_id, sig.detector_version = target.target_id, version
        sig.extra["search_window"] = {"min_period_days": th.min_period_days, "max_period_days": th.max_period_days}
        sig.extra["prewhitening"] = prewhitening
        sig.extra["detrend"] = detrend_record
        timings = []
        outcome, results = ladder.run(sig, _vetting_bundle(bundle, sig),
                                      on_result=lambda r, dt, ts=timings: ts.append(dt))
        out.append({"signal": sig.model_dump(), "outcome": outcome,
                    "tests": [r.model_dump() for r in results], "timings": timings})
    # a peak "survived rung 1" when its first test did not fail; the detector always returns its top peaks, so
    # "has a signal" alone would keep the periodogram of every target
    survived = any(item["tests"] and item["tests"][0]["status"] != "fail" for item in out)
    keep = store_periodogram == "all" or (store_periodogram == "signals" and survived)
    periodogram = res.periodogram.model_dump() if (keep and res.periodogram is not None) else None
    # default=str: an unexpected type in a metric must not error the target (and lose its query log)
    return jsonutil.dumps({"signals": out, "queries": recorder.rows, "periodogram": periodogram}, default=str)


def _write_results(store: Store, blob: Blob, run_id: int, target_id: str, payload: str) -> dict:
    """Parse a worker payload, store the periodogram summary (content addressed, so a retry reuses the same file)
    and write everything else atomically through ``Store.record_results``; evidence (the summary included) is pinned
    when any signal passed or is undetermined. Returns counts per outcome."""
    data = json.loads(payload)
    items = data["signals"]
    pin = any(i["outcome"] in ("pass", "undetermined") for i in items)
    periodogram = None
    if data.get("periodogram"):
        npz = PeriodogramSummary(**data["periodogram"]).to_npz()
        sha, loc = blob.put(npz, f"{target_id}_periodogram.npz")
        periodogram = (sha, len(npz), loc)
    return store.record_results(run_id, target_id, items, data["queries"], pin_evidence=pin, periodogram=periodogram)


def _salvage_queries(store: Store, payload, target_id: str) -> None:
    """When the result write failed, the worker's catalogue requests still happened: log them on their own so
    the queries table keeps every HTTP request even for a target that ends in error."""
    if not isinstance(payload, str): return
    try:
        for q in json.loads(payload).get("queries", []):
            store.log_query(q["service"], q["url"], q.get("params") or {}, q.get("status"), q.get("nbytes", 0),
                            q.get("sha256"), q.get("error") or "", target_id=target_id)
    except Exception:  # best effort: the target is already being marked error
        log.warning("could not salvage query log", exc_info=True)


def _enumerate_targets(source, store: Store, filter: dict) -> list[Target]:
    """Enumerate the whole source, record every target (in batches) and return them once each, in source order."""
    out: list[Target] = []
    seen: set[str] = set()         # a source yielding one target twice must not start two runs of one job
    targets = iter(source.list_targets(filter))
    while batch := list(islice(targets, UPSERT_BATCH)):
        store.upsert_targets(batch)
        for t in batch:
            if t.target_id in seen: continue
            seen.add(t.target_id)
            out.append(t)
    return out


def ranked_targets(source, store: Store, filter: dict, max_targets: int | None) -> list[Target]:
    """The top ``max_targets`` targets of the source by priority, highest first, whatever has been searched before
    (the null calibration takes the same stars a sweep would start with)."""
    targets = sorted(_enumerate_targets(source, store, filter), key=priority_score, reverse=True)   # stable
    return targets if max_targets is None else targets[:max_targets]


def _select_targets(source, store: Store, filter: dict, det, version: str, ladder_version: str,
                    max_targets: int | None) -> tuple[list[tuple[Target, str]], int, int]:
    """Enumerate the whole source, record every target (in batches), skip done job keys, keys that already errored
    MAX_ATTEMPTS times and keys whose runs were lost in MAX_POOL_BREAKS broken pools (uncharged breaks included: at
    production depth a break holds several runs and charges none, so a target that always kills its worker would
    otherwise take its neighbours' work down every sweep), and return (the top ``max_targets`` remaining targets by
    priority, highest first; skipped count; gave-up count)."""
    todo, skipped, gave_up = [], 0, 0
    for t in _enumerate_targets(source, store, filter):
        key = make_job_key(t.target_id, det.name, version, ladder_version)
        if store.job_done(key): skipped += 1
        elif store.attempts(key) >= MAX_ATTEMPTS or store.pool_breaks(key) >= MAX_POOL_BREAKS: gave_up += 1
        else: todo.append((t, key))
    todo.sort(key=lambda x: priority_score(x[0]), reverse=True)     # stable: ties keep source order
    return (todo if max_targets is None else todo[:max_targets]), skipped, gave_up


def _fetch(source, t: Target) -> bytes:
    lc, _sha, _url = source.fetch_lightcurve(t)       # the adapter records the artifact itself
    return pickle.dumps(lc)


def _ignore_sigint() -> None:
    """Worker initializer: a terminal's Ctrl+C reaches every process in the foreground group, and a worker that died
    on it would break the pool and lose the target it was vetting. Only the scheduler decides what an interrupt means
    (graceful or immediate stop); the dashboard's hard stop terminates the scheduler, and orphaned workers exit on
    their own after their current target."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def terminate_workers(pool: ProcessPoolExecutor) -> None:
    """The hard stop: kill the pool's worker processes so shutdown() returns at once instead of after their current
    target (up to a few minutes of BLS). The workers ignore SIGINT, so a terminal's Ctrl+C alone does not end them."""
    for proc in list(getattr(pool, "_processes", {}).values()):     # the executor keeps no public handle on them
        try: proc.terminate()
        except OSError: pass


def cpu_pool(settings: Settings) -> ProcessPoolExecutor:
    """The CPU worker pool of a sweep (and of calibrate): ``workers_cpu`` spawned processes that ignore SIGINT."""
    # spawn everywhere (the Windows default): forking while the I/O threads run is unsafe on POSIX
    return ProcessPoolExecutor(settings.workers_cpu, mp_context=multiprocessing.get_context("spawn"),
                               initializer=_ignore_sigint)


_cpu_pool, _terminate_workers = cpu_pool, terminate_workers     # the former private names


def run(source, store: Store, blob: Blob, settings: Settings, filter: dict, detector_name: str = "box_dip",
        max_targets: int | None = None, progress=None, with_novelty: bool = True, fetch_module: str | None = None,
        stop_requested: Callable[[], bool] | None = None) -> dict:
    """Bounded two-pool scheduler: at most 2*workers_io fetches (fetched light curves waiting for a CPU slot
    included) and 2*workers_cpu CPU jobs are in flight, so memory and disk stay flat however long the sweep.

    ``stop_requested`` is polled between submissions (by default: does the stop file under the data directory
    exist). Once it answers True no further fetch is started; every target already fetched or being fetched is still
    vetted and written, and the summary carries ``"stopped": "graceful"``. Nothing is left 'running' for the next
    sweep to supersede. An exception (Ctrl+C a second time, a disk budget breach) is the hard stop: in-flight work is
    dropped and the interrupted runs are superseded when redone."""
    det = get_detector(detector_name)
    if stop_requested is None:
        stop_path = stop_request_path(settings.data_dir)
        stop_requested = stop_path.exists
    retention = Retention(RetentionPolicy.parse(settings.retention), store, blob)
    ladder_version = DEFAULT_LADDER_VERSION
    th_json = settings.thresholds.model_dump_json()
    version = search_version(det, settings.thresholds)
    todo, skipped, gave_up = _select_targets(source, store, filter, det, version, ladder_version, max_targets)
    summary = {"processed": 0, "skipped": skipped, "gave_up": gave_up, "errors": 0, "signals": 0, "pool_restarts": 0,
               "outcomes": {"pass": 0, "fail": 0, "undetermined": 0}}
    start = time.time()
    max_fetch, max_cpu = 2 * settings.workers_io, 2 * settings.workers_cpu
    pending = iter(todo)
    stopping = False
    fetches: dict[Future, tuple[Target, str]] = {}
    ready: deque[tuple[Target, int, bytes]] = deque()      # fetched, run started, waiting for a CPU slot
    cpu_jobs: dict[Future, tuple[Target, int]] = {}

    def fail(t: Target, rid: int | None, e: Exception, text: str | None = None) -> None:
        # an outage (MAST down, TLS failure) is recorded but never counted toward the target's three attempts
        if text is None: text = f"{UNAVAILABLE_PREFIX} {e}" if isinstance(e, ServiceUnavailable) else repr(e)
        if rid is not None: store.finish_run(rid, "error", text)
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

    def pool_broke(e: BrokenProcessPool, popped: dict[Future, tuple[Target, int]] | None = None) -> None:
        """A worker died and took the pool with it. Held jobs that finished before the break are written as usual;
        the rest fail. A lone unfinished run is charged an attempt (its target most likely killed the worker); several
        are all marked unavailable, uncharged, as the culprit is unknown. Either way the run counts toward
        MAX_POOL_BREAKS, so a target that always kills its worker is given up after three breaks. The pool is rebuilt MAX_POOL_RESTARTS times per sweep, then the break ends the sweep. Fetched
        targets in ``ready`` go to the new pool."""
        nonlocal workers
        held = {**(popped or {}), **cpu_jobs}
        cpu_jobs.clear()
        # settled before the break: a result, or the worker's own exception (vetted fails it with that error)
        settled = [f for f in held
                   if f.done() and not f.cancelled() and not isinstance(f.exception(), BrokenProcessPool)]
        lost = [job for f, job in held.items() if f not in settled]
        log.error("CPU worker pool broke (a worker process died): %d held runs lost, %d settled jobs kept",
                  len(lost), len(settled))
        text = WORKER_DIED_ERROR if len(lost) == 1 else POOL_BROKE_ERROR
        for t, rid in lost: fail(t, rid, e, text)
        for i, f in enumerate(settled):     # later settled targets have not pinned their evidence yet: never evict it
            vetted(f, *held[f], unwritten=frozenset(held[g][0].target_id for g in settled[i + 1:]))
        if summary["pool_restarts"] >= MAX_POOL_RESTARTS: raise e
        summary["pool_restarts"] += 1
        workers.shutdown(wait=True, cancel_futures=True)
        workers = cpu_pool(settings)

    def on_vetted(fut: Future) -> None:
        vetted(fut, *cpu_jobs.pop(fut))

    def vetted(fut: Future, t: Target, rid: int, unwritten: frozenset[str] = frozenset()) -> None:
        payload = None
        try:
            payload = fut.result()
            # one transaction: signals, tests, queries, periodogram row, evidence pins, finished run, target state
            counts = _write_results(store, blob, rid, t.target_id, payload)
        except BrokenProcessPool as e:
            pool_broke(e, {fut: (t, rid)}); return
        except Exception as e:
            log.exception("processing failed for %s", t.target_id)
            _salvage_queries(store, payload, t.target_id)
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
                     | {x[0].target_id for x in cpu_jobs.values()} | unwritten)
        retention.enforce_budget(exclude_targets=in_flight)   # raises DiskBudgetExceeded -> stops the run

    def top_up() -> None:
        nonlocal stopping
        # fetched targets always go on to the CPU pool: their run has started and the file is on disk
        while ready and len(cpu_jobs) < max_cpu:
            t, rid, lc_pickle = ready[0]
            try:
                fut = workers.submit(_cpu_work, lc_pickle, t.model_dump_json(), th_json, detector_name,
                                      ladder_version, with_novelty, fetch_module, settings.store_periodogram)
            except BrokenProcessPool as e:      # broke since the last result: this target stays first in line
                pool_broke(e); continue
            ready.popleft(); cpu_jobs[fut] = (t, rid)
        if not stopping and stop_requested():
            stopping = True
            summary["stopped"] = "graceful"
            log.warning("stop requested: finishing %d in-flight targets, starting no more",
                        len(fetches) + len(ready) + len(cpu_jobs))
        while not stopping and len(fetches) + len(ready) < max_fetch and (nxt := next(pending, None)) is not None:
            fetches[io_pool.submit(_fetch, source, nxt[0])] = nxt

    io_pool = ThreadPoolExecutor(settings.workers_io)
    workers = cpu_pool(settings)
    try:
        top_up()
        while fetches or cpu_jobs:
            done, _ = wait([*fetches, *cpu_jobs], return_when=FIRST_COMPLETED)
            for fut in done:
                if fut in fetches: on_fetched(fut)
                elif fut in cpu_jobs: on_vetted(fut)        # absent: a pool break in this batch already handled it
            done = fut = None                              # drop the last references to payloads and results
            top_up()
    except BaseException as e:
        # stop now: queued fetches and CPU jobs are dropped; their runs stay 'running' (superseded when redone)
        if isinstance(e, KeyboardInterrupt): terminate_workers(workers)     # "stop now" means now
        io_pool.shutdown(wait=True, cancel_futures=True); workers.shutdown(wait=True, cancel_futures=True)
        if isinstance(e, DiskBudgetExceeded):
            e.summary = {**summary, "seconds": time.time() - start}       # the CLI still reports the work done
        raise
    io_pool.shutdown(); workers.shutdown()
    summary["seconds"] = time.time() - start
    return summary
