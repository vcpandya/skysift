"""Measure the systematic floor of the pixel rung's centroid error on the kept pixel files (spec 2026-10-22 referee
pack, section 5), reading a copy of the engine database and writing nothing to it.

What it measures, per star with a rung 5 row and per archive season, on the season frame the rung builds
(``season_images``): each usable quarter's out-of-transit mean image (the direct image of the cadences around the
transits, as the season builder averages it) is placed on the frame at the quarter's integer offset, and the 3 x 3
flux-weighted centroid is taken around the target's rounded frame position, and the same around the neighbour's. A
quarter whose own pixels do not hold the star's complete box gives no centroid for that star. The scatter of the
quarters' centroids about the season mean is the quarter-to-quarter systematic the Monte Carlo error cannot see
(pointing differences between the quarters of one season, PRF changes, the integer co-registration). The floor is
the pooled rms per axis over the seasons with both boxes complete on the frame and at least two quarters
(``pooled_floor``); the neighbour's scatter is pooled the same way as a check, and the larger of the two (the
quadratic mean of the two axes) is the value adopted (``adopted``): the target's box also holds the bright
neighbour's light, which damps its response to a pointing shift, while the difference image the rung centroids holds
the target's light alone. The registration residual (the event-weighted mean of the quarters' fractional offsets,
``residual_offsets_pix``) is reported apart.

With ``--validate`` (a signal id; KIC 6146676 in the note) it also measures, per voting season, the difference-image
S/N-box centroid of each quarter with its own Monte Carlo error, and the scatter of those centroids beyond their
errors (``excess_scatter``): the excess the floor must cover.

    python -m tools.centroid_floor --db <copy.db> --root <repo root> --out <out.json> [--validate 1032]

``--root`` is the directory the artifact rows' ``local_path`` is relative to. The pooled numbers and the per-season
table are printed; the constant in ``skysift.lc.pixels`` (``CENTROID_FLOOR_PIX``, with ``CENTROID_FLOOR_MEASURED``)
is changed by hand.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
from sqlalchemy import select

from skysift.lc.pixels import (
    EVENT_IN_HALF,
    EVENT_OUT_HI,
    EVENT_OUT_LO,
    RNG_SEED,
    Ephemeris,
    QuarterData,
    _box_centroid,
    _place_quarter,
    _quarter_events,
    box_coverage,
    localise_quarters,
    season_images,
    snr_box_centroid,
)
from skysift.store.db import SignalRow, TestRow
from tools.pixels_rerun import load, read_only_store

QUARTERS_MIN = 2            # a season needs this many quarters with a centroid to enter the pooled number


def _frames(quarters: list[QuarterData], eph: Ephemeris):
    """Per season image of ``season_images``, the season's quarters on its frame: for each quarter (in the image's
    order) its direct image, valid mask, stacked difference image and variance on the frame, and its event count.
    The events and the placement are the season builder's own (``_quarter_events`` with the rung's windows,
    ``_place_quarter`` at the image's ``quarter_shifts``), each quarter stacked on its own."""
    by_q = {int(qd.quarter): qd for qd in quarters}
    out = []
    for im in season_images(quarters, eph):
        shape = im.valid.shape
        placed = []
        for q, (sc, sr) in zip(im.quarters, im.quarter_shifts, strict=True):
            events, valid, direct = _quarter_events(by_q[q], eph, EVENT_IN_HALF, EVENT_OUT_LO, EVENT_OUT_HI)
            num, wsum = np.zeros(shape), np.zeros(shape)
            v, direct_frame = _place_quarter(events, valid, direct, shape, sc, sr, num, wsum)
            ok = wsum > 0
            placed.append({"quarter": q, "direct": np.where(v, direct_frame, np.nan),
                           "valid": v, "diff": np.where(ok, num / np.where(ok, wsum, 1.0), 0.0),
                           "var": np.where(ok, 1.0 / np.where(ok, wsum, 1.0), np.inf), "n_events": len(events)})
        out.append((im, placed))
    return out


