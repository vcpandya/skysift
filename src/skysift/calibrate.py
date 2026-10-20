"""Null calibration of the detection threshold (the 1000-star pilot). For each of the highest-priority targets the
detrended, pre-whitened flux is searched as it is, inverted and scrambled within each quarter (noise only), with the
raw first-pass statistics (no masked pass, no alias resolution) and the sweep's restore of a detection's whitened
harmonics (``pipeline.search_with_restore``). Inverting (2 - f) turns dips into bumps and keeps
systematics and variability; the detector clips upward outliers only, so the former dips are removed while former
flares and upward ramps become dips: the inverted search is a "systematics as dips" null. One JSON line per target
holds the top three SDEs of each search; ``summarise`` turns a file into the distribution ``sde_min`` is read from,
and ``false_alarm`` into the false-alarm probability a dossier prints for a candidate's SDE (the add-one rule).
Lines carry the search version, so a file is tied to the code that wrote it."""
from __future__ import annotations

import json
import logging
import math
import pickle
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, wait
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sqlalchemy import select

from skysift import jsonutil
from skysift.config import Settings, Thresholds
from skysift.detectors.box_dip import BoxDip
from skysift.models import LightCurve, Target
from skysift.pipeline import (
    cpu_pool,
    ranked_targets,
    search_version,
    search_with_restore,
    terminate_workers,
    whitened_flux,
)
from skysift.store.db import ArtifactRow, Store
from skysift.store.retention import Retention, RetentionPolicy

log = logging.getLogger(__name__)
KINDS = ("real", "inverted", "scrambled")
NULLS = ("scrambled", "inverted")
SDE_LEVELS = (7, 8, 9, 10, 11, 12, 15)
TOP_N = 3
SCRAMBLE_SEED = 1
MIN_NULLS = 200                     # null searches needed before a false-alarm probability is shown (spec 4.1)


class VersionMismatch(ValueError):
    """The output file holds lines written by another search version; appending would mix two detectors."""


def calibration_detector() -> BoxDip:
    """The sweep's detector without the masked second pass and the half-period alias resolution: the raw spectrum
    statistics, which is what rung 1 thresholds."""
    return BoxDip(mask_iterations=0, alias_resolution=0)


def default_path(data_dir: Path, source_name: str) -> Path:
    return Path(data_dir) / "calibration" / f"{source_name}.jsonl"


def scramble_within_quarters(fnorm: np.ndarray, quarter: np.ndarray, seed: int = SCRAMBLE_SEED) -> np.ndarray:
    """Permute the finite flux values within each quarter (NaNs stay where they are): any periodic signal is destroyed,
    the point scatter and each quarter's noise level are kept."""
    rng = np.random.default_rng(seed)
    out = np.array(fnorm, float, copy=True)
    finite = np.isfinite(out)
    for q in np.unique(quarter):
        idx = np.flatnonzero((quarter == q) & finite)
        out[idx] = fnorm[rng.permutation(idx)]
    return out


def _inverted_record(record: dict) -> dict:
    """The pre-whitening record of the inverted flux: 2 - (detrend - model) is (2 - detrend) minus the negated model,
    so the same components with their coefficients negated, and a restore puts the waves back the right way up."""
    return {**record, "components": [{**c, "coef": [-float(x) for x in c["coef"]]}
                                     for c in record.get("components") or []]}


