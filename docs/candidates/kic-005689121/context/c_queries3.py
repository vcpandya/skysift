"""C follow-up: retry of the DR25 stellar table with explicit columns; older TCE tables; Kepler FPWG table."""
import json, pathlib, hashlib, requests
from skysift.net import fetch, ServiceUnavailable
PROOF = pathlib.Path(__file__).parent / "archive-proof"
man_path = PROOF / "manifest.json"; manifest = json.loads(man_path.read_text())
NEA = "https://exoplanetarchive.ipac.caltech.edu/TAP/sync"
def nea(name, adql):
    p = {"query": adql, "format": "csv"}; full = requests.Request("GET", NEA, params=p).prepare().url
    try: data = fetch(NEA, params=p, service="nasa-exoplanet-archive", timeout=300); err = None
    except ServiceUnavailable as e: data = b""; err = str(e)
    (PROOF / f"{name}.csv").write_bytes(data)
    manifest[name] = {"url": full, "file": f"{name}.csv", "bytes": len(data), "error": err, "sha256": hashlib.sha256(data).hexdigest()}
    print(name, len(data), err or "", data[:300])
nea("C_nea_dr25stellar_kepid_retry", "select kepid,teff,logg,radius,mass,kepmag from q1_q17_dr25_stellar where kepid in (5689116,5689121)")
nea("C_nea_q1q16tce_kepid", "select kepid,tce_plnt_num,tce_period,tce_depth from q1_q16_tce where kepid in (5689116,5689121)")
nea("C_nea_q1q12tce_kepid", "select kepid,tce_plnt_num,tce_period,tce_depth from q1_q12_tce where kepid in (5689116,5689121)")
nea("C_nea_fpwg_kepid", "select kepid,kepoi_name,fpwg_disp_status from fpwg where kepid in (5689116,5689121)")
man_path.write_text(json.dumps(manifest, indent=1))
