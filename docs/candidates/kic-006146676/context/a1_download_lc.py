"""A.1 List the MAST Kepler light-curve directory of KIC 6146666 and download every *_llc.fits file."""
import re, json, pathlib, hashlib
from skysift.net import fetch
HERE = pathlib.Path(__file__).parent
DL = HERE / "downloads" / "kic006146666_llc"; DL.mkdir(parents=True, exist_ok=True)
PROOF = HERE / "archive-proof"
base = "https://archive.stsci.edu/missions/kepler/lightcurves/0061/006146666/"
idx = fetch(base, service="mast-kepler-lc", timeout=300)
(PROOF / "A_mast_dirlisting_006146666.html").write_bytes(idx)
names = sorted(set(re.findall(rb'href="(kplr006146666-\d+_llc\.fits)"', idx)))
log = []
for n in names:
    n = n.decode(); out = DL / n
    if not out.exists():
        out.write_bytes(fetch(base + n, service="mast-kepler-lc", timeout=300))
    log.append({"url": base + n, "bytes": out.stat().st_size, "sha256": hashlib.sha256(out.read_bytes()).hexdigest()})
(PROOF / "A_downloads_manifest.json").write_text(json.dumps(log, indent=1))
print(len(names), "files")
