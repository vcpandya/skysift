"""Offline stand-in for ``skysift.net.fetch``, imported by name inside spawned pipeline workers
(``pipeline.run(..., fetch_module="tests.unit.fake_fetch")``). Every catalogue answers "no rows": an empty CSV with a
header for the TAP services, an empty asu-tsv table for VizieR. Like the real fetch, it logs each request."""
import hashlib

EMPTY_CSV = b"kepid,kepoi_name,koi_period\n"
EMPTY_TSV = b"#\nKIC\tPer\n\td\n---\t---\n"


def fetch(url, *, params=None, service, store=None, target_id=None, **_kw) -> bytes:
    data = EMPTY_TSV if service == "vizier" else EMPTY_CSV
    if store: store.log_query(service, url, params or {}, 200, len(data), hashlib.sha256(data).hexdigest(), target_id=target_id)
    return data
