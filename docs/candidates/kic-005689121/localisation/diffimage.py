"""Difference-image localisation of the P = 13.77 d transit signal on KIC 5689121, using the
Kepler long-cadence TPFs of the neighbouring target KIC 5689116 (7.1 arcsec = 1.8 px away, 2.2 times brighter) (Bryson et al. 2013 style).

Usage:  python -I diffimage.py <tpf_dir> <out_dir>
Writes <out_dir>/results.json and <out_dir>/diffimage_products.npz (images for the figure)."""
import sys, glob, json, warnings
from pathlib import Path
import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from scipy.optimize import least_squares

warnings.simplefilter("ignore")
TPF_DIR, OUT_DIR = Path(sys.argv[1]), Path(sys.argv[2])
P, T0, DUR = 13.769508917830883, 136.31664409177998, 2.0 / 24.0   # d, BKJD, d (SkySift box_dip v5 on the KBonus PSF LC)
HALF_IN, OUT_LO, OUT_HI = 0.5 * DUR, 1.0 * DUR, 3.0 * DUR
PIX_ARCSEC = 3.98
NBOOT, NMC = 200, 200
STARS = {"candidate": (285.90782692874626, 40.94198844296448),   # KIC 5689121, Gaia DR3 2103792461826460288
         "target":    (285.9052171647008, 40.942069424984716)}  # KIC 5689116, Gaia DR3 2103792427466721792
DEPTH_PPM = 1983.0
FLUX_RATIO_T_OVER_C = 10 ** (0.4 * (16.332 - 15.476))   # Kp 16.332 (candidate) vs 15.476 (target) = 2.20
rng = np.random.default_rng(20261020)


def flux_centroid(img, valid, half=1, around=None):
    """Flux-weighted centroid (col, row) of positive valid pixels in a (2*half+1)^2 box around the
    brightest valid pixel (or around the given integer pixel (row, col))."""
    im = np.where(valid, img, -np.inf)
    if around is None:
        r0, c0 = np.unravel_index(np.argmax(im), im.shape)
    else:
        r0, c0 = around
    rs = slice(max(r0 - half, 0), min(r0 + half + 1, img.shape[0]))
    cs = slice(max(c0 - half, 0), min(c0 + half + 1, img.shape[1]))
    sub = np.clip(np.where(valid[rs, cs], img[rs, cs], 0.0), 0.0, None)
    rr, cc = np.mgrid[rs, cs]
    s = sub.sum()
    if s <= 0:
        return np.nan, np.nan, (int(r0), int(c0))
    return float((sub * cc).sum() / s), float((sub * rr).sum() / s), (int(r0), int(c0))


def box_sum(img, var, valid, rc, half=1):
    r0, c0 = rc
    rs = slice(max(r0 - half, 0), min(r0 + half + 1, img.shape[0]))
    cs = slice(max(c0 - half, 0), min(c0 + half + 1, img.shape[1]))
    m = valid[rs, cs]
    return float(img[rs, cs][m].sum()), float(np.sqrt(var[rs, cs][m].sum())), int(m.sum())


def box_direct(direct, valid, rc, half=1):
    r0, c0 = rc
    rs = slice(max(r0 - half, 0), min(r0 + half + 1, direct.shape[0]))
    cs = slice(max(c0 - half, 0), min(c0 + half + 1, direct.shape[1]))
    return float(np.nansum(np.where(valid[rs, cs], direct[rs, cs], 0.0)))


def gauss_psf(shape, col, row, sig):
    rr, cc = np.mgrid[0:shape[0], 0:shape[1]]
    return np.exp(-0.5 * ((cc - col) ** 2 + (rr - row) ** 2) / sig ** 2) / (2 * np.pi * sig ** 2)


