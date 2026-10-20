"""A.2 Neighbour leak test and candidate SAP-vs-PSF depth.
Box depth in a 3.0 h window at P=40.29109 d, t0=135.788 BKJD, after a 2-day running median (transits masked) per quarter.
depth = mean(out) - mean(in); out = 1..3 durations from mid-transit; formal error = std(out)*sqrt(1/N_in+1/N_out);
inflated by the scatter of 100 control phases (phase 0.03..0.97)."""
import json, pathlib, glob
import numpy as np
from astropy.io import fits
HERE = pathlib.Path(__file__).parent
P, T0, DUR = 40.29109, 135.788, 3.0 / 24
CAND = pathlib.Path(r"C:\Research\skysift\.superpowers\sample-run\blobs\8a\8a56c51dfb58d9d1de7f946d008872830df508fb9f4fe124b1c39553194a85ef_hlsp_kbonus-bkg_kepler_kepler_kic-006146676_kepler_v1.0_lc.fits")

def dt_of(t, t0=T0): return (t - t0 + P / 2) % P - P / 2

def detrend(t, f, q, window=2.0):
    """Per quarter: divide by running median (+-window/2) computed with in-transit points (|dt|<DUR) excluded."""
    out = np.full_like(f, np.nan)
    keep = np.abs(dt_of(t)) >= DUR
    for qq in np.unique(q):
        m = q == qq; tq, fq, kq = t[m], f[m], keep[m]
        tk, fk = tq[kq], fq[kq]
        lo = np.searchsorted(tk, tq - window / 2); hi = np.searchsorted(tk, tq + window / 2)
        med = np.array([np.median(fk[a:b]) if b - a >= 10 else np.nan for a, b in zip(lo, hi)])
        out[m] = fq / med
    return out

def box(t, fn, t0=T0):
    d = np.abs(dt_of(t, t0)); ok = np.isfinite(fn)
    fn = np.asarray(fn, float); fin = fn[ok & (d < DUR / 2)]; fout = fn[ok & (d >= DUR) & (d <= 3 * DUR)]
    if len(fin) < 5 or len(fout) < 10: return np.nan, np.nan, len(fin), len(fout)
    s = np.std(fout, ddof=1)
    return (np.mean(fout) - np.mean(fin)) * 1e6, s * np.sqrt(1 / len(fin) + 1 / len(fout)) * 1e6, len(fin), len(fout)

def measure(t, fn, label):
    dep, err, nin, nout = box(t, fn)
    ctrl = []
    for ph in np.linspace(0.03, 0.97, 100):
        ctrl.append(box(t, fn, T0 + ph * P)[:2])
    ctrl = np.array(ctrl); good = np.isfinite(ctrl[:, 0])
    scat = float(np.std(ctrl[good, 0], ddof=1)); infl = max(1.0, scat / float(np.median(ctrl[good, 1])))
    n_ev = len(np.unique(np.round((t[np.abs(dt_of(t)) < DUR / 2] - T0) / P)))
    r = {"label": label, "depth_ppm": round(dep, 1), "formal_err_ppm": round(err, 1), "control_scatter_ppm": round(scat, 1),
         "control_mean_ppm": round(float(np.mean(ctrl[good, 0])), 1), "inflation": round(infl, 3),
         "err_ppm": round(err * infl, 1), "snr": round(dep / (err * infl), 2), "n_in": nin, "n_out": nout,
         "n_events_with_data": n_ev, "n_control_phases": int(good.sum()),
         "rms_ppm_per_cadence": round(float(np.nanstd(fn[np.abs(dt_of(t)) > 3 * DUR])) * 1e6, 1)}
    print(json.dumps(r, default=float)); return r


