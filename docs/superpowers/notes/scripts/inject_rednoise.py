"""Re-express the injection-recovery results against the red noise the search actually faces. For each injected
transit of inject_recovery.jsonl: bin the detrended, pre-whitened flux of its star in windows one transit duration
wide, take the robust scatter of the bin means (the noise a single box of that width sees, red and white together),
and form SNR_red = depth x sqrt(N_transits) / scatter. Completeness is then tabulated against SNR_red.

    .venv/Scripts/python.exe docs/superpowers/notes/scripts/inject_rednoise.py <engine.db> <inject_recovery.jsonl>
"""
from __future__ import annotations

import json
import sqlite3
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from inject_recovery import TH  # noqa: E402
from skysift.lc.kbonus import parse_kbonus_fits  # noqa: E402
from skysift.lc.ops import detrend  # noqa: E402
from skysift.lc.prewhiten import prewhiten  # noqa: E402


def binned_scatter(t, y, width_days):
    good = np.isfinite(y)
    t, y = t[good], y[good]
    idx = ((t - t.min()) // width_days).astype(int)
    sums, counts = np.bincount(idx, weights=y), np.bincount(idx)
    full = counts >= max(3, int(0.6 * width_days / np.median(np.diff(t))))
    means = sums[full] / counts[full]
    return 1.4826 * float(np.median(np.abs(means - np.median(means)))) * 1e6


def star_noise(path: str, durations: list[float]) -> dict:
    lc = parse_kbonus_fits(Path(path).read_bytes())
    fn = detrend(lc)
    pw = prewhiten(lc.time, fn, max_components=TH.prewhiten_max_components, fap_max=TH.prewhiten_fap)
    y = pw.residual
    white = 1.4826 * float(np.nanmedian(np.abs(y - np.nanmedian(y)))) * 1e6
    cad = float(np.median(np.diff(lc.time[np.isfinite(y)])))
    out = {"white_ppm": white, "cadence_d": cad, "baseline_d": float(np.nanmax(lc.time) - np.nanmin(lc.time)),
           "n_good": int(np.isfinite(y).sum()), "scatter": {}}
    for d in sorted(set(round(x, 4) for x in durations)):
        out["scatter"][d] = {"red_ppm": binned_scatter(lc.time, y, d), "white_bin_ppm": white / np.sqrt(max(1.0, d / cad))}
    return out


def main(db_path: str, jsonl: str) -> None:
    rows = [json.loads(line) for line in open(jsonl, encoding="utf-8")]
    c = sqlite3.connect(db_path); c.row_factory = sqlite3.Row
    by_star: dict[str, list[float]] = {}
    for r in rows:
        by_star.setdefault(r["target_id"], []).append(r["duration"])
    paths = {}
    for tid in by_star:
        art = c.execute("select local_path from artifacts where target_id=? and kind='raw' and deleted_at is null order by created desc limit 1", (tid,)).fetchone()
        p = Path(art["local_path"].replace("\\", "/"))
        paths[tid] = str(p if p.is_absolute() else ROOT / p)
    with ProcessPoolExecutor(max_workers=8) as pool:
        noise = dict(zip(by_star, pool.map(star_noise, [paths[t] for t in by_star], [by_star[t] for t in by_star])))
    excess = []
    for r in rows:
        n = noise[r["target_id"]]
        sc = n["scatter"][round(r["duration"], 4)]
        n_tr = n["n_good"] * n["cadence_d"] / r["period"]          # transits covered by data
        r["snr_red"] = r["depth_ppm"] * np.sqrt(n_tr) / sc["red_ppm"]
        r["snr_white_check"] = r["depth_ppm"] * np.sqrt(n_tr) / sc["white_bin_ppm"]
        r["red_excess"] = sc["red_ppm"] / sc["white_bin_ppm"]
        excess.append(r["red_excess"])
    print(f"red-noise excess on transit timescales (scatter of duration-wide bins / white expectation): "
          f"median {np.median(excess):.2f}, 10-90%: {np.percentile(excess, 10):.2f}-{np.percentile(excess, 90):.2f}")
    print(f"\n{'SNR_red bin':>12} {'n':>3} {'found':>6} {'pass rungs 1-3':>14} {'median SDE of found':>20}")
    for lo, hi in ((0, 5), (5, 7), (7, 9), (9, 11), (11, 14), (14, 20), (20, 1e9)):
        sub = [r for r in rows if lo <= r["snr_red"] < hi]
        if not sub: continue
        f = [r for r in sub if r["found"]]
        ok = [r for r in f if r["hit"]["outcome"] == "pass"]
        sdes = [r["hit"]["sde"] for r in f]
        print(f"{lo:5.0f}-{hi if hi < 1e8 else 99:<6.0f} {len(sub):3d} {len(f):6d} {len(ok):14d} {np.median(sdes) if sdes else float('nan'):20.1f}")
    print("\nby period, SNR_red >= 9:")
    for lo, hi in ((0.8, 3), (3, 10), (10, 25.1)):
        sub = [r for r in rows if r["snr_red"] >= 9 and lo <= r["period"] < hi]
        f = [r for r in sub if r["found"]]
        print(f"  P {lo:4.1f}-{hi:4.1f} d: n={len(sub):2d} found={len(f):2d}")
    with open(jsonl.replace(".jsonl", "_rednoise.jsonl"), "w", encoding="utf-8") as fh:
        for r in rows: fh.write(json.dumps(r) + "\n")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
