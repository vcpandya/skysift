import hashlib
from pathlib import Path

import pytest

CACHE = Path(__file__).resolve().parents[1] / "fixtures" / "cache"
FIXTURES = {
    "kbonus_005689121.fits": ("https://archive.stsci.edu/hlsps/kbonus-bkg/lcs/0056/005689121/hlsp_kbonus-bkg_kepler_kepler_kic-005689121_kepler_v1.0_lc.fits",
                              "44980afb7843a4507c898eb1ce5d5733029a0f4658f6b96d6196db49df37c06f"),
    "kbonus_005689116.fits": ("https://archive.stsci.edu/hlsps/kbonus-bkg/lcs/0056/005689116/hlsp_kbonus-bkg_kepler_kepler_kic-005689116_kepler_v1.0_lc.fits",
                              "67ca0e7545f2ddb8bf051b30dabdf63a87fa6acc86d513f2ade5b1e6812382d3"),
}


def fixture_bytes(name: str) -> bytes:
    url, sha = FIXTURES[name]
    CACHE.mkdir(parents=True, exist_ok=True); p = CACHE / name
    if not p.exists():
        try:
            from skysift.net import fetch
            p.write_bytes(fetch(url, service="mast", timeout=300))
        except Exception as e:  # noqa: BLE001 - any fetch failure means offline: skip, never fail
            pytest.skip(f"fixture {name} unavailable offline: {e!r}")
    data = p.read_bytes()
    assert hashlib.sha256(data).hexdigest() == sha, f"fixture {name} hash mismatch"
    return data
