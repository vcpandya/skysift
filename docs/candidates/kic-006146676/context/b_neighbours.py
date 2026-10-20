"""B: Gaia DR3 neighbours within 30 arcsec of KIC 6146676 and whether each could be a blended eclipsing binary
that produces the 2165 ppm dip (required eclipse depth = 2165 ppm * 10^(0.4*dG), possible only if < 100 %, i.e. dG < 6.7).
The magnitude criterion assumes full blending; PSF photometry that models each Gaia source separately
suppresses the light of resolved neighbours, so the real requirement is deeper. Also lists the variability / NSS results."""
import json, csv, io, pathlib
import numpy as np

HERE = pathlib.Path(__file__).parent; PROOF = HERE / "archive-proof"
D = 2165e-6; PIX = 3.98
rows = list(csv.DictReader(io.StringIO((PROOF / "B_gaia_cone30.csv").read_text())))
cand = rows[0]; assert cand["source_id"] == "2073896294004902272"
Gc = float(cand["phot_g_mean_mag"]); plx_c = float(cand["parallax"]); pmra_c, pmdec_c = float(cand["pmra"]), float(cand["pmdec"])
out = []
for r in rows[1:]:
    g = float(r["phot_g_mean_mag"]); dg = g - Gc
    plx = float(r["parallax"]); poe = float(r["parallax_over_error"]); pmra, pmdec = float(r["pmra"]), float(r["pmdec"])
    req = D * 10 ** (0.4 * dg)
    out.append({"source_id": r["source_id"], "ra": float(r["ra"]), "dec": float(r["dec"]),
                "sep_arcsec": round(float(r["sep_arcsec"]), 2), "sep_kepler_px": round(float(r["sep_arcsec"]) / PIX, 2),
                "G": round(g, 3), "bp_rp": round(float(r["bp_rp"]), 3) if r["bp_rp"] else None,
                "parallax": round(plx, 3), "parallax_over_error": round(poe, 2), "pmra": round(pmra, 2), "pmdec": round(pmdec, 2),
                "ruwe": round(float(r["ruwe"]), 3), "dG_vs_candidate": round(dg, 3),
                "required_eclipse_depth_frac": round(req, 4) if dg > 0 else None,
                "can_mimic_by_magnitude": bool(dg > 0 and req < 1.0),
                "note": ("brighter than the candidate; tested directly in check A (own Kepler light curve)" if dg <= 0 else
                         "co-moving? " + ("yes" if abs(plx - plx_c) < 3 * 0.1 and np.hypot(pmra - pmra_c, pmdec - pmdec_c) < 3 else "no"))})
kic_only = {"KIC": 6146680, "sep_arcsec": 3.99, "Kp": 18.852, "gmag": 18.542, "rmag": 18.786, "imag": 18.985,
            "required_eclipse_depth_frac": round(D * 10 ** (0.4 * (18.852 - 16.257)), 4),
            "note": "KIC source with no 2MASS and no Gaia DR3 counterpart within 3 arcsec; the nearest Gaia source "
                    "(2073896294004902144, G 19.77) lies 3.2 arcsec from the KIC position. Probably the same object with poor "
                    "KIC astrometry/photometry, or a spurious KIC entry; Gaia should resolve a G~19 star 4 arcsec from a G 16.2 star."}
var_tables = {}
for t in ("vari_summary", "vari_classifier_result", "nss_two_body_orbit", "vari_eclipsing_binary", "nss_non_linear_spectro", "nss_acceleration_astro"):
    txt = (PROOF / f"B_gaia_{t}_cone30_ids.csv").read_text().strip().splitlines()
    var_tables[t + "_cone30"] = {"rows": len(txt) - 1, "verbatim": txt}
for t in ("vari_summary", "vari_classifier_result", "nss_two_body_orbit", "vari_eclipsing_binary"):
    txt = (PROOF / f"B_gaia_{t}_candidate.csv").read_text().strip().splitlines()
    var_tables[t + "_candidate"] = {"rows": len(txt) - 1}
gs = next(csv.DictReader(io.StringIO((PROOF / "B_gaia_source_candidate.csv").read_text())))
res = {"candidate_gaia_source": {k: gs[k] for k in ("source_id", "parallax", "parallax_error", "ruwe", "radial_velocity", "radial_velocity_error",
                                                   "phot_variable_flag", "non_single_star", "ipd_frac_multi_peak", "ipd_gof_harmonic_amplitude",
                                                   "astrometric_excess_noise", "phot_bp_rp_excess_factor", "has_rvs", "pmra", "pmdec")},
       "n_neighbours_30arcsec": len(out), "neighbours": out,
       "neighbours_able_to_mimic_by_magnitude": [o["source_id"] for o in out if o["can_mimic_by_magnitude"]],
       "max_required_depth_frac": max(o["required_eclipse_depth_frac"] or 0 for o in out),
       "kic_only_source": kic_only, "gaia_variability_and_nss": var_tables}
(HERE / "results_B.json").write_text(json.dumps(res, indent=1))
for o in out:
    print(o["source_id"], o["sep_arcsec"], o["G"], o["dG_vs_candidate"], o["required_eclipse_depth_frac"], o["can_mimic_by_magnitude"], o["note"])
print({k: v["rows"] for k, v in var_tables.items()}); print(res["candidate_gaia_source"])