def search_target(lc_pickle: bytes, thresholds_json: str, nulls: tuple[str, ...]) -> dict:
    """Runs in a worker process. Detrend and pre-whiten with the sweep's own ``whitened_flux``, then search the real
    flux and each null through the sweep's ``search_with_restore`` (a detection whose harmonics were whitened away is
    searched again with them put back); returns the line without its target id and version. Each flux brings its own
    record: the real one, its sign-flipped copy for the inverted flux, and no components for the scrambled flux (a
    permutation of the residual holds no removed waves, as pre-whitening noise removes none). The pickle comes only
    from this package's own main process (``run``), never from outside data."""
    start = time.perf_counter()
    lc: LightCurve = pickle.loads(lc_pickle)
    th = Thresholds.model_validate_json(thresholds_json)
    fn, record = whitened_flux(lc, th)[:2]
    flux = {"real": (fn, record)}
    if "inverted" in nulls: flux["inverted"] = (2.0 - fn, _inverted_record(record))
    if "scrambled" in nulls:
        flux["scrambled"] = (scramble_within_quarters(fn, lc.quarter), {**record, "n": 0, "components": []})
    det = calibration_detector()
    row: dict = {"n_points": int(np.isfinite(fn).sum())}
    for kind, (f, rec) in flux.items():
        # alias resolution and the masked pass are off, so the signals are the top_k separated peaks in SDE order
        signals = search_with_restore(det, lc, f, th, rec)[0].signals[:TOP_N]
        row[kind] = [float(s.sde) for s in signals]
        if kind == "real":
            s = signals[0] if signals else None
            row["real_top"] = None if s is None else {"period": s.period, "duration": s.duration,
                                                      "depth_ppm": s.depth_ppm, "sde": s.sde, "snr": s.snr}
    row["seconds"] = round(time.perf_counter() - start, 3)
    return row


def read_rows(path: Path) -> list[dict]:
    """Every parseable line of a calibration file. A line cut short by a hard kill is skipped with a warning (``run``
    removes it and searches its target again)."""
    rows = []
    for i, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip(): continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            log.warning("%s line %d is not valid JSON; skipped", path, i)
    return rows


def _drop_cut_tail(path: Path) -> None:
    """A hard kill while a line was being written leaves the file ending mid-line: cut it back to the last complete
    line, so the file holds valid lines only and the target is searched again."""
    data = path.read_bytes()
    if not data or data.endswith(b"\n"): return
    keep = data.rfind(b"\n") + 1
    log.warning("%s ends with a line cut short; removing it (%d bytes) so its target is searched again", path,
                len(data) - keep)
    with path.open("r+b") as fh: fh.truncate(keep)


def _cached_raw(store: Store, target_id: str) -> list[tuple[str, str]]:
    """(sha256, local path) of the target's live raw files, newest first."""
    with store.session() as s:
        rows = s.execute(select(ArtifactRow.sha256, ArtifactRow.local_path).where(
            ArtifactRow.target_id == target_id, ArtifactRow.kind == "raw", ArtifactRow.deleted_at.is_(None))
            .order_by(ArtifactRow.created.desc())).all()
    return [(r[0], r[1]) for r in rows]


def _load(source, store: Store, blob, t: Target, parse: Callable[[bytes], LightCurve] | None) -> tuple[LightCurve, bool]:
    """The target's light curve and whether it came from the blob: a raw file still on disk is parsed (the adapters
    always download), otherwise the adapter fetches and records it as in a sweep."""
    if parse is not None:
        for sha, loc in _cached_raw(store, t.target_id):
            if not blob.exists(loc): continue
            try:
                lc = parse(blob.get(loc))
            except Exception:                   # an unreadable kept file: download a fresh copy instead
                log.warning("stored raw file %s of %s does not parse; fetching it again", sha[:12], t.target_id,
                            exc_info=True)
                break
            store.touch_artifact(sha)
            lc.meta.setdefault("target_id", t.target_id)
            return lc, True
    lc, _sha, _url = source.fetch_lightcurve(t)
    return lc, False