def two_source_fit(direct, diff, dvar, valid, pos):
    """Approximate check (Gaussian PSF, relative positions fixed at Gaia/WCS). Direct image: amplitudes of
    both stars + constant, common width and a common shift (absorbs WCS / PSF-centre error). Difference
    image: the same PSF shapes, free amplitudes a_c, a_t (weighted linear least squares)."""
    (cc, rc), (ct, rt) = pos["candidate"], pos["target"]
    v = valid & np.isfinite(direct) & np.isfinite(dvar)
    def resid(p):
        dx, dy, sig, fc, ft, b = p
        m = fc * gauss_psf(direct.shape, cc + dx, rc + dy, sig) + ft * gauss_psf(direct.shape, ct + dx, rt + dy, sig) + b
        return ((direct - m) / np.sqrt(np.abs(direct) + 100.0))[v]
    tot = float(np.nansum(np.where(v, direct, 0)))
    p0 = [0, 0, 0.8, tot / (1 + FLUX_RATIO_T_OVER_C), tot * FLUX_RATIO_T_OVER_C / (1 + FLUX_RATIO_T_OVER_C), 0]
    sol = least_squares(resid, p0, bounds=([-1, -1, 0.3, 0, 0, -1e4], [1, 1, 3, np.inf, np.inf, 1e4]))
    dx, dy, sig, fc, ft, b = sol.x
    Pc = gauss_psf(direct.shape, cc + dx, rc + dy, sig)[v]
    Pt = gauss_psf(direct.shape, ct + dx, rt + dy, sig)[v]
    w = 1.0 / dvar[v]
    A = np.vstack([Pc, Pt]).T
    ATA = A.T @ (A * w[:, None]); ATb = A.T @ (diff[v] * w)
    cov = np.linalg.inv(ATA); a = cov @ ATb
    out = {"psf_sigma_pix": float(sig), "shift_pix": [float(dx), float(dy)],
           "direct_flux_candidate": float(fc), "direct_flux_target": float(ft),
           "direct_flux_ratio_target_over_candidate": float(ft / fc) if fc > 0 else None,
           "diff_amp_candidate": float(a[0]), "diff_amp_candidate_err": float(np.sqrt(cov[0, 0])),
           "diff_amp_target": float(a[1]), "diff_amp_target_err": float(np.sqrt(cov[1, 1])),
           "chi2_two_source": float((((diff[v] - A @ a) ** 2) * w).sum()), "npix": int(v.sum())}
    for name, col in (("candidate", Pc), ("target", Pt)):   # single-source fits for a delta-chi2
        aa = (col * w * diff[v]).sum() / (col * w * col).sum()
        out[f"chi2_only_{name}"] = float((((diff[v] - aa * col) ** 2) * w).sum())
    out["implied_depth_ppm_candidate"] = 1e6 * a[0] / fc if fc > 0 else None
    out["implied_depth_ppm_candidate_err"] = 1e6 * np.sqrt(cov[0, 0]) / fc if fc > 0 else None
    out["implied_depth_ppm_target"] = float(1e6 * a[1] / ft)
    out["implied_depth_ppm_target_err"] = float(1e6 * np.sqrt(cov[1, 1]) / ft)
    return out


def stack(events, idx=None):
    idx = range(len(events)) if idx is None else idx
    num = 0.0; wsum = 0.0
    for i in idx:
        w = 1.0 / events[i]["var"]
        num = num + w * events[i]["diff"]; wsum = wsum + w
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.nan_to_num(num / wsum), 1.0 / wsum


def centroid_errors(events, valid, D, V):
    boot = []
    for _ in range(NBOOT):
        Db, _v = stack(events, rng.integers(0, len(events), len(events)))
        boot.append(flux_centroid(Db, valid)[:2])
    boot = np.array(boot); boot = boot[np.all(np.isfinite(boot), axis=1)]
    mc = []
    for _ in range(NMC):
        Dm = D + rng.normal(size=D.shape) * np.sqrt(np.where(valid, V, 0.0))
        mc.append(flux_centroid(Dm, valid)[:2])
    mc = np.array(mc); mc = mc[np.all(np.isfinite(mc), axis=1)]
    cov_boot = np.cov(boot.T) if len(events) > 1 else np.full((2, 2), np.nan)
    cov_mc = np.cov(mc.T)
    use_mc = len(events) < 2 or np.trace(cov_mc) > np.trace(cov_boot)
    return boot, mc, cov_boot, cov_mc, (cov_mc if use_mc else cov_boot), ("noise-MC" if use_mc else "bootstrap")


