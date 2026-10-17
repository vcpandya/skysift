"""Regenerate the tables of docs/superpowers/notes/2026-10-17-long-period-sde.md for one cached KBonus light curve.

Usage (from the repository root, with the project venv):

    .venv/Scripts/python.exe docs/superpowers/notes/scripts/long_period_sde.py <kbonus.fits> [--prewhiten]
        [--period P [--t0 T0 --duration HOURS]] [--inject P] [--inject-depth PPM] [--edge-mask-days D]

The flux is ``detrend(lc)`` with its gap-edge mask (``--edge-mask-days 0`` turns the mask off),
optionally pre-whitened as the pipeline does today (``--prewhiten``), then put through the same finite mask and upward
5-MAD clip as ``BoxDip.search``. The coarse search and ranking are ``BoxDip._coarse_search`` and ``BoxDip._rank`` with
``BoxDip(mask_iterations=0, alias_resolution=0)`` and the 0.5 to 30 d window. The peak studied is the refined candidate
nearest ``--period`` (default: the longest-period signal among the top three); with ``--t0`` and ``--duration`` as well
it is exactly that ephemeris, so an injection lands where an earlier run put it. ``--inject`` is
the period of the injected box (default: the band period farthest from the band's eight strongest separated peaks).
One run costs one coarse search (about 2 minutes) plus eight single-band searches.
"""
from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

import numpy as np
from astropy.timeseries import BoxLeastSquares

from skysift.config import Thresholds
from skysift.detectors.box_dip import BoxDip, _separated
from skysift.lc.kbonus import parse_kbonus_fits
from skysift.lc.ops import GAP_EDGE_MASK_DAYS, detrend, event_depths, fold_phase
from skysift.lc.prewhiten import prewhiten
from skysift.vetting.rung1_detection import SYSTEMATIC_PERIODS

GAP_D = 0.5             # a time step longer than this starts a new segment
MASK_D = 1.0            # the masking experiment drops this many days after every segment start
INJECT_PHASE = 0.37     # injected box t0 = peak t0 + INJECT_PHASE * peak period (clear of the peak's own events)
DEEP_PPM = 3000.0
KEYS = ("period", "power", "duration", "t0", "depth", "err", "band")


def table(header: list[str], rows: list[list]) -> None:
    print("| " + " | ".join(header) + " |")
    print("|" + "---|" * len(header))
    for r in rows:
        print("| " + " | ".join(str(c) for c in r) + " |")
    print()


def prepare(fits_path: str, white: bool, edge_mask_days: float = GAP_EDGE_MASK_DAYS):
    lc = parse_kbonus_fits(Path(fits_path).read_bytes())
    fn = detrend(lc, edge_mask_days=edge_mask_days)
    pw = prewhiten(lc.time, fn, max_components=50, fap_max=1e-6)
    fnorm = pw.residual if white else fn
    m = np.isfinite(fnorm) & np.isfinite(lc.time)                      # BoxDip.search preprocessing, verbatim
    t, y, q = lc.time[m], fnorm[m], lc.quarter[m]
    med = np.median(y); mad = 1.4826 * np.median(np.abs(y - med))
    k = (y - med) < 5 * mad
    t, y, q = t[k], y[k], q[k]
    sigma = max(1.4826 * float(np.median(np.abs(y - np.median(y)))), 1e-12)
    return lc, t, y, q, sigma, len(pw.components)


def pick_peak(spec: dict, cands: list, sdes: list, period: float | None, top: list[dict]) -> dict:
    allc = [dict(zip(KEYS, c, strict=True)) | {"sde": s} for c, s in zip(cands, sdes, strict=True)]
    if period is None:
        return max(top, key=lambda c: c["period"])
    best = min(allc, key=lambda c: abs(c["period"] / period - 1))
    if abs(best["period"] / period - 1) < 0.005:
        return best
    # not among the refined candidates: take the strongest coarse trial within 0.2 percent
    w = np.where(np.abs(spec["period"] / period - 1) < 0.002)[0]
    i = int(w[np.argmax(spec["power"][w])])
    return {"period": float(spec["period"][i]), "power": float(spec["power"][i]), "duration": float(spec["duration"][i]),
            "t0": float(spec["t0"][i]), "depth": float(spec["depth"][i]), "err": float(spec["err"][i]),
            "band": int(spec["band"][i]), "sde": float(spec["sde"][i])}


