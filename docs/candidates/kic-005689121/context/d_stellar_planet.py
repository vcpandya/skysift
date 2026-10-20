"""D: stellar parameters (Mamajek dwarf table at BP-RP, Mann et al. M_Ks relations as an extinction-insensitive check,
TIC v8.2 and Gaia DR3 for comparison) and derived planet properties by Monte Carlo (N=200000)."""
import json, csv, io, pathlib, re
import numpy as np
from zero_point import zpt

HERE = pathlib.Path(__file__).parent; PROOF = HERE / "archive-proof"
rng = np.random.default_rng(5689121); N = 200_000
gs = next(csv.DictReader(io.StringIO((PROOF / "B_gaia_source_candidate.csv").read_text())))
ap = next(csv.DictReader(io.StringIO((PROOF / "B_gaia_astrophys_candidate.csv").read_text())))
G, BPRP = float(gs["phot_g_mean_mag"]), float(gs["bp_rp"]); plx, eplx = float(gs["parallax"]), float(gs["parallax_error"])
zpt.load_tables()
zp = float(zpt.get_zpt(np.array([G]), np.array([float(gs["nu_eff_used_in_astrometry"])]), np.array([-999.]),
                       np.array([float(gs["ecl_lat"])]), np.array([int(gs["astrometric_params_solved"])]))[0])
plx_c, eplx_c = plx - zp, float(np.hypot(eplx, 0.015))  # 0.015 mas zero-point systematic added in quadrature
man = json.loads((PROOF / "manifest.json").read_text()); TIC = man["_candidate_tic"]
exo = json.loads((PROOF / f"D_exofop_tic{TIC}.json").read_text(encoding="utf-8"))
mags = {m["band"]: (float(m["value"]), float(m["value_e"] or 0.03)) for m in exo["magnitudes"] if m["value"]}
sp = exo["stellar_parameters"]
tic = next((x for x in sp if x.get("teff") and "TIC" in str(x.get("prov", "")) ), next((x for x in sp if x.get("teff")), sp[0]))

# --- Mamajek dwarf table (columns: SpT Teff logT BCv logL Mbol R Mv B-V Bt-Vt G-V Bp-Rp G-Rp M_G ... M_Ks(22) ... Msun(30))
rows = []
for line in (PROOF / "D_mamajek_dwarf_table.txt").read_text().splitlines()[23:140]:
    p = line.split()
    if len(p) > 30 and re.match(r"^[OBAFGKML]\d", p[0]):
        try:
            rows.append((float(p[11]), float(p[1]), float(p[6]), float(p[30]) if p[30] != "..." else np.nan,
                         float(p[13]), float(p[22]), p[0]))
        except ValueError:
            pass
tab = np.array([r[:6] for r in rows]); spt = [r[6] for r in rows]
sel = (tab[:, 0] > 1.3) & (tab[:, 0] < 3.0) & np.isfinite(tab[:, 3])
tab = tab[sel]; spt = [s_ for s_, k_ in zip(spt, sel) if k_]
o = np.argsort(tab[:, 0]); tab = tab[o]; spt = [spt[i] for i in o]


def interp(c, col):
    return np.interp(c, tab[:, 0], tab[:, col])


def mam(c):
    return {"Teff": interp(c, 1), "R": interp(c, 2), "M": interp(c, 3), "M_G": interp(c, 4), "M_Ks": interp(c, 5)}


dist = 1000 / plx_c; DM = 5 * np.log10(dist) - 5
dust = (PROOF / "D_irsa_dust.xml").read_text(encoding="utf-8", errors="replace")
def _ebv(tag):
    m = re.search(r"<" + tag + r">\s*([0-9.]+)", dust); return float(m.group(1)) if m else None
EBV_SFD, EBV_SF11 = _ebv("meanValueSFD"), _ebv("meanValueSandF")
Ks, eKs = mags["Ks"] if "Ks" in mags else mags["K"]
MG_obs, MKs_obs = G - DM, Ks - DM

