"""Fetch the inputs of the TESS non-detection statement (referee pack spec 10.2) into ``targets.meta["tess"]``.

For each ``--target``: the star's TIC row by its KIC number (MAST's invoke API, ``Mast.Catalogs.Filtered.Tic`` with a
``KIC`` filter; the ``/api/v0.1/Catalogs/filtered/Tic`` path returns 404), the TIC cone of ``tess.CONE_DEG`` (0.03
degrees, 108 arcsec, past five TESS pixels) around the TIC position (``Mast.Catalogs.Tic.Cone``), and the sectors whose
full-frame images hold the position (the TESScut sector endpoint). Every request goes through ``skysift.net.fetch``
(GET, the invoke JSON in the ``request`` query parameter) and is logged in the query log against the target. The
stored block is ``{"queried", "tic", "tmag", "tmag_source", "ra", "dec", "sectors", "neighbours": [{"tic",
"dst_arcsec", "dra_pix", "ddec_pix", "tmag", "tmag_source"}]}``: a null Tmag is filled from Gaia by Stassun et al.
2019 equation 1 (equation 2 without BP and RP) with the source recorded, the neighbours keep their offsets in TESS
pixels (21 arcsec, RA scaled by cos Dec) so the dossier needs no trigonometry, and the TIC's own target row and its
DUPLICATE and ARTIFACT rows are dropped. The rest of ``meta`` (the KBonus CROWDSAP and PSFFRAC) is kept.

The dossier's fingerprint does not cover ``meta``, so regenerate the dossier with ``--force`` after a fetch.

    PYTHONPATH=src python -m tools.tess_inputs --target kic-006146676 [--target ...] [--db <database url>]
        [--dry-run] [--today YYYY-MM-DD]

``--dry-run`` prints the request URLs (the cone and the sectors at the target row's position, since the TIC's is not
yet known) and fetches and writes nothing.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import UTC, datetime

import requests

from skysift import net, tess
from skysift.models import Target
from skysift.net import ServiceUnavailable
from skysift.store.db import Store, TargetRow

INVOKE_URL = "https://mast.stsci.edu/api/v0/invoke"
TESSCUT_SECTOR_URL = "https://mast.stsci.edu/tesscut/api/v0.1/sector"
DROPPED_DISPOSITIONS = ("DUPLICATE", "ARTIFACT")


def tic_request(kic: int) -> dict:
    return {"service": "Mast.Catalogs.Filtered.Tic", "format": "json",
            "params": {"columns": "*", "filters": [{"paramName": "KIC", "values": [str(int(kic))]}]}}


def cone_request(ra: float, dec: float, radius: float = tess.CONE_DEG) -> dict:
    return {"service": "Mast.Catalogs.Tic.Cone", "format": "json",
            "params": {"ra": float(ra), "dec": float(dec), "radius": radius}}


def _invoke_params(req: dict) -> dict:
    return {"request": json.dumps(req)}


def invoke(req: dict, *, store, target_id: str) -> list[dict]:
    """The ``data`` rows of one invoke request (GET); ServiceUnavailable when MAST says it did not complete."""
    raw = net.fetch(INVOKE_URL, params=_invoke_params(req), service=f"mast-{req['service']}", store=store,
                    target_id=target_id)
    try:
        answer = json.loads(raw)
    except ValueError as e:
        raise ServiceUnavailable(f"{req['service']} answered with something that is not JSON ({e})") from e
    status = answer.get("status")
    if status not in (None, "COMPLETE"):
        raise ServiceUnavailable(f"{req['service']} did not complete (status {status}: {answer.get('msg')})")
    return [r for r in answer.get("data") or [] if isinstance(r, dict)]


def _num(x) -> float | None:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def magnitude(row: dict) -> tuple[float | None, str | None]:
    """(Tmag, its source): the TIC's, else Gaia by Stassun et al. 2019 eq. 1 (eq. 2 without both colours)."""
    t = _num(row.get("Tmag"))
    if t is not None:
        return t, tess.TMAG_TIC
    g, bp, rp = _num(row.get("GAIAmag")), _num(row.get("gaiabp")), _num(row.get("gaiarp"))
    if g is None:
        return None, None
    colour = bp - rp if bp is not None and rp is not None else None
    return tess.tmag_from_gaia(g, colour), tess.TMAG_EQ1 if colour is not None else tess.TMAG_EQ2


def offsets_pix(ra: float, dec: float, ra0: float, dec0: float) -> tuple[float, float]:
    """(dRA cos Dec, dDec) of a neighbour from the target in TESS pixels, RA wrapped to the nearer side."""
    dra = (ra - ra0 + 180.0) % 360.0 - 180.0
    return (dra * math.cos(math.radians(dec0)) * 3600.0 / tess.PIXEL_ARCSEC,
            (dec - dec0) * 3600.0 / tess.PIXEL_ARCSEC)


def pick_star(rows: list[dict], kic: int) -> dict:
    good = [r for r in rows if str(r.get("disposition") or "").upper() not in DROPPED_DISPOSITIONS]
    if not good:
        raise LookupError(f"no TIC row for KIC {kic}")
    if len({r.get("ID") for r in good}) > 1:
        raise LookupError(f"KIC {kic} matches {len(good)} TIC rows ({', '.join(str(r.get('ID')) for r in good)})")
    return good[0]


