import hashlib
import os
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
    import requests

    from skysift.net import ServiceUnavailable, fetch

    url, sha = FIXTURES[name]
    CACHE.mkdir(parents=True, exist_ok=True); p = CACHE / name
    if not p.exists():
        part = p.with_suffix(".part")
        try:
            part.write_bytes(fetch(url, service="mast", timeout=300))
            os.replace(part, p)
        except (requests.ConnectionError, requests.Timeout, OSError) as e:
            pytest.skip(f"fixture {name} unavailable offline: {e!r}")
        except ServiceUnavailable as e:
            # fetch() retries network errors and then wraps them; an HTTP status in the message means the
            # archive answered, which is a real failure and must not be hidden as a skip.
            if "HTTP " in str(e):
                raise
            pytest.skip(f"fixture {name} unavailable offline: {e}")
    data = p.read_bytes()
    if hashlib.sha256(data).hexdigest() != sha:
        pytest.fail(f"fixture {name} hash mismatch; delete {p} and re-run")
    return data