def _star_centroid(direct: np.ndarray, valid: np.ndarray, pos) -> list[float] | None:
    """The 3 x 3 flux-weighted centroid around the rounded (col, row) position, as (dcol, drow) from the position;
    None when the quarter's own pixels do not hold the complete box."""
    if not box_coverage(pos, valid).complete:
        return None
    c, r = _box_centroid(direct, valid, round(pos[1]), round(pos[0]))
    if not (math.isfinite(c) and math.isfinite(r)):
        return None
    return [float(c - pos[0]), float(r - pos[1])]


def season_quarter_centroids(quarters: list[QuarterData], eph: Ephemeris) -> list[dict]:
    """One row per season frame: ``season``, ``quarters`` (usable, in frame order), ``box_complete`` (both stars'
    3 x 3 boxes complete on the frame), per quarter the ``target`` and ``neighbour`` direct-image centroids as
    (dcol, drow) from the star's frame position (None where that quarter's pixels cut the box), ``n_events`` and
    ``residual_offsets_pix``, the event-weighted ``registration_mean`` of the residuals, and ``n_quarters``, the
    quarters with a target centroid."""
    rows = []
    for im, placed in _frames(quarters, eph):
        target = [_star_centroid(p["direct"], p["valid"], im.pos_target) for p in placed]
        neighbour = [_star_centroid(p["direct"], p["valid"], im.pos_neighbour) for p in placed]
        w = np.array([p["n_events"] for p in placed], float)
        res = np.array(im.residual_offsets_pix, float).reshape(-1, 2)
        reg = (w[:, None] * res).sum(axis=0) / w.sum()
        rows.append({"season": int(im.season), "quarters": [int(q) for q in im.quarters],
                     "box_complete": bool(im.box_target.complete and im.box_neighbour.complete),
                     "target": target, "neighbour": neighbour, "n_events": [int(x) for x in w],
                     "residual_offsets_pix": [[float(a), float(b)] for a, b in res],
                     "registration_mean": [float(reg[0]), float(reg[1])],
                     "n_quarters": sum(1 for t in target if t is not None)})
    return rows


def _deviations(points) -> np.ndarray:
    """(dcol, drow) of the measured points about their mean, an (n, 2) array (n may be 0)."""
    pts = np.array([p for p in points if p is not None], float).reshape(-1, 2)
    return pts - pts.mean(axis=0) if len(pts) else pts


def _rms(dev: np.ndarray) -> list[float] | None:
    """Per-axis rms about the mean with one degree of freedom spent on it; None below two points."""
    if len(dev) < 2:
        return None
    return [float(x) for x in np.sqrt((dev ** 2).sum(axis=0) / (len(dev) - 1))]


def _pool(devs: list[np.ndarray]) -> dict:
    """Pooled per-axis rms over seasons: the squared deviations summed over all of them, over the summed degrees of
    freedom (each season's points minus one), and the quadratic mean of the two axes."""
    devs = [d for d in devs if len(d) >= 2]
    dof = sum(len(d) - 1 for d in devs)
    if not dof:
        return {"rms_col": None, "rms_row": None, "rms_axis_mean": None, "dof": 0}
    ss = sum((d ** 2).sum(axis=0) for d in devs)
    col, row = (float(math.sqrt(x / dof)) for x in ss)
    return {"rms_col": col, "rms_row": row, "rms_axis_mean": float(math.sqrt(0.5 * (col ** 2 + row ** 2))), "dof": dof}