# Monte Carlo
c0 = rng.normal(BPRP, 0.10, N)  # BP-RP (observed 2.19) +- 0.1, E(BP-RP) <= 0.1 assumed
Teff, R, M = interp(c0, 1), interp(c0, 2), interp(c0, 3)
plx_s = rng.normal(plx_c, eplx_c, N); dist_s = 1000 / plx_s
MKs_s = rng.normal(Ks, eKs, N) - (5 * np.log10(dist_s) - 5) - rng.uniform(0.0, 0.07, N)  # A_Ks 0-0.07
R_mann = (1.9515 - 0.3520 * MKs_s + 0.01680 * MKs_s ** 2) * rng.normal(1, 0.0289, N)  # Mann et al. 2015
x = MKs_s - 7.5  # Mann et al. 2019, n=5 polynomial
M_mann = 10 ** (-0.642 - 0.208 * x - 8.43e-4 * x ** 2 + 7.87e-3 * x ** 3 + 1.42e-4 * x ** 4 - 2.13e-4 * x ** 5) * rng.normal(1, 0.021, N)


def s(a, nd=3):
    q = np.percentile(a, [16, 50, 84])
    return {"median": round(float(q[1]), nd), "minus": round(float(q[1] - q[0]), nd), "plus": round(float(q[2] - q[1]), nd)}


P, Dbox, k, b_fit, Tobs = 13.769508917830883, 1983e-6, 0.046, 0.86, 2.0
G_SI, MSUN, RSUN, AU, REARTH = 6.674e-11, 1.98892e30, 6.957e8, 1.495978707e11, 6.3781e6


def planet(Teff, R, M, tag):
    n = len(R)
    a_m = (G_SI * M * MSUN * (P * 86400) ** 2 / (4 * np.pi ** 2)) ** (1 / 3); aR = a_m / (R * RSUN)
    Rp_k = k * R * RSUN / REARTH; Rp_box = np.sqrt(Dbox) * R * RSUN / REARTH
    Teq = Teff * np.sqrt(1 / (2 * aR)) * 0.7 ** 0.25
    T0 = P * 24 / np.pi / aR  # hours; T14 = T0*sqrt((1+k)^2-b^2) for a circular orbit, small R*/a
    T14_central = T0 * (1 + k)
    Tobs_s = rng.normal(Tobs, 0.25, n)  # 0.25 h ~ grid / cadence uncertainty on the 2.0 h box duration
    b_T14 = np.sqrt(np.clip((1 + k) ** 2 - (Tobs_s / T0) ** 2, 0, None))
    bb = np.linspace(0, 1 - k, 2001); f = np.sqrt((1 + k) ** 2 - bb ** 2) + np.sqrt((1 - k) ** 2 - bb ** 2)
    b_mid = np.interp(-2 * Tobs_s / T0, -f, bb, left=0, right=np.nan)  # observed duration = (T14+T23)/2
    T14_b089 = T0 * np.sqrt((1 + k) ** 2 - b_fit ** 2)
    rho_star = M * MSUN / (4 / 3 * np.pi * (R * RSUN) ** 3) / 1000
    return {"label": tag, "Teff_K": s(Teff, 0), "R_star_Rsun": s(R), "M_star_Msun": s(M), "rho_star_gcc": s(rho_star, 2),
            "a_AU": s(a_m / AU, 4), "a_over_Rstar": s(aR, 1), "Rp_from_k_Rearth": s(Rp_k, 2), "Rp_from_boxdepth_Rearth": s(Rp_box, 2),
            "Teq_K_A0.3_full_redistribution": s(Teq, 0), "transit_probability_Rstar_over_a": s(1 / aR, 4),
            "T14_central_h": s(T14_central, 2), "b_if_obs_dur_is_T14": s(b_T14, 2),
            "b_if_obs_dur_is_mean_T14_T23": s(b_mid[np.isfinite(b_mid)], 2),
            "fraction_b_mid_undefined": round(float(np.mean(~np.isfinite(b_mid))), 3),
            "T14_for_fitted_b_h": s(T14_b089, 2)}