def describe(rec, D, V, valid, direct, pos, events):
    c_col, c_row, (br, bc) = flux_centroid(D, valid)
    snr = np.where(valid, D / np.sqrt(V), -np.inf); sr, sc = np.unravel_index(np.argmax(snr), snr.shape)
    rec["brightest_diff_pixel_rowcol"] = [br, bc]
    rec["brightest_diff_pixel_snr"] = float(D[br, bc] / np.sqrt(V[br, bc]))
    rec["max_snr_diff_pixel_rowcol"] = [int(sr), int(sc)]; rec["max_snr"] = float(snr[sr, sc])
    for k in ("candidate", "target"):
        rec[f"brightest_diff_pixel_dist_to_{k}_pix"] = float(np.hypot(bc - pos[k][0], br - pos[k][1]))
    boot, mc, cov_boot, cov_mc, cov, src = centroid_errors(events, valid, D, V)
    rec["diff_centroid_colrow"] = [c_col, c_row]
    rec["diff_centroid_sigma_boot_colrow"] = np.sqrt(np.diag(cov_boot)).tolist()
    rec["diff_centroid_sigma_mc_colrow"] = np.sqrt(np.diag(cov_mc)).tolist()
    rec["diff_centroid_cov_adopted"] = cov.tolist(); rec["adopted_sigma_source"] = src
    for k in ("candidate", "target"):
        dc = np.array([c_col - pos[k][0], c_row - pos[k][1]]); dist = float(np.hypot(*dc))
        rec[f"diff_centroid_dist_to_{k}_pix"] = dist; rec[f"diff_centroid_dist_to_{k}_arcsec"] = dist * PIX_ARCSEC
        rec[f"diff_centroid_dist_to_{k}_sigma_pix"] = float(np.sqrt(dc @ cov @ dc) / dist) if dist > 0 else float(np.sqrt(np.trace(cov) / 2))
    for name, arr in (("boot", boot), ("mc", mc)):
        dcand = np.hypot(arr[:, 0] - pos["candidate"][0], arr[:, 1] - pos["candidate"][1])
        dtarg = np.hypot(arr[:, 0] - pos["target"][0], arr[:, 1] - pos["target"][1])
        rec[f"frac_{name}_draws_nearer_candidate"] = float(np.mean(dcand < dtarg))
    rec["nearer"] = "candidate" if rec["diff_centroid_dist_to_candidate_pix"] < rec["diff_centroid_dist_to_target_pix"] else "target"
    # supplementary: same 3x3 flux-weighted centroid, but boxed on the highest-S/N difference pixel
    s_col, s_row, _ = flux_centroid(D, valid, around=(int(sr), int(sc)))
    rec["snrbox_diff_centroid_colrow"] = [s_col, s_row]
    sm = []
    for _ in range(NMC):
        Dm = D + rng.normal(size=D.shape) * np.sqrt(np.where(valid, V, 0.0))
        sn = np.where(valid, Dm / np.sqrt(V), -np.inf); a, b = np.unravel_index(np.argmax(sn), sn.shape)
        sm.append(flux_centroid(Dm, valid, around=(int(a), int(b)))[:2])
    sm = np.array(sm); sm = sm[np.all(np.isfinite(sm), axis=1)]
    rec["snrbox_diff_centroid_sigma_mc_colrow"] = np.std(sm, axis=0, ddof=1).tolist()
    for k in ("candidate", "target"):
        rec[f"snrbox_diff_centroid_dist_to_{k}_pix"] = float(np.hypot(s_col - pos[k][0], s_row - pos[k][1]))
    rec["snrbox_frac_mc_draws_nearer_candidate"] = float(np.mean(np.hypot(sm[:, 0] - pos["candidate"][0], sm[:, 1] - pos["candidate"][1])
                                                                 < np.hypot(sm[:, 0] - pos["target"][0], sm[:, 1] - pos["target"][1])))
    # PRF-free checks: difference flux in 3x3 boxes centred on each star's pixel, and fractional depth there
    for k in ("candidate", "target"):
        rc = (int(np.round(pos[k][1])), int(np.round(pos[k][0])))
        s, e, npx = box_sum(D, V, valid, rc)
        ds = box_direct(direct, valid, rc)
        rec[f"box3_{k}"] = {"center_rowcol": list(rc), "npix": npx, "diff_sum": s, "diff_sum_err": e,
                            "direct_sum": ds, "frac_depth_ppm": 1e6 * s / ds, "frac_depth_ppm_err": 1e6 * e / ds}
    rec["box3_ratio_candidate_over_target"] = rec["box3_candidate"]["diff_sum"] / rec["box3_target"]["diff_sum"]
    rec["two_source_fit"] = two_source_fit(np.nan_to_num(direct), D, V, valid, pos)
    return cov