def pooled_floor(rows: list[dict]) -> dict:
    """The floor from ``season_quarter_centroids`` rows (of one star or many; extra keys such as ``star`` are carried
    into ``per_season``): the pooled per-axis rms of the target's quarter centroids (``rms_col``, ``rms_row`` and
    their quadratic mean ``rms_axis_mean``) over the seasons with a complete box and at least QUARTERS_MIN quarters,
    the same for the neighbour (``neighbour``), the larger of the two quadratic means (``adopted``, from
    ``adopted_from``), and every season's own numbers with ``pooled`` and ``why`` ("cut box", "one quarter") for those
    left out."""
    per, t_devs, n_devs = [], [], []
    for r in rows:
        why = None if r["box_complete"] else "cut box"
        if why is None and r["n_quarters"] < QUARTERS_MIN:
            why = "one quarter"
        dt, dn = _deviations(r["target"]), _deviations(r["neighbour"])
        if why is None:
            t_devs.append(dt); n_devs.append(dn)
        per.append({**{k: v for k, v in r.items() if k not in ("target", "neighbour", "residual_offsets_pix")},
                    "target_rms": _rms(dt), "neighbour_rms": _rms(dn), "pooled": why is None, "why": why})
    target, neighbour = _pool(t_devs), _pool(n_devs)
    means = [(m, who) for m, who in ((target["rms_axis_mean"], "target"), (neighbour["rms_axis_mean"], "neighbour"))
             if m is not None]
    adopted, adopted_from = max(means) if means else (None, None)
    return {"n_seasons": len(t_devs), "n_quarters": int(sum(len(d) for d in t_devs)),
            "rms_col": target["rms_col"], "rms_row": target["rms_row"], "rms_axis_mean": target["rms_axis_mean"],
            "neighbour": neighbour, "adopted": adopted, "adopted_from": adopted_from, "per_season": per}


def quarter_diff_centroids(quarters: list[QuarterData], eph: Ephemeris, seed: int = RNG_SEED) -> dict[int, list]:
    """Per season, each quarter's difference-image S/N-box centroid on the season frame (``snr_box_centroid`` on
    that quarter's events alone) with its Monte Carlo error: {season: [{"quarter", "centroid", "sigma_mc"}]}."""
    rng = np.random.default_rng(seed)
    out: dict[int, list] = {}
    for im, placed in _frames(quarters, eph):
        rows = []
        for p in placed:
            (c, r), sig, _snr, _cov = snr_box_centroid(p["diff"], p["var"], p["valid"] & np.isfinite(p["var"]), rng)
            if math.isfinite(c) and math.isfinite(r) and math.isfinite(sig):
                rows.append({"quarter": p["quarter"], "centroid": [float(c), float(r)], "sigma_mc": float(sig)})
        out[int(im.season)] = rows
    return out


def excess_scatter(seasons_metrics: list[dict], per_quarter_diff_centroids: dict) -> dict:
    """The validation: per voting season (``voting`` true in the rung's season metrics) with complete boxes (a cut
    box, ``box_partial``, is cut differently by each quarter's own stamp) and at least two quarters, the scatter of
    the quarters' difference-image centroids about their inverse-variance weighted mean (per axis, over n - 1), the
    quarters' mean Monte Carlo variance, and the excess, sqrt(max(0, scatter^2 - mean sigma^2)); pooled over the
    seasons the same way (``excess_col``, ``excess_row``, ``excess_axis_mean``). At the season level, the empirical
    error of the season's centroid (``sem``, the scatter over sqrt(n)) beside the season's own Monte Carlo error
    (``centroid_sigma_mc_pix``, else ``centroid_sigma_pix``) and the excess of the first over the second
    (``season_excess``; ``season_excess_max`` over seasons and axes)."""
    per, ss, dof, var_sum, n_q = [], np.zeros(2), 0, 0.0, 0
    for s in seasons_metrics:
        rows = per_quarter_diff_centroids.get(s["season"], [])
        if s.get("voting") is not True or s.get("box_partial") is True or len(rows) < 2:
            continue
        pts = np.array([r["centroid"] for r in rows], float)
        sig2 = np.array([r["sigma_mc"] for r in rows], float) ** 2
        mean = (pts / sig2[:, None]).sum(axis=0) / (1.0 / sig2).sum()
        dev2 = ((pts - mean) ** 2).sum(axis=0)
        scatter2 = dev2 / (len(rows) - 1)
        excess = [float(math.sqrt(max(0.0, x - sig2.mean()))) for x in scatter2]
        sem = [float(math.sqrt(x / len(rows))) for x in scatter2]
        mc = s.get("centroid_sigma_mc_pix", s.get("centroid_sigma_pix"))
        per.append({"season": s["season"], "quarters": [r["quarter"] for r in rows],
                    "scatter_rms": [float(math.sqrt(x)) for x in scatter2],
                    "quarter_sigma_mc_rms": float(math.sqrt(sig2.mean())), "excess": excess,
                    "chi2_dof": [float(x / sig2.mean()) for x in scatter2], "season_sigma_mc": mc, "sem": sem,
                    "season_excess": None if mc is None else [float(math.sqrt(max(0.0, x ** 2 - mc ** 2)))
                                                              for x in sem]})
        ss += dev2; dof += len(rows) - 1; var_sum += float(sig2.sum()); n_q += len(rows)
    season_max = max((x for p in per if p["season_excess"] for x in p["season_excess"]), default=None)
    if not dof:
        return {"n_seasons": 0, "n_quarters": 0, "excess_col": None, "excess_row": None, "excess_axis_mean": None,
                "season_excess_max": None, "per_season": per}
    col, row = (float(math.sqrt(max(0.0, x / dof - var_sum / n_q))) for x in ss)
    return {"n_seasons": len(per), "n_quarters": n_q, "excess_col": col, "excess_row": row,
            "excess_axis_mean": float(math.sqrt(0.5 * (col ** 2 + row ** 2))), "season_excess_max": season_max,
            "per_season": per}


