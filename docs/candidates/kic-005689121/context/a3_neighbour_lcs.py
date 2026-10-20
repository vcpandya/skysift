"""A.3 (extension): own light curves of the other neighbours within 30 arcsec.
For every Gaia DR3 neighbour, try the KBonus-BKG PSF light curve (by KIC id when a KIC source lies within 2 arcsec,
else by Gaia DR3 id) and, for KIC ids, the Kepler mission LLC directory. Measure the box depth at the candidate's
ephemeris exactly as in a2_leak_test.py. A neighbour that hosts the eclipse must show a depth of at least
1983 ppm * F_cand/F_neighbour (i.e. the required eclipse depth from check B), usually much more."""
import csv, io, json, pathlib, re, hashlib, sys
import numpy as np
import requests
from astropy.io import fits

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from a2_leak_test import detrend, measure  # noqa: E402  (same estimator)
from skysift.net import fetch, ServiceUnavailable

HERE = pathlib.Path(__file__).parent; PROOF = HERE / "archive-proof"
DL = HERE / "downloads" / "neighbour_lcs"; DL.mkdir(parents=True, exist_ok=True)
man_path = PROOF / "manifest.json"; manifest = json.loads(man_path.read_text())
KB = "https://archive.stsci.edu/hlsps/kbonus-bkg/lcs"
gaia = list(csv.DictReader(io.StringIO((PROOF / "B_gaia_cone30.csv").read_text())))[1:]
kic = []
for line in (PROOF / "C_vizier_KIC.tsv").read_text().splitlines():
    p = line.split("\t")
    if len(p) > 10 and re.match(r"^\s*\d+\.\d+$", p[0]) and p[1].strip().isdigit():
        kic.append((int(p[1]), float(p[2]), float(p[3]), p[10].strip()))


def get(name, url):
    out = DL / url.rsplit("/", 1)[-1]
    try:
        data = out.read_bytes() if out.exists() else fetch(url, service="mast-neighbour-lc", timeout=300); err = None
        out.write_bytes(data)
    except ServiceUnavailable as e:
        data, err = b"", str(e)
    manifest[name] = {"url": url, "file": str(out.relative_to(HERE)) if not err else None, "bytes": len(data), "error": err,
                      "sha256": hashlib.sha256(data).hexdigest() if data else None}
    return None if err else out


res = []
for g in gaia:
    ra, dec = float(g["ra"]), float(g["dec"])
    # KIC match: KIC source within 2 arcsec whose nearest Gaia neighbour is this source (one-to-one)
    def sep(k, a, b): return np.hypot((k[1] - a) * np.cos(np.radians(b)), k[2] - b) * 3600
    m = [k for k in kic if sep(k, ra, dec) < 2.0 and min(gaia, key=lambda gg: sep(k, float(gg["ra"]), float(gg["dec"])))["source_id"] == g["source_id"]]
    kid = m[0][0] if m else None
    entry = {"source_id": g["source_id"], "sep_arcsec": round(float(g["sep_arcsec"]), 2), "G": float(g["phot_g_mean_mag"]), "kic": kid}
    sid = f"{kid:09d}" if kid else g["source_id"]; stem = f"kic-{kid:09d}" if kid else f"gaia-dr3-{g['source_id']}"
    f = get(f"A3_kbonus_{stem}", f"{KB}/{sid[:4]}/{sid}/hlsp_kbonus-bkg_kepler_kepler_{stem}_kepler_v1.0_lc.fits")
    if f is not None:
        T, F, Q, QL = [], [], [], []
        with fits.open(f, memmap=False) as h:  # per-quarter HDUs: the stitched table is NaN in quarters where stitching failed
            for x in h[2:]:
                if x.name.startswith("LIGHTCURVE_Q"):
                    d = x.data; T.append(np.asarray(d["TIME"], float)); F.append(np.asarray(d["FLUX"], float))
                    QL.append(np.asarray(d["SAP_QUALITY"], int)); Q.append(np.full(len(d), int(x.header["QUARTER"])))
        t, fl, q, ql = map(np.concatenate, (T, F, Q, QL))
        entry["kbonus_quarters_with_flux"] = sorted({int(v) for v in q[np.isfinite(fl)]})
        ok = (ql == 0) & np.isfinite(t) & np.isfinite(fl) & (fl > 0); t, fl, q = t[ok], fl[ok], q[ok]; o = np.argsort(t)
        entry["kbonus_psf"] = measure(t[o], detrend(t[o], fl[o], q[o]), f"KBonus PSF {stem}")
    if kid and kid != 5689116:
        base = f"https://archive.stsci.edu/missions/kepler/lightcurves/{sid[:4]}/{sid}/"
        try:
            idx = fetch(base, service="mast-kepler-lc", timeout=300); (PROOF / f"A3_mast_dirlisting_{sid}.html").write_bytes(idx)
            manifest[f"A3_mast_dirlisting_{sid}"] = {"url": base, "file": f"A3_mast_dirlisting_{sid}.html", "bytes": len(idx), "error": None}
            entry["kepler_llc_files"] = len(re.findall(rb'href="kplr\d+-\d+_llc\.fits"', idx))
        except ServiceUnavailable as e:
            manifest[f"A3_mast_dirlisting_{sid}"] = {"url": base, "file": None, "bytes": 0, "error": str(e)}
            entry["kepler_llc_files"] = 0
    res.append(entry); print(json.dumps({k: v for k, v in entry.items() if k != "kbonus_psf"}), entry.get("kbonus_psf", {}).get("depth_ppm"))
man_path.write_text(json.dumps(manifest, indent=1))
(HERE / "results_A3.json").write_text(json.dumps(res, indent=1, default=float))
