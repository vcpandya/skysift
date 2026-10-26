"""Store the extinction of a candidate's host once (spec 12.3 as amended by the controller's addenda of 23 October
2026 and the Task 16 fix-round rulings of 25 October 2026), in ``docs/candidates/<target>/extinction.json``, which
``skysift.stars.load_extinction`` reads and ``skysift dossier`` refuses to run without for a star Gaia can
characterise.

What is stored. The 3D reddening at the star's Gaia distance (Bayestar19, Green et al. 2019) is the value spec 12.3
adopts. A query run asks the Argonaut web service for it once: one logged GET through ``skysift.net.fetch`` (a single
attempt, 20 s) of ``ARGONAUT_URL`` with the position, the distance in kpc and the 16th, 50th and 84th percentiles,
converted to E(B-V) with Green et al.'s factor 0.884. An answer is stored as the adopted value (``status: "adopted"``).
Any failure (an HTTP error, a timeout, an answer that is not three percentiles) stores ``status: "not available"`` with
the reason ``ARGONAUT_FAILED``; no local map file is used. Either way, beside it, under ``column``, goes the full-column
E(B-V) of Schlafly and Finkbeiner 2011 from the IRSA DUST service: one logged GET (a single attempt) of
``https://irsa.ipac.caltech.edu/cgi-bin/DUST/nph-dust?locstr=<ra>+<dec>+equ+j2000``, parsed for
``refPixelValueSandF`` (the value at the star, the one used: ``"used": "pixel"``) and ``meanValueSandF`` and
``stdSandF`` (within the service's 5-arcmin radius; the std is the spread the upper end is drawn with). The full column
is the whole line of sight, dust beyond the star included, so it is an upper bound on the reddening at the star's
distance; without a 3D value the dossier brackets the star from unreddened (E(B-V) = 0), adopted, to that bound. When
IRSA cannot be reached or answers with an error, ``column`` records the reason instead. A 3D value obtained elsewhere is
stored by hand with ``--store`` for one target (``status: "manual"``), which keeps a stored column and says so; it is
then adopted.

The local map (spec 12.5). With ``--map <bayestar2019.h5>`` (or ``SKYSIFT_BAYESTAR_MAP``) the 3D value comes from the
file and Argonaut is not asked. ``tools/bayestar_map.py`` reads it at the Gaia distance's median, 16th and 84th
percentiles: in this interpreter when it holds h5py and astropy-healpix, otherwise as ``-I`` under
``SKYSIFT_DUSTMAPS_PYTHON`` or the first ``.venv-dustmaps`` above the tool. The record is the adopted block
(``map_block``): the samples' median and percentiles at the median distance in E(B-V), the sample medians at the
distance's percentiles, the source, the file's name, the MD5 from the sidecar ``<file>.md5`` the controller writes
(``"unverified"`` without one; a digest other than the Dataverse record's stops the tool), the map version, the
pixel and the convergence and reliable-distance flags. A sightline outside the footprint, an unconverged pixel or no
finite sample stores "not available" with that reason. A stored column is kept, so a run over the five live records
makes no network call; without one (or with ``--force``) IRSA is asked once. A stored record without a 3D value is
read again when a map is given; a stored 3D value needs ``--force``.

A run never queries again while a valid file is there (``--force`` does); a stored file that ``load_extinction``
refuses is reported with its path. Exit codes: 0 written with a 3D value, nothing to do, or a star without a Gaia
distance; 3 written with the 3D value not available (the store instruction is printed); 2 a missing or malformed
dossier JSON, a malformed stored file, ``--store`` with ``--all``, ``--store`` arguments that do not make a valid
block, or a map that cannot be read. ``--all`` runs ``LIVE_TARGETS`` in turn and returns the largest code. It reads
only ``<data-dir>/dossiers/<target>/dossier_data.json`` (``target.ra``, ``target.dec``, ``star.distance_pc``), never a
database. It runs under the engine's interpreter from the repository root:

    PYTHONUTF8=1 PYTHONPATH=src .venv/Scripts/python.exe -m tools.extinction --all \\
        --data-dir C:/Research/skysift/.superpowers/sample-run --root docs/candidates
    PYTHONUTF8=1 PYTHONPATH=src .venv/Scripts/python.exe -m tools.extinction --all \\
        --data-dir C:/Research/skysift/.superpowers/sample-run --root docs/candidates --map <bayestar2019.h5>
    PYTHONUTF8=1 PYTHONPATH=src .venv/Scripts/python.exe -m tools.extinction --target <id> --data-dir <dir> \\
        --store --ebv <E(B-V)> --ebv-p16 <16th> --ebv-p84 <84th> --distance-pc <pc> --source "<where from>" --force
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

from skysift import net, stars
from tools import bayestar_map as bm

ARGONAUT_URL = "http://argonaut.skymaps.info/api/v2/bayestar2019/query"
ARGONAUT_SOURCE = "Bayestar19 (Green et al. 2019) via Argonaut"
ARGONAUT_MAP = "bayestar2019, Argonaut API v2"
ARGONAUT_TIMEOUT = 20.0
ARGONAUT_PERCENTILES = (16, 50, 84)
BAYESTAR_TO_EBV = bm.BAYESTAR_TO_EBV  # Green et al. 2019: E(B-V) = 0.884 x the map's value
ARGONAUT_FAILED = ("Bayestar19 (Green et al. 2019) not available: the Argonaut web query failed ({why}); no local map "
                   "file")
COLUMN_URL = "https://irsa.ipac.caltech.edu/cgi-bin/DUST/nph-dust"
COLUMN_SOURCE = "IRSA DUST, Schlafly and Finkbeiner 2011"
COLUMN_STATS_ARCMIN = 5.0           # the service computes its mean and std within this radius of the position
COLUMN_USED = "pixel"               # ruling (b): the pixel value at the star is the upper end, the mean is context
COLUMN_TIMEOUT = 60.0
LIVE_TARGETS = ("kic-005689121", "kic-006146676", "kic-006699887", "kic-009243078", "kic-010352243")
EXTINCTION_FILE = stars.EXTINCTION_FILE
DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "docs" / "candidates"
STORE_HINT = ("store a 3D value by hand when one is available: python -m tools.extinction --target {target} "
              "--data-dir <data dir> --store --ebv <E(B-V)> --ebv-p16 <16th> --ebv-p84 <84th> --distance-pc {distance} "
              "--source \"<where the value comes from>\" --force")
# spec 12.5: the Bayestar19 map read from a local file (tools/bayestar_map.py)
MAP_ENV = "SKYSIFT_BAYESTAR_MAP"            # the map file when --map is not given
MAP_PYTHON_ENV = "SKYSIFT_DUSTMAPS_PYTHON"  # an interpreter with h5py and astropy-healpix (else .venv-dustmaps)
MAP_SOURCE = "Bayestar19 (Green et al. 2019), local file"
MAP_LABEL = f"{bm.MAP_VERSION} (doi:{bm.MAP_DOI}), local file"
MAP_FAILED = "Bayestar19 (Green et al. 2019) not available: {why}"
MAP_UNVERIFIED = "unverified"               # the map_md5 of a map without its sidecar <file>.md5
MAP_TIMEOUT = 900.0                         # seconds for the reader under the third venv (it reads pixel_info once)
MAP_READER = Path(bm.__file__).resolve()
DUSTMAPS_VENV = ".venv-dustmaps"
_MD5 = re.compile(r"[0-9a-fA-F]{32}")


class MapError(ValueError):
    """The local map cannot be read: missing, a wrong MD5, a layout the reader refuses, or no interpreter for it."""


class ColumnError(ValueError):
    """An IRSA DUST answer that carries no full-column E(B-V)."""


class ArgonautError(ValueError):
    """An Argonaut answer that is not three percentiles."""


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


def argonaut_params(ra_deg: float, dec_deg: float, distance_pc: float) -> dict:
    """The Argonaut query for one star: the position, the distance in kpc and the three percentiles."""
    return {"ra": ra_deg, "dec": dec_deg, "coordsys": "equ", "d": round(distance_pc / 1000.0, 6), "mode": "percentile",
            "pct": ",".join(str(p) for p in ARGONAUT_PERCENTILES)}


def parse_argonaut(answer: bytes) -> tuple[float, float, float]:
    """(16th, 50th, 84th) in the map's units from an Argonaut answer: a JSON list of three numbers, or one nested a
    level down (a one-star list, or an object holding the list); ``ArgonautError`` otherwise."""
    try:
        doc = json.loads(answer)
    except (ValueError, UnicodeDecodeError) as e:
        raise ArgonautError("the Argonaut answer does not read as JSON") from e
    if isinstance(doc, dict) and len(doc) == 1:
        doc = next(iter(doc.values()))
    if isinstance(doc, list) and len(doc) == 1 and isinstance(doc[0], list):
        doc = doc[0]
    if not (isinstance(doc, list) and len(doc) == 3 and all(stars._finite(v) and v >= 0 for v in doc)):
        raise ArgonautError("the Argonaut answer is not three percentiles")
    low, mid, high = (float(v) for v in doc)
    if not low <= mid <= high:
        raise ArgonautError("the Argonaut percentiles are not in order")
    return low, mid, high


def argonaut_reason(error: Exception) -> str:
    """Ruling (a)'s neutral reason: the HTTP status, else the exception class the fetch reported, else "unexpected
    answer" for an answer that is not three percentiles."""
    if isinstance(error, ArgonautError):
        why = "unexpected answer"
    else:
        text = str(error)
        status = re.search(r"HTTP \d{3}", text)
        named = re.search(r"attempts: ([A-Za-z_]\w*)", text)
        why = status.group(0) if status else named.group(1) if named else type(error).__name__
    return ARGONAUT_FAILED.format(why=why)