# ------------------------------------------------------------------------------------------------- the live run

def quarters_of(store, signal_id: int, root: Path) -> tuple[dict, list[QuarterData], Ephemeris]:
    """The signal's star, its neighbour's kept pixel files as ``QuarterData`` (placed as ``Rung5Pixels.run`` places
    them) and its ephemeris."""
    signal, target, tpfs, kic = load(store, signal_id, root)
    n_ra, n_dec = target.meta.get("tpf_org_ra"), target.meta.get("tpf_org_dec")
    quarters = []
    for tpf in tpfs:
        ra, dec = (n_ra, n_dec) if n_ra is not None and n_dec is not None else (tpf.ra_obj, tpf.dec_obj)
        quarters.append(QuarterData(quarter=int(tpf.quarter), cube=tpf.cube, time=tpf.time, quality=tpf.quality,
                                    pos_target=tpf.pixel_of(target.ra, target.dec), pos_neighbour=tpf.pixel_of(ra, dec),
                                    season=tpf.season))
    star = {"target_id": target.target_id, "neighbour_kic": kic, "signal_id": signal_id, "n_tpf": len(tpfs),
            "n_missing": len(tpfs.missing)}
    return star, quarters, Ephemeris(float(signal.period), float(signal.t0), float(signal.duration))


def rung5_rows(store) -> dict[str, list[dict]]:
    """Per target, the latest rung 5 row of each of its signals that localised (seasons in its metrics):
    [{"signal_id", "status", "reason", "voting_seasons"}], by signal id."""
    with store.session() as s:
        rows = s.execute(select(TestRow.id, TestRow.signal_id, TestRow.status, TestRow.reason, TestRow.metrics,
                                SignalRow.target_id).join(SignalRow, SignalRow.id == TestRow.signal_id)
                         .where(TestRow.rung == "rung5_pixels").order_by(TestRow.id)).all()
    latest = {}
    for r in rows:
        latest[r.signal_id] = r
    out: dict[str, list[dict]] = {}
    for sid in sorted(latest):
        r = latest[sid]
        seasons = (r.metrics or {}).get("seasons") if isinstance(r.metrics, dict) else None
        if not isinstance(seasons, list):
            continue
        out.setdefault(r.target_id, []).append({
            "signal_id": sid, "status": r.status, "reason": r.reason,
            "voting_seasons": sorted(int(x["season"]) for x in seasons if x.get("voting") is True)})
    return out


