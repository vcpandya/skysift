"""C follow-up: SIMBAD bibliography by the sim-id HTML/ASCII endpoint (the two sim-tap ADQL forms gave HTTP 400)."""
import json, pathlib, hashlib, requests
from skysift.net import fetch, ServiceUnavailable
PROOF = pathlib.Path(__file__).parent / "archive-proof"
man_path = PROOF / "manifest.json"; manifest = json.loads(man_path.read_text())
for tag, ident in (("candidate", "Gaia DR3 2103792461826460288"), ("neighbour", "KIC 5689116")):
    url = "https://simbad.cds.unistra.fr/simbad/sim-id"
    p = {"Ident": ident, "output.format": "ASCII", "obj.bibsel": "on", "list.bibsel": "on", "bibdisplay": "refsum", "bibyear1": "1850", "bibyear2": "$currentYear"}
    full = requests.Request("GET", url, params=p).prepare().url
    try: data = fetch(url, params=p, service="simbad", timeout=120); err = None
    except ServiceUnavailable as e: data = b""; err = str(e)
    name = f"C_simbad_bib_{tag}"; (PROOF / f"{name}.txt").write_bytes(data)
    manifest[name] = {"url": full, "file": f"{name}.txt", "bytes": len(data), "error": err, "sha256": hashlib.sha256(data).hexdigest()}
    txt = data.decode("utf-8", "replace")
    print(name, len(data), err or ""); print("\n".join(l for l in txt.splitlines() if "Bib" in l or l.startswith("20") or l.startswith("19"))[:1500])
man_path.write_text(json.dumps(manifest, indent=1))