def query_argonaut(ra_deg: float, dec_deg: float, distance_pc: float, *, fetch=None) -> dict:
    """{"ebv", "ebv_p16", "ebv_p84"} (E(B-V), rounded to 0.0001) from one Argonaut GET, or {"reason"} on any failure."""
    fetch = fetch or net.fetch
    # a probe, not the documented API: Argonaut documents a JSON POST, and net.fetch is GET only, so a reachable
    # service is likely to refuse this (stored as "not available", never as a value). Follow-up before any adopted
    # value is claimed: the POST form, and the scale of BAYESTAR_TO_EBV checked against IRSA's SandF column so both
    # ends of the bracket sit on one scale (the round 3 final review, M10)
    try:
        answer = fetch(ARGONAUT_URL, params=argonaut_params(ra_deg, dec_deg, distance_pc), service="argonaut",
                       store=_Log(), attempts=1, timeout=ARGONAUT_TIMEOUT)
        low, mid, high = parse_argonaut(answer)
    except (net.ServiceUnavailable, ArgonautError) as e:
        return {"reason": argonaut_reason(e)}
    return {"ebv": round(BAYESTAR_TO_EBV * mid, 4), "ebv_p16": round(BAYESTAR_TO_EBV * low, 4),
            "ebv_p84": round(BAYESTAR_TO_EBV * high, 4)}


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
    "ebv_mean", "ebv_std", "stats_radius_arcmin", "source", "queried_utc", "ra", "dec", "url", "used"}; when the
    service cannot be reached or gives no value, {"status": "not available", "reason", "queried_utc", "url"}."""
    fetch = fetch or net.fetch
    url, when = column_url(ra_deg, dec_deg), now or _utc_now()
    try:
        values = parse_column(fetch(url, service="irsa-dust", store=_Log(), attempts=1, timeout=COLUMN_TIMEOUT))
    except (net.ServiceUnavailable, ColumnError) as e:
        return {"status": stars.EXT_NOT_AVAILABLE, "reason": str(e), "queried_utc": when, "url": url}
    return {"status": stars.COLUMN_OK, **values, "stats_radius_arcmin": COLUMN_STATS_ARCMIN, "source": COLUMN_SOURCE,
            "queried_utc": when, "ra": ra_deg, "dec": dec_deg, "url": url, "used": COLUMN_USED}


def sidecar_md5(path: Path) -> str:
    """The MD5 the controller wrote beside the map, ``<file>.md5``: the first line whose first token, or whose
    characters once the spaces go (an older ``certutil``), are 32 hex digits, lower-cased; ``MAP_UNVERIFIED`` without
    the sidecar. ``MapError`` when the sidecar holds no digest. The file itself is not hashed here."""
    sidecar = Path(f"{path}.md5")
    if not sidecar.is_file():
        return MAP_UNVERIFIED
    for line in sidecar.read_text(encoding="utf-8", errors="replace").splitlines():
        tokens = line.split()
        for candidate in (tokens[0] if tokens else "", "".join(tokens)):
            if _MD5.fullmatch(candidate):
                return candidate.lower()
    raise MapError(f"{sidecar} holds no MD5 digest")


def dustmaps_python(start: Path | None = None, stop: Path | None = None) -> Path:
    """The interpreter that reads the map when this one cannot: ``MAP_PYTHON_ENV``, else the first ``.venv-dustmaps``
    (``Scripts/python.exe``, or ``bin/python``) in the tool's folder or above it, not above ``stop``. ``MapError``
    when there is none."""
    named = os.environ.get(MAP_PYTHON_ENV)
    if named:
        if not Path(named).is_file():
            raise MapError(f"{named} is not there ({MAP_PYTHON_ENV})")
        return Path(named)
    here = Path(start or Path(__file__).resolve().parent)
    for folder in (here, *here.parents):
        for rel in (("Scripts", "python.exe"), ("bin", "python")):
            candidate = folder.joinpath(DUSTMAPS_VENV, *rel)
            if candidate.is_file():
                return candidate
        if stop is not None and folder == Path(stop):
            break
    raise MapError(f"h5py and astropy-healpix are not importable here and no {DUSTMAPS_VENV} was found above the "
                   f"tool; make it (tools/bayestar_map.py) or set {MAP_PYTHON_ENV}")


def _can_read_here() -> bool:
    """Whether this interpreter holds h5py and astropy-healpix (the engine's ``.venv`` does not)."""
    try:
        import astropy_healpix  # noqa: F401
        import h5py  # noqa: F401
    except ImportError:
        return False
    return True


def read_map(path: Path, ra_deg: float, dec_deg: float, distances_pc, *, run=None) -> list[dict]:
    """``tools.bayestar_map.query_many`` at each distance: in this interpreter when it can open the file, otherwise
    through the reader under ``dustmaps_python()`` (``-I``, the queries as JSON on stdout; ``run`` stands in for
    ``subprocess.run`` in the tests). ``MapError`` with the reader's last word when it fails."""
    distances = [float(d) for d in distances_pc]
    if run is None and _can_read_here():
        import h5py
        try:
            with h5py.File(path, "r") as h5:
                return bm.query_many(h5, ra_deg, dec_deg, distances)
        except (bm.MapLayoutError, OSError) as e:
            raise MapError(f"{path}: {e}") from e
    python = dustmaps_python()
    cmd = [str(python), "-I", str(MAP_READER), "--map", str(path), "--ra", str(float(ra_deg)),
           "--dec", str(float(dec_deg)), "--distance", *(str(d) for d in distances)]
    try:
        done = (run or subprocess.run)(cmd, capture_output=True, text=True, timeout=MAP_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        raise MapError(f"the map reader under {python} did not run ({e!r})") from e
    if done.returncode != 0:
        last = ((done.stderr or "").strip().splitlines() or ["no message"])[-1]
        raise MapError(f"the map reader under {python} failed (exit {done.returncode}): {last}")
    try:
        out = json.loads(done.stdout)
    except ValueError as e:
        raise MapError(f"the map reader under {python} printed no JSON") from e
    if not (isinstance(out, list) and len(out) == len(distances) and all(isinstance(q, dict) for q in out)):
        raise MapError(f"the map reader under {python} gave not one query per distance")
    return out


def _distances(star: dict) -> list[float]:
    """The Gaia distance's median, then its 16th and 84th percentiles (median - minus, median + plus) when both
    errors are stored; the median alone otherwise."""
    d = star.get("distance_pc") or {}
    median, minus, plus = d.get("median"), d.get("minus"), d.get("plus")
    if all(stars._finite(v) for v in (minus, plus)) and median - minus > 0:
        return [float(median), round(float(median - minus), 1), round(float(median + plus), 1)]
    return [float(median)]


def _map_why(q: dict, distance: float) -> tuple[str, str] | None:
    """(the reason, its short form for the texts) when the map gives no usable value at the star, else None."""
    if not q.get("in_footprint"):
        return (f"the sightline (l {q['l_deg']:.2f}, b {q['b_deg']:.2f}) is outside the map's footprint",
                "outside the map's footprint")
    if q.get("converged") is not True:
        why = f"the map's pixel on this sightline did not converge (nside {q['nside']}, pixel {q['healpix_index']})"
        return why, "an unconverged map pixel"
    if not q.get("ebv"):
        return (f"the map gives no finite reddening on this sightline at {distance:.0f} pc",
                "no finite map value at the star's distance")
    return None


def map_block(target: str, queries: list[dict], *, distance: list[float], map_path: Path, md5: str,
              queried_utc: str) -> dict:
    """The stored record from the queries at ``distance`` (the median first, then the 16th and 84th percentiles when
    there): the adopted block of spec 12.5, E(B-V) the samples' median and percentiles at the median distance
    rounded to 4 decimals, with the sample medians at the distance's percentiles beside it; or "not available" with
    ``MAP_FAILED`` and the short reason (``map_reason``) outside the footprint, in an unconverged pixel or without
    a finite sample. The column is added by the caller."""
    q = queries[0]
    file = {"map_file": Path(map_path).name, "map_md5": md5, "map_version": bm.MAP_VERSION}
    why = _map_why(q, distance[0])
    if why is not None:
        return {"target": target, "status": stars.EXT_NOT_AVAILABLE, "reason": MAP_FAILED.format(why=why[0]),
                "map_reason": why[1], "queried_utc": queried_utc, "distance_pc": distance[0], **file}
    ebv, units = q["ebv"], q["ebv_map_units"]
    block = {"target": target, "status": stars.EXT_ADOPTED, "ebv": round(ebv["median"], 4),
             "ebv_p16": round(ebv["p16"], 4), "ebv_p84": round(ebv["p84"], 4),
             "ebv_map_units": {k: round(units[k], 5) for k in ("median", "p16", "p84")}}
    if len(queries) == 3 and all(x.get("ebv") for x in queries[1:]):
        block.update({"ebv_at_distance_p16": round(queries[1]["ebv"]["median"], 4),
                      "ebv_at_distance_p84": round(queries[2]["ebv"]["median"], 4)})
    block.update({"source": MAP_SOURCE, "map": MAP_LABEL, **file, "queried_utc": queried_utc,
                  "distance_pc": distance[0]})
    if len(distance) == 3:
        block.update({"distance_pc_p16": distance[1], "distance_pc_p84": distance[2]})
    block.update({"l_deg": round(q["l_deg"], 4), "b_deg": round(q["b_deg"], 4), "nside": q["nside"],
                  "healpix_index": q["healpix_index"], "n_samples": q["n_samples"], "converged": True,
                  "reliable_distance": bool(q["reliable_distance"])})
    return block


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
    kept = isinstance(old, dict) and isinstance(old.get("column"), dict)
    if kept:
        block["column"] = old["column"]
    problem = stars._block_problem(block)
    if problem:
        _say(f"{target}: the stored block would be invalid: {problem}")
        return 2
    _write(path, block)
    _say(f"{target}: stored by hand, E(B-V) {args.ebv} ({args.ebv_p16} to {args.ebv_p84}) at {args.distance_pc} pc"
         + ("; kept the stored column" if kept else ""))
    return 0


def _column_shown(column: dict) -> str:
    return (f"E(B-V) full column {column['ebv']:.3f} (mean {column['ebv_mean']:.3f} within "
            f"{COLUMN_STATS_ARCMIN:g} arcmin)" if column["status"] == stars.COLUMN_OK
            else f"full column not available: {column['reason']}")


def _run_map(args, target: str, path: Path, ra: float, dec: float, star: dict, stored: dict | None, fetch,
             map_reader) -> int:
    """Spec 12.5: the 3D value from the local map at the Gaia distance and its percentiles; the column kept from a
    stored record (unless ``--force``), else one IRSA GET. Exit 0 adopted, 3 not available, 2 when the map cannot
    be read (the stored file is left as it was)."""
    distance = _distances(star)
    map_path = Path(args.map)
    try:
        if not map_path.is_file():
            raise MapError(f"{map_path} is not there")
        md5 = sidecar_md5(map_path)
        if md5 not in (MAP_UNVERIFIED, bm.MAP_MD5):
            raise MapError(f"the map's MD5 {md5} (from {map_path}.md5) is not the Dataverse record's {bm.MAP_MD5}")
        queries = (map_reader or read_map)(map_path, ra, dec, distance)
    except MapError as e:
        _say(f"{target}: {e}")
        _say(f"{target}: {_hint(target, distance[0])}")
        return 2
    if md5 == MAP_UNVERIFIED:
        _say(f"{target}: no {map_path.name}.md5 beside the map, so map_md5 is {MAP_UNVERIFIED!r}")
    old = (stored or {}).get("column")
    kept = not args.force and isinstance(old, dict) and old.get("status") == stars.COLUMN_OK
    column = old if kept else query_column(ra, dec, fetch=fetch)
    block = {**map_block(target, queries, distance=distance, map_path=map_path, md5=md5, queried_utc=_utc_now()),
             "column": column}
    problem = stars._block_problem(block)
    if problem:
        _say(f"{target}: the map gives a block the dossier would refuse ({problem}); nothing written")
        return 2
    _write(path, block)
    shown = _column_shown(column) + ("; kept the stored column" if kept else "")
    if block["status"] == stars.EXT_NOT_AVAILABLE:
        _say(f"{target}: RA {ra} Dec {dec}, {distance[0]:.1f} pc: {block['reason']}; {shown}; wrote {path}")
        _say(f"{target}: {_hint(target, distance[0])}")
        return 3
    spread = (f", {block['ebv_at_distance_p16']:.3f} to {block['ebv_at_distance_p84']:.3f} at the distance's 16th "
              f"and 84th percentiles ({distance[1]:.1f} and {distance[2]:.1f} pc)"
              if "ebv_at_distance_p16" in block else "")
    flag = "" if block["reliable_distance"] else ", beyond the pixel's reliable distance range"
    _say(f"{target}: RA {ra} Dec {dec}, {distance[0]:.1f} pc: Bayestar19 E(B-V) {block['ebv']:.3f} "
         f"({block['ebv_p16']:.3f} to {block['ebv_p84']:.3f}) from the local map (nside {block['nside']}, pixel "
         f"{block['healpix_index']}){spread}{flag}, adopted; {shown}; wrote {path}")
    return 0


def run_target(args, target: str, fetch=None, map_reader=None) -> int:
    """One target, in the order of the module docstring; the exit code."""
    path = store_path(args.root, target)
    stored = None
    if path.is_file() and not args.force:
        try:
            stored = stars.load_extinction(target, args.root)
        except stars.ExtinctionError as e:
            _say(f"{target}: {path} is malformed ({e}); fix it or write it again with --force")
            return 2
        # spec 12.5: with a map, a record without a 3D value is read again (its column kept); a 3D value stays
        if not (args.map and not args.store and stars.extinction_state(stored) == stars.EXT_NOT_AVAILABLE):
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
    if args.map:
        return _run_map(args, target, path, ra, dec, data["star"], stored, fetch, map_reader)
    bayestar = query_argonaut(ra, dec, float(distance), fetch=fetch)
    column = query_column(ra, dec, fetch=fetch)
    if "reason" in bayestar:
        block = {"target": target, "status": stars.EXT_NOT_AVAILABLE, "reason": bayestar["reason"],
                 "queried_utc": column["queried_utc"], "distance_pc": float(distance), "column": column}
    else:
        block = {"target": target, "status": stars.EXT_ADOPTED, **bayestar, "source": ARGONAUT_SOURCE,
                 "map": ARGONAUT_MAP, "queried_utc": column["queried_utc"], "distance_pc": float(distance),
                 "column": column}
    _write(path, block)
    shown = _column_shown(column)
    if "reason" in bayestar:
        _say(f"{target}: RA {ra} Dec {dec}, {distance:.1f} pc: {shown}; {bayestar['reason']}; wrote {path}")
        _say(f"{target}: {_hint(target, distance)}")
        return 3
    _say(f"{target}: RA {ra} Dec {dec}, {distance:.1f} pc: Bayestar19 E(B-V) {bayestar['ebv']:.3f} "
         f"({bayestar['ebv_p16']:.3f} to {bayestar['ebv_p84']:.3f}) adopted; {shown}; wrote {path}")
    return 0


def main(argv: list[str] | None = None, fetch=None, map_reader=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    who = ap.add_mutually_exclusive_group(required=True)
    who.add_argument("--target")
    who.add_argument("--all", action="store_true", help="every target of LIVE_TARGETS in turn")
    ap.add_argument("--data-dir", type=Path, required=True, help="the folder holding dossiers/<target>/")
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="the candidates folder (docs/candidates)")
    ap.add_argument("--store", action="store_true", help="store a 3D value by hand (one target)")
    ap.add_argument("--ebv", type=float)
    ap.add_argument("--ebv-p16", type=float)
    ap.add_argument("--ebv-p84", type=float)
    ap.add_argument("--distance-pc", type=float)
    ap.add_argument("--source")
    ap.add_argument("--map", type=Path, help=f"the local bayestar2019.h5 (else {MAP_ENV}); no Argonaut query then")
    ap.add_argument("--force", action="store_true", help="write again over a stored file")
    args = ap.parse_args(argv)
    if args.all and args.store:
        _say("--store with --all: store one target at a time")
        return 2
    if args.map is None and os.environ.get(MAP_ENV):
        args.map = Path(os.environ[MAP_ENV])
    targets = LIVE_TARGETS if args.all else (args.target,)
    return max(run_target(args, t, fetch, map_reader) for t in targets)


if __name__ == "__main__":
    sys.exit(main())
