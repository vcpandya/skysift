"""A.1 List the MAST Kepler light-curve directory of KIC 5689116 and download every *_llc.fits file."""
import re, json, pathlib, hashlib
from skysift.net import fetch
HERE = pathlib.Path(__file__).parent
DL = HERE / "downloads" / "kic005689116_llc"; DL.mkdir(parents=True, exist_ok=True)
PROOF = HERE / "archive-proof"
base = "https://archive.stsci.edu/missions/kepler/lightcurves/0056/005689116/"
idx = fetch(base, service="mast-kepler-lc", timeout=300)
(PROOF / "A_mast_dirlisting_005689116.html").write_bytes(idx)
names = sorted(set(re.findall(rb'href="(kplr005689116-\d+_llc\.fits)"', idx)))
log = []
for n in names:
    n = n.decode(); out = DL / n
    if not out.exists():
        out.write_bytes(fetch(base + n, service="mast-kepler-lc", timeout=300))
    log.append({"url": base + n, "bytes": out.stat().st_size, "sha256": hashlib.sha256(out.read_bytes()).hexdigest()})
(PROOF / "A_downloads_manifest.json").write_text(json.dumps(log, indent=1))
print(len(names), "files")
