"""Download every Kepler long-cadence TPF of KIC 5689116 from MAST into ./tpf/ (data only)."""
import re, sys, json
from pathlib import Path
from skysift.net import fetch

BASE = "https://archive.stsci.edu/missions/kepler/target_pixel_files/0056/005689116/"
OUT = Path(__file__).resolve().parent / "tpf"
OUT.mkdir(exist_ok=True)

listing = fetch(BASE, service="mast", timeout=600).decode("utf-8", "replace")
names = sorted(set(re.findall(r'href="(kplr005689116-\d{13}_lpd-targ\.fits\.gz)"', listing)))
print(f"{len(names)} long-cadence TPFs listed")
log = {}
for n in names:
    dest = OUT / n
    if dest.exists() and dest.stat().st_size > 0:
        log[n] = "cached"; print(n, "cached"); continue
    try:
        data = fetch(BASE + n, service="mast", timeout=600)
        dest.write_bytes(data)
        log[n] = f"ok {len(data)} bytes"
    except Exception as e:  # noqa: BLE001
        log[n] = f"FAILED {e!r}"
    print(n, log[n], flush=True)
(OUT.parent / "download_log.json").write_text(json.dumps(log, indent=1))
