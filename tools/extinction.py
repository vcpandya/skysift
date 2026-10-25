"""Store the extinction of a candidate's host once (spec 12.3 as amended by the controller's addenda of 23 October
2026), in ``docs/candidates/<target>/extinction.json``, which ``skysift.stars.load_extinction`` reads and
``skysift dossier`` refuses to run without for a star Gaia can characterise.

What is stored. The 3D reddening at the star's Gaia distance (Bayestar19, Green et al. 2019) is the value spec 12.3
adopts, and it is not available on the build machine: the 728 MB map file (doi:10.7910/DVN/2EJ9TX) needs the owner's
approval to download, not given, and the Argonaut web service (argonaut.skymaps.info) did not answer from this machine
on 23 October 2026. So a query run stores the 3D value as ``status: "not available"`` with that reason, and beside it,
under ``column``, the full-column E(B-V) of Schlafly and Finkbeiner 2011 from the IRSA DUST service: one logged GET
through ``skysift.net.fetch`` (a single attempt) of
``https://irsa.ipac.caltech.edu/cgi-bin/DUST/nph-dust?locstr=<ra>+<dec>+equ+j2000``, parsed for
``refPixelValueSandF`` (the value at the star, the one used) and ``meanValueSandF`` and ``stdSandF`` (within the
service's 5-arcmin radius, kept as context). The full column is the whole line of sight, dust beyond the star
included, so it is an upper bound on the reddening at the star's distance; the dossier brackets the star between no
reddening (adopted) and that bound. When IRSA cannot be reached or answers with an error, ``column`` records the
reason instead and the texts fall back to the "not available" wording. A 3D value obtained elsewhere is stored by hand
with ``--store`` (``status: "manual"``), which keeps a stored column; it is then adopted.

A run never queries again while the file is there (``--force`` does). Exit codes: 0 written with a 3D value, nothing
to do, or a star without a Gaia distance; 3 written with the 3D value not available (the store instruction is
printed); 2 a missing or malformed dossier JSON or ``--store`` arguments that do not make a valid block. ``--all``
runs ``LIVE_TARGETS`` in turn and returns the largest code. It reads only ``<data-dir>/dossiers/<target>/
dossier_data.json`` (``target.ra``, ``target.dec``, ``star.distance_pc.median``), never a database. It runs under the
engine's interpreter from the repository root:

    PYTHONUTF8=1 PYTHONPATH=src .venv/Scripts/python.exe -m tools.extinction --all \\
        --data-dir C:/Research/skysift/.superpowers/sample-run --root docs/candidates
    PYTHONUTF8=1 PYTHONPATH=src .venv/Scripts/python.exe -m tools.extinction --target <id> --data-dir <dir> \\
        --store --ebv <E(B-V)> --ebv-p16 <16th> --ebv-p84 <84th> --distance-pc <pc> --source "<where from>" --force
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

from skysift import net, stars

COLUMN_URL = "https://irsa.ipac.caltech.edu/cgi-bin/DUST/nph-dust"
COLUMN_SOURCE = "IRSA DUST, Schlafly and Finkbeiner 2011"
COLUMN_STATS_ARCMIN = 5.0           # the service computes its mean and std within this radius of the position
COLUMN_TIMEOUT = 60.0
LIVE_TARGETS = ("kic-005689121", "kic-006146676", "kic-006699887", "kic-009243078", "kic-010352243")
EXTINCTION_FILE = stars.EXTINCTION_FILE
DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "docs" / "candidates"
BAYESTAR_NOT_AVAILABLE = ("Bayestar19 at the Gaia distance was not queried: the 728 MB map file (doi:10.7910/DVN/2EJ9TX) "
                          "needs the owner's approval to download, not given, and the Argonaut web service "
                          "(argonaut.skymaps.info) did not answer from the build machine on 23 October 2026")
STORE_HINT = ("store a 3D value by hand when one is available: python -m tools.extinction --target {target} "
              "--data-dir <data dir> --store --ebv <E(B-V)> --ebv-p16 <16th> --ebv-p84 <84th> --distance-pc {distance} "
              "--source \"<where the value comes from>\" --force")


class ColumnError(ValueError):
    """An IRSA DUST answer that carries no full-column E(B-V)."""


def _say(text: str) -> None:
    print(text, file=sys.stderr)


class _Log:
    """``net.fetch``'s query log, printed to stderr: the tool keeps no database."""

    def log_query(self, service, url, params, status, n_bytes, sha, target_id=None, error=None):
        query = f" {params}" if params else ""
        if error is not None:
            _say(f"GET {url}{query} ({service}) failed: {error}")
        else:
            _say(f"GET {url}{query} ({service}) -> {status}, {n_bytes} bytes, sha256 {str(sha)[:16]}")


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def column_url(ra_deg: float, dec_deg: float) -> str:
    """The IRSA DUST query for one position, as the controller's addendum gives it."""
    return f"{COLUMN_URL}?locstr={ra_deg}+{dec_deg}+equ+j2000"