def main():
    res = {"ephemeris": {"P_d": P, "t0_bkjd": T0, "dur_h": 3.0}, "method": __doc__}
    # --- bright neighbour KIC 6146666, Kepler mission LLC (PDCSAP and SAP) ---
    T, PDC, SAP, Q, hdr = [], [], [], [], {}
    for fn in sorted(glob.glob(str(HERE / "downloads" / "kic006146666_llc" / "*_llc.fits"))):
        with fits.open(fn, memmap=False) as h:
            d = h[1].data; q = int(h[0].header["QUARTER"])
            hdr[q] = {"CROWDSAP": h[1].header.get("CROWDSAP"), "FLFRCSAP": h[1].header.get("FLFRCSAP"), "file": pathlib.Path(fn).name}
            m = (d["SAP_QUALITY"] == 0) & np.isfinite(d["TIME"]) & np.isfinite(d["PDCSAP_FLUX"]) & np.isfinite(d["SAP_FLUX"])
            T.append(d["TIME"][m]); PDC.append(d["PDCSAP_FLUX"][m]); SAP.append(d["SAP_FLUX"][m]); Q.append(np.full(m.sum(), q))
    T, PDC, SAP, Q = (np.concatenate(x).astype(float) for x in (T, PDC, SAP, Q)); Q = Q.astype(int); o = np.argsort(T); T, PDC, SAP, Q = T[o], PDC[o], SAP[o], Q[o]
    res["neighbour_KIC6146666"] = {"quarters": hdr, "n_cadences": int(len(T)),
        "pdcsap": measure(T, detrend(T, PDC, Q), "KIC 6146666 PDCSAP"),
        "sap": measure(T, detrend(T, SAP, Q), "KIC 6146666 SAP")}
    # expected depths
    kp_c, kp_t, D = 16.26, 12.85, 2165.0
    ratio = 10 ** (0.4 * (kp_c - kp_t))
    res["neighbour_expectation"] = {"flux_ratio_target_over_candidate": round(ratio, 2),
        "leak_case_max_depth_on_target_ppm": round(D / (1 + ratio), 1),
        "leak_case_max_depth_on_target_ppm_simple": round(D / ratio, 1),
        "on_target_case_min_depth_ppm": D,
        "note": "Leak case: candidate light fully mixed into the target aperture gives at most D*Fc/(Fc+Ft); in reality the target's optimal aperture holds only part of the candidate's light 11.5 arcsec (2.9 px) away, so less. On-target case: a target dip D_t leaking a fraction e of the target flux into the candidate photometry gives D_c = D_t*e*Ft/(Fc+e*Ft) < D_t, so D_t > 2165 ppm."}
    # --- candidate KBonus PSF vs SAP ---
    with fits.open(CAND, memmap=False) as h:
        d = h[1].data
        cq = {int(x.header["QUARTER"]): {k: x.header.get(k) for k in ("CROWDSAP", "FLFRCSAP", "NPIXSAP", "PSFFRAC", "SEASON")}
              for x in h[2:] if x.name.startswith("LIGHTCURVE_Q")}
        t = np.asarray(d["TIME"], float); psf = np.asarray(d["FLUX"], float); sap = np.asarray(d["SAP_FLUX"], float)
        q = np.asarray(d["QUARTER"], int); qual = np.asarray(d["SAP_QUALITY"], int)
    m = (qual == 0) & np.isfinite(t) & np.isfinite(psf) & np.isfinite(sap) & (psf > 0) & (sap > 0)
    t, psf, sap, q = t[m], psf[m], sap[m], q[m]; o = np.argsort(t); t, psf, sap, q = t[o], psf[o], sap[o], q[o]
    fpsf, fsap = detrend(t, psf, q), detrend(t, sap, q)
    rp, rs = measure(t, fpsf, "KIC 6146676 KBonus PSF"), measure(t, fsap, "KIC 6146676 KBonus SAP")
    ratio_sp = rs["depth_ppm"] / rp["depth_ppm"]
    ratio_err = ratio_sp * np.hypot(rs["err_ppm"] / rs["depth_ppm"], rp["err_ppm"] / rp["depth_ppm"])
    season = {}
    for s in range(4):
        qs = [qq for qq, v in cq.items() if v["SEASON"] == s]; mm = np.isin(q, qs)
        a, b = measure(t[mm], fpsf[mm], f"PSF season {s}"), measure(t[mm], fsap[mm], f"SAP season {s}")
        season[s] = {"quarters": qs, "psf": a, "sap": b, "mean_crowdsap": round(float(np.mean([cq[x]["CROWDSAP"] for x in qs])), 4)}
    cr = np.array([v["CROWDSAP"] for v in cq.values()], float)
    res["candidate_KIC6146676"] = {"psf": rp, "sap": rs, "sap_over_psf": round(ratio_sp, 3), "sap_over_psf_err": round(float(ratio_err), 3),
        "kbonus_crowdsap_median": round(float(np.median(cr)), 4), "kbonus_crowdsap_range": [round(float(cr.min()), 4), round(float(cr.max()), 4)],
        "per_quarter_header": cq, "per_season": season}
    (HERE / "results_A.json").write_text(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
