"""Light-curve numbers and plot arrays for the KIC 6146676 dossier, from the cached KBonus file and the engine's own
functions. Writes lc_data.json next to this script. Read-only on the sample run."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from skysift.config import Thresholds
from skysift.lc.kbonus import parse_kbonus_fits
from skysift.lc.ops import (
    event_depths,
    event_quality,
    fold_phase,
    phase_significance,
    season_of,
    secondary_search,
    stacked_depth,
)
from skysift.lc.transit_model import fit_transit, uniform_disc_flux
from skysift.pipeline import whitened_flux

ROOT = Path("C:/Research/skysift")
RAW = ROOT / ".superpowers/sample-run/blobs/8a/8a56c51dfb58d9d1de7f946d008872830df508fb9f4fe124b1c39553194a85ef_hlsp_kbonus-bkg_kepler_kepler_kic-006146676_kepler_v1.0_lc.fits"
OUT = Path(__file__).with_name("lc_data.json")
P, T0, DUR = 40.29109, 135.788, 3.0 / 24


def binned(x, y, width, lo, hi):
    edges = np.arange(lo, hi + width, width)
    idx = np.digitize(x, edges) - 1
    out = []
    for i in range(edges.size - 1):
        m = (idx == i) & np.isfinite(y)
        if m.sum() >= 3:
            out.append((float(edges[i] + width / 2), float(np.mean(y[m])), float(np.std(y[m]) / np.sqrt(m.sum())), int(m.sum())))
    return out


def main():
    lc = parse_kbonus_fits(RAW.read_bytes())
    th = Thresholds(sde_min=13.0)
    fn, record, _settings, detrended = whitened_flux(lc, th)
    fn_alt = whitened_flux(lc, th, flux=lc.flux_alt)[0]
    t = lc.time
    good = np.isfinite(fn) & np.isfinite(t)
    r = lambda x, n=6: None if x is None or not np.isfinite(x) else round(float(x), n)

    # refine t0 on this flux: slide +-0.1 d in 1 min steps for the deepest stacked depth
    best = (None, -np.inf)
    for dt in np.arange(-0.1, 0.1001, 1 / 1440):
        sd = stacked_depth(t, fn, P, T0 + dt, DUR)
        if np.isfinite(sd.depth_ppm) and sd.depth_ppm > best[1]:
            best = (float(T0 + dt), float(sd.depth_ppm))
    t0 = best[0]

    fit = fit_transit(t, fn, P, t0, DUR)
    ed = event_depths(t, fn, lc.quarter, P, t0, DUR)
    ed_alt = event_depths(t, fn_alt, lc.quarter, P, t0, DUR)
    odd = stacked_depth(t, fn, P, t0, DUR, epochs="odd"); even = stacked_depth(t, fn, P, t0, DUR, epochs="even")
    sec = secondary_search(t, fn, P, t0, DUR)
    half = stacked_depth(t, fn, P, t0, DUR, phase_offset=0.5)
    ps = phase_significance(t, fn, P, t0, DUR)
    eq = event_quality(t, fn, P, t0, DUR)
    ph = fold_phase(t[good], P, t0)                      # phase in days? check range and convert to days from mid-transit
    if np.nanmax(np.abs(ph)) <= 0.5 + 1e-9:
        ph_days = ph * P
    else:
        ph_days = ph
    y = (fn[good] - 1) * 1e6
    win = 4 * DUR
    m = np.abs(ph_days) < win
    fold_pts = [(r(a, 4), r(b, 1)) for a, b in zip(ph_days[m] * 24, y[m])]        # hours, ppm
    fold_bins = [(r(a * 24, 3), r(b, 1), r(c, 1), n) for a, b, c, n in binned(ph_days[m], y[m], DUR / 8, -win, win)]
    full_bins = [(r(a / P, 4), r(b, 1), r(c, 1), n) for a, b, c, n in binned(ph_days, y, P / 200, -P / 2, P / 2)]
    model_t = np.linspace(-win, win, 400)
    model = (uniform_disc_flux(model_t, P, fit.k, fit.b, fit.a_over_r) - 1) * 1e6 if fit.ok else None
    cyc = np.round((t[good] - t0) / P).astype(int)
    oddm, evenm = m & (cyc % 2 == 1), m & (cyc % 2 == 0)
    odd_bins = [(r(a * 24, 3), r(b, 1), r(c, 1), n) for a, b, c, n in binned(ph_days[oddm], y[oddm], DUR / 4, -win, win)]
    even_bins = [(r(a * 24, 3), r(b, 1), r(c, 1), n) for a, b, c, n in binned(ph_days[evenm], y[evenm], DUR / 4, -win, win)]
    sec_ph = ph_days - np.where(ph_days < 0, -P / 2, P / 2)      # days from phase 0.5
    sm = np.abs(sec_ph) < win
    sec_bins = [(r(a * 24, 3), r(b, 1), r(c, 1), n) for a, b, c, n in binned(sec_ph[sm], y[sm], DUR / 4, -win, win)]

    seasons = season_of(lc.quarter)
    season_depths = {}
    for s in sorted(set(int(v) for v in seasons[good])):
        mm = (seasons == s)
        sd = stacked_depth(t[mm], fn[mm], P, t0, DUR)
        season_depths[str(s)] = {"depth_ppm": r(sd.depth_ppm, 1), "err_ppm": r(sd.err_ppm, 1), "n_in": int(sd.n_in)}
    # per-quarter coverage and scatter
    quarters = []
    for q in sorted(set(int(v) for v in lc.quarter[good])):
        mm = good & (lc.quarter == q)
        quarters.append({"q": q, "t_start": r(t[mm].min(), 2), "t_end": r(t[mm].max(), 2), "n": int(mm.sum()),
                         "rms_ppm": r(1.4826 * np.median(np.abs(fn[mm] - np.median(fn[mm]))) * 1e6, 0)})
    # full series, thinned to 1 point per cadence (already) but limited to finite: time, flux ppm
    series = [(r(a, 4), r(b, 0)) for a, b in zip(t[good], y)]
    events = [{"epoch": int(e), "t_mid": r(t0 + e * P, 3), "quarter": int(q), "depth_ppm": r(d, 0), "err_ppm": r(s_, 0)}
              for e, q, d, s_ in zip(ed.epochs, ed.quarters, ed.depths, ed.errors)]
    data = {
        "ephemeris": {"period_days": P, "t0_bkjd": r(t0, 4), "t0_input": T0, "duration_h": 3.0, "t0_bjd": r(t0 + 2454833.0, 4)},
        "points": {"total": int(t.size), "good": int(good.sum()), "baseline_days": r(t[good].max() - t[good].min(), 1),
                   "prewhitened_components": len(record.get("components", [])), "cadence_min": 29.42},
        "depth": {"stacked_ppm": r(stacked_depth(t, fn, P, t0, DUR).depth_ppm, 1), "event_mean_ppm": r(ed.mean, 1), "event_err_ppm": r(ed.err_scaled, 1),
                  "event_err_formal_ppm": r(ed.err_formal, 1), "n_events": int(ed.n), "sap_event_mean_ppm": r(ed_alt.mean, 1), "sap_event_err_ppm": r(ed_alt.err_scaled, 1)},
        "odd_even": {"odd_ppm": r(odd.depth_ppm, 1), "odd_err": r(odd.err_ppm, 1), "even_ppm": r(even.depth_ppm, 1), "even_err": r(even.err_ppm, 1)},
        "secondary": {"best_phase": r(sec[0], 3), "depth_ppm": r(sec[1], 1), "sigma": r(sec[2], 2),
                      "at_half": {"depth_ppm": r(half.depth_ppm, 1), "err_ppm": r(half.err_ppm, 1)}},
        "uniqueness": {"sigma_primary": r(ps.sigma_pri, 2), "sigma_secondary": r(ps.sigma_sec, 2), "sigma_tertiary": r(ps.sigma_ter, 2), "sigma_positive": r(ps.sigma_pos, 2), "ms2": r(ps.ms2, 2), "ms3": r(ps.ms3, 2), "fa2": r(ps.fa2, 2)},
        "fit": {"ok": bool(fit.ok), "k": r(fit.k, 4), "b": r(fit.b, 3), "a_over_r": r(fit.a_over_r, 2), "depth_ppm": r(fit.depth_ppm, 1), "t14_h": r(fit.t14_days * 24, 3), "chi2": r(fit.chi2, 1), "chi2_nongrazing": r(fit.chi2_nongrazing, 1), "n_bins": int(fit.n_bins)},
        "events_quality": {"duty_cycle": r(eq.duty, 3), "n_expected": len(eq.epochs), "coverage_median": r(np.nanmedian(eq.coverage), 2), "n_with_data": int(np.sum(np.asarray(eq.coverage) > 0))},
        "season_depths": season_depths, "quarters": quarters, "events": events,
        "fold_points_hours_ppm": fold_pts, "fold_bins": fold_bins, "full_phase_bins": full_bins,
        "model_hours_ppm": None if model is None else [(r(a * 24, 3), r(b, 1)) for a, b in zip(model_t, model)],
        "odd_bins": odd_bins, "even_bins": even_bins, "secondary_bins": sec_bins, "series_bkjd_ppm": series,
    }
    OUT.write_text(json.dumps(data), encoding="utf-8")
    print(json.dumps({k: v for k, v in data.items() if k in ("ephemeris", "points", "depth", "odd_even", "secondary", "uniqueness", "fit", "events_quality", "season_depths")}, indent=1)[:4000])
    print("events:", len(events), "fold points:", len(fold_pts), "series:", len(series), "->", OUT)


if __name__ == "__main__":
    main()
