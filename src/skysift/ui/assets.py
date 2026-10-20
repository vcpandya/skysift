"""Front-end assets without a CDN at view time. HTMX (about 50 kB) and the woff2 fonts are vendored in the package;
Plotly (about 4.5 MB) is downloaded once into the data directory, SHA-256 verified against the hash recorded here, and served locally."""
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
# committed in static/fonts (Latin subsets, SIL OFL 1.1); sources and license texts in static/fonts/LICENSES.md
FONT_SHA256 = {
    "SpaceGrotesk-Medium.woff2": "1b1a8131d9edf975d9decee81e2f2bf504812f7a4f498e5500f28a613e22e64c",
    "SpaceGrotesk-Bold.woff2": "35f8aec56cfd5cbfdb03cc68733a54a0b05bb3617ffcd5fd332badc0b045ca55",
    "IBMPlexSans-Regular.woff2": "3b646991d30055a93a4ecc499713d4347953a74a947ecab435ab72070cbdab0e",
    "IBMPlexSans-Medium.woff2": "0717336fb31fcdcde4b8deb3675bb4a0f7f6d484864afcd6751ac29975962203",
    "IBMPlexSans-SemiBold.woff2": "8960851d691c054ed38e259bdcf1a6190d157b4203ed5bb32c632a863fb8ec2f",
    "IBMPlexMono-Regular.woff2": "08949f728dc52d528e69b1667d15c89a5686a4ee9a296ff90983985f99c380f7",
    "IBMPlexMono-Medium.woff2": "01d285447409c8a588692162439a038b8cbd7871309ee20267b0d2d91c6e8e22",
}


def ensure_plotly(data_dir: Path, fetch=net.fetch) -> Path | None:
    """Return the local Plotly file, downloading it on first use. Never raises: returns None (and logs why) when the
    network is unavailable, the download does not match the recorded hash or the data directory is unusable; the
    dashboard then runs without plots."""
    try:
        return _ensure_plotly(data_dir, fetch)
    except Exception:   # a missing plot bundle must never stop the dashboard from starting
        log.exception("Plotly bundle unavailable; plots disabled")
        return None


def _ensure_plotly(data_dir: Path, fetch) -> Path | None:
    target = Path(data_dir) / "ui" / "assets" / "plotly.min.js"
    if target.exists():
        try:
            cached = hashlib.sha256(target.read_bytes()).hexdigest()
        except OSError as e:
            log.warning("cached Plotly %s is unreadable (%s); fetching it again", target, e)
        else:
            if cached == PLOTLY_SHA256:
                return target
            log.error("cached Plotly %s does not match the recorded hash; discarding it", target)
        try:
            target.unlink()
        except OSError as e:
            log.warning("could not remove the cached Plotly (%s); plots are disabled", e)
            return None
    try:
        data = fetch(PLOTLY_URL, service="plotly-cdn", timeout=120)
    except Exception as e:  # noqa: BLE001  (offline is a normal condition for the dashboard)
        log.warning("Plotly %s not available (%s); plots are disabled until the next start", PLOTLY_VERSION, e)
        return None
    digest = hashlib.sha256(data).hexdigest()
    if digest != PLOTLY_SHA256:
        log.error("Plotly download hash %s does not match the recorded %s; refusing to serve it", digest, PLOTLY_SHA256)
        return None
    tmp = target.with_suffix(f".{os.getpid()}.part")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        for stale in target.parent.glob("plotly.min.*.part"):   # left behind by an interrupted earlier start
            try:
                stale.unlink(missing_ok=True)
            except OSError as e:
                log.warning("could not remove stale partial download %s (%s); leaving it", stale, e)
        tmp.write_bytes(data)
        os.replace(tmp, target)
    except OSError as e:
        log.warning("could not store Plotly under %s (%s); plots are disabled until the next start", target.parent, e)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    return target
