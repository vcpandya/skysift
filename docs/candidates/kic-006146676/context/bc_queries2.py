"""B/C/D follow-up: Gaia variability/NSS tables for every cone source (explicit id list), ExoFOP TIC JSON,
Mamajek dwarf table, Bayestar19 extinction, TIC v8.2 for candidate. Saved verbatim in archive-proof/."""
import json, csv, io, pathlib, hashlib
import requests
from skysift.net import fetch, ServiceUnavailable
HERE = pathlib.Path(__file__).parent; PROOF = HERE / "archive-proof"
man_path = PROOF / "manifest.json"; manifest = json.loads(man_path.read_text())
for k in [k for k, v in manifest.items() if v.get("error") and k.startswith("B_gaia_") and k.endswith("_cone30")]:
    manifest[k]["superseded_by"] = k + "_ids"
def q(name, url, params, service, ext):
    full = requests.Request("GET", url, params=params).prepare().url
    try: data = fetch(url, params=params, service=service, timeout=300); err = None
    except ServiceUnavailable as e: data = b""; err = str(e)
    (PROOF / f"{name}.{ext}").write_bytes(data)
    manifest[name] = {"url": full, "file": f"{name}.{ext}", "bytes": len(data), "error": err, "sha256": hashlib.sha256(data).hexdigest()}
    print(name, len(data), err or ""); return data
ids = [r["source_id"] for r in csv.DictReader(io.StringIO((PROOF / "B_gaia_cone30.csv").read_text()))]
GAIA = "https://gea.esac.esa.int/tap-server/tap/sync"
for t in ("vari_summary", "vari_classifier_result", "nss_two_body_orbit", "vari_eclipsing_binary", "nss_non_linear_spectro", "nss_acceleration_astro"):
    q(f"B_gaia_{t}_cone30_ids", GAIA, {"request": "doQuery", "lang": "ADQL", "format": "csv",
      "query": f"SELECT source_id FROM gaiadr3.{t} WHERE source_id IN ({','.join(ids)})"}, "gaia-tap", "csv")
q("D_exofop_tic169822650", "https://exofop.ipac.caltech.edu/tess/target.php", {"id": "169822650", "json": ""}, "exofop", "json")
q("D_mamajek_dwarf_table", "https://www.pas.rochester.edu/~emamajek/EEM_dwarf_UBVIJHK_colors_Teff.txt", None, "mamajek", "txt")
from astropy.coordinates import SkyCoord; import astropy.units as u
g = SkyCoord(297.868708651717 * u.deg, 41.45556694086529 * u.deg).galactic
for name, url, p in [("D_bayestar19_a", "http://argonaut.skymaps.info/api/v2/bayestar19/query", {"l": f"{g.l.deg:.5f}", "b": f"{g.b.deg:.5f}", "mode": "percentile"}),
                     ("D_bayestar19_b", "https://argonaut.skymaps.info/api/v2/bayestar19/query", {"lon": f"{g.l.deg:.5f}", "lat": f"{g.b.deg:.5f}", "coordsys": "gal", "mode": "percentile"})]:
    q(name, url, p, "bayestar", "json")
man_path.write_text(json.dumps(manifest, indent=1))
