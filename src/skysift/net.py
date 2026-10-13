from __future__ import annotations

import hashlib
import re
import time

import truststore

truststore.inject_into_ssl()
import requests  # after trust-store injection on purpose


class ServiceUnavailable(RuntimeError):
    pass


_ERR_VOT = re.compile(rb'QUERY_STATUS"\s+value="ERROR">([^<]*)', re.IGNORECASE)
_ERR_HTML = re.compile(rb"<title>\s*(\d{3}[^<]*)</title>", re.IGNORECASE)


def looks_like_error_document(data: bytes) -> str | None:
    head = data[:4000]
    m = _ERR_VOT.search(head)
    if m: return m.group(1).decode(errors="replace").strip()
    if b"ORA-" in head and b"ORACLE" in head: return head.decode(errors="replace")[:200]
    m = _ERR_HTML.search(head)
    if m and head.lstrip().lower().startswith((b"<!doctype html", b"<html")): return m.group(1).decode(errors="replace")
    return None


def fetch(url, *, params=None, service, store=None, session=None, timeout=60.0, retries=5, backoff=1.5) -> bytes:
    sess = session or requests.Session()
    last = ""
    for attempt in range(retries + 1):
        try:
            r = sess.get(url, params=params, timeout=timeout)
            data = r.content
            sha = hashlib.sha256(data).hexdigest()
            if store: store.log_query(service, url, params or {}, r.status_code, len(data), sha)
            if r.status_code == 200:
                return data
            if r.status_code in (400, 401, 403, 404):
                raise ServiceUnavailable(f"{service} {url} -> HTTP {r.status_code}")
            last = f"HTTP {r.status_code}"
        except ServiceUnavailable:
            raise
        except Exception as e:  # noqa: BLE001  (connection errors, timeouts)
            last = repr(e)
            if store: store.log_query(service, url, params or {}, None, 0, None, error=last)
        if attempt < retries:
            time.sleep(backoff * (2 ** attempt))
    raise ServiceUnavailable(f"{service} {url} failed after {retries + 1} attempts: {last}")
