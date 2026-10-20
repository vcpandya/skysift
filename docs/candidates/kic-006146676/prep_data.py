"""Compact dossier data: thin the full series, keep everything else, merge the vet json, spectrum and subagent results
when present. Writes dossier_data.json (small) for embedding in dossier.html."""
import json, os
from pathlib import Path
import numpy as np

H = Path(__file__).parent
d = json.load(open(H / "lc_data.json", encoding="utf-8"))
out = {k: v for k, v in d.items() if k not in ("series_bkjd_ppm",)}
# full series binned to 0.25 d (about 12 cadences) for the overview plot
s = np.array(d["series_bkjd_ppm"], float)
t, y = s[:, 0], s[:, 1]
w = 0.25
edges = np.arange(np.floor(t.min()), t.max() + w, w)
idx = np.digitize(t, edges) - 1
ser = []
for i in np.unique(idx):
    m = idx == i
    if m.sum() >= 3:
        ser.append([round(float(t[m].mean()), 3), round(float(np.mean(y[m])), 0), int(m.sum())])
out["series_binned"] = ser
out["series_bin_days"] = w
# per-event windows: raw points within +-8 h of each event midpoint (for the event gallery)
P, t0 = d["ephemeris"]["period_days"], d["ephemeris"]["t0_bkjd"]
gal = []
for e in d["events"]:
    m = np.abs(t - e["t_mid"]) < 8 / 24
    gal.append({"epoch": e["epoch"], "t_mid": e["t_mid"], "quarter": e["quarter"], "depth_ppm": e["depth_ppm"], "err_ppm": e["err_ppm"],
                "pts": [[round(float((a - e["t_mid"]) * 24), 2), round(float(b), 0)] for a, b in zip(t[m], y[m])]})
out["event_gallery"] = gal
# optional merges
vet = Path("C:/Research/skysift/.superpowers/sample-run/vet/kic-006146676.json")
if vet.exists():
    out["vet"] = json.load(open(vet, encoding="utf-8"))
npz = Path("C:/Research/skysift/.superpowers/sample-run/vet/kic-006146676_spectrum.npz")
if npz.exists():
    z = np.load(npz)
    per, sde = z["period"], z["sde"]
    # thin: max SDE per log-period bin, 1500 bins
    lb = np.geomspace(per.min(), per.max(), 1501)
    bi = np.clip(np.digitize(per, lb) - 1, 0, 1499)
    spec = []
    for i in range(1500):
        m = bi == i
        if m.any():
            j = np.flatnonzero(m)[np.argmax(sde[m])]
            spec.append([round(float(per[j]), 4), round(float(sde[j]), 2)])
    out["spectrum"] = spec
for name in ("localisation", "context"):
    p = H / name / "results.json"
    if p.exists():
        out[name] = json.load(open(p, encoding="utf-8"))
if "context" in out:
    C = out["context"]
    C.pop("A4_centroid_test_INVALID", None); C.pop("A3_neighbour_own_lightcurves", None)
    C["A_leak_test"] = {k: C["A_leak_test"][k] for k in ("neighbour_KIC6146666", "neighbour_expectation", "candidate_KIC6146676")}
    C["A_leak_test"]["neighbour_KIC6146666"] = {k: C["A_leak_test"]["neighbour_KIC6146666"][k] for k in ("pdcsap", "sap", "n_cadences")}
    C["A_leak_test"]["candidate_KIC6146676"] = {k: v for k, v in C["A_leak_test"]["candidate_KIC6146676"].items() if k != "per_quarter_header"}
    C["B_gaia_neighbourhood"].pop("gaia_variability_and_nss", None)
    for k, v in C["C_archives"].items():
        if isinstance(v, dict): v.pop("file", None)
    C["D_stellar_planet"].pop("mamajek_rows_used", None)
# pixel localisation: season-stacked difference images as S/N maps, direct images, masks and the star positions
pz = H / "localisation" / "diffimage_products.npz"
if pz.exists() and "localisation" in out:
    z = np.load(pz)
    seasons = []
    for s_, v in sorted(out["localisation"]["seasons"].items()):
        diff, var, direct, valid = z[f"s{s_}_diff"], z[f"s{s_}_var"], z[f"s{s_}_direct"], z[f"s{s_}_valid"]
        snr = np.where(valid & (var > 0), diff / np.sqrt(np.where(var > 0, var, 1)), np.nan)
        seasons.append({"season": int(s_), "quarters": v["quarters"], "shape": list(diff.shape),
                        "snr": [[None if not np.isfinite(x) else round(float(x), 2) for x in row] for row in snr],
                        "direct": [[None if not m else round(float(x), 1) for x, m in zip(row, mrow)] for row, mrow in zip(direct, valid)],
                        "cand_colrow": v["pix_candidate_colrow"], "targ_colrow": v["pix_target_colrow"],
                        "cen_colrow": v["snrbox_diff_centroid_colrow"], "cen_sigma": v["snrbox_diff_centroid_sigma_mc_colrow"],
                        "max_snr": v["max_snr"], "n_events": v["n_events"],
                        "two_source": {k: v["two_source_fit"][k] for k in ("implied_depth_ppm_candidate", "implied_depth_ppm_candidate_err", "implied_depth_ppm_target", "implied_depth_ppm_target_err", "chi2_two_source", "chi2_only_candidate", "chi2_only_target", "direct_flux_ratio_target_over_candidate")},
                        "box3_candidate_ppm": [v["box3_candidate"]["frac_depth_ppm"], v["box3_candidate"]["frac_depth_ppm_err"]],
                        "box3_target_ppm": [v["box3_target"]["frac_depth_ppm"], v["box3_target"]["frac_depth_ppm_err"]]})
    out["pixels"] = {"arcsec_per_pixel": out["localisation"]["inputs"]["pixel_arcsec"], "seasons": seasons,
                     "summary": out["localisation"]["summary"]}
    out["localisation"] = {"inputs": out["localisation"]["inputs"], "quarters": [{k: q.get(k) for k in ("quarter", "season", "n_events_used", "usable", "max_snr", "snrbox_diff_centroid_dist_to_candidate_pix", "snrbox_diff_centroid_dist_to_target_pix", "diff_centroid_dist_to_candidate_arcsec", "diff_centroid_dist_to_target_arcsec")} for q in out["localisation"]["quarters"]]}
json.dump(out, open(H / "dossier_data.json", "w", encoding="utf-8"), separators=(",", ":"))
print("series bins", len(ser), "gallery", len(gal), "keys", sorted(out), "MB", round(os.path.getsize(H / "dossier_data.json") / 1e6, 2))
