"""Rerun rung 5 (the pixel rung) for named signals against a copy of the engine database, without writing anything.

Reads, from the database given by ``--db`` (opened read-only, ``mode=ro``): each signal row and its target row, built
as the recheck builds them, and the kept pixel files of the neighbour the rung tests against (``meta["tpf_org"]``):
the undeleted ``kind="tpf"`` artifact rows whose url lies under that Kepler target's archive directory
(``tpf_listing_url``), whichever target they were kept for, the latest row per url. Reads each file from ``--root``
joined with the row's ``local_path``. Writes nothing to the database or the blob store: the rung gets no blob and no
signal id, so it stores no ``pixels.npz``, and its fetch refuses every request, so nothing reaches the archive. A file
the blob store no longer holds counts as unreachable, so the rung reports an outage rather than a verdict on a
partial set. Prints, per signal, the status, the reason and one row per season; with ``--study`` (the hand-built
localisation's ``results.json``) the study's season values beside them for the signal whose target is the study's
star (the study calls our target "candidate" and our neighbour "target"); with ``--json`` the full metrics per
signal to that file, the only file it writes.

    python -m tools.pixels_rerun --db <copy.db> --root <repo root> --signal 1064 [--signal ...]
        [--study <results.json>] [--json <out.json>]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from skysift.config import Thresholds
from skysift.lc.loader import TARGET_FIELDS
from skysift.lc.tpf import TpfSet, parse_tpf, tpf_listing_url
from skysift.models import LightCurve, Signal, Target
from skysift.net import ServiceUnavailable
from skysift.store.db import SignalRow, Store, TargetRow
from skysift.vetting.base import DataBundle
from skysift.vetting.rung5_pixels import Rung5Pixels

COLUMNS = ("season", "quarters", "n_events", "voting", "box_t", "box_n", "missing_t", "sep_raw", "sep", "edge_bias",
           "cen_sig", "d_target", "d_neighb", "depth_target_ppm", "depth_neighbour_ppm", "non_voting_reason")
WIDTHS = (6, 9, 8, 6, 5, 5, 22, 8, 7, 9, 7, 8, 8, 16, 19, 0)


def read_only_store(db: str) -> Store:
    return Store("sqlite:///file:" + Path(db).resolve().as_posix() + "?mode=ro&uri=true")


def refuse(url, **_kw):
    raise ServiceUnavailable("network refused by pixels_rerun")


def load(store: Store, signal_id: int, root: Path) -> tuple[Signal, Target, TpfSet, int | None]:
    """The signal and its target as the recheck builds them, and the kept pixel files of the target's neighbour."""
    with store.session() as s:
        row = s.get(SignalRow, signal_id)
        if row is None:
            raise SystemExit(f"signal {signal_id} not in the database")
        signal = Signal(**{k: getattr(row, k) for k in Signal.model_fields})
        t = s.get(TargetRow, row.target_id)
        target = {**{k: getattr(t, k) for k in TARGET_FIELDS}, "meta": dict(t.meta or {})}
        kic = target["meta"].get("tpf_org") or target.get("kic")
    latest = store.kept_artifacts("tpf", tpf_listing_url(int(kic))) if kic else {}
    tpfs, missing = [], []
    for url, path in sorted(latest.items()):
        name = url.rsplit("/", 1)[-1]
        try:
            data = Path(root, path).read_bytes()
        except OSError:
            missing.append({"name": name, "cause": "unreachable"}); continue
        try:
            tpfs.append(parse_tpf(data, name))
        except ValueError:
            missing.append({"name": name, "cause": "unreadable"})
    tgt = Target(**{k: target[k] for k in TARGET_FIELDS if target.get(k) is not None}, meta=target["meta"])
    return signal, tgt, TpfSet(tpfs, missing), None if not kic else int(kic)


