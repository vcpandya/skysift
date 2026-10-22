"""The independent false-positive cross-check (referee pack spec 4): TRICERATOPS (Giacalone et al. 2021), run in its
own environment by ``tools/triceratops_check.py``, since it needs numpy 1.26 and the engine stands on numpy 2.

``export_inputs`` writes what the tool reads from the stored dossier: ``inputs.json`` (the target, the ephemeris with
its errors, the star, the Gaia neighbours, the neighbour KIC whose pixel files hold the star, the quarters with a
pixel file and the inputs ``fingerprint``), ``lightcurve.csv`` (the whitened flux within three durations of each
transit) and ``tpf/q<quarter>.fits.gz``, copies of the kept pixel files. ``import_result`` checks the tool's
``result.json`` and stores it in ``dossiers.cross_check`` with ``current``: whether it was made for the inputs of the
stored dossier. A regenerated dossier keeps the block and recomputes ``current`` (``with_current``). ``verdict`` is
what the live Zenodo gate reads: no block, a block made for other inputs, or a likely false positive on the tool's own
scale blocks it; a disagreement with SkySift's estimate does not, since the two models answer different questions,
and ``comparison_sentence`` prints both numbers for the pack."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import statistics
from pathlib import Path

import numpy as np

from skysift import jsonutil
from skysift.lc.tpf import parse_tpf, tpf_listing_url
from skysift.pack_text import Raw, tex_escape

TOOL = "triceratops"
LIKELY_FP_FPP = 0.5             # Giacalone et al. 2021: FPP at or above this is a likely false positive
LIKELY_NEARBY_NFPP = 0.1        # and NFPP at or above this a likely nearby false positive
N_RUNS = 10                     # runs with different seeds; the median is quoted and every run is kept
WINDOW_DURATIONS = 3.0          # the exported light curve: within this many durations of each transit
RATIO_SENTENCE_ABOVE = 3.0      # the pack explains the difference when the two estimates differ by more than this
RESULT_KEYS = ("tool", "tool_version", "run_utc", "n_runs", "fpp", "nfpp", "fpp_runs", "nfpp_runs", "scenarios",
               "stars", "inputs_sha256", "caveats", "settings")
CITATION = "(Giacalone et al. 2021)"
DIFFERENT_QUESTIONS = ("The two models answer different questions: SkySift's scenario model counts the catalogued "
                       "neighbours and the pixel exclusion, while TRICERATOPS marginalises over an unresolved "
                       "companion population.")
_SHA = re.compile(r"[0-9a-f]{64}")
_FINGERPRINT_DIGITS = 8         # the dossier JSON's significant figures (dossier.SIG_FIGS)


class NoDossier(LookupError):
    """The target has no generated dossier, or its data file is gone."""


class BadResult(ValueError):
    """A result file that is not the tool's: unreadable, not JSON, or a key missing or out of range."""


class NoPixelFiles(ValueError):
    """The export found no readable kept pixel file of the neighbour KIC: the tool has no image to place stars on."""


def _round(x):
    return None if x is None else float(f"{float(x):.{_FINGERPRINT_DIGITS}g}")


def inputs_fingerprint(data: dict) -> str:
    """The SHA-256 of what the cross-check depends on in a dossier: the period, T0, depth and duration, the target's
    G and the Gaia neighbours (source id and G, sorted by id), each number at the dossier's 8 significant figures."""
    eph = data.get("ephemeris") or {}
    inputs = (data.get("fpp") or {}).get("inputs") or {}
    gmag = (data.get("target") or {}).get("gmag")
    neighbours = sorted([str(n.get("source_id")), _round(n.get("gmag"))] for n in inputs.get("neighbours") or [])
    key = {"period_days": _round(eph.get("period_days")), "t0_bkjd": _round(eph.get("t0_bkjd")),
           "depth_ppm": _round(eph.get("depth_ppm")), "duration_h": _round(eph.get("duration_h")),
           "gmag": _round(gmag if gmag is not None else inputs.get("gmag")), "neighbours": neighbours}
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode("utf-8")).hexdigest()