def _mag(stats, name: str) -> float:
    node = stats.find(name)
    if node is None or not (node.text or "").split():
        raise ColumnError(f"the IRSA DUST answer has no {name}")
    try:
        value = float((node.text or "").split()[0])
    except ValueError as e:
        raise ColumnError(f"the IRSA DUST {name} {node.text.strip()!r} is not a number") from e
    if not math.isfinite(value) or value < 0:
        raise ColumnError(f"the IRSA DUST {name} {value} is not a reddening")
    return value


def parse_column(xml: bytes) -> dict:
    """{"ebv": refPixelValueSandF, "ebv_mean": meanValueSandF, "ebv_std": stdSandF} from the E(B-V) result of an IRSA
    DUST answer; ``ColumnError`` for an answer that is not XML, a status other than ok (with the service's message) or
    a missing value. An answer with a document type declaration is refused before parsing (defusedxml is not
    installed; without a DTD there is no entity to expand)."""
    if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
        raise ColumnError("the IRSA DUST answer carries a document type declaration; refused")
    try:
        root = ET.fromstring(xml)          # no DTD, checked above
    except ET.ParseError as e:
        raise ColumnError(f"the IRSA DUST answer does not read as XML ({e})") from e
    status = root.get("status")
    if status != "ok":
        message = " ".join((root.findtext("message") or "").split()) or "no message"
        raise ColumnError(f"IRSA DUST answered status {status}: {message}")
    for result in root.findall("result"):
        if "E(B-V)" in (result.findtext("desc") or ""):
            stats = result.find("statistics")
            if stats is None:
                break
            return {"ebv": _mag(stats, "refPixelValueSandF"), "ebv_mean": _mag(stats, "meanValueSandF"),
                    "ebv_std": _mag(stats, "stdSandF")}
    raise ColumnError("the IRSA DUST answer has no E(B-V) statistics")


def query_column(ra_deg: float, dec_deg: float, *, fetch=None, now: str | None = None) -> dict:
    """The ``column`` block: one GET (a single attempt, logged to stderr) and its parse, {"status": "ok", "ebv",
    "ebv_mean", "ebv_std", "stats_radius_arcmin", "source", "queried_utc", "ra", "dec", "url"}; when the service cannot be
    reached or gives no value, {"status": "not available", "reason", "queried_utc", "url"}."""
    fetch = fetch or net.fetch
    url, when = column_url(ra_deg, dec_deg), now or _utc_now()
    try:
        values = parse_column(fetch(url, service="irsa-dust", store=_Log(), attempts=1, timeout=COLUMN_TIMEOUT))
    except (net.ServiceUnavailable, ColumnError) as e:
        return {"status": stars.EXT_NOT_AVAILABLE, "reason": str(e), "queried_utc": when, "url": url}
    return {"status": stars.COLUMN_OK, **values, "stats_radius_arcmin": COLUMN_STATS_ARCMIN, "source": COLUMN_SOURCE,
            "queried_utc": when, "ra": ra_deg, "dec": dec_deg, "url": url}


def store_path(root: Path, target_id: str) -> Path:
    return Path(root) / target_id / EXTINCTION_FILE