def r3(d):
    return {k_: round(float(v), 3) for k_, v in d.items()}


out = {"inputs": {"G": G, "BP_RP": BPRP, "parallax_mas": plx, "parallax_err_mas": eplx, "zero_point_L21_mas": round(zp, 4),
                  "parallax_corrected_mas": round(plx_c, 4), "parallax_err_used_mas": round(eplx_c, 4), "Ks_2MASS": [Ks, eKs],
                  "P_d": P, "box_depth_ppm": 1983, "k": k, "b_fit": b_fit, "duration_h": Tobs},
       "distance_pc": s(dist_s, 1), "distance_pc_simple": round(dist, 1),
       "abs_G_mag_noext": round(MG_obs, 3), "abs_Ks_mag_noext": round(MKs_obs, 3),
       "mamajek_at_observed_colour": r3(mam(BPRP)), "mamajek_at_2.10": r3(mam(2.10)), "mamajek_at_2.20": r3(mam(2.20)), "mamajek_at_2.30": r3(mam(2.30)),
       "mamajek_at_2.00_if_E0.2": r3(mam(2.00)),
       "mamajek_rows_used": [dict(zip(["SpT", "BP_RP", "Teff", "R", "M", "M_G", "M_Ks"], [sp, *map(float, r)]))
                             for sp, r in zip(spt, tab) if 1.7 < r[0] < 2.6],
       "mann_MKs_check": {"R_Mann2015_Rsun": s(R_mann), "M_Mann2019_Msun": s(M_mann), "M_Ks": s(MKs_s, 3)},
       "tic_v82": {"Teff": tic["teff"], "Teff_e": tic["teff_e"], "R": tic["srad"], "R_e": tic["srad_e"], "M": tic["mass"],
                   "M_e": tic["mass_e"], "dist_pc": tic["dist"], "logg": tic["logg"],
                   "source": f"ExoFOP target.php?id={TIC}&json ({tic.get('prov')})"},
       "gaia_dr3_apsis": {k_: ap[k_] for k_ in ("teff_gspphot", "radius_gspphot", "ebpminrp_gspphot", "ag_gspphot", "radius_flame",
                                                "mass_flame", "teff_msc1", "mh_msc", "mh_gspphot", "spectraltype_esphs")},
       "extinction": {"SFD98_EBV_total_los": EBV_SFD, "SF11_EBV_total_los": EBV_SF11, "b_deg": float(gs["b"]),
                      "SF11_scaled_to_distance_h125pc": round(EBV_SF11 * (1 - np.exp(-dist * np.sin(np.radians(abs(float(gs["b"])))) / 125)), 3),
                      "note": "Bayestar19 (argonaut.skymaps.info) timed out from this machine; values from IRSA DUST (2-D, full line of sight)."}}
out["planet_baseline_observed_colour_pm0.10"] = planet(Teff, R, M, f"Mamajek at BP-RP {BPRP:.2f} +- 0.10 (E(BP-RP) <= 0.1 assumed)")
c1 = rng.normal(BPRP - 0.2, 0.10, N)
out["planet_alt_dereddened_minus0.2_pm0.10"] = planet(interp(c1, 1), interp(c1, 2), interp(c1, 3), f"Mamajek at BP-RP {BPRP-0.2:.2f} +- 0.10 (if E(BP-RP) ~ 0.2)")
Teff_mann = float(np.median(Teff))
out["planet_mann_MKs"] = planet(rng.normal(Teff_mann, 100, N), R_mann, M_mann, f"Mann M_Ks radius and mass, Teff {Teff_mann:.0f} +- 100")
(HERE / "results_D.json").write_text(json.dumps(out, indent=1, default=float))
print(json.dumps({k_: v for k_, v in out.items() if k_ != "mamajek_rows_used"}, indent=1, default=float))
