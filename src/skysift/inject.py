"""Injection and recovery as a standing regression: how complete is the search, and how many recovered box transits
survive vetting. For each of the highest-priority targets, box transits at set nominal signal-to-noise (7, 10, 15, 25)
are injected into the cached raw light curve (the period log-uniform in 0.8 to 25 d, clear of the star's own peaks, a
solar-ish duration, a random epoch) and the production worker (``pipeline._cpu_work``: detrend, pre-whiten, BoxDip, rungs
1 to 3, rung 4 off) searches the result. A line per injection records whether its period came back (itself or its
2, 1/2, 3, 1/3 multiple), whether it ranked first, and what the ladder made of it. Two signal-to-noise figures are kept:
the nominal one the depth was set from (white noise of the detrended flux) and the red-noise one, which uses the
scatter of duration-wide bins of the pre-whitened flux, the noise a box of that width actually sees. This turns the
one-off scripts of 18 October 2026 (docs/superpowers/notes/scripts/inject_recovery.py and inject_rednoise.py) into
``skysift inject`` and ``skysift inject-report``. Lines carry the search and ladder versions, so a file is tied to the
code that wrote it."""
from __future__ import annotations

import json
import logging
import pickle
import time
import zlib
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, wait
from pathlib import Path

import numpy as np

from skysift import jsonutil
from skysift.calibrate import VersionMismatch, _drop_cut_tail, _load, read_rows
from skysift.config import Settings, Thresholds
from skysift.detectors.base import get_detector
from skysift.lc.ops import detrend
from skysift.models import LightCurve, Target
from skysift.pipeline import (
    DEFAULT_LADDER_VERSION,
    _cpu_work,
    cpu_pool,
    ranked_targets,
    search_version,
    search_with_restore,
    terminate_workers,
    whitened_flux,
)
from skysift.store.retention import Retention, RetentionPolicy

log = logging.getLogger(__name__)
SNR_DEFAULT = (7.0, 10.0, 15.0, 25.0)
P_MIN, P_MAX = 0.8, 25.0              # injected periods (days), cut to the search window and the baseline
DEPTH_CAP_PPM = 5000.0                # a cap keeps a quiet star from getting a depth no small planet has
MATCH_TOL = 0.01                      # a returned period within 1 percent of the injected one, or of a multiple, is found
RATIOS = (1.0, 2.0, 0.5, 3.0, 1.0 / 3.0)
AVOID_TOL = 0.02                      # an injected period stays this far (relative) from the star's own peaks and multiples
RED_BINS = ((0.0, 5.0, "0-5"), (5.0, 7.0, "5-7"), (7.0, 9.0, "7-9"), (9.0, 11.0, "9-11"), (11.0, 14.0, "11-14"),
            (14.0, 20.0, "14-20"), (20.0, float("inf"), "20+"))
PERIOD_BINS = ((0.0, 3.0, "0.8-3"), (3.0, 10.0, "3-10"), (10.0, 25.0, "10-25"), (25.0, float("inf"), "25+"))


def default_path(data_dir: Path, source_name: str) -> Path:
    return Path(data_dir) / "injections" / f"{source_name}.jsonl"


def duration_days(period: float) -> float:
    """A solar-ish central transit: 13 h at one year, scaling as P^(1/3), never under 1.5 h."""
    return max(1.5 / 24.0, 13.0 / 24.0 * (period / 365.25) ** (1 / 3))


def inject_box(lc: LightCurve, period: float, t0: float, duration: float, depth_ppm: float) -> LightCurve:
    """A copy of ``lc`` with a box transit multiplied into the primary and the secondary flux."""
    ph = (lc.time - t0 + 0.5 * period) % period - 0.5 * period
    inside = np.abs(ph) < 0.5 * duration
    scale = 1 - depth_ppm * 1e-6
    flux = lc.flux.copy(); flux[inside] *= scale
    alt = None
    if lc.flux_alt is not None:
        alt = lc.flux_alt.copy(); alt[inside] *= scale
    return LightCurve(lc.time, flux, lc.flux_err, lc.quality, lc.quarter, alt, dict(lc.meta))