def _write(path: Path, block: dict) -> None:
    """``block`` as JSON at ``path``, through a temporary file and ``os.replace``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(block, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _hint(target: str, distance) -> str:
    shown = f"{distance:.0f}" if isinstance(distance, (int, float)) else "<pc>"
    return STORE_HINT.format(target=target, distance=shown)


def _store_by_hand(args, target: str, path: Path) -> int:
    missing = [n for n in ("ebv", "ebv_p16", "ebv_p84", "distance_pc", "source") if getattr(args, n) is None]
    if missing:
        _say(f"{target}: --store needs --ebv, --ebv-p16, --ebv-p84, --distance-pc and --source")
        return 2
    block = {"target": target, "status": stars.EXT_MANUAL, "ebv": args.ebv, "ebv_p16": args.ebv_p16,
             "ebv_p84": args.ebv_p84, "source": args.source, "map": "by hand", "queried_utc": _utc_now(),
             "distance_pc": args.distance_pc}
    old = None
    if path.is_file():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            old = None
    if isinstance(old, dict) and isinstance(old.get("column"), dict):
        block["column"] = old["column"]
    problem = stars._block_problem(block)
    if problem:
        _say(f"{target}: the stored block would be invalid: {problem}")
        return 2
    _write(path, block)
    _say(f"{target}: stored by hand, E(B-V) {args.ebv} ({args.ebv_p16} to {args.ebv_p84}) at {args.distance_pc} pc")
    return 0


def run_target(args, target: str, fetch=None) -> int:
    """One target, in the order of the module docstring; the exit code."""
    path = store_path(args.root, target)
    if path.is_file() and not args.force:
        _say(f"{target}: {path} is there; stored; nothing to do")
        return 0
    if args.store:
        return _store_by_hand(args, target, path)
    dossier = Path(args.data_dir) / "dossiers" / target / "dossier_data.json"
    if not dossier.is_file():
        _say(f"{target}: {dossier} is not there; run skysift dossier first")
        return 2
    try:
        data = json.loads(dossier.read_text(encoding="utf-8"))
        ra, dec = float(data["target"]["ra"]), float(data["target"]["dec"])
    except (OSError, ValueError, KeyError, TypeError) as e:
        _say(f"{target}: {dossier} gives no position ({e!r})")
        return 2
    distance = ((data.get("star") or {}).get("distance_pc") or {}).get("median")
    if not isinstance(distance, (int, float)) or isinstance(distance, bool) or not math.isfinite(distance):
        _say(f"{target}: the star has no Gaia distance; nothing to store")
        return 0
    column = query_column(ra, dec, fetch=fetch)
    block = {"target": target, "status": stars.EXT_NOT_AVAILABLE, "reason": BAYESTAR_NOT_AVAILABLE,
             "queried_utc": column["queried_utc"], "distance_pc": float(distance), "column": column}
    _write(path, block)
    shown = (f"E(B-V) full column {column['ebv']:.3f} (mean {column['ebv_mean']:.3f} within "
             f"{COLUMN_STATS_ARCMIN:g} arcmin)" if column["status"] == stars.COLUMN_OK
             else f"full column not available: {column['reason']}")
    _say(f"{target}: RA {ra} Dec {dec}, {distance:.1f} pc: {shown}; Bayestar19 not available; wrote {path}")
    _say(f"{target}: {_hint(target, distance)}")
    return 3


def main(argv: list[str] | None = None, fetch=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    who = ap.add_mutually_exclusive_group(required=True)
    who.add_argument("--target")
    who.add_argument("--all", action="store_true", help="every target of LIVE_TARGETS in turn")
    ap.add_argument("--data-dir", type=Path, required=True, help="the folder holding dossiers/<target>/")
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="the candidates folder (docs/candidates)")
    ap.add_argument("--store", action="store_true", help="store a 3D value by hand")
    ap.add_argument("--ebv", type=float)
    ap.add_argument("--ebv-p16", type=float)
    ap.add_argument("--ebv-p84", type=float)
    ap.add_argument("--distance-pc", type=float)
    ap.add_argument("--source")
    ap.add_argument("--force", action="store_true", help="write again over a stored file")
    args = ap.parse_args(argv)
    targets = LIVE_TARGETS if args.all else (args.target,)
    return max(run_target(args, t, fetch) for t in targets)


if __name__ == "__main__":
    sys.exit(main())