def run(source, store: Store, blob, settings: Settings, filter: dict, limit: int | None, out: Path,
        nulls: tuple[str, ...] = NULLS, parse: Callable[[bytes], LightCurve] | None = None,
        progress: Callable[[dict], None] | None = None) -> dict:
    """Search the ``limit`` highest-priority targets (the order of ``run``) and append one line per target to ``out``,
    written and flushed as soon as the target is done. Targets already in the file are skipped, so the same command
    resumes. At most 2 * workers_cpu targets are fetched and waiting or searching at once; raw files follow the
    retention policy as in a sweep. Raises VersionMismatch when the file holds lines of another search version."""
    det = calibration_detector()
    th = settings.thresholds
    version = search_version(det, th)
    out = Path(out)
    if out.exists(): _drop_cut_tail(out)
    rows = read_rows(out) if out.exists() else []
    other = sorted({str(r.get("search_version")) for r in rows} - {version})
    if other:
        raise VersionMismatch(f"{out} holds lines of search version {', '.join(other)}; this run is {version}. "
                              "Pass --out with a new file (or move the old one) so the two detectors are not mixed.")
    done = {r.get("target_id") for r in rows}
    targets = ranked_targets(source, store, filter, limit)
    todo = iter([t for t in targets if t.target_id not in done])
    summary = {"search_version": version, "targets": len(targets), "skipped": sum(t.target_id in done for t in targets),
               "written": 0, "errors": 0, "from_cache": 0}
    retention = Retention(RetentionPolicy.parse(settings.retention), store, blob)
    th_json = th.model_dump_json()
    out.parent.mkdir(parents=True, exist_ok=True)
    start = time.time()
    pool = cpu_pool(settings)
    jobs: dict = {}

    def top_up() -> None:
        while len(jobs) < 2 * settings.workers_cpu and (t := next(todo, None)) is not None:
            try:
                lc, cached = _load(source, store, blob, t, parse)
                lc_pickle = pickle.dumps(lc)
            except Exception:
                log.warning("could not load %s; a later run retries it", t.target_id, exc_info=True)
                summary["errors"] += 1
                retention.release_target(t.target_id)    # a download that failed after storing its file frees it
                continue
            summary["from_cache"] += cached
            # the curve is in memory now: a file this run downloaded goes (stream) as in a sweep; a file served from
            # the blob was kept by someone (an earlier sweep, a --retention keep pilot) and stays
            if not cached: retention.release_target(t.target_id)
            jobs[pool.submit(search_target, lc_pickle, th_json, tuple(nulls))] = t
            # budget: evict least recently used files, never those of targets still being searched (as the sweep)
            retention.enforce_budget(exclude_targets={x.target_id for x in jobs.values()})

    with out.open("a", encoding="utf-8", newline="\n") as fh:
        def write(fut, t: Target) -> None:
            try:
                row = fut.result()
            except Exception:
                log.exception("search failed for %s; a later run retries it", t.target_id)
                summary["errors"] += 1
                return
            fh.write(jsonutil.dumps({"target_id": t.target_id, "search_version": version, **row}) + "\n")
            fh.flush()
            summary["written"] += 1
            if progress: progress(summary)

        try:
            top_up()
            while jobs:
                # a timeout keeps the main thread returning to Python, so Ctrl+C is seen at once on Windows too
                finished, _ = wait(jobs, timeout=1.0, return_when=FIRST_COMPLETED)
                for fut in finished:
                    write(fut, jobs.pop(fut))
                top_up()
        except BaseException:
            # Ctrl+C, a disk budget breach, a broken pool: keep what already finished, then stop the workers at once
            # rather than wait minutes for searches whose results would be dropped; the same command resumes
            for fut, t in list(jobs.items()):
                if fut.done() and not fut.cancelled(): write(fut, t)
            terminate_workers(pool)                   # the workers ignore SIGINT: terminate them
            pool.shutdown(wait=True, cancel_futures=True)
            raise
    pool.shutdown()
    summary["seconds"] = round(time.time() - start, 1)
    return summary


def _top(row: dict, kind: str) -> float | None:
    """The top SDE of one search; a None (a non-finite SDE written as null) is ignored."""
    values = [float(v) for v in (row.get(kind) or []) if v is not None]
    return max(values) if values else None


@dataclass(frozen=True)
class FalseAlarm:
    """How often noise alone gave a top peak at least as strong as the candidate's, from the null searches."""
    k: int                      # null searches with top SDE >= sde
    n: int                      # null searches counted (both null kinds, rows with a finite top only)
    fap: float | None           # (k + 1) / (n + 1); None when n < MIN_NULLS
    calibrated: bool            # n >= MIN_NULLS
    version_matches: bool       # every row's search_version == version
    versions: tuple[str, ...]   # the search versions seen, sorted

    def one_in(self) -> int | None:
        """1 / fap rounded to two significant figures, and to a whole number below 10 (1 / 0.55 is 2, not 1.8);
        None when not calibrated."""
        if not self.calibrated or self.fap is None: return None
        x = 1.0 / self.fap
        return int(round(x, min(0, 1 - math.floor(math.log10(x)))))

    def words(self) -> str:
        """The tile's text: "1 in 1,700", "not yet calibrated" or "calibration is for an older search"."""
        if not self.calibrated: return "not yet calibrated"
        if not self.version_matches: return "calibration is for an older search"
        return f"1 in {self.one_in():,}"