def plan_injections(lc: LightCurve, th: Thresholds, rng: np.random.Generator, per_star: int,
                    snr_targets: tuple[float, ...], own_periods: list[float]) -> list[dict]:
    """``per_star`` injections, the nominal SNR cycling through ``snr_targets``. The period is log-uniform over 0.8 to
    25 d cut to the search window and to ``baseline / min_events`` (at least that many transits fit), and stays 2
    percent clear of each own period and its 2x, 1/2, 3x and 1/3 multiples (50 draws, then the last one is kept). The
    depth gives the nominal SNR ``depth * sqrt(n_in) / sigma``: sigma is the robust scatter of the detrended flux and
    n_in the good points inside the transits; the depth is capped at 5000 ppm (so a quiet star's nominal SNR is
    then lower than asked). Raises ValueError when no period fits."""
    fn = detrend(lc)
    good = np.isfinite(fn)
    if good.sum() < 10: raise ValueError("too few good points to plan injections")
    sigma = 1.4826 * float(np.median(np.abs(fn[good] - np.median(fn[good]))))
    t_good = lc.time[good]
    t_start = float(t_good.min())
    baseline = float(t_good.max() - t_start)
    p_lo = max(P_MIN, th.min_period_days)
    p_hi = min(P_MAX, th.max_period_days, baseline / max(1, th.min_events))
    if not p_lo < p_hi:
        raise ValueError(f"no injection period fits the search window {th.min_period_days:g} to "
                         f"{th.max_period_days:g} d and a {baseline:.0f} d baseline")
    out = []
    for i in range(per_star):
        snr = float(snr_targets[i % len(snr_targets)])
        for _ in range(50):
            p = float(np.exp(rng.uniform(np.log(p_lo), np.log(p_hi))))
            if all(abs(p / a - r) > AVOID_TOL * r for a in own_periods if a > 0 for r in (0.5, 1.0, 2.0, 3.0, 1 / 3)):
                break
        dur = duration_days(p)
        n_in = good.sum() * dur / p
        depth = min(DEPTH_CAP_PPM, snr * sigma * 1e6 / np.sqrt(n_in))
        out.append({"period": p, "t0": t_start + float(rng.uniform(0, p)), "duration": dur, "depth_ppm": float(depth),
                    "snr_nominal": snr})
    return out


