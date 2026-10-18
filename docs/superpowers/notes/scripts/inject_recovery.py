"""Injection and recovery on real KBonus light curves (18 October 2026).

For every cached raw light curve of a sweep database, inject four box transits at expected stacked signal-to-noise
7, 10, 15 and 25 (period log-uniform in 0.8 to 25 d, avoiding the star's own peaks; a solar-ish duration; a random
epoch), run the production worker (detrend, pre-whiten, BoxDip, rungs 1 to 3; rung 4 off) and record whether the
injected period comes back, at what depth and SDE, and which rung it reaches. The summary answers three
production questions: how complete the search is against expected SNR, whether the measured depth is biased, and
whether the vetting rungs reject real box transits (rung 2 "sinusoidal", rung 3 season tests).

    SKYSIFT_DATABASE_URL and friends are not needed: the script reads the SQLite file and the cached FITS directly.
    .venv/Scripts/python.exe docs/superpowers/notes/scripts/inject_recovery.py <engine.db> <out.jsonl> [workers]
"""
from __future__ import annotations

import json
import pickle
import sqlite3
import sys
import time as clock
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from skysift.config import Thresholds  # noqa: E402
from skysift.lc.kbonus import parse_kbonus_fits  # noqa: E402
from skysift.lc.ops import detrend  # noqa: E402
from skysift.models import LightCurve, Target  # noqa: E402
from skysift.pipeline import _cpu_work  # noqa: E402

SNR_TARGETS = (7.0, 10.0, 15.0, 25.0)
P_MIN, P_MAX, DEPTH_CAP = 0.8, 25.0, 5000.0
TH = Thresholds(min_period_days=0.5, max_period_days=30.0)


def duration_days(period: float) -> float:
    """A solar-ish central transit: 13 h at one year, scaling as P^(1/3), never under 1.5 h."""
    return max(1.5 / 24.0, 13.0 / 24.0 * (period / 365.25) ** (1 / 3))


def inject(lc: LightCurve, period, t0, duration, depth_ppm) -> LightCurve:
    ph = (lc.time - t0 + 0.5 * period) % period - 0.5 * period
    inside = np.abs(ph) < 0.5 * duration
    f = lc.flux.copy(); f[inside] *= 1 - depth_ppm * 1e-6
    alt = None
    if lc.flux_alt is not None:
        alt = lc.flux_alt.copy(); alt[inside] *= 1 - depth_ppm * 1e-6
    return LightCurve(lc.time, f, lc.flux_err, lc.quality, lc.quarter, alt, dict(lc.meta))


def plan_injections(lc: LightCurve, avoid: list[float], seed: int) -> list[dict]:
    rng = np.random.default_rng(seed)
    fn = detrend(lc)
    good = np.isfinite(fn)
    sigma = 1.4826 * float(np.median(np.abs(fn[good] - np.median(fn[good]))))
    n_good = int(good.sum())
    t_start = float(np.nanmin(lc.time[good]))
    out = []
    for snr in SNR_TARGETS:
        for _ in range(50):
            p = float(np.exp(rng.uniform(np.log(P_MIN), np.log(P_MAX))))
            if all(abs(p / a - r) > 0.03 * r for a in avoid for r in (0.5, 1.0, 2.0, 3.0, 1 / 3)):
                break
        dur = duration_days(p)
        n_in = n_good * dur / p
        depth = min(DEPTH_CAP, snr * sigma * 1e6 / np.sqrt(n_in))
        out.append({"snr_target": snr, "period": p, "duration": dur, "depth_ppm": float(depth),
                    "t0": t_start + float(rng.uniform(0, p)), "sigma_ppm": sigma * 1e6, "n_in": float(n_in)})
    return out


def run_one(lc_pickle: bytes, target_json: str, inj: dict) -> dict:
    t = clock.time()
    out = json.loads(_cpu_work(lc_pickle, target_json, TH.model_dump_json(), "box_dip", "1", False))
    items = out["signals"]
    p = inj["period"]
    def ratio_match(q):
        for r in (1.0, 2.0, 0.5, 3.0, 1 / 3):
            if abs(q / (p * r) - 1) < 0.01:
                return r
        return None
    hit = None
    for rank, item in enumerate(items):
        r = ratio_match(item["signal"]["period"])
        if r is not None:
            s = item["signal"]
            hit = {"rank": rank, "ratio": r, "period": s["period"], "depth_ppm": s["depth_ppm"], "sde": s["sde"],
                   "snr": s["snr"], "n_events": s["n_events"], "outcome": item["outcome"],
                   "rungs": [(x["rung"], x["status"], x.get("reason") or "") for x in item["tests"]],
                   "alias_resolved": bool((s.get("extra") or {}).get("alias_resolved")),
                   "restored": len(((s.get("extra") or {}).get("prewhitening") or {}).get("restored") or [])}
            break
    top = items[0]["signal"] if items else None
    return {**inj, "found": hit is not None, "hit": hit,
            "top": {"period": top["period"], "sde": top["sde"], "depth_ppm": top["depth_ppm"]} if top else None,
            "seconds": clock.time() - t}


