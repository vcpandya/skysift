"""D: line-of-sight dust (IRSA DUST service: SFD98 and Schlafly & Finkbeiner 2011) at the candidate; Bayestar was unreachable."""
import json, pathlib, hashlib, requests
from skysift.net import fetch, ServiceUnavailable
PROOF = pathlib.Path(__file__).parent / "archive-proof"
man_path = PROOF / "manifest.json"; manifest = json.loads(man_path.read_text())
url = "https://irsa.ipac.caltech.edu/cgi-bin/DUST/nph-dust"; p = {"locstr": "285.90782692874626 40.94198844296448 equ j2000", "regSize": "2.0"}
full = requests.Request("GET", url, params=p).prepare().url
try: data = fetch(url, params=p, service="irsa-dust", timeout=300); err = None
except ServiceUnavailable as e: data = b""; err = str(e)
(PROOF / "D_irsa_dust.xml").write_bytes(data)
manifest["D_irsa_dust"] = {"url": full, "file": "D_irsa_dust.xml", "bytes": len(data), "error": err, "sha256": hashlib.sha256(data).hexdigest()}
man_path.write_text(json.dumps(manifest, indent=1)); print(len(data), err)
