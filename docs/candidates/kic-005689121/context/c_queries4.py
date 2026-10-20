"""C follow-up (KIC 5689121 only): SIMBAD bibliography of the candidate and of KIC 5689116 (SIMBAD lists 1 and 4
references), and the Planet Hunters Talk-era tables by KIC id rather than by cone. Saved verbatim in archive-proof/."""
import json, pathlib, hashlib, requests
from skysift.net import fetch, ServiceUnavailable
PROOF = pathlib.Path(__file__).parent / "archive-proof"
man_path = PROOF / "manifest.json"; manifest = json.loads(man_path.read_text())


def q(name, url, params, service, ext):
    full = requests.Request("GET", url, params=params).prepare().url
    try: data = fetch(url, params=params, service=service, timeout=300); err = None
    except ServiceUnavailable as e: data = b""; err = str(e)
    (PROOF / f"{name}.{ext}").write_bytes(data)
    manifest[name] = {"url": full, "file": f"{name}.{ext}", "bytes": len(data), "error": err, "sha256": hashlib.sha256(data).hexdigest()}
    print(name, len(data), err or "", data[:400])


STAP = "https://simbad.cds.unistra.fr/simbad/sim-tap/sync"
for tag, ident in (("candidate", "Gaia DR3 2103792461826460288"), ("neighbour", "2MASS J19033726+4056316")):
    adql = (f"SELECT DISTINCT r.bibcode, r.title, r.journal, r.year FROM ref AS r JOIN has_ref AS h ON h.oidbibref = r.oidbib "
            f"JOIN ident AS i ON i.oidref = h.oidref WHERE i.id = '{ident}' ORDER BY r.year")
    q(f"C_simbad_refs_{tag}", STAP, {"request": "doQuery", "lang": "ADQL", "format": "csv", "query": adql}, "simbad", "csv")
    adql2 = f"SELECT i2.id FROM ident AS i JOIN ident AS i2 ON i.oidref = i2.oidref WHERE i.id = '{ident}'"
    q(f"C_simbad_ids_{tag}", STAP, {"request": "doQuery", "lang": "ADQL", "format": "csv", "query": adql2}, "simbad", "csv")
VIZ = "https://vizier.cds.unistra.fr/viz-bin/asu-tsv"
for name, src, col in (("C_vizier_planethunters_lintott2013_bykic", "J/AJ/145/151", "KIC"),
                       ("C_vizier_planethunters_wang2013_bykic", "J/ApJ/776/10", "KIC"),
                       ("C_vizier_planethunters_schmitt2014_PH6_bykic", "J/AJ/148/28", "KIC"),
                       ("C_vizier_planethunters_schwamb2012_bykic", "J/ApJ/754/129", "KIC")):
    q(name, VIZ, {"-source": src, col: "=5689121", "-out.all": ""}, "vizier", "tsv")
man_path.write_text(json.dumps(manifest, indent=1))