def segment_starts(t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """First searched time of every segment and the length of the gap before it (NaN for the start of the data)."""
    i = np.where(np.diff(t) > GAP_D)[0]
    return np.concatenate([[t[0]], t[i + 1]]), np.concatenate([[np.nan], t[i + 1] - t[i]])


def band_grid(edges: np.ndarray, b: int, durs: np.ndarray, baseline: float, oversample: float) -> np.ndarray:
    p_lo, p_hi = edges[b], edges[b + 1]
    df = durs.min() / (oversample * baseline * p_hi)                    # the coarse grid of _coarse_search
    return 1 / np.arange(1 / p_hi, 1 / p_lo, df)[::-1]


def band_power(t, y, sigma, periods, durs):
    pw = np.asarray(BoxLeastSquares(t, y, dy=np.full(t.size, sigma)).power(periods, durs, objective="snr").power)
    med = float(np.median(pw))
    return pw, med, 1.4826 * float(np.median(np.abs(pw - med)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("fits")
    ap.add_argument("--prewhiten", action="store_true")
    ap.add_argument("--period", type=float)
    ap.add_argument("--inject", type=float)
    ap.add_argument("--inject-depth", type=float, help="injected depth in ppm (default: the peak's BLS depth)")
    ap.add_argument("--t0", type=float, help="with --period and --duration: study exactly this peak")
    ap.add_argument("--duration", type=float, help="studied peak duration in hours (with --period and --t0)")
    ap.add_argument("--edge-mask-days", type=float, default=GAP_EDGE_MASK_DAYS,
                    help="detrend gap-edge mask in days; 0 turns it off")
    a = ap.parse_args()

    lc, t, y, q, sigma, n_comp = prepare(a.fits, a.prewhiten, a.edge_mask_days)
    th = Thresholds(min_period_days=0.5, max_period_days=30.0)
    det = BoxDip(mask_iterations=0, alias_resolution=0)
    baseline = float(t.max() - t.min()); pmax = min(th.max_period_days, baseline / 3.0)
    model = BoxLeastSquares(t, y, dy=np.full(t.size, sigma))
    spec = det._coarse_search(model, baseline, th, pmax)
    cands, sdes, _ = det._rank(model, spec)
    keep = _separated(np.argsort(sdes)[::-1], np.array([c[0] for c in cands]), det.top_k)
    top = [dict(zip(KEYS, cands[i], strict=True)) | {"sde": sdes[i]} for i in keep]
    pk = pick_peak(spec, cands, sdes, a.period, top)
    if a.period and a.t0 is not None and a.duration:      # pinned: keep the band and powers, fix the ephemeris
        pk = pk | {"period": a.period, "t0": a.t0, "duration": a.duration / 24}
    P, T0, D, PW, B = pk["period"], pk["t0"], pk["duration"], pk["power"], int(pk["band"])
    edges = np.geomspace(th.min_period_days, pmax, det.n_bands + 1)
    per, pw, band, dur = spec["period"], spec["power"], spec["band"], spec["duration"]
    bmed, bstd = spec["band_med"], spec["band_std"]

    print(f"# {lc.meta.get('gaia_id')}: {'pre-whitened' if a.prewhiten else 'detrended, not pre-whitened'} "
          f"({n_comp} sinusoids fitted by prewhiten); {t.size} points, baseline {baseline:.1f} d, sigma {sigma * 1e6:.1f} ppm\n")
    print("## Top three signals and the studied peak\n")
    rows = [[f"{s['period']:.5f}", f"{s['t0']:.5f}", f"{s['duration'] * 24:.1f}", f"{s['depth'] * 1e6:.0f}",
             f"{s['power']:.3f}", f"{s['sde']:.4f}", int(s["band"])] for s in top + [pk]]
    rows[-1][0] = f"studied: {rows[-1][0]}"
    table(["Period (d)", "t0 (BKJD)", "Duration (h)", "Depth (ppm)", "snr = BLS power", "SDE", "Band"], rows)

    # ---- band statistics
    inb = band == B
    bp, bpw = per[inb], pw[inb]
    mad = float(np.median(np.abs(bpw - bmed[B])))
    picked = _separated(np.argsort(bpw)[::-1], bp, 8)
    print("## Band statistics\n")
    table(["Quantity", "Value"], [
        ["Band", f"{B}: {edges[B]:.3f} to {edges[B + 1]:.3f} d"],
        ["Trial periods", int(inb.sum())],
        ["Trial durations (h)", ", ".join(f"{d * 24:g}" for d in spec["band_durs"][B])],
        ["Trials whose best duration is the longest", f"{int((dur[inb] == spec['band_durs'][B].max()).sum())}"],
        ["Band median power", f"{bmed[B]:.3f}"], ["Band MAD", f"{mad:.3f}"], ["Band MAD-sigma", f"{bstd[B]:.3f}"],
        ["Peak raw power", f"{PW:.3f}"],
        ["Peak raw power rank in band", f"{int((bpw > PW).sum()) + 1} of {int(inb.sum())}"],
        ["Percentiles 50 / 90 / 99 / 99.9", " / ".join(f"{v:.2f}" for v in np.percentile(bpw, [50, 90, 99, 99.9]))],
        ["Separated band peaks (period, power, SDE)",
         "; ".join(f"{bp[i]:.3f} d, {bpw[i]:.2f}, {(bpw[i] - bmed[B]) / bstd[B]:.2f}" for i in picked)]])
    table(["Band", "Periods (d)", "Median power", "MAD-sigma", "Trials"],
          [[b, f"{edges[b]:.3f} to {edges[b + 1]:.3f}", f"{bmed[b]:.2f}", f"{bstd[b]:.2f}", int((band == b).sum())]
           for b in range(det.n_bands)])

    # ---- systematic comb exclusion: P/k for k = 1..kmax of each SYSTEMATIC_PERIODS entry, 1 percent
    print("## Systematic comb exclusion\n")
    rows = []
    for kmax in (5, 20):
        comb = np.array(sorted({s / k for s in SYSTEMATIC_PERIODS for k in range(1, kmax + 1)}))
        excl = np.any(np.abs(bp[:, None] / comb[None, :] - 1.0) < 0.01, axis=1)
        kept = bpw[~excl]
        m2 = float(np.median(kept)); s2 = 1.4826 * float(np.median(np.abs(kept - m2)))
        inband = [f"{c:.3f}" for c in comb if edges[B] * 0.99 <= c <= edges[B + 1] * 1.01]
        rows.append([f"1 to {kmax}", ", ".join(inband) or "none", bpw.size, int(excl.sum()), kept.size,
                     f"{bmed[B]:.3f} / {m2:.3f}", f"{bstd[B]:.3f} / {s2:.3f}", f"{pk['sde']:.3f} / {(PW - m2) / s2:.3f}",
                     f"{np.median(bpw[excl]):.2f}" if excl.any() else "n/a"])
    table(["k", "Comb periods in band", "Trials before", "Excluded", "Trials after", "Median before / after",
           "MAD-sigma before / after", "SDE before / after", "Median power of excluded"], rows)

    # ---- fold, 100 bins
    ph = fold_phase(t, P, T0) / P
    idx = np.clip(np.digitize(ph, np.linspace(-0.5, 0.5, 101)) - 1, 0, 99)
    cnt = np.bincount(idx, minlength=100)
    dppm = (1 - np.bincount(idx, weights=y, minlength=100) / np.maximum(cnt, 1)) * 1e6
    centre = np.linspace(-0.495, 0.495, 100)
    oot = np.abs(centre) >= 0.03
    print(f"## Fold at {P:.5f} d, 100 bins of {P / 100 * 24:.2f} h, median {int(np.median(cnt))} points per bin\n")
    table(["Phase", "Points", "Depth (ppm)"],
          [[f"{centre[i]:+.3f}", cnt[i], f"{dppm[i]:.0f}"] for i in np.where(np.abs(centre) < 0.04)[0]])
    deepest = np.argsort(dppm[oot])[-3:][::-1]
    table(["Fold statistic", "Value"], [
        ["Out-of-transit bin mean", f"{dppm[oot].mean():.0f} ppm"],
        ["Out-of-transit bin scatter", f"{dppm[oot].std():.0f} ppm"],
        ["White-noise expectation per bin", f"{sigma * 1e6 / np.sqrt(np.median(cnt)):.0f} ppm"],
        ["Deepest out-of-transit bins", ", ".join(f"{dppm[oot][i]:.0f} ppm at {centre[oot][i]:+.3f}" for i in deepest)]])
    lv = " .:-=+*#%@"
    print("```\n|" + "".join(lv[int(np.clip(d / max(dppm.max(), 1) * 9, 0, 9))] for d in dppm) + "|\n```\n")

    # ---- per-epoch window depths, with the segment start before each epoch
    starts, gaplen = segment_starts(t)
    ep = np.round((t - T0) / P).astype(int)
    inw = np.abs(fold_phase(t, P, T0)) < D / 2
    rows = []
    for e in np.unique(ep[inw]):
        s = inw & (ep == e)
        tm = T0 + e * P
        j = int(np.searchsorted(starts, tm, side="right")) - 1        # the segment holding (or preceding) the epoch
        rows.append((e, tm, int(np.median(q[s])), int(s.sum()), (1 - y[s].mean()) * 1e6,
                     sigma / np.sqrt(s.sum()) * 1e6, tm - starts[max(j, 0)], gaplen[max(j, 0)]))
    r = np.array(rows, dtype=float)
    n, dep = r[:, 3], r[:, 4]
    total = float(np.sum(n * dep))
    stack = total / n.sum(); stack_err = sigma * 1e6 / np.sqrt(n.sum())
    order = np.argsort(n * dep)[::-1]
    show = [i for i in order if dep[i] > DEEP_PPM]
    show += [i for i in order if i not in show][:1]
    print(f"## Per-epoch depths: {len(rows)} epochs with points in the {D * 24:.1f} h window\n")
    print("Offset = epoch mid-time minus the first searched time of its segment (a segment starts after a time step "
          f"longer than {GAP_D} d). Gap = length of that preceding step; 'data start' for the first segment.\n")
    table(["Epoch", "Mid-time (BKJD)", "Quarter", "Points", "Depth (ppm)", "White error (ppm)", "Share of stack",
           "Offset after segment start (d)", "Preceding gap (d)"],
          [[int(r[i, 0]), f"{r[i, 1]:.3f}", int(r[i, 2]), int(n[i]), f"{dep[i]:.0f}", f"{r[i, 5]:.0f}",
            f"{dep[i] * n[i] / total:.3f}", f"{r[i, 6]:.2f}",
            "data start" if np.isnan(r[i, 7]) else f"{r[i, 7]:.2f}"] for i in show])
    srows = [["All epochs (point-weighted)", f"{stack:.0f}", f"{stack / stack_err:.1f}"]]
    for drop in (1, 2, 3, 5):
        k = np.ones(len(rows), bool); k[order[:drop]] = False
        sd = float(np.sum(n[k] * dep[k]) / n[k].sum())
        srows.append([f"Without the {drop} largest contributors", f"{sd:.0f}", f"{sd / (sigma * 1e6 / np.sqrt(n[k].sum())):.1f}"])
    ed = event_depths(t, y, q, P, T0, D)
    missing = sorted(int(e) for e in set(r[:, 0].astype(int)) - set(ed.epochs.tolist()))
    chi2 = float(np.sum(((ed.depths - ed.mean) / ed.errors) ** 2))
    srows.append(["event_depths median (linear local baseline)", f"{np.median(ed.depths):.0f}", ""])
    table(["Stacked depth", "Depth (ppm)", "White snr"], srows)
    rest = np.delete(dep, order[:3])
    print(f"BLS depth {pk['depth'] * 1e6:.0f} ppm, depth_err {pk['err'] * 1e6:.1f} ppm. Of the other {rest.size} epochs, "
          f"{int((np.abs(rest) < 750).sum())} lie within 750 ppm of zero and {int((np.abs(rest) < 500).sum())} within 500 ppm. "
          f"event_depths measured {ed.n} epochs (chi-square {chi2:.1f} for {ed.n - 1} dof); it could not measure "
          f"{missing} (fewer than 3 window points or 20 baseline points).\n")

    # ---- gap-edge dips across the curve
    cuts = np.concatenate([[0], np.where(np.diff(t) > GAP_D)[0] + 1, [t.size]])
    first, last, inner = [], [], []
    for i0, i1 in itertools.pairwise(cuts):
        ts, ys = t[i0:i1], y[i0:i1]
        if ts[-1] - ts[0] < 3 * D:
            continue
        first.append((1 - ys[ts < ts[0] + D].mean()) * 1e6)
        last.append((1 - ys[ts > ts[-1] - D].mean()) * 1e6)
        for s0 in np.arange(ts[0] + D, ts[-1] - 2 * D, D):
            m = (ts >= s0) & (ts < s0 + D)
            if m.sum() >= 10:
                inner.append((1 - ys[m].mean()) * 1e6)
    print(f"## Gap-edge dips ({D * 24:.1f} h windows)\n")
    table(["Window", "Windows", "Median depth (ppm)", "Deeper than 1000 ppm", "Deeper than 5000 ppm"],
          [[name, v.size, f"{np.median(v):.0f}", f"{int((v > 1000).sum())} ({(v > 1000).mean():.3f})",
            f"{int((v > 5000).sum())} ({(v > 5000).mean():.4f})"]
           for name, v in (("First of a segment", np.array(first)), ("Last of a segment", np.array(last)),
                           ("Interior", np.array(inner)))])

    # ---- red noise
    nb = max(2, round(float(D / np.median(np.diff(t)))))
    bm = np.array([y[i0:i1][j:j + nb].mean() for i0, i1 in itertools.pairwise(cuts)
                   for j in range(0, i1 - i0 - nb + 1, nb)])
    beta = 1.4826 * np.median(np.abs(bm - np.median(bm))) / (sigma / np.sqrt(nb))
    offs = np.linspace(0.05, 0.95, 91)
    offs = offs[np.abs(offs - 0.5) <= 0.5 - 3 * D / P]
    ctrl = np.array([1 - y[np.abs(fold_phase(t, P, T0 + f * P)) < D / 2].mean() for f in offs]) * 1e6
    print("## Red noise\n")
    table(["Quantity", "Value"], [
        [f"Robust scatter of {nb}-point bins / white expectation (beta)", f"{beta:.2f}"],
        ["snr / beta", f"{PW / beta:.1f}"],
        [f"Robust spread of the box depth at {offs.size} control phases", f"{1.4826 * np.median(np.abs(ctrl - np.median(ctrl))):.0f} ppm"],
        ["BLS depth_err", f"{pk['err'] * 1e6:.1f} ppm"]])

    # ---- masking and injection on the band's own grid
    durs = spec["band_durs"][B]
    periods = band_grid(edges, B, durs, baseline, det.oversample)
    pinj = a.inject
    depth_inj = a.inject_depth * 1e-6 if a.inject_depth else pk["depth"]
    if pinj is None:
        cand = periods[(periods > edges[B] * 1.02) & (periods < edges[B + 1] * 0.98)]
        peaks = bp[picked]
        pinj = float(cand[np.argmax(np.min(np.abs(cand[:, None] / peaks[None, :] - 1), axis=1))])
    after = np.zeros(t.size, bool)
    for s0 in starts:
        after |= (t >= s0) & (t < s0 + MASK_D)

    def near(power, p0):
        return float(power[np.abs(periods / p0 - 1) < 0.001].max())

    rows = []
    for label, kk in (("as searched", np.ones(t.size, bool)), (f"first {MASK_D} d after gaps masked", ~after)):
        tt, yy = t[kk], y[kk]
        p0, m0, s0 = band_power(tt, yy, sigma, periods, durs)
        row = [label, int((~kk).sum()), f"{m0:.2f}", f"{s0:.2f}", f"{(near(p0, P) - m0) / s0:.2f}", f"{pinj:.2f}",
               f"{near(p0, pinj):.2f}"]
        for frac in (1.0, 0.5, 0.25):
            yi = yy.copy()
            yi[np.abs(fold_phase(tt, pinj, T0 + INJECT_PHASE * P)) < D / 2] -= frac * depth_inj
            pi, mi, si = band_power(tt, yi, sigma, periods, durs)
            row.append(f"{(near(pi, pinj) - mi) / si:.2f}")
        rows.append(row)
    print(f"## Masking and injection (band {B}, {periods.size} trials, injected depth = {depth_inj * 1e6:.0f} ppm x 1, "
          f"0.5, 0.25)\n")
    table(["Flux", "Points masked", "Band median", "Band MAD-sigma", "Peak SDE", "Injected P (d)",
           "Power at injected P before injection", "SDE 1x", "SDE 0.5x", "SDE 0.25x"], rows)
    print(json.dumps({"period": P, "t0": T0, "duration": D, "sde": pk["sde"], "inject": pinj}))


if __name__ == "__main__":
    main()