def false_alarm(rows: list[dict], sde: float, version: str) -> FalseAlarm:
    """The false-alarm probability of a peak of ``sde`` against the null searches (each kind in NULLS of every row):
    ``k`` of the ``n`` finite null tops sit at or above it, and FAP = (k + 1) / (n + 1), the add-one rule, so a peak
    above every null reads as "1 in n + 1", never zero. Rows of another search version are still counted; the result
    notes the mismatch. Below MIN_NULLS null searches the FAP is None."""
    tops = [v for r in rows for kind in NULLS if (v := _top(r, kind)) is not None and math.isfinite(v)]
    n = len(tops)
    k = sum(v >= sde for v in tops)
    calibrated = n >= MIN_NULLS
    versions = tuple(sorted({str(r.get("search_version")) for r in rows}))
    return FalseAlarm(k=k, n=n, fap=(k + 1) / (n + 1) if calibrated else None, calibrated=calibrated,
                      version_matches=all(str(r.get("search_version")) == version for r in rows), versions=versions)


def summarise(rows: list[dict], sde_min: float, all_versions: bool = False) -> dict:
    """Per kind: how many curves gave a peak, the median and maximum top SDE and how many top SDEs sit at or above each
    of SDE_LEVELS (at or above, as rung 1 passes ``sde >= sde_min``); plus how many real curves would clear rung 1's
    SDE test. Rows with no real peak (too few points) are counted as ``empty``. Strict JSON. Raises ValueError when the
    rows come from more than one search version, unless ``all_versions``."""
    versions = sorted({str(r.get("search_version")) for r in rows})
    if len(versions) > 1 and not all_versions:
        raise ValueError(f"the rows come from {len(versions)} search versions ({', '.join(versions)}); "
                         "pass --all-versions to pool them anyway")
    kinds = {}
    for kind in KINDS:
        tops = [v for r in rows if (v := _top(r, kind)) is not None]
        kinds[kind] = {"n": len(tops), "median": float(np.median(tops)) if tops else None,
                       "max": max(tops) if tops else None,
                       "at_or_above": {str(level): sum(v >= level for v in tops) for level in SDE_LEVELS}}
    real = [v for r in rows if (v := _top(r, "real")) is not None]
    passing = sum(v >= sde_min for v in real)
    return {"search_versions": versions, "rows": len(rows), "empty": sum(_top(r, "real") is None for r in rows),
            "sde_min": float(sde_min), "kinds": kinds,
            "real_at_or_above_sde_min": {"count": passing, "n": len(real),
                                         "fraction": passing / len(real) if real else None}}


def format_table(summary: dict) -> str:
    """The summary as a plain-text table, one line per kind."""
    def num(v): return "-" if v is None else f"{v:.2f}"
    head = f"{'kind':<10}{'n':>6}{'median':>8}{'max':>8}" + "".join(f"{'>=' + str(x):>6}" for x in SDE_LEVELS)
    title = (f"search version {', '.join(summary['search_versions'])}; {summary['rows']} curves "
             f"({summary['empty']} too short to search); top SDE per curve, counts at or above each level")
    lines = [title, "", head]
    for kind, k in summary["kinds"].items():
        lines.append(f"{kind:<10}{k['n']:>6}{num(k['median']):>8}{num(k['max']):>8}"
                     + "".join(f"{k['at_or_above'][str(x)]:>6}" for x in SDE_LEVELS))
    p = summary["real_at_or_above_sde_min"]
    frac = "-" if p["fraction"] is None else f"{p['fraction']:.1%}"
    lines += ["", f"real curves at or above sde_min {summary['sde_min']:g}: {p['count']} of {p['n']} ({frac})"]
    return "\n".join(lines)