quarters, products = [], {}
for f in sorted(glob.glob(str(TPF_DIR / "kplr005689116-*_lpd-targ.fits.gz"))):
    with fits.open(f) as h:
        q = int(h[0].header["QUARTER"]); season = int(h[0].header["SEASON"])
        ah = h[2].header; wcs = WCS(ah)
        t = np.asarray(h[1].data["TIME"], float); fl = np.asarray(h[1].data["FLUX"], float)
        qual = np.asarray(h[1].data["QUALITY"])
        col0, row0 = int(ah["CRVAL1P"]), int(ah["CRVAL2P"])
        module, output = h[0].header.get("MODULE"), h[0].header.get("OUTPUT")
    ffin = np.isfinite(fl).mean(axis=0)
    valid = ffin > 0.9 * ffin.max()        # whole-cadence NaN gaps (e.g. Q16) do not invalidate pixels
    good = (qual == 0) & np.isfinite(t) & np.all(np.isfinite(fl[:, valid]), axis=1)
    pos = {k: tuple(float(x) for x in np.array(wcs.all_world2pix([ra], [de], 0)).ravel()) for k, (ra, de) in STARS.items()}
    n = np.round((t - T0) / P); dt = t - (T0 + n * P)
    rec = {"quarter": q, "season": season, "module": module, "output": output, "file": Path(f).name,
           "stamp_shape": list(fl.shape[1:]), "ccd_origin_colrow": [col0, row0], "n_good_cadences": int(good.sum()),
           "pix_candidate_colrow": list(pos["candidate"]), "pix_target_colrow": list(pos["target"]),
           "sep_pix": float(np.hypot(pos["candidate"][0] - pos["target"][0], pos["candidate"][1] - pos["target"][1])),
           "events": []}
    events, outmask = [], np.zeros(len(t), bool)
    for e in sorted(set(n[good & (np.abs(dt) < HALF_IN)].astype(int))):
        sel = good & (n == e)
        ins = sel & (np.abs(dt) < HALF_IN)
        outs = sel & (np.abs(dt) >= OUT_LO) & (np.abs(dt) <= OUT_HI)
        nb, na = int((outs & (dt < 0)).sum()), int((outs & (dt > 0)).sum())
        ok = bool(ins.sum() >= 3 and outs.sum() >= 10 and nb >= 3 and na >= 3)
        rec["events"].append({"epoch": e, "tmid": float(T0 + e * P), "n_in": int(ins.sum()), "n_out_before": nb,
                              "n_out_after": na, "used": ok})
        if not ok:
            continue
        fo, fi = fl[outs], fl[ins]
        s = np.nanstd(fo, axis=0, ddof=1)
        d = np.nanmean(fo, axis=0) - np.nanmean(fi, axis=0)
        var = s ** 2 * (1.0 / ins.sum() + 1.0 / outs.sum())
        d[~valid] = 0.0; var[~valid] = np.inf
        events.append({"diff": d, "var": var}); outmask |= outs
    rec["n_events_used"] = len(events)
    direct = np.nanmean(fl[outmask if outmask.any() else good], axis=0)
    rec["direct_centroid_colrow"] = list(flux_centroid(np.nan_to_num(direct), valid)[:2])
    rec["direct_centroid_minus_target_pix"] = float(np.hypot(rec["direct_centroid_colrow"][0] - pos["target"][0],
                                                            rec["direct_centroid_colrow"][1] - pos["target"][1]))
    products[f"q{q}_direct"] = direct; products[f"q{q}_valid"] = valid
    if not events:
        rec["usable"] = False; quarters.append(rec); print(f"Q{q:2d}: no usable events"); continue
    rec["usable"] = True
    D, V = stack(events)
    products[f"q{q}_diff"] = D; products[f"q{q}_var"] = V
    cov = describe(rec, D, V, valid, direct, pos, events)
    c_col, c_row = rec["diff_centroid_colrow"]
    ra_c, de_c = np.array(wcs.all_pix2world([c_col], [c_row], 0)).ravel()
    for k, (ra, de) in STARS.items():
        rec[f"diff_centroid_offset_from_{k}_EN_arcsec"] = [float((ra_c - ra) * np.cos(np.radians(de)) * 3600), float((de_c - de) * 3600)]
    J = np.zeros((2, 2)); eps = 0.01
    for j in range(2):
        p = [c_col, c_row]; p[j] += eps
        ra2, de2 = np.array(wcs.all_pix2world([p[0]], [p[1]], 0)).ravel()
        J[0, j] = (ra2 - ra_c) * np.cos(np.radians(de_c)) * 3600 / eps; J[1, j] = (de2 - de_c) * 3600 / eps
    rec["diff_centroid_cov_EN_arcsec2"] = (J @ cov @ J.T).tolist()
    products[f"q{q}_events_D"] = np.array([e["diff"] for e in events]); products[f"q{q}_events_V"] = np.array([e["var"] for e in events])
    quarters.append(rec)
    tsf = rec["two_source_fit"]
    print(f"Q{q:2d} s{season} ev={len(events)} cen=({c_col:.2f},{c_row:.2f}) dC={rec['diff_centroid_dist_to_candidate_pix']:.2f} "
          f"dT={rec['diff_centroid_dist_to_target_pix']:.2f} sig={np.sqrt(np.trace(cov)/2):.2f}({rec['adopted_sigma_source']}) nearer={rec['nearer']} "
          f"snrcen=({rec['snrbox_diff_centroid_colrow'][0]:.2f},{rec['snrbox_diff_centroid_colrow'][1]:.2f}) bright={rec['brightest_diff_pixel_rowcol']} "
          f"ratio={rec['box3_ratio_candidate_over_target']:.2f} maxSNR={rec['max_snr']:.1f}@{rec['max_snr_diff_pixel_rowcol']} "
          f"fracdepth C={rec['box3_candidate']['frac_depth_ppm']:.0f} T={rec['box3_target']['frac_depth_ppm']:.0f} "
          f"fit C={tsf['implied_depth_ppm_candidate']:.0f}+-{tsf['implied_depth_ppm_candidate_err']:.0f} T={tsf['implied_depth_ppm_target']:.0f}+-{tsf['implied_depth_ppm_target_err']:.0f}", flush=True)

