"""Front-end assets without a CDN at view time. HTMX (about 50 kB) is vendored in the package; Plotly (about 4.5 MB)
is downloaded once into the data directory, SHA-256 verified against the hash recorded here, and served locally."""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

from skysift import net

log = logging.getLogger(__name__)
STATIC = Path(__file__).resolve().parent / "static"
HTMX_VERSION = "2.0.3"
PLOTLY_VERSION = "2.35.2"
PLOTLY_URL = f"https://cdn.plot.ly/plotly-{PLOTLY_VERSION}.min.js"
PLOTLY_SHA256 = "6d21266ce1bd7d9e5ab4e115989c70c20de0382fd973a8f26ab58619eba4d603"


def ensure_plotly(data_dir: Path, fetch=net.fetch) -> Path | None:
    """Return the local Plotly file, downloading it on first use. Returns None (and logs why) when the network is
    unavailable or the download does not match the recorded hash; the dashboard then runs without plots."""
    target = Path(data_dir) / "ui" / "assets" / "plotly.min.js"
    if target.exists():
        return target
    try:
        data = fetch(PLOTLY_URL, service="plotly-cdn", timeout=120)
    except Exception as e:  # noqa: BLE001  (offline is a normal condition for the dashboard)
        log.warning("Plotly %s not available (%s); plots are disabled until the next start", PLOTLY_VERSION, e)
        return None
    digest = hashlib.sha256(data).hexdigest()
    if digest != PLOTLY_SHA256:
        log.error("Plotly download hash %s does not match the recorded %s; refusing to serve it", digest, PLOTLY_SHA256)
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(f".{os.getpid()}.part")
    tmp.write_bytes(data); os.replace(tmp, target)
    return target