def main(db_path: str, out_path: str, workers: int = 8) -> None:
    c = sqlite3.connect(db_path); c.row_factory = sqlite3.Row
    version = c.execute("select detector_version from runs order by started desc limit 1").fetchone()[0]
    targets = c.execute("select t.* from targets t where exists (select 1 from runs r where r.target_id=t.target_id and r.detector_version=?)", (version,)).fetchall()
    jobs = []
    for k, t in enumerate(targets):
        art = c.execute("select local_path from artifacts where target_id=? and kind='raw' and deleted_at is null order by created desc limit 1", (t["target_id"],)).fetchone()
        if art is None:
            continue
        path = Path(art["local_path"].replace("\\", "/"))
        if not path.is_absolute():
            path = ROOT / path
        lc = parse_kbonus_fits(path.read_bytes())
        lc.meta["target_id"] = t["target_id"]
        avoid = [r[0] for r in c.execute("select s.period from signals s join runs r on r.id=s.run_id where r.detector_version=? and s.target_id=?", (version, t["target_id"]))]
        target = Target(source=t["source"], target_id=t["target_id"], ra=t["ra"], dec=t["dec"], gaia_id=t["gaia_id"], kic=t["kic"], gmag=t["gmag"])
        for inj in plan_injections(lc, avoid, seed=1000 + k):
            jobs.append((pickle.dumps(inject(lc, inj["period"], inj["t0"], inj["duration"], inj["depth_ppm"])),
                         target.model_dump_json(), {**inj, "target_id": t["target_id"]}))
    print(f"{len(targets)} targets, {len(jobs)} injections, version {version}, {workers} workers", flush=True)
    done = 0
    with open(out_path, "w", encoding="utf-8") as fh, ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run_one, *j) for j in jobs]
        for fut in as_completed(futures):
            row = fut.result()
            fh.write(json.dumps(row) + "\n"); fh.flush()
            done += 1
            h = row["hit"]
            print(f"[{done}/{len(jobs)}] {row['target_id'][-8:]} P={row['period']:6.2f} depth={row['depth_ppm']:6.0f} snr={row['snr_target']:4.0f} "
                  f"-> {'FOUND' if h else 'miss '} " + (f"rank {h['rank']} x{h['ratio']:.2f} sde {h['sde']:5.1f} depth {h['depth_ppm']:5.0f} {h['outcome']} {h['rungs'][-1][0]}:{h['rungs'][-1][2]}" if h else f"top P={row['top']['period']:.3f} sde={row['top']['sde']:.1f}") + f" ({row['seconds']:.0f}s)", flush=True)


def summarise(out_path: str) -> None:
    rows = [json.loads(line) for line in open(out_path, encoding="utf-8")]
    print(f"\n{len(rows)} injections")
    print(f"{'snr':>4} {'n':>3} {'found':>6} {'ranked 1st':>10} {'aliased':>7} {'depth ratio (median)':>21} {'rung2 fail':>10} {'rung3 fail':>10} {'pass':>5}")
    for snr in SNR_TARGETS:
        sub = [r for r in rows if r["snr_target"] == snr]
        hits = [r for r in sub if r["found"]]
        ratios = [r["hit"]["depth_ppm"] / r["depth_ppm"] for r in hits if r["hit"]["ratio"] == 1.0]
        r2 = sum(1 for r in hits if any(x[0].startswith("rung2") and x[1] == "fail" for x in r["hit"]["rungs"]))
        r3 = sum(1 for r in hits if any(x[0].startswith("rung3") and x[1] == "fail" for x in r["hit"]["rungs"]))
        ok = sum(1 for r in hits if r["hit"]["outcome"] == "pass")
        print(f"{snr:4.0f} {len(sub):3d} {len(hits):6d} {sum(1 for r in hits if r['hit']['rank'] == 0):10d} "
              f"{sum(1 for r in hits if r['hit']['ratio'] != 1.0):7d} {np.median(ratios) if ratios else float('nan'):21.2f} {r2:10d} {r3:10d} {ok:5d}")
    print("\nrung 2 / rung 3 failure reasons among recovered injections:")
    reasons = {}
    for r in rows:
        if r["found"]:
            for rung, status, reason in r["hit"]["rungs"]:
                if status == "fail":
                    reasons[(rung, reason.split(" (")[0])] = reasons.get((rung, reason.split(" (")[0]), 0) + 1
    for k, v in sorted(reasons.items(), key=lambda kv: -kv[1]):
        print(f"  {v:3d}  {k[0]} {k[1]}")
    print("\nmisses:")
    for r in rows:
        if not r["found"]:
            print(f"  {r['target_id']} P={r['period']:.3f} depth={r['depth_ppm']:.0f} snr={r['snr_target']:.0f} top={r['top']}")


if __name__ == "__main__":
    if sys.argv[1] == "summary":
        summarise(sys.argv[2])
    else:
        main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 8)
        summarise(sys.argv[2])
