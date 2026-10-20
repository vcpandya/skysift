"""C follow-up (KIC 5689121 only): the independent "planethunt" claim. A web search for the KIC id returns a
self-published candidate page and a GitHub release (Kuber Mehta, 13 October 2026) for the same star and period.
Both pages are fetched and saved verbatim (untrusted text; only its existence, date and quoted ephemeris are used)."""
import json, pathlib, hashlib, requests
from skysift.net import fetch, ServiceUnavailable
PROOF = pathlib.Path(__file__).parent / "archive-proof"
man_path = PROOF / "manifest.json"; manifest = json.loads(man_path.read_text())


def q(name, url, service, ext):
    try: data = fetch(url, service=service, timeout=120); err = None
    except ServiceUnavailable as e: data = b""; err = str(e)
    (PROOF / f"{name}.{ext}").write_bytes(data)
    manifest[name] = {"url": url, "file": f"{name}.{ext}", "bytes": len(data), "error": err, "sha256": hashlib.sha256(data).hexdigest()}
    print(name, len(data), err or "")


q("C_planethunt_site", "https://kuber.studio/planethunt/", "web", "html")
q("C_planethunt_github_release", "https://github.com/Kuberwastaken/planethunt/releases/tag/evidence-20261013", "web", "html")
q("C_planethunt_github_readme", "https://raw.githubusercontent.com/Kuberwastaken/planethunt/main/README.md", "web", "md")
man_path.write_text(json.dumps(manifest, indent=1))

# SIMBAD bibliography retry (the aliased ADQL in c_queries4.py gave HTTP 400)
STAP = "https://simbad.cds.unistra.fr/simbad/sim-tap/sync"
for tag, ident in (("candidate", "Gaia DR3 2103792461826460288"), ("neighbour", "KIC 5689116")):
    adql = ("SELECT ref.bibcode, ref.year, ref.journal, ref.title FROM ref JOIN has_ref ON ref.oidbib = has_ref.oidbibref "
            f"JOIN ident ON ident.oidref = has_ref.oidref WHERE ident.id = '{ident}'")
    try: data = fetch(STAP, params={"request": "doQuery", "lang": "ADQL", "format": "csv", "query": adql}, service="simbad", timeout=120); err = None
    except ServiceUnavailable as e: data = b""; err = str(e)
    name = f"C_simbad_refs_{tag}_retry"; (PROOF / f"{name}.csv").write_bytes(data)
    manifest[name] = {"url": requests.Request("GET", STAP, params={"request": "doQuery", "lang": "ADQL", "format": "csv", "query": adql}).prepare().url,
                      "file": f"{name}.csv", "bytes": len(data), "error": err, "sha256": hashlib.sha256(data).hexdigest()}
    print(name, len(data), err or "", data[:300])
man_path.write_text(json.dumps(manifest, indent=1))
