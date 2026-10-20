"""E: merge the per-check JSON files and the archive row counts into results.json."""
import json, pathlib, csv, io

HERE = pathlib.Path(__file__).parent; PROOF = HERE / "archive-proof"
man = json.loads((PROOF / "manifest.json").read_text())


def rows(name):
    """Data rows in a saved archive response (CSV: lines after header; VizieR TSV: lines after the '---' rule)."""
    v = man.get(name, {})
    if v.get("error"): return {"rows": None, "error": v["error"], "url": v["url"]}
    f = PROOF / v["file"]; txt = f.read_text(encoding="utf-8", errors="replace")
    if f.suffix == ".csv":
        n = max(0, len([l for l in txt.splitlines() if l.strip()]) - 1)
    elif f.suffix == ".tsv":
        lines = [l for l in txt.splitlines() if l.strip() and not l.startswith("#")]
        rule = [i for i, l in enumerate(lines) if set(l.replace("\t", "")) <= {"-", " "}]
        n = len(lines) - rule[0] - 1 if rule else 0
        err = [l for l in txt.splitlines() if "Error=" in l]
        if err: return {"rows": None, "error": err[0].strip("#INFO\t"), "url": v["url"]}
    else:
        n = None
    return {"rows": n, "url": v["url"], "file": v["file"]}


A = json.loads((HERE / "results_A.json").read_text())
A3 = json.loads((HERE / "results_A3.json").read_text())
A4 = json.loads((HERE / "results_A4.json").read_text())
B = json.loads((HERE / "results_B.json").read_text())
D = json.loads((HERE / "results_D.json").read_text())
exo = json.loads((PROOF / "D_exofop_tic169822650.json").read_text(encoding="utf-8"))
C = {k: rows(k) for k in man if k.startswith("C_")}
C["exofop_TIC169822650"] = {"url": man["D_exofop_tic169822650"]["url"], "tois": exo["tois"], "ctois": exo["ctois"],
                            "imaging": exo["imaging"], "spectroscopy": exo["spectroscopy"], "stellar_companions": exo["stellar_companions"]}
C["simbad_objects_in_30arcsec"] = [l.split("---")[0].replace("Object", "").strip()
                                   for l in (PROOF / "C_simbad_cone30.txt").read_text().splitlines() if l.startswith("Object ")]
nb = A["neighbour_KIC6146666"]; cd = A["candidate_KIC6146676"]
summary = {
    "neighbour_KIC6146666_pdcsap_depth_ppm": [nb["pdcsap"]["depth_ppm"], nb["pdcsap"]["err_ppm"]],
    "neighbour_KIC6146666_sap_depth_ppm": [nb["sap"]["depth_ppm"], nb["sap"]["err_ppm"]],
    "neighbour_expected_leak_case_ppm_max": A["neighbour_expectation"]["leak_case_max_depth_on_target_ppm"],
    "neighbour_expected_on_target_case_ppm_min": 2165,
    "candidate_psf_depth_ppm": [cd["psf"]["depth_ppm"], cd["psf"]["err_ppm"]],
    "candidate_sap_depth_ppm": [cd["sap"]["depth_ppm"], cd["sap"]["err_ppm"]],
    "candidate_sap_over_psf": [cd["sap_over_psf"], cd["sap_over_psf_err"]],
    "gaia_neighbours_30arcsec": B["n_neighbours_30arcsec"],
    "gaia_neighbours_able_to_mimic_by_magnitude": len(B["neighbours_able_to_mimic_by_magnitude"]),
    "stellar_baseline": {k: D["planet_baseline_colour_2.10pm0.10"][k] for k in ("Teff_K", "R_star_Rsun", "M_star_Msun")},
    "distance_pc": D["distance_pc"],
    "planet_baseline": {k: D["planet_baseline_colour_2.10pm0.10"][k] for k in
                        ("Rp_from_k_Rearth", "Rp_from_boxdepth_Rearth", "a_AU", "a_over_Rstar", "Teq_K_A0.3_full_redistribution",
                         "transit_probability_Rstar_over_a", "T14_central_h", "b_if_3h_is_T14", "b_if_3h_is_mean_T14_T23", "T14_for_fitted_b0.89_h")},
    "archives_any_match": False,
}
out = {"target": {"name": "KIC 6146676", "gaia_dr3": "2073896294004902272", "tic": "169822650", "ra": 297.868708651717, "dec": 41.45556694086529},
       "ephemeris": A["ephemeris"], "summary": summary,
       "A_leak_test": A, "A3_neighbour_own_lightcurves": A3, "A4_centroid_test_INVALID": A4,
       "B_gaia_neighbourhood": B, "C_archives": C, "D_stellar_planet": D}
(HERE / "results.json").write_text(json.dumps(out, indent=1, default=float))
print(json.dumps(summary, indent=1)); print(json.dumps({k: v.get("rows") for k, v in C.items() if isinstance(v, dict) and "rows" in v}, indent=1))
print(C["simbad_objects_in_30arcsec"])