def neighbours(rows: list[dict], star: dict) -> list[dict]:
    ra0, dec0 = float(star["ra"]), float(star["dec"])
    out = []
    for r in rows:
        if str(r.get("ID")) == str(star.get("ID")):
            continue
        if str(r.get("disposition") or "").upper() in DROPPED_DISPOSITIONS:
            continue
        if r.get("objType") not in (None, "STAR"):
            continue
        ra, dec = _num(r.get("ra")), _num(r.get("dec"))
        if ra is None or dec is None:
            continue
        dx, dy = offsets_pix(ra, dec, ra0, dec0)
        t, source = magnitude(r)
        dst = _num(r.get("dstArcSec"))
        out.append({"tic": int(r["ID"]), "dst_arcsec": dst if dst is not None else math.hypot(dx, dy) * tess.PIXEL_ARCSEC,
                    "dra_pix": dx, "ddec_pix": dy, "tmag": t, "tmag_source": source})
    return sorted(out, key=lambda n: n["dst_arcsec"])


def sectors(raw: bytes) -> list[int]:
    try:
        answer = json.loads(raw)
    except ValueError as e:
        raise ServiceUnavailable(f"the TESScut sector list is not JSON ({e})") from e
    return sorted({int(r["sector"]) for r in answer.get("results") or [] if isinstance(r, dict) and r.get("sector")})


def fetch_inputs(target: dict, *, store=None, today: str | None = None) -> dict:
    """The ``meta["tess"]`` block for one target row (a dict with ``target_id`` and ``kic``)."""
    kic, target_id = target.get("kic"), target["target_id"]
    if not kic:
        raise LookupError(f"{target_id} has no KIC number")
    star = pick_star(invoke(tic_request(kic), store=store, target_id=target_id), kic)
    ra, dec = float(star["ra"]), float(star["dec"])
    cone = invoke(cone_request(ra, dec), store=store, target_id=target_id)
    raw = net.fetch(TESSCUT_SECTOR_URL, params={"ra": ra, "dec": dec}, service="tesscut-sector", store=store,
                    target_id=target_id)
    tmag, source = magnitude(star)
    return {"queried": today or datetime.now(UTC).date().isoformat(), "tic": int(star["ID"]), "tmag": tmag,
            "tmag_source": source, "ra": ra, "dec": dec, "sectors": sectors(raw),
            "neighbours": neighbours(cone, star)}


def target_row(store: Store, target_id: str) -> dict:
    with store.session() as s:
        t = s.get(TargetRow, target_id)
        if t is None:
            raise LookupError(f"no target {target_id}")
        return {c.name: getattr(t, c.name) for c in TargetRow.__table__.columns}


def store_inputs(store: Store, row: dict, inputs: dict) -> None:
    """``meta["tess"]`` = ``inputs`` on the target row, every other ``meta`` key kept (``Store.upsert_target``)."""
    meta = {**(row.get("meta") or {}), "tess": inputs}
    store.upsert_target(Target(source=row["source"], target_id=row["target_id"], ra=row["ra"], dec=row["dec"],
                               gaia_id=row.get("gaia_id"), kic=row.get("kic"), gmag=row.get("gmag"), meta=meta))


def _url(url: str, params: dict) -> str:
    return requests.Request("GET", url, params=params).prepare().url


def dry_run(row: dict) -> list[str]:
    ra, dec = row["ra"], row["dec"]
    return [_url(INVOKE_URL, _invoke_params(tic_request(row["kic"]))),
            _url(INVOKE_URL, _invoke_params(cone_request(ra, dec))),
            _url(TESSCUT_SECTOR_URL, {"ra": ra, "dec": dec})]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", action="append", required=True, help="a target id, e.g. kic-006146676 (repeatable)")
    ap.add_argument("--db", help="the engine database URL (default: the settings' SKYSIFT_DATABASE_URL)")
    ap.add_argument("--dry-run", action="store_true", help="print the request URLs; fetch and write nothing")
    ap.add_argument("--today", help="the query date stored with the inputs (default: today, UTC)")
    a = ap.parse_args(argv)
    if a.db:
        url = a.db
    else:
        from skysift.config import load_settings
        url = load_settings().database_url
    store = Store(url)
    failed = 0
    for target_id in a.target:
        try:
            row = target_row(store, target_id)
            if a.dry_run:
                for line in dry_run(row):
                    print(line)
                continue
            inputs = fetch_inputs(row, store=store, today=a.today)
            store_inputs(store, row, inputs)
        except (LookupError, ServiceUnavailable) as e:
            print(f"{target_id}: {e}", file=sys.stderr)
            failed += 1
            continue
        print(f"{target_id}: TIC {inputs['tic']}, Tmag {inputs['tmag']:.3f} ({inputs['tmag_source']}), sectors "
              f"{', '.join(map(str, inputs['sectors'])) or 'none'}, {len(inputs['neighbours'])} neighbours; "
              f"regenerate the dossier with --force", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