def red_noise_snr(time: np.ndarray, flux_whitened: np.ndarray, period: float, duration: float,
                  depth_ppm: float) -> float:
    """``depth * sqrt(N_transits) / scatter``: the scatter is the robust (MAD) scatter of the means of bins one transit
    duration wide of the pre-whitened flux (bins with too few points are dropped), the noise a single box of that width
    sees, red and white together. N_transits is the span the good points cover divided by the period. NaN when too few
    complete bins exist or the scatter is zero."""
    t = np.asarray(time, float); y = np.asarray(flux_whitened, float)
    good = np.isfinite(t) & np.isfinite(y)
    t, y = t[good], y[good]
    if t.size < 10: return float("nan")
    cadence = float(np.median(np.diff(t)))
    if not cadence > 0: return float("nan")
    idx = ((t - t.min()) // duration).astype(int)
    sums, counts = np.bincount(idx, weights=y), np.bincount(idx)
    full = counts >= max(3, int(0.6 * duration / cadence))
    if full.sum() < 3: return float("nan")
    means = sums[full] / counts[full]
    scatter_ppm = 1.4826 * float(np.median(np.abs(means - np.median(means)))) * 1e6
    if not scatter_ppm > 0: return float("nan")
    return float(depth_ppm * np.sqrt(t.size * cadence / period) / scatter_ppm)


def _ratio_match(found: float, injected: float) -> float | None:
    for r in RATIOS:
        if abs(found / (injected * r) - 1) < MATCH_TOL:
            return r
    return None


def score(injection: dict, signals_with_results: list[dict]) -> dict:
    """Find the injection among the worker's signals (``_cpu_work``'s ``signals`` list, in rank order): the first whose
    period is within 1 percent of the injected period or of its 2, 1/2, 3 or 1/3 multiple. Returns ``found``,
    ``ranked_first`` (found, and it is the top signal) and ``hit`` (rank, ratio to the injected period, period, depth,
    SDE, SNR, events, ladder outcome, the rung results as [rung, status, reason], whether the alias was resolved, how
    many whitened components were restored) or None."""
    for rank, item in enumerate(signals_with_results):
        sig = item["signal"]
        ratio = _ratio_match(float(sig["period"]), float(injection["period"]))
        if ratio is None: continue
        extra = sig.get("extra") or {}
        hit = {"rank": rank, "ratio": ratio, "period": sig["period"], "depth_ppm": sig["depth_ppm"], "sde": sig["sde"],
               "snr": sig.get("snr"), "n_events": sig.get("n_events"), "outcome": item["outcome"],
               "rungs": [[x["rung"], x["status"], x.get("reason") or ""] for x in item["tests"]],
               "alias_resolved": bool(extra.get("alias_resolved")),
               "restored": len((extra.get("prewhitening") or {}).get("restored") or [])}
        return {"found": True, "ranked_first": rank == 0, "hit": hit}
    return {"found": False, "ranked_first": False, "hit": None}


def inject_star(lc_pickle: bytes, target_json: str, thresholds_json: str, ladder_version: str, per_star: int,
                snr_targets: tuple[float, ...], seed: int) -> list[dict]:
    """Runs in a worker process. Whiten the star's real flux and search it once (``search_with_restore``, as the sweep)
    for its own peaks, plan the injections, and for each one run the production worker ``_cpu_work`` on the injected curve
    (rung 4 off) and score it. Returns the lines without the target id and the versions. The random stream depends on the
    seed and the target id, so a target gives the same injections whenever it is run. The pickle comes only from this
    package's own main process (``run``), never from outside data."""
    lc: LightCurve = pickle.loads(lc_pickle)
    target = Target.model_validate_json(target_json)
    th = Thresholds.model_validate_json(thresholds_json)
    lc.meta.setdefault("target_id", target.target_id)
    fn, record = whitened_flux(lc, th)[:2]
    own = [float(s.period) for s in search_with_restore(get_detector("box_dip"), lc, fn, th, record)[0].signals]
    rng = np.random.default_rng([seed, zlib.crc32(target.target_id.encode())])
    rows = []
    for inj in plan_injections(lc, th, rng, per_star, snr_targets, own):
        injected = inject_box(lc, inj["period"], inj["t0"], inj["duration"], inj["depth_ppm"])
        out = json.loads(_cpu_work(pickle.dumps(injected), target_json, thresholds_json, "box_dip", ladder_version, False))
        rows.append({"period": inj["period"], "t0": inj["t0"], "duration": inj["duration"], "depth_ppm": inj["depth_ppm"],
                     "snr_nominal": inj["snr_nominal"],
                     "snr_red": red_noise_snr(lc.time, fn, inj["period"], inj["duration"], inj["depth_ppm"]),
                     **score(inj, out["signals"])})
    return rows


def run(source, store, blob, settings: Settings, filter: dict, limit: int | None, per_star: int,
        snr_targets: tuple[float, ...], out: Path, workers_cpu: int, seed: int = 1,
        parse: Callable[[bytes], LightCurve] | None = None, ladder_version: str = DEFAULT_LADDER_VERSION,
        progress: Callable[[dict], None] | None = None) -> dict:
    """Inject into the ``limit`` highest-priority targets (the order of ``pipeline.run``) and append one line per
    injection to ``out`` (a target's lines together, as soon as it is done). Targets already in the file are skipped, so
    the same command resumes. The raw curve comes from ``calibrate._load`` (a kept file is parsed, otherwise the adapter
    fetches it and the file follows the retention policy as in a sweep). At most 2 * workers_cpu targets are loaded and
    waiting or running at once. Raises VersionMismatch when the file holds lines of another search or ladder version."""
    th = settings.thresholds
    version = search_version(get_detector("box_dip"), th)
    out = Path(out)
    if out.exists(): _drop_cut_tail(out)
    rows = read_rows(out) if out.exists() else []
    other = sorted({f"{r.get('search_version')} / ladder {r.get('ladder_version')}" for r in rows}
                   - {f"{version} / ladder {ladder_version}"})
    if other:
        raise VersionMismatch(f"{out} holds lines of {', '.join(other)}; this run is {version} / ladder {ladder_version}. "
                              "Pass --out with a new file (or move the old one) so the two versions are not mixed.")
    done = {r.get("target_id") for r in rows}
    targets = ranked_targets(source, store, filter, limit)
    todo = iter([t for t in targets if t.target_id not in done])
    summary = {"search_version": version, "ladder_version": ladder_version, "targets": len(targets),
               "skipped": sum(t.target_id in done for t in targets), "written": 0, "errors": 0, "from_cache": 0}
    retention = Retention(RetentionPolicy.parse(settings.retention), store, blob)
    th_json = th.model_dump_json()
    out.parent.mkdir(parents=True, exist_ok=True)
    start = time.time()
    pool = cpu_pool(settings.model_copy(update={"workers_cpu": workers_cpu}))
    jobs: dict = {}

    def top_up() -> None:
        while len(jobs) < 2 * workers_cpu and (t := next(todo, None)) is not None:
            try:
                lc, cached = _load(source, store, blob, t, parse)
                lc_pickle = pickle.dumps(lc)
            except Exception:
                log.warning("could not load %s; a later run retries it", t.target_id, exc_info=True)
                summary["errors"] += 1
                retention.release_target(t.target_id)
                continue
            summary["from_cache"] += cached
            if not cached: retention.release_target(t.target_id)
            jobs[pool.submit(inject_star, lc_pickle, t.model_dump_json(), th_json, ladder_version, per_star,
                             tuple(snr_targets), seed)] = t
            retention.enforce_budget(exclude_targets={x.target_id for x in jobs.values()})

    with out.open("a", encoding="utf-8", newline="\n") as fh:
        def write(fut, t: Target) -> None:
            try:
                star_rows = fut.result()
            except Exception:
                log.exception("injection failed for %s; a later run retries it", t.target_id)
                summary["errors"] += 1
                return
            fh.write("".join(jsonutil.dumps({"target_id": t.target_id, "search_version": version,
                                             "ladder_version": ladder_version, **r}) + "\n" for r in star_rows))
            fh.flush()
            summary["written"] += len(star_rows)
            if progress: progress(summary)

        try:
            top_up()
            while jobs:
                finished, _ = wait(jobs, timeout=1.0, return_when=FIRST_COMPLETED)
                for fut in finished:
                    write(fut, jobs.pop(fut))
                top_up()
        except BaseException:
            for fut, t in list(jobs.items()):
                if fut.done() and not fut.cancelled(): write(fut, t)
            terminate_workers(pool)
            pool.shutdown(wait=True, cancel_futures=True)
            raise
    pool.shutdown()
    summary["seconds"] = round(time.time() - start, 1)
    return summary


def _group(rows: list[dict], label: str) -> dict:
    found = [r for r in rows if r.get("found")]
    ratios = [r["hit"]["depth_ppm"] / r["depth_ppm"] for r in found
              if r["hit"].get("ratio") == 1.0 and r.get("depth_ppm")]
    return {"label": label, "n": len(rows), "found": len(found),
            "ranked_first": sum(bool(r.get("ranked_first")) for r in found),
            "passed": sum(r["hit"].get("outcome") == "pass" for r in found),
            "median_depth_ratio": float(np.median(ratios)) if ratios else None}


def _binned(rows: list[dict], key: str, bins) -> list[dict]:
    out = []
    for lo, hi, label in bins:
        sub = [r for r in rows if r.get(key) is not None and lo <= float(r[key]) < hi]
        out.append(_group(sub, label))
    return out


def summarise(rows: list[dict], all_versions: bool = False) -> dict:
    """Found, ranked first and passing rungs 1 to 3 (outcome ``pass``) against the nominal SNR (one group per target
    value), the red-noise SNR (0-5, 5-7, 7-9, 9-11, 11-14, 14-20, 20+) and the period (0.8-3, 3-10, 10-25, 25+ d), and
    the median ratio of the recovered depth to the injected one over the injections found at their own period.
    Injections with no red-noise SNR are left out of that table. Strict JSON. Raises ValueError when the rows come from
    more than one search or ladder version, unless ``all_versions``."""
    versions = sorted({str(r.get("search_version")) for r in rows})
    ladders = sorted({str(r.get("ladder_version")) for r in rows})
    if (len(versions) > 1 or len(ladders) > 1) and not all_versions:
        raise ValueError(f"the rows come from several versions (search {', '.join(versions)}; ladder "
                         f"{', '.join(ladders)}); pass --all-versions to pool them anyway")
    nominal = sorted({float(r["snr_nominal"]) for r in rows})
    return {"search_versions": versions, "ladder_versions": ladders, "rows": len(rows),
            "overall": _group(rows, "all"),
            "by_nominal": [_group([r for r in rows if float(r["snr_nominal"]) == v], f"{v:g}") for v in nominal],
            "by_red": _binned(rows, "snr_red", RED_BINS), "by_period": _binned(rows, "period", PERIOD_BINS),
            "median_depth_ratio": _group(rows, "all")["median_depth_ratio"]}


def format_table(summary: dict) -> str:
    """The summary as plain-text tables: nominal SNR, red-noise SNR and period, one line per group."""
    def pct(k, n): return "-" if not n else f"{k / n:.0%}"
    def ratio(v): return "-" if v is None else f"{v:.2f}"
    head = f"{{:<14}}{'n':>5}{'found':>7}{'found %':>9}{'1st':>6}{'pass':>6}{'pass %':>8}{'depth':>8}"
    lines = [(f"search version {', '.join(summary['search_versions'])}, ladder {', '.join(summary['ladder_versions'])}; "
              f"{summary['rows']} injections; found = the injected period or its 2, 1/2, 3, 1/3 multiple returned; "
              "1st = ranked first; pass = outcome pass (rungs 1 to 3); depth = median recovered / injected")]
    for title, key in (("nominal SNR", "by_nominal"), ("red-noise SNR", "by_red"), ("period (d)", "by_period")):
        lines += ["", head.format(title)]
        for g in summary[key]:
            if not g["n"]: continue
            lines.append(f"{g['label']:<14}{g['n']:>5}{g['found']:>7}{pct(g['found'], g['n']):>9}{g['ranked_first']:>6}"
                         f"{g['passed']:>6}{pct(g['passed'], g['n']):>8}{ratio(g['median_depth_ratio']):>8}")
    o = summary["overall"]
    lines += ["", (f"all: {o['n']} injections, {o['found']} found ({pct(o['found'], o['n'])}), {o['ranked_first']} ranked "
                  f"first, {o['passed']} pass; median depth ratio {ratio(summary['median_depth_ratio'])}")]
    return "\n".join(lines)
