"""A.4 (extension): in-transit centroid shift of the candidate from the per-quarter CENTROID_COLUMN / CENTROID_ROW
columns of its KBonus file (read-only cache). Per quarter: subtract a 2-day running median (transits masked),
take mean(in) - mean(out) with the same 3.0 h / 1-3 duration windows as the depth test. Quarters of one season share the
CCD orientation, so they are averaged per season (inverse-variance). Implied offset of the dip source from the
out-of-transit centroid = -shift / depth (depth = SAP depth of that season), in pixels and arcsec (3.98 arcsec/px)."""
import json, pathlib
import numpy as np
from astropy.io import fits

HERE = pathlib.Path(__file__).parent
CAND = pathlib.Path(r"C:\Research\skysift\.superpowers\sample-run\blobs\8a\8a56c51dfb58d9d1de7f946d008872830df508fb9f4fe124b1c39553194a85ef_hlsp_kbonus-bkg_kepler_kepler_kic-006146676_kepler_v1.0_lc.fits")
P, T0, DUR = 40.29109, 135.788, 3.0 / 24


def dt_of(t, t0=T0): return (t - t0 + P / 2) % P - P / 2


def resid(t, c, window=2.0):
    keep = np.abs(dt_of(t)) >= DUR; tk, ck = t[keep], c[keep]
    lo = np.searchsorted(tk, t - window / 2); hi = np.searchsorted(tk, t + window / 2)
    return c - np.array([np.median(ck[a:b]) if b - a >= 10 else np.nan for a, b in zip(lo, hi)])


def shift(t, r, t0=T0):
    d = np.abs(dt_of(t, t0)); ok = np.isfinite(r)
    i, o = r[ok & (d < DUR / 2)], r[ok & (d >= DUR) & (d <= 3 * DUR)]
    if len(i) < 3 or len(o) < 6: return np.nan, np.nan
    return np.mean(i) - np.mean(o), np.std(o, ddof=1) * np.sqrt(1 / len(i) + 1 / len(o))


A = json.loads((HERE / "results_A.json").read_text())["candidate_KIC6146676"]["per_season"]
per_q, seasons = {}, {}
with fits.open(CAND, memmap=False) as h:
    for x in h[2:]:
        if not x.name.startswith("LIGHTCURVE_Q"): continue
        d = x.data; q = int(x.header["QUARTER"]); s = int(x.header["SEASON"])
        t = np.asarray(d["TIME"], float); m = (np.asarray(d["SAP_QUALITY"]) == 0) & np.isfinite(t)
        cc, cr = np.asarray(d["CENTROID_COLUMN"], float)[m], np.asarray(d["CENTROID_ROW"], float)[m]; t = t[m]
        if not np.isfinite(cc).any(): continue
        o = np.argsort(t); t, cc, cr = t[o], cc[o], cr[o]
        rc, rr = resid(t, cc), resid(t, cr)
        (dc, ec), (dr, er) = shift(t, rc), shift(t, rr)
        ctrl = np.array([[*shift(t, rc, T0 + ph * P), *shift(t, rr, T0 + ph * P)] for ph in np.linspace(0.03, 0.97, 100)])
        g = np.isfinite(ctrl).all(1)
        infl = max(1.0, np.std(ctrl[g, 0]) / np.median(ctrl[g, 1]), np.std(ctrl[g, 2]) / np.median(ctrl[g, 3])) if g.sum() > 20 else 1.0
        per_q[q] = {"season": s, "dcol_px": dc, "dcol_err": ec * infl, "drow_px": dr, "drow_err": er * infl, "inflation": infl,
                    "centroid_rms_px": float(np.nanstd(rc))}
for s in range(4):
    qs = [v for v in per_q.values() if v["season"] == s and np.isfinite(v["dcol_px"])]
    if not qs: continue
    w = lambda k, e: (np.sum([v[k] / v[e] ** 2 for v in qs]) / np.sum([1 / v[e] ** 2 for v in qs]), np.sum([1 / v[e] ** 2 for v in qs]) ** -0.5)
    (mc, ec), (mr, er) = w("dcol_px", "dcol_err"), w("drow_px", "drow_err")
    D = A[str(s)]["sap"]["depth_ppm"] * 1e-6; Dpsf = A[str(s)]["psf"]["depth_ppm"] * 1e-6
    off = np.hypot(mc, mr) / D; off_err = np.hypot(mc * ec, mr * er) / max(np.hypot(mc, mr), 1e-12) / D
    seasons[s] = {"quarters": sorted(int(k) for k, v in per_q.items() if v["season"] == s), "dcol_px": mc, "dcol_err": ec, "drow_px": mr,
                  "drow_err": er, "shift_sig": float(np.hypot(mc / ec, mr / er)), "sap_depth_used": D,
                  "implied_offset_px": off, "implied_offset_err_px": off_err, "implied_offset_arcsec": off * 3.98,
                  "implied_offset_err_arcsec": off_err * 3.98,
                  "offset_3sigma_upper_arcsec": (off + 3 * off_err) * 3.98}
# Validity diagnostic: compare with the centroid columns of KIC 6146666 in its own KBonus file (11.5 arcsec away).
NB = HERE / "downloads" / "neighbour_lcs" / "hlsp_kbonus-bkg_kepler_kepler_kic-006146666_kepler_v1.0_lc.fits"
diag = {}
with fits.open(CAND, memmap=False) as a, fits.open(NB, memmap=False) as b:
    for i in range(2, len(a)):
        if not a[i].name.startswith("LIGHTCURVE_Q"): continue
        ca, cb = np.asarray(a[i].data["CENTROID_COLUMN"], float), np.asarray(b[i].data["CENTROID_COLUMN"], float)
        m = np.isfinite(ca) & np.isfinite(cb)
        if m.sum() > 100:
            diag[a[i].name] = {"std_candidate_px": float(np.std(ca[m])), "std_of_difference_px": float(np.std(ca[m] - cb[m])),
                               "corr": float(np.corrcoef(ca[m], cb[m])[0, 1])}
res = {"method": __doc__, "VALID": False,
       "verdict": "NOT USABLE: the KBonus CENTROID_COLUMN/ROW of the candidate and of KIC 6146666 move identically "
                  "(difference scatter ~1e-6 px, correlation 1.0), i.e. they are the PSF-model positions (catalogue position plus "
                  "a common pointing model), not flux-weighted centroids. They cannot respond to a dip, so the near-zero shifts below "
                  "carry no information about the location of the dip source.",
       "diagnostic_vs_KIC6146666": diag, "per_quarter": per_q, "per_season": seasons}
(HERE / "results_A4.json").write_text(json.dumps(res, indent=1, default=float))
for s, v in seasons.items():
    print(s, {k: (round(x, 5) if isinstance(x, float) else x) for k, x in v.items()})
