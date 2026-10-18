"""Diagnose injection misses: re-run chosen injections of inject_recovery.jsonl through the worker and print every
signal of both passes (period, SDE, depth, pass, alias_of) plus the per-band SDE of the spectrum nearest the injected
period, so the reason for a miss is visible (masked pass filtered it, below sde_min, or absent).

    .venv/Scripts/python.exe docs/superpowers/notes/scripts/inject_misses.py <engine.db> <inject_recovery.jsonl> <target_suffix:period> ...
"""
from __future__ import annotations

import json
import pickle
import sqlite3
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from inject_recovery import TH, inject  # noqa: E402
from skysift.lc.kbonus import parse_kbonus_fits  # noqa: E402
from skysift.models import Target  # noqa: E402
from skysift.pipeline import _cpu_work  # noqa: E402


def run(lc_pickle: bytes, target_json: str, inj: dict) -> str:
    spec_path = Path(tempfile.mkdtemp()) / "spec.npz"
    out = json.loads(_cpu_work(lc_pickle, target_json, TH.model_dump_json(), "box_dip", "1", False,
                               spectrum_path=str(spec_path)))
    lines = [f"== {inj['target_id']} injected P={inj['period']:.4f} depth={inj['depth_ppm']:.0f} ppm dur={inj['duration']*24:.1f} h snr~{inj['snr_target']:.0f}"]
    for item in out["signals"]:
        s = item["signal"]; e = s.get("extra") or {}
        lines.append(f"   signal P={s['period']:9.4f} sde={s['sde']:6.1f} depth={s['depth_ppm']:7.0f} dur={s['duration']*24:4.1f}h pass={e.get('pass', 1)} alias_of={e.get('alias_of')} "
                     f"masked={e.get('masked_period')} restored={len(((e.get('prewhitening') or {}).get('restored')) or [])} outcome={item['outcome']} last={item['tests'][-1]['rung']}:{item['tests'][-1].get('reason') or ''}")
    if spec_path.exists():
        z = np.load(spec_path)
        per, sde = z["period"], z["sde"]
        for r, label in ((1.0, "P"), (2.0, "2P"), (0.5, "P/2")):
            i = int(np.argmin(np.abs(per - inj["period"] * r)))
            win = np.abs(per / (inj["period"] * r) - 1) < 0.01
            lines.append(f"   first-pass spectrum near {label}: nearest trial {per[i]:.4f} d sde {sde[i]:.1f}; max within 1%: {sde[win].max() if win.any() else float('nan'):.1f}")
        if "sde2" in z.files:
            per2, sde2 = z["period2"], z["sde2"]
            i = int(np.argmin(np.abs(per2 - inj["period"])))
            lines.append(f"   masked-pass spectrum near P: {per2[i]:.4f} d sde {sde2[i]:.1f}; spectrum keys {z.files}")
        else:
            lines.append(f"   spectrum keys: {z.files}")
    return "\n".join(lines)


def main(db_path: str, jsonl: str, picks: list[str]) -> None:
    rows = [json.loads(l) for l in open(jsonl, encoding="utf-8")]
    c = sqlite3.connect(db_path); c.row_factory = sqlite3.Row
    jobs = []
    for pick in picks:
        suffix, period = pick.split(":")
        inj = min((r for r in rows if r["target_id"].endswith(suffix)), key=lambda r: abs(r["period"] - float(period)))
        t = c.execute("select * from targets where target_id=?", (inj["target_id"],)).fetchone()
        art = c.execute("select local_path from artifacts where target_id=? and kind='raw' and deleted_at is null order by created desc limit 1", (t["target_id"],)).fetchone()
        path = Path(art["local_path"].replace("\\", "/"))
        if not path.is_absolute(): path = ROOT / path
        lc = parse_kbonus_fits(path.read_bytes()); lc.meta["target_id"] = t["target_id"]
        target = Target(source=t["source"], target_id=t["target_id"], ra=t["ra"], dec=t["dec"], gaia_id=t["gaia_id"], kic=t["kic"], gmag=t["gmag"])
        jobs.append((pickle.dumps(inject(lc, inj["period"], inj["t0"], inj["duration"], inj["depth_ppm"])), target.model_dump_json(), inj))
    with ProcessPoolExecutor(max_workers=min(8, len(jobs))) as pool:
        for text in pool.map(run, *zip(*jobs)):
            print(text, flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3:])