def _stored(store, target_id: str) -> dict:
    row = store.dossier(target_id)
    if row is None or not row.get("data_path") or not Path(row["data_path"]).is_file():
        raise NoDossier(f"no dossier for {target_id}; run skysift dossier --target {target_id} first")
    return json.loads(Path(row["data_path"]).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------------------------------- export

def _lightcurve_rows(lc, fnorm, eph: dict) -> np.ndarray:
    """(time, flux, err, quarter) of the whitened flux within ``WINDOW_DURATIONS`` durations of each transit; the
    error relative to the median flux, as the pack's light curve."""
    time, flux, err = np.asarray(lc.time, float), np.asarray(lc.flux, float), np.asarray(lc.flux_err, float)
    period, t0, dur = float(eph["period_days"]), float(eph["t0_bkjd"]), float(eph["duration_h"]) / 24.0
    phase = (time - t0 + period / 2) % period - period / 2
    good = np.isfinite(fnorm) & np.isfinite(time) & np.isfinite(err) & (np.abs(phase) <= WINDOW_DURATIONS * dur)
    scale = float(np.nanmedian(flux[np.isfinite(flux)])) or 1.0
    return np.column_stack([time[good], fnorm[good], err[good] / scale, np.asarray(lc.quarter)[good]])


def _copy_pixel_files(store, blob, kic: int | None, tpf_dir: Path) -> tuple[list[int], list[str], list[dict]]:
    """Copy each kept pixel file of ``kic`` to ``tpf_dir/q<quarter>.fits.gz`` (``.fits`` when it is not gzipped).
    Returns (quarters, the copies' paths relative to the export, the files that could not be read)."""
    quarters, files, missing = [], [], []
    kept = store.kept_artifacts("tpf", tpf_listing_url(kic)) if kic else {}
    for url, local_path in sorted(kept.items()):
        name = url.rsplit("/", 1)[-1]
        try:
            raw = blob.get(local_path)
            quarter = parse_tpf(raw, name).quarter
        except Exception as e:          # noqa: BLE001  (gone from the blob, or not a readable pixel file)
            missing.append({"name": name, "cause": f"{type(e).__name__}: {e}"})
            continue
        if quarter in quarters:
            missing.append({"name": name, "cause": f"a second file for quarter {quarter}"})
            continue
        rel = f"tpf/q{quarter:02d}.fits" + (".gz" if raw[:2] == b"\x1f\x8b" else "")
        tpf_dir.mkdir(parents=True, exist_ok=True)
        (tpf_dir.parent / rel).write_bytes(raw)
        quarters.append(quarter)
        files.append(rel)
    order = sorted(range(len(quarters)), key=quarters.__getitem__)
    return [quarters[i] for i in order], [files[i] for i in order], missing


def export_inputs(store, blob, settings, target_id: str, out_dir: Path) -> Path:
    """Write ``inputs.json``, ``lightcurve.csv`` and ``tpf/`` for the tool into ``out_dir`` from the stored dossier
    and the loader's light curve, loaded with ``pin=True`` as the pack loads it (so the raw file stays out of budget
    retention; nothing is downloaded: a released light curve raises ``dossier.LightCurveReleased``). Returns
    ``out_dir``, the tool's ``--inputs``. Raises NoDossier, and NoPixelFiles (naming the files it could not read)
    when no pixel file was copied; the light curve is then written, ``inputs.json`` is not."""
    from skysift import dossier as ds
    from skysift.lc.loader import LightCurveLoader
    data = _stored(store, target_id)
    sid = int(data["signal"]["id"])
    target = ds._target_dict(store, target_id)
    lc = LightCurveLoader(store, blob, source_factory=ds._offline_factory(target_id, sid)).load(target, pin=True)
    rows = _lightcurve_rows(lc, ds.whitened(lc, data["signal"]), data["ephemeris"])
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "lightcurve.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["time_bkjd", "flux_rel", "err_rel", "quarter"])
        w.writerows([f"{t:.6f}", f"{f:.8f}", f"{e:.8f}", int(q)] for t, f, e, q in rows)
    t = data.get("target") or {}
    kic = (t.get("meta") or {}).get("tpf_org") or t.get("kic")
    kic = int(kic) if kic else None
    quarters, files, missing = _copy_pixel_files(store, blob, kic, out_dir / "tpf")
    if not quarters:
        unread = "; ".join(f"{m['name']}: {m['cause']}" for m in missing) or "none is kept"
        raise NoPixelFiles(f"no pixel file of KIC {kic} could be copied for {target_id} (tpf_missing: {unread}); a "
                           "kept file is read from its stored path, and a relative path is read from the folder the "
                           "command runs in, so run it from the folder the engine's data paths start at")
    neighbours = [{k: n.get(k) for k in ("source_id", "sep_arcsec", "gmag", "bp_rp")}
                  for n in ((data.get("fpp") or {}).get("inputs") or {}).get("neighbours") or []]
    inputs = {"target": {k: t.get(k) for k in ("target_id", "gaia_id", "kic", "ra", "dec", "gmag")},
              "signal_id": sid, "dossier_generated": data.get("generated"), "ephemeris": data.get("ephemeris"),
              "star": data.get("star"), "neighbours": neighbours, "neighbour_kic": kic, "quarters": quarters,
              "tpf_files": files, "tpf_missing": missing,
              "lightcurve": {"file": "lightcurve.csv", "window_durations": int(WINDOW_DURATIONS), "n": len(rows),
                             "columns": ["time_bkjd", "flux_rel", "err_rel", "quarter"]},
              "fingerprint": inputs_fingerprint(data)}
    (out_dir / "inputs.json").write_text(jsonutil.dumps(inputs, indent=1), encoding="utf-8")
    return out_dir


# ---------------------------------------------------------------------------------------------------- import

def _probability(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and 0.0 <= x <= 1.0


def validate_result(result) -> None:
    """Raise BadResult naming the first thing wrong with a result: a missing key, another tool, a run count that is
    not a positive integer, a probability outside 0 to 1 or not finite, a run list of the wrong length, a median that
    is not the runs' median, or an inputs hash that is not a SHA-256."""
    if not isinstance(result, dict):
        raise BadResult("the result file does not hold a JSON object")
    lacking = [k for k in RESULT_KEYS if k not in result]
    if lacking:
        raise BadResult(f"the result file lacks {', '.join(lacking)}")
    if result["tool"] != TOOL:
        raise BadResult(f"the result's tool is {result['tool']!r}, not {TOOL!r}")
    n = result["n_runs"]
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise BadResult(f"n_runs must be a positive integer, not {n!r}")
    for key in ("fpp", "nfpp"):
        if not _probability(result[key]):
            raise BadResult(f"{key} must be a probability between 0 and 1, not {result[key]!r}")
        runs = result[f"{key}_runs"]
        if not isinstance(runs, list) or len(runs) != n or not all(_probability(x) for x in runs):
            raise BadResult(f"{key}_runs must list {n} probabilities between 0 and 1")
        if not math.isclose(result[key], statistics.median(runs), rel_tol=1e-6, abs_tol=1e-12):
            raise BadResult(f"{key} {result[key]!r} is not the median of {key}_runs")
    if not isinstance(result["inputs_sha256"], str) or not _SHA.fullmatch(result["inputs_sha256"]):
        raise BadResult("inputs_sha256 must be the 64-character SHA-256 of the exported inputs")
    for key, kind in (("tool_version", str), ("run_utc", str), ("scenarios", list), ("stars", list),
                      ("caveats", list), ("settings", dict)):
        if not isinstance(result[key], kind):
            raise BadResult(f"{key} must be a {kind.__name__}")


def with_current(block: dict | None, data: dict) -> dict | None:
    """The stored block with ``current`` recomputed against ``data`` (a dossier's JSON); None without a block."""
    if not block:
        return None
    return {**block, "current": block.get("inputs_sha256") == inputs_fingerprint(data)}


def import_result(store, target_id: str, path: Path) -> dict:
    """Check the tool's result file and store it on the target's dossier row with ``current`` (made for the stored
    dossier's inputs or not). Returns the stored block. Raises NoDossier and BadResult (nothing is stored)."""
    data = _stored(store, target_id)
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as e:
        raise BadResult(f"the result file cannot be read ({type(e).__name__})") from None
    try:
        result = json.loads(text)
    except ValueError:
        raise BadResult("the result file is not a JSON file") from None
    validate_result(result)
    block = with_current({k: result[k] for k in RESULT_KEYS}, data)
    store.set_cross_check(target_id, block)
    return block


# ---------------------------------------------------------------------------------------------------- verdict

def verdict(block: dict | None) -> tuple[bool, str | None]:
    """(whether the live gate is blocked, the reason): no block, a block made for an earlier dossier, or a likely
    false positive (median FPP at or above 0.5) or likely nearby false positive (median NFPP at or above 0.1) on
    the tool's own scale. A disagreement with SkySift's estimate never blocks."""
    if not block:
        return True, "no independent cross-check"
    if block.get("current") is not True:
        return True, "the cross-check was made for an earlier dossier"
    if block["fpp"] >= LIKELY_FP_FPP:
        return True, "the cross-check calls it a likely false positive"
    if block["nfpp"] >= LIKELY_NEARBY_NFPP:
        return True, "the cross-check calls it a likely nearby false positive"
    return False, None


def probability_text(x: float) -> str:
    """Two significant figures: 0.012, 0.0035, and 1.0e-04 below a thousandth; 0 as 0."""
    if x == 0:
        return "0"
    if x < 1e-3:
        return f"{x:.1e}"
    return f"{x:.{max(0, 1 - math.floor(math.log10(x)))}f}"


def comparison_sentence(data: dict, tex: bool = False) -> str | None:
    """For the pack's texts: the cross-check's median FPP and NFPP, SkySift's own FPP beside it, and, when the two
    differ by more than a factor of three, one sentence on why. None without a cross-check made for this dossier's
    inputs. With ``tex`` a LaTeX ``Raw`` citing ``giacalone2021``."""
    block = data.get("cross_check")
    if not block or block.get("current") is not True:
        return None
    head = "An independent estimate with TRICERATOPS "
    rest = (f" gives a false-positive probability of {probability_text(block['fpp'])} and a nearby false-positive "
            f"probability of {probability_text(block['nfpp'])}, the median over {block['n_runs']} runs.")
    own = (data.get("fpp") or {}).get("value")
    if isinstance(own, (int, float)) and not isinstance(own, bool) and math.isfinite(own):
        hi, lo = max(own, block["fpp"]), min(own, block["fpp"])
        ratio = 1.0 if hi == 0 else hi / lo if lo > 0 else math.inf          # both 0 agree
        if ratio > RATIO_SENTENCE_ABOVE:
            apart = "far" if math.isinf(ratio) else f"a factor of {ratio:.1f}"
            rest += (f" SkySift's own estimate is {probability_text(own)}, so the two are {apart} apart. "
                     f"{DIFFERENT_QUESTIONS}")
        else:
            rest += f" SkySift's own estimate is {probability_text(own)}."
    if tex:
        return Raw(tex_escape(head) + r"\citep{giacalone2021}" + tex_escape(rest))
    return head + CITATION + rest