# ---------- per-season stacks on the CCD pixel grid (same season = same module/output and orientation) ----------
seasons = {}
for s in sorted({r["season"] for r in quarters if r["usable"]}):
    qs = [r for r in quarters if r["usable"] and r["season"] == s]
    c0 = min(r["ccd_origin_colrow"][0] for r in qs); r0 = min(r["ccd_origin_colrow"][1] for r in qs)
    c1 = max(r["ccd_origin_colrow"][0] + r["stamp_shape"][1] for r in qs); r1 = max(r["ccd_origin_colrow"][1] + r["stamp_shape"][0] for r in qs)
    shp = (r1 - r0, c1 - c0)
    dsum = np.zeros(shp); dn = np.zeros(shp); evD, evV, pc, pt = [], [], [], []
    for r in qs:
        q = r["quarter"]; oc, orow = r["ccd_origin_colrow"][0] - c0, r["ccd_origin_colrow"][1] - r0
        hh, ww = r["stamp_shape"]; sl = (slice(orow, orow + hh), slice(oc, oc + ww))
        v = products[f"q{q}_valid"]
        for D1, V1 in zip(products[f"q{q}_events_D"], products[f"q{q}_events_V"]):
            Dg = np.zeros(shp); Vg = np.full(shp, np.inf); Dg[sl] = np.where(v, D1, 0); Vg[sl] = np.where(v, V1, np.inf)
            evD.append(Dg); evV.append(Vg)
        dsum[sl] += np.where(v, products[f"q{q}_direct"], 0); dn[sl] += v
        pc.append((r["pix_candidate_colrow"][0] + oc, r["pix_candidate_colrow"][1] + orow))
        pt.append((r["pix_target_colrow"][0] + oc, r["pix_target_colrow"][1] + orow))
    events = [{"diff": d, "var": v} for d, v in zip(evD, evV)]
    nfin = np.sum([np.isfinite(e["var"]) for e in events], axis=0)
    valid = nfin == len(events)            # pixels present in every quarter of the season
    for e in events:
        e["diff"] = np.where(valid, e["diff"], 0.0); e["var"] = np.where(valid, e["var"], np.inf)
    D, V = stack(events)
    direct = np.where(dn > 0, dsum / np.maximum(dn, 1), np.nan)
    pos = {"candidate": tuple(np.mean(pc, axis=0)), "target": tuple(np.mean(pt, axis=0))}
    srec = {"season": s, "quarters": [r["quarter"] for r in qs], "n_events": len(events), "grid_ccd_origin_colrow": [c0, r0],
            "n_valid_pixels": int(valid.sum()),
            "pix_candidate_colrow": list(pos["candidate"]), "pix_target_colrow": list(pos["target"]),
            "pos_scatter_between_quarters_pix": float(np.max(np.std(pc, axis=0)))}
    describe(srec, D, V, valid, direct, pos, events)
    srec["direct_centroid_colrow"] = list(flux_centroid(np.nan_to_num(direct), valid)[:2])
    seasons[s] = srec
    products[f"s{s}_diff"] = D; products[f"s{s}_var"] = V; products[f"s{s}_direct"] = direct; products[f"s{s}_valid"] = valid
    tsf = srec["two_source_fit"]
    print(f"season {s}: Q{srec['quarters']} ev={len(events)} cen=({srec['diff_centroid_colrow'][0]:.2f},{srec['diff_centroid_colrow'][1]:.2f}) "
          f"C=({pos['candidate'][0]:.2f},{pos['candidate'][1]:.2f}) T=({pos['target'][0]:.2f},{pos['target'][1]:.2f}) "
          f"dC={srec['diff_centroid_dist_to_candidate_pix']:.2f}+-{srec['diff_centroid_dist_to_candidate_sigma_pix']:.2f} "
          f"dT={srec['diff_centroid_dist_to_target_pix']:.2f}+-{srec['diff_centroid_dist_to_target_sigma_pix']:.2f} nearer={srec['nearer']} "
          f"ratio={srec['box3_ratio_candidate_over_target']:.2f} maxSNR={srec['max_snr']:.1f}@{srec['max_snr_diff_pixel_rowcol']} "
          f"fracdepth C={srec['box3_candidate']['frac_depth_ppm']:.0f}+-{srec['box3_candidate']['frac_depth_ppm_err']:.0f} "
          f"T={srec['box3_target']['frac_depth_ppm']:.0f}+-{srec['box3_target']['frac_depth_ppm_err']:.0f} "
          f"fit C={tsf['implied_depth_ppm_candidate']:.0f}+-{tsf['implied_depth_ppm_candidate_err']:.0f} "
          f"T={tsf['implied_depth_ppm_target']:.0f}+-{tsf['implied_depth_ppm_target_err']:.0f}", flush=True)

