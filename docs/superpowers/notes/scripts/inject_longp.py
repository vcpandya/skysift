"""Why do long-period injections of nominal SNR 15 come out below SDE 9? For each listed injection of
inject_recovery.jsonl, detrend with three gap-edge masks (none, the production 1.5 d, 3.0 d), pre-whiten, run the
first-pass BoxDip spectrum (no masked pass, no alias resolution) and report the SDE within 1 percent of the injected
period, the band's SDE scatter, and the point scatter on 4 h timescales (the red-noise level a box of that width sees).

    .venv/Scripts/python.exe docs/superpowers/notes/scripts/inject_longp.py <engine.db> <inject_recovery.jsonl> <target_suffix:period> ...
"""
from __future__ import annotations

import json
import pickle
import sqlite3
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from inject_recovery import TH, inject  # noqa: E402
from skysift.detectors.box_dip import BoxDip  # noqa: E402
from skysift.lc.kbonus import parse_kbonus_fits  # noqa: E402
from skysift.lc.ops import detrend  # noqa: E402
from skysift.lc.prewhiten import prewhiten  # noqa: E402

MASKS = (0.0, 1.5, 3.0)


def binned_scatter(t, y, width_days):
    good = np.isfinite(y)
    t, y = t[good], y[good]
    idx = ((t - t.min()) // width_days).astype(int)
    sums = np.bincount(idx, weights=y); counts = np.bincount(idx)
    means = sums[counts >= 4] / counts[counts >= 4]
    return 1.4826 * float(np.median(np.abs(means - np.median(means)))) * 1e6


def run(lc_pickle: bytes, inj: dict) -> str:
    lc = pickle.loads(lc_pickle)   # the pickle is made by this script's own main process a moment earlier, never read from outside
    det = BoxDip(mask_iterations=0, alias_resolution=0)
    lines = [f"== {inj['target_id']} injected P={inj['period']:.3f} depth={inj['depth_ppm']:.0f} dur={inj['duration']*24:.1f} h nominal snr {inj['snr_target']:.0f}"]
    for mask in MASKS:
        fn = detrend(lc, edge_mask_days=mask)
        pw = prewhiten(lc.time, fn, max_components=TH.prewhiten_max_components, fap_max=TH.prewhiten_fap)
        res = det.search(lc, pw.residual, TH, keep_spectrum=True)
        per, sde = np.asarray(res.spectrum["period"]), np.asarray(res.spectrum["sde"])
        win = np.abs(per / inj["period"] - 1) < 0.01
        top = res.signals[0]
        lines.append(f"   mask {mask:3.1f} d: points {int(np.isfinite(pw.residual).sum()):6d}  SDE at P {sde[win].max():5.1f}  "
                     f"top {top.period:7.3f} d sde {top.sde:5.1f}  scatter/point {1.4826*np.nanmedian(np.abs(pw.residual-np.nanmedian(pw.residual)))*1e6:5.0f} ppm  "
                     f"4h-bin scatter {binned_scatter(lc.time, pw.residual, 4/24):4.0f} ppm  1d-bin {binned_scatter(lc.time, pw.residual, 1.0):4.0f} ppm")
    return "\n".join(lines)


def main(db_path: str, jsonl: str, picks: list[str]) -> None:
    rows = [json.loads(l) for l in open(jsonl, encoding="utf-8")]
    c = sqlite3.connect(db_path); c.row_factory = sqlite3.Row
    jobs = []
    for pick in picks:
        suffix, period = pick.split(":")
        inj = min((r for r in rows if r["target_id"].endswith(suffix)), key=lambda r: abs(r["period"] - float(period)))
        art = c.execute("select local_path from artifacts where target_id=? and kind='raw' and deleted_at is null order by created desc limit 1", (inj["target_id"],)).fetchone()
        path = Path(art["local_path"].replace("\\", "/"))
        if not path.is_absolute(): path = ROOT / path
        lc = parse_kbonus_fits(path.read_bytes()); lc.meta["target_id"] = inj["target_id"]
        jobs.append((pickle.dumps(inject(lc, inj["period"], inj["t0"], inj["duration"], inj["depth_ppm"])), inj))
    with ProcessPoolExecutor(max_workers=min(8, len(jobs))) as pool:
        for text in pool.map(run, *zip(*jobs)):
            print(text, flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3:])