def bundle(target: Target) -> DataBundle:
    empty = np.array([], float)
    lc = LightCurve(time=empty, flux=empty, flux_err=empty, quality=np.array([], int), quarter=np.array([], int),
                    meta={})
    return DataBundle(lc=lc, fnorm=empty, target=target, thresholds=Thresholds(), store=None, fetch=refuse)


def _n(x, nd: int = 2) -> str:
    return "-" if x is None else f"{x:.{nd}f}"


def _pm(pair) -> str:
    d, e = pair
    return "-" if d is None else f"{d:.0f} +/- {_n(e, 0)}"


def season_row(s: dict) -> tuple:
    missing = ";".join(f"{c},{r}" for c, r in s.get("box_missing_target", [])) or "-"
    return (s["season"], ",".join(map(str, s["quarters"])), s["n_events"], "yes" if s["voting"] else "no",
            s.get("box_pixels_target", "-"), s.get("box_pixels_neighbour", "-"), missing,
            _n(s.get("separation_sigma_raw")), _n(s["separation_sigma"]), _n(s.get("edge_bias_pix"), 3),
            _n(s["centroid_sigma_pix"], 3), _n(s["dist_target_pix"], 3), _n(s["dist_neighbour_pix"], 3),
            _pm(s["depth_target_ppm"]), _pm(s["depth_neighbour_ppm"]), s["non_voting_reason"] or "-")


def study_row(x: dict) -> str:
    f = x["two_source_fit"]
    return (f"{'':6} study: d_target {x['snrbox_diff_centroid_dist_to_candidate_pix']:.3f}, d_neighb "
            f"{x['snrbox_diff_centroid_dist_to_target_pix']:.3f}, depth_target "
            f"{f['implied_depth_ppm_candidate']:.0f} +/- {f['implied_depth_ppm_candidate_err']:.0f}, depth_neighbour "
            f"{f['implied_depth_ppm_target']:.0f} +/- {f['implied_depth_ppm_target_err']:.0f}")


def same_star(target: Target, study: dict) -> bool:
    """Whether the study's candidate (its ``inputs.stars.candidate`` RA/Dec) is this target, within 1 arcsec."""
    ra, dec = study["inputs"]["stars"]["candidate"]
    return float(np.hypot((target.ra - ra) * np.cos(np.radians(dec)), target.dec - dec)) * 3600.0 < 1.0


def line(cells) -> str:
    return " ".join(str(c).ljust(w) for c, w in zip(cells, WIDTHS, strict=True)).rstrip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True, help="a copy of the engine database (opened read-only)")
    ap.add_argument("--root", required=True, help="the directory the artifact local paths are relative to")
    ap.add_argument("--signal", type=int, action="append", required=True, help="a signal id (repeatable)")
    ap.add_argument("--study", help="the hand-built localisation's results.json, printed beside each season")
    ap.add_argument("--json", help="write the full metrics per signal to this file")
    a = ap.parse_args(argv)
    store = read_only_store(a.db)
    study = json.loads(Path(a.study).read_text(encoding="utf-8")) if a.study else None
    out = {}
    for sid in a.signal:
        signal, target, tpfs, kic = load(store, sid, Path(a.root))
        beside = study["seasons"] if study and same_star(target, study) else {}
        r = Rung5Pixels(tpf_loader=lambda kic, _t=tpfs, **kw: _t, blob=None, signal_id=None).run(signal, bundle(target))
        out[str(sid)] = {"target_id": target.target_id, "neighbour_kic": kic, "status": r.status.value,
                         "reason": r.reason, "metrics": r.metrics}
        print(f"signal {sid} ({target.target_id}, neighbour KIC {kic}, {len(tpfs)} pixel files, "
              f"{len(tpfs.missing)} missing): {r.status.value}: {r.reason}")
        seasons = r.metrics.get("seasons", [])
        if seasons:
            print(line(COLUMNS))
        for s in seasons:
            print(line(season_row(s)))
            if str(s["season"]) in beside:
                print(study_row(beside[str(s["season"])]))
        print()
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1, allow_nan=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