# ---------- summary ----------
U = [r for r in quarters if r["usable"]]
dC = np.array([r["diff_centroid_dist_to_candidate_pix"] for r in U]); dT = np.array([r["diff_centroid_dist_to_target_pix"] for r in U])
mq = {}   # multi-quarter inverse-variance mean sky offset of the difference centroid from each star (as in Kepler DV)
for k in ("candidate", "target"):
    num = np.zeros(2); W = np.zeros((2, 2))
    for r in U:
        Ci = np.linalg.inv(np.array(r["diff_centroid_cov_EN_arcsec2"])); W += Ci; num += Ci @ np.array(r[f"diff_centroid_offset_from_{k}_EN_arcsec"])
    C = np.linalg.inv(W); m = C @ num
    mq[k] = {"offset_EN_arcsec": m.tolist(), "sigma_EN_arcsec": np.sqrt(np.diag(C)).tolist(),
             "offset_arcsec": float(np.hypot(*m)), "offset_significance_sigma": float(np.sqrt(m @ W @ m))}
summary = {
    "n_quarters_total": len(quarters), "n_quarters_usable": len(U),
    "unusable_quarters": [r["quarter"] for r in quarters if not r["usable"]],
    "n_events_used": int(sum(r["n_events_used"] for r in U)),
    "n_events_rejected": int(sum(sum(not e["used"] for e in r["events"]) for r in quarters)),
    "n_quarters_centroid_nearer_candidate": int(np.sum(dC < dT)),
    "n_quarters_brightest_diff_pixel_nearer_candidate": int(sum(r["brightest_diff_pixel_dist_to_candidate_pix"] < r["brightest_diff_pixel_dist_to_target_pix"] for r in U)),
    "n_quarters_max_snr_pixel_nearer_candidate": int(sum(np.hypot(r["max_snr_diff_pixel_rowcol"][1] - r["pix_candidate_colrow"][0], r["max_snr_diff_pixel_rowcol"][0] - r["pix_candidate_colrow"][1])
                                                         < np.hypot(r["max_snr_diff_pixel_rowcol"][1] - r["pix_target_colrow"][0], r["max_snr_diff_pixel_rowcol"][0] - r["pix_target_colrow"][1]) for r in U)),
    "n_quarters_snrbox_centroid_nearer_candidate": int(sum(r["snrbox_diff_centroid_dist_to_candidate_pix"] < r["snrbox_diff_centroid_dist_to_target_pix"] for r in U)),
    "n_quarters_box3_frac_depth_candidate_gt_target_3sigma": int(sum((r["box3_candidate"]["frac_depth_ppm"] - r["box3_target"]["frac_depth_ppm"])
        > 3 * np.hypot(r["box3_candidate"]["frac_depth_ppm_err"], r["box3_target"]["frac_depth_ppm_err"]) for r in U)),
    "two_source_fit_depth_ppm_candidate_weighted_mean": None, "two_source_fit_depth_ppm_target_weighted_mean": None,
    "n_quarters_box3_ratio_gt1": int(sum(r["box3_ratio_candidate_over_target"] > 1 for r in U)),
    "median_diff_centroid_dist_to_candidate_pix": float(np.median(dC)), "median_diff_centroid_dist_to_candidate_arcsec": float(np.median(dC) * PIX_ARCSEC),
    "median_diff_centroid_dist_to_target_pix": float(np.median(dT)), "median_diff_centroid_dist_to_target_arcsec": float(np.median(dT) * PIX_ARCSEC),
    "median_direct_centroid_minus_target_pix": float(np.median([r["direct_centroid_minus_target_pix"] for r in quarters])),
    "multi_quarter_mean_offset": mq,
    "seasons_nearer": {str(s): seasons[s]["nearer"] for s in seasons},
    "seasons_snrbox_centroid_dist_to_candidate_pix": {str(s): seasons[s]["snrbox_diff_centroid_dist_to_candidate_pix"] for s in seasons},
    "seasons_snrbox_centroid_dist_to_target_pix": {str(s): seasons[s]["snrbox_diff_centroid_dist_to_target_pix"] for s in seasons},
    "seasons_max_snr": {str(s): seasons[s]["max_snr"] for s in seasons},
    "notes": ["diff_centroid_* is the specified estimator (3x3 box around the brightest difference pixel by flux). Per quarter "
              "the brightest pixel is often a noise excursion in the target's core (its S/N is listed as brightest_diff_pixel_snr), "
              "so this estimator is bimodal; its bootstrap/MC covariance and the multi_quarter_mean_offset built from it are not "
              "reliable. snrbox_* uses the same 3x3 flux-weighted centroid boxed on the highest-S/N difference pixel.",
              "two_source_fit uses a Gaussian PSF (not the Kepler PRF) with both stars at their Gaia/WCS positions; it is an "
              "approximate check, implied depth = fitted difference amplitude / fitted direct amplitude of the same star."],
}
for k in ("candidate", "target"):
    v = np.array([r["two_source_fit"][f"implied_depth_ppm_{k}"] for r in U]); e = np.array([r["two_source_fit"][f"implied_depth_ppm_{k}_err"] for r in U])
    w = 1 / e ** 2
    summary[f"two_source_fit_depth_ppm_{k}_weighted_mean"] = [float((w * v).sum() / w.sum()), float(np.sqrt(1 / w.sum()))]
res = {"inputs": {"period_d": P, "t0_bkjd": T0, "duration_d": DUR, "in_transit": "|dt| < 0.5 dur",
                  "out_of_transit": "1.0 dur <= |dt| <= 3.0 dur, per event", "event_requirement": ">=3 in, >=10 out, >=3 out on each side",
                  "quality": "QUALITY == 0 and finite flux in all valid pixels", "stars": STARS, "pixel_arcsec": PIX_ARCSEC,
                  "n_boot": NBOOT, "n_mc": NMC, "input_depth_ppm": DEPTH_PPM, "kp_flux_ratio_target_over_candidate": FLUX_RATIO_T_OVER_C},
       "quarters": quarters, "seasons": {str(k): v for k, v in seasons.items()}, "summary": summary}
(OUT_DIR / "results.json").write_text(json.dumps(res, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
np.savez_compressed(OUT_DIR / "diffimage_products.npz", **products)
print(json.dumps(summary, indent=1))