def _fmt(pair, nd: int = 3) -> str:
    """Numbers joined by "/", "-" for a missing pair or a missing number in it."""
    return "-" if pair is None else "/".join("-" if x is None else f"{x:.{nd}f}" for x in pair)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True, help="a copy of the engine database (opened read-only)")
    ap.add_argument("--root", required=True, help="the directory the artifact local paths are relative to")
    ap.add_argument("--out", required=True, help="the JSON file the numbers are written to")
    ap.add_argument("--validate", type=int, action="append", default=[],
                    help="a signal id whose per-quarter difference-image centroids validate the floor (repeatable)")
    a = ap.parse_args(argv)
    store = read_only_store(a.db)
    live = rung5_rows(store)
    rows, stars = [], []
    for target_id in sorted(live):
        sid = live[target_id][0]["signal_id"]          # the star's first localised signal gives the ephemeris
        star, quarters, eph = quarters_of(store, sid, Path(a.root))
        voting = sorted({s for x in live[target_id] for s in x["voting_seasons"]})
        stars.append({**star, "rung5": live[target_id], "live_voting_seasons": voting})
        for r in season_quarter_centroids(quarters, eph):
            rows.append({"star": target_id, "live_voting": r["season"] in voting, **r})
        print(f"{target_id} (signal {sid}, neighbour KIC {star['neighbour_kic']}, {star['n_tpf']} pixel files)",
              flush=True)
    pooled = pooled_floor(rows)
    validation = {}
    for sid in a.validate:
        star, quarters, eph = quarters_of(store, sid, Path(a.root))
        seasons = localise_quarters(quarters, eph).metrics()["seasons"]
        per_q = quarter_diff_centroids(quarters, eph)
        validation[str(sid)] = {**star, "seasons": seasons, "quarters": {str(k): v for k, v in per_q.items()},
                                "excess": excess_scatter(seasons, per_q)}
    out = {"pooled": pooled, "rows": rows, "stars": stars, "validation": validation}
    Path(a.out).write_text(json.dumps(out, indent=1, allow_nan=False), encoding="utf-8")

    print()
    print(f"{'star':16} {'season':6} {'quarters':14} {'box':5} {'pool':5} {'target rms c/r':16} "
          f"{'neighb rms c/r':16} {'registration c/r':18} live_voting")
    for s in pooled["per_season"]:
        print(f"{s['star']:16} {s['season']:<6} {','.join(map(str, s['quarters'])):14} "
              f"{'yes' if s['box_complete'] else 'cut':5} {'yes' if s['pooled'] else s['why']:5} "
              f"{_fmt(s['target_rms']):16} {_fmt(s['neighbour_rms']):16} {_fmt(s['registration_mean']):18} "
              f"{'yes' if s['live_voting'] else 'no'}")
    nb = pooled["neighbour"]
    print(f"\npooled target: {pooled['n_seasons']} seasons, {pooled['n_quarters']} quarters, rms col/row/axis mean "
          f"{_fmt([pooled['rms_col'], pooled['rms_row'], pooled['rms_axis_mean']], 4)} px")
    print(f"pooled neighbour: rms col/row/axis mean {_fmt([nb['rms_col'], nb['rms_row'], nb['rms_axis_mean']], 4)} px "
          f"({nb['dof']} degrees of freedom)")
    print(f"adopted: {_fmt([pooled['adopted']], 4)} px per axis (the {pooled['adopted_from'] or 'no'} measurement)")
    for sid, v in validation.items():
        e = v["excess"]
        print(f"\nvalidation, signal {sid} ({v['target_id']}): {e['n_seasons']} voting seasons, {e['n_quarters']} "
              f"quarters, excess col {_fmt([e['excess_col']], 4)} row {_fmt([e['excess_row']], 4)} axis mean "
              f"{_fmt([e['excess_axis_mean']], 4)} px")
        for s in e["per_season"]:
            print(f"  season {s['season']} quarters {s['quarters']}: scatter {_fmt(s['scatter_rms'])}, quarter MC "
                  f"{s['quarter_sigma_mc_rms']:.3f}, excess {_fmt(s['excess'])}, chi2/dof {_fmt(s['chi2_dof'], 2)}; "
                  f"season: empirical {_fmt(s['sem'])}, MC {_fmt([s['season_sigma_mc']])}, excess "
                  f"{_fmt(s['season_excess'])}")
        print(f"  season-level excess, largest: {_fmt([e['season_excess_max']], 4)} px")
    return 0


if __name__ == "__main__":
    sys.exit(main())
