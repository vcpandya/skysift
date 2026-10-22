"""The independent false-positive cross-check (referee pack spec 4, revised by its rulings (n) to (v) of 22 October
2026): TRICERATOPS (Giacalone et al. 2021), run in its own environment by ``tools/triceratops_check.py``, since it
needs numpy 1.26 and the engine stands on numpy 2.

``export_inputs`` writes what the tool reads from the stored dossier: ``inputs.json`` (the target with its KBonus
CROWDSAP and PSFFRAC, the ephemeris with its errors, the star, the Gaia neighbours with the Teff, radius and mass the
tool sets on any TIC row that lacks them (``neighbour_params``), rung 5's leak depth, the neighbour KIC whose pixel
files hold the star, the quarters with a pixel file and the inputs ``fingerprint``), ``lightcurve.csv`` (the whitened
flux within three durations of each transit, the points beyond 5 MAD of a box model clipped and counted:
``clip_window``) and ``tpf/q<quarter>.fits.gz``, copies of the kept pixel files. ``import_result`` checks the tool's
``result.json`` and stores it in ``dossiers.cross_check`` with ``current``: whether it was made for the inputs of the
stored dossier. A regenerated dossier keeps the block and recomputes ``current`` and the ``class`` of its medians
(``with_current``). ``verdict`` is what the live Zenodo gate reads: no block, a block made for other inputs or one
stored by an earlier version of the tool blocks it; the numbers never do, since Giacalone et al.'s classes are labels
(ruling t). ``comparison_sentence`` prints the medians with their 16th to 84th percentiles for the pack."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from skysift import jsonutil
from skysift.lc.tpf import fetch_tpfs, parse_tpf, tpf_listing_url
from skysift.pack_text import Raw, tex_escape

TOOL = "triceratops"
MIN_DRAWS = 1_000_000  # draws per run the import accepts; the tool's default (its N_DRAWS); fewer is a smoke
RADIUS_MISMATCH = 0.2  # a radius more than this fraction above the dwarf table's at the star's Teff is evolved
N_RUNS = 20                     # at least this many seeded runs (ruling s); the import refuses fewer
WINDOW_DURATIONS = 3.0          # the exported light curve: within this many durations of each transit
CLIP_MAD = 5.0                  # a point further than this many MADs from a box model is clipped (ruling p)
RATIO_SENTENCE_ABOVE = 3.0      # the pack explains the difference when the two estimates differ by more than this
# Giacalone et al. 2021 (abstract, section 4.1, Figure 12): the lines of its classes, labels only (ruling t)
VALIDATED_FPP, LIKELY_PLANET_FPP, PLANET_NFPP, LIKELY_NFP_NFPP = 0.015, 0.5, 1e-3, 0.1
CLASSES = ("validated", "likely planet", "likely nearby false positive", "candidate")
DOMINANT_ABOVE = 0.5            # a median full FPP at or above this is explained by its dominant scenario
METRICS = ("fpp", "fpp_no_stp", "nfpp", "p_stp", "p_ptp")
SUMMARY_KEYS = tuple(f"{m}{s}" for m in METRICS for s in ("", "_p16", "_p84"))
RESULT_KEYS = ("tool", "tool_version", "run_utc", "n_runs", "n_degenerate", *SUMMARY_KEYS, "runs", "scenarios",
               "stars", "inputs_sha256", "caveats", "settings")
RUN_KEYS = ("seed", "fpp", "fpp_no_stp", "nfpp", "p_stp", "p_ptp", "best_scenario", "degenerate")
CITATION = "(Giacalone et al. 2021)"
# the reason the two estimates may differ, in the short form the word-limited note can carry (Task 8 fix round 1):
# SkySift's scenario model counts the catalogued neighbours and the pixel exclusion, TRICERATOPS marginalises over an
# unresolved companion population with its own priors
DIFFERENT_QUESTIONS = "the two models answer different questions"
# the scenarios of the package's probability table in words, for the sentence on a dominant scenario
SCENARIO_WORDS = {
    "TP": "a planet transiting the target", "EB": "an eclipsing binary on the target",
    "EBx2P": "an eclipsing binary on the target at twice the period",
    "PTP": "a planet transiting the target with an unresolved bound companion",
    "PEB": "an eclipsing binary on the target with an unresolved bound companion",
    "PEBx2P": "an eclipsing binary on the target with an unresolved bound companion, at twice the period",
    "STP": "a planet transiting an unresolved bound companion",
    "SEB": "an unresolved bound companion that is an eclipsing binary",
    "SEBx2P": "an unresolved bound companion that is an eclipsing binary at twice the period",
    "DTP": "a planet transiting the target diluted by an unresolved background star",
    "DEB": "an eclipsing binary on the target diluted by an unresolved background star",
    "DEBx2P": "an eclipsing binary on the target diluted by an unresolved background star, at twice the period",
    "BTP": "a planet transiting an unresolved background star",
    "BEB": "an unresolved background eclipsing binary",
    "BEBx2P": "an unresolved background eclipsing binary at twice the period",
    "NTP": "a planet transiting a nearby star", "NEB": "an eclipsing binary on a nearby star",
    "NEBx2P": "an eclipsing binary on a nearby star at twice the period",
}
NEARBY_SCENARIOS = ("NTP", "NEB", "NEBx2P")     # the scenarios on a named nearby star
NOT_FALSE_POSITIVES = ("TP", "PTP", "DTP")      # the package's FPP is one minus these three (its equation 4)
# the Teff, radius and mass of each Gaia neighbour, for a TIC row that lacks them (ruling q); radius_gspphot lives in
# astrophysical_parameters, not gaia_source (asking gaia_source for it is an HTTP 400, checked 22 October 2026)
Q_NEIGHBOUR_PARAMS = ("SELECT n.source_id, n.teff_gspphot, n.bp_rp, ap.radius_gspphot, ap.mass_flame "
                      "FROM gaiadr3.gaia_source AS n LEFT OUTER JOIN gaiadr3.astrophysical_parameters AS ap "
                      "ON ap.source_id = n.source_id WHERE n.source_id IN ({ids})")
_SHA = re.compile(r"[0-9a-f]{64}")
_FINGERPRINT_DIGITS = 8         # the dossier JSON's significant figures (dossier.SIG_FIGS)


class NoDossier(LookupError):
    """The target has no generated dossier, or its data file is gone."""


class OldDossier(ValueError):
    """The stored dossier is below the current ``dossier.DOSSIER_VERSION``: its ephemeris has no errors and other
    period, epoch, duration and depth keys, so a cross-check made from it could never be current."""


class BadResult(ValueError):
    """A result file that is not the tool's: unreadable, not JSON, or a key missing or out of range."""


class NoPixelFiles(ValueError):
    """The export found no readable kept pixel file of the neighbour KIC: the tool has no image to place stars on."""


def _round(x):
    return None if x is None else float(f"{float(x):.{_FINGERPRINT_DIGITS}g}")


def _finite(x) -> float | None:
    if isinstance(x, bool) or x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


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


def clip_window(time, flux, eph: dict) -> tuple[np.ndarray, float | None]:
    """(the mask of points kept, the clipping half-width in relative flux): a point is clipped when its residual
    from a box model at the ephemeris (one minus the depth within half a duration of mid-transit, one elsewhere) lies
    further than ``CLIP_MAD`` median absolute deviations from the residuals' median (ruling p; the Holczer et al. 2016
    rule ``skysift.ephemeris`` applies to transit times). The box keeps the transits out of the clip. Every point is
    kept, with no half-width, when the depth is unknown or the deviation is zero."""
    time, flux = np.asarray(time, float), np.asarray(flux, float)
    keep = np.ones(time.size, bool)
    depth = _finite(eph.get("depth_ppm"))
    if depth is None or time.size == 0:
        return keep, None
    period, t0, half = float(eph["period_days"]), float(eph["t0_bkjd"]), float(eph["duration_h"]) / 48.0
    phase = (time - t0 + period / 2) % period - period / 2
    resid = flux - np.where(np.abs(phase) <= half, 1.0 - depth / 1e6, 1.0)
    dev = np.abs(resid - np.median(resid))
    mad = float(np.median(dev))
    if mad <= 0:
        return keep, None
    return dev <= CLIP_MAD * mad, CLIP_MAD * mad


def _table_value(x: float, xcol: int, ycol: int) -> float:
    """The Mamajek dwarf table's column ``ycol`` at ``x`` in column ``xcol``, interpolated and held at the edges."""
    from skysift.stars import mamajek_table
    tab = mamajek_table()
    order = np.argsort(tab[:, xcol], kind="stable")
    return float(np.interp(x, tab[order, xcol], tab[order, ycol]))


def _off_table(x: float, col: int) -> bool:
    """Whether ``x`` lies outside the Mamajek dwarf table's range in column ``col``, so a value read at it is held at
    the table's edge."""
    from skysift.stars import mamajek_table
    tab = mamajek_table()[:, col]
    return not float(np.min(tab)) <= x <= float(np.max(tab))


def neighbour_params(row: dict | None, bp_rp) -> dict:
    """The Teff (K), radius and mass (solar) the tool sets on a TIC row that lacks them (ruling q), each with its
    source: Gaia DR3's GSP-Phot Teff and radius and its FLAME mass from ``row`` (the neighbour's row of the Gaia query,
    None when Gaia had none or was not reached); a mass missing beside a GSP-Phot radius from the Mamajek dwarf table's
    mass-radius relation at that radius; anything else missing from the table at the BP-RP colour (Pecaut and Mamajek
    2013), the row's own colour first. A GSP-Phot radius more than ``RADIUS_MISMATCH`` above the dwarf table's radius
    at the star's Teff (an evolved star), or without a Teff above the table's largest radius, leaves the mass None
    when there is no FLAME mass, its source saying why and "evolved; mass left to the package", since the dwarf
    relation would give a dwarf's mass. A value read outside the table's range says it is held at the table's edge. A parameter neither Gaia nor a
    colour gives is None, so the package's own estimate stays. Returns {"teff", "radius", "mass", "mass_radius" (the
    radius the mass belongs to, so the tool can leave the mass to the package when the radius the package uses is
    another; None without a mass), "source": {"teff", "radius", "mass"}}."""
    from skysift.priors import CITATIONS
    from skysift.stars import mamajek_table
    row = row or {}
    colour = _finite(row.get("bp_rp"))
    colour = _finite(bp_rp) if colour is None else colour
    ref = CITATIONS["DWARF_SEQUENCE"]
    table = (f"Mamajek dwarf table at BP-RP {colour:.3f} ({ref})"
             + (", held at the table's edge" if _off_table(colour, 0) else "")) if colour is not None else None
    largest = float(np.max(mamajek_table()[:, 2]))
    out: dict = {"source": {}}
    for key, col, gaia_key, gaia_words in (("teff", 1, "teff_gspphot", "Gaia DR3 GSP-Phot"),
                                           ("radius", 2, "radius_gspphot", "Gaia DR3 GSP-Phot"),
                                           ("mass", 3, "mass_flame", "Gaia DR3 FLAME")):
        value = _finite(row.get(gaia_key))
        if value is not None and value > 0:
            out[key], out["source"][key] = value, gaia_words
        elif key == "mass" and out["source"].get("radius") == "Gaia DR3 GSP-Phot" and out.get("teff") and \
                out["radius"] > (1 + RADIUS_MISMATCH) * (dwarf := _table_value(out["teff"], 1, 2)):
            held = " (Teff held at the table's edge)" if _off_table(out["teff"], 1) else ""
            out[key] = None
            out["source"][key] = (f"Gaia DR3 GSP-Phot radius {out['radius']:.3f} is more than "
                                  f"{RADIUS_MISMATCH:.0%} above the dwarf table's {dwarf:.3f} at Teff "
                                  f"{out['teff']:.0f} K{held}: evolved; mass left to the package")
        elif key == "mass" and out["source"].get("radius") == "Gaia DR3 GSP-Phot" and not out.get("teff") and \
                out["radius"] > largest:
            out[key] = None
            out["source"][key] = (f"Gaia DR3 GSP-Phot radius {out['radius']:.3f} above the dwarf table's largest "
                                  f"{largest:.2f}, with no Teff to compare it at: evolved; mass left to the package")
        elif key == "mass" and out["source"].get("radius") == "Gaia DR3 GSP-Phot":
            out[key] = round(_table_value(out["radius"], 2, 3), 3)
            out["source"][key] = (f"Mamajek dwarf table mass-radius relation at the GSP-Phot radius "
                                  f"{out['radius']:.3f} ({ref})"
                                  + (", held at the table's edge" if _off_table(out["radius"], 2) else ""))
        elif table is not None:
            out[key] = round(_table_value(colour, 0, col), 0 if key == "teff" else 3)
            out["source"][key] = table
        else:
            out[key], out["source"][key] = None, None
    out["mass_radius"] = out["radius"] if out["mass"] is not None else None
    return out


def _query_neighbour_params(neighbours: list[dict], *, fetch, store, target_id: str) -> tuple[dict, str | None]:
    """({source_id: Gaia row}, None) for the neighbours from one logged query, or ({}, the reason, naming the error's
    type and message) when the query failed in any way (an outage, a refusal of the budget, an HTTP 400 on a schema
    change): the colour route then gives the parameters, and nothing long has started."""
    from skysift import stars
    ids = [str(n.get("source_id")) for n in neighbours if re.fullmatch(r"[0-9]+", str(n.get("source_id") or ""))]
    if not ids:
        return {}, None
    try:
        rows = stars._adql(Q_NEIGHBOUR_PARAMS.format(ids=", ".join(ids)), "neighbour parameters", ("source_id",),
                           fetch=fetch, store=store, target=SimpleNamespace(target_id=target_id))
    except Exception as e:          # noqa: BLE001  (any failure: the colour route stands, and the note names it)
        return {}, (f"The Gaia DR3 query for the neighbours' parameters failed ({type(e).__name__}: {e}), so they "
                    "come from the colour")
    return {str(r.get("source_id")): r for r in rows}, None


def _leak(data: dict) -> dict | None:
    """Rung 5's leak test, the dip at the ephemeris in the neighbour's own aperture: {"depth_ppm", "err_ppm",
    "kic"}, or None when it was not stored. It is the opposite direction to the leak into this star's light curve, so
    the tool reports it as a consistency check and never uses it as a share (ruling n)."""
    px = next((t.get("metrics") or {} for t in data.get("tests") or [] if t.get("rung") == "rung5_pixels"), {})
    leak = px.get("leak_depth_ppm")
    if not (isinstance(leak, list) and leak and _finite(leak[0]) is not None):
        return None
    kic = ((data.get("target") or {}).get("meta") or {}).get("tpf_org") or px.get("neighbour_kic")
    return {"depth_ppm": _finite(leak[0]), "err_ppm": _finite(leak[1]) if len(leak) > 1 else None, "kic": kic}


def _copy_pixel_files(store, blob, kic: int | None,
                      tpf_dir: Path) -> tuple[list[int], list[str], list[str], list[dict]]:
    """Copy each kept pixel file of ``kic`` to ``tpf_dir/q<quarter>.fits.gz`` (``.fits`` when it is not gzipped).
    Returns (quarters, the copies' paths relative to the export, their archive names, the files that could not be
    read), the first three in quarter order."""
    quarters, files, names, missing = [], [], [], []
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
        names.append(name)
    order = sorted(range(len(quarters)), key=quarters.__getitem__)
    return [quarters[i] for i in order], [files[i] for i in order], [names[i] for i in order], missing


def _manifest(data: dict) -> list[str]:
    """The pixel files rung 5 used (its ``tpf_manifest``), [] when it stored none."""
    px = next((t.get("metrics") or {} for t in data.get("tests") or [] if t.get("rung") == "rung5_pixels"), {})
    return [str(n) for n in px.get("tpf_manifest") or []]


def _pixel_files(store, blob, kic: int | None, manifest: list[str], tpf_dir: Path, *, fetch,
                 target_id: str) -> tuple[list[int], list[str], list[int], list[dict]]:
    """Copy the pixel files of ``kic`` for the tool and pin them, so budget retention keeps the files the
    cross-check was made from. The kept files that read back are copied first; when one of them does not, or rung 5's
    manifest names a file that is not kept (retention dropped it: its row deleted, or its file gone from disk), the
    engine's own pixel-file path (``lc.tpf.fetch_tpfs``: the MAST listing and one request per file the cache cannot
    give, service "mast", stored through the blob and logged under ``target_id``) fetches what is missing and the
    copy runs again. Every copied file is then pinned (``Store.add_artifact`` with ``pinned=True`` on its live row).
    Returns (quarters, the copies' paths, the quarters fetched fresh, the files neither kept nor supplied by MAST)."""
    quarters, files, names, missing = _copy_pixel_files(store, blob, kic, tpf_dir)
    fetched: list[int] = []
    if kic and (missing or not manifest or not set(manifest) <= set(names)):
        kept, unsupplied = set(names), []
        try:
            unsupplied = list(fetch_tpfs(kic, fetch=fetch, blob=blob, store=store, target_id=target_id).missing)
        except Exception as e:          # noqa: BLE001  (an outage or any other failure: the kept files still go out,
            # and the cause is named in tpf_missing)
            unsupplied = [{"name": tpf_listing_url(kic),
                           "cause": f"MAST did not supply the pixel files: {type(e).__name__}: {e}"}]
        quarters, files, names, missing = _copy_pixel_files(store, blob, kic, tpf_dir)
        missing += [m for m in unsupplied if m.get("name") not in names]
        fetched = [q for q, n in zip(quarters, names, strict=True) if n not in kept]
    base = tpf_listing_url(kic) if kic else ""
    for name in names:
        art = store.live_artifact(base + name)
        if art is not None and not art["pinned"]:
            store.add_artifact(art["sha256"], art["nbytes"], base + name, art["local_path"], pinned=True,
                               kind="tpf")
    return quarters, files, fetched, missing


def export_inputs(store, blob, settings, target_id: str, out_dir: Path, *, fetch=None) -> Path:
    """Write ``inputs.json``, ``lightcurve.csv`` and ``tpf/`` for the tool into ``out_dir`` from the stored dossier
    and the loader's light curve, loaded with ``pin=True`` as the pack loads it (so the raw file stays out of budget
    retention; the light curve is never downloaded: a released one raises ``dossier.LightCurveReleased``). Through
    ``fetch`` (``net.fetch`` when None), each request logged: the Gaia query for the neighbours' Teff, radius and
    mass (when Gaia does not answer they come from the colour and ``neighbour_params_note`` says so), and, when
    retention has dropped pixel files rung 5 used, MAST for those (``_pixel_files``; every copied file is pinned, and
    ``tpf_kept`` and ``tpf_fetched`` name the quarters that were kept and fetched fresh). Returns
    ``out_dir``, the tool's ``--inputs``. Raises NoDossier, OldDossier for a dossier below the current version (before
    anything is written), and NoPixelFiles (naming the files it could not read) when no pixel file was copied; the
    light curve is then written, ``inputs.json`` is not."""
    from skysift import dossier as ds
    from skysift import net
    from skysift.lc.loader import LightCurveLoader
    fetch = net.fetch if fetch is None else fetch
    data = _stored(store, target_id)
    stored = str(data.get("dossier_version") or "1")
    if not stored.isdigit() or int(stored) < int(ds.DOSSIER_VERSION):
        raise OldDossier(f"dossier version {stored} is below {ds.DOSSIER_VERSION}; regenerate first (skysift dossier "
                         f"--target {target_id} --force), since a cross-check made from it could never be current")
    sid = int(data["signal"]["id"])
    target = ds._target_dict(store, target_id)
    lc = LightCurveLoader(store, blob, source_factory=ds._offline_factory(target_id, sid)).load(target, pin=True)
    rows = _lightcurve_rows(lc, ds.whitened(lc, data["signal"]), data["ephemeris"])
    keep, half_width = clip_window(rows[:, 0], rows[:, 1], data["ephemeris"])
    eph = data["ephemeris"]
    phase = (rows[:, 0] - float(eph["t0_bkjd"]) + float(eph["period_days"]) / 2) % float(eph["period_days"]) \
        - float(eph["period_days"]) / 2
    in_transit = np.abs(phase) <= float(eph["duration_h"]) / 48.0     # the box model's half a duration
    clipped, rows = int((~keep).sum()), rows[keep]
    clipped_in = int((~keep & in_transit).sum())
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "lightcurve.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["time_bkjd", "flux_rel", "err_rel", "quarter"])
        w.writerows([f"{t:.6f}", f"{f:.8f}", f"{e:.8f}", int(q)] for t, f, e, q in rows)
    t = data.get("target") or {}
    meta = t.get("meta") or {}
    kic = meta.get("tpf_org") or t.get("kic")
    kic = int(kic) if kic else None
    quarters, files, fetched, missing = _pixel_files(store, blob, kic, _manifest(data), out_dir / "tpf",
                                                     fetch=fetch, target_id=target_id)
    if not quarters:
        unread = ("; ".join(f"{m['name']}: {m['cause']}" for m in missing)
                  or "none is kept and MAST lists none")
        raise NoPixelFiles(f"no pixel file of KIC {kic} could be copied for {target_id} (tpf_missing: {unread}); a "
                           "kept file is read from its stored path, and a relative path is read from the folder the "
                           "command runs in, so run it from the folder the engine's data paths start at")
    stored_neighbours = ((data.get("fpp") or {}).get("inputs") or {}).get("neighbours") or []
    gaia_rows, params_note = _query_neighbour_params(stored_neighbours, fetch=fetch, store=store,
                                                     target_id=target_id)
    neighbours = []
    for n in stored_neighbours:
        p = neighbour_params(gaia_rows.get(str(n.get("source_id"))), n.get("bp_rp"))
        neighbours.append({**{k: n.get(k) for k in ("source_id", "sep_arcsec", "gmag", "bp_rp")},
                           "teff": p["teff"], "radius": p["radius"], "mass": p["mass"], "mass_radius": p["mass_radius"],
                           "params_source": p["source"]})
    kepmag = next((meta.get(k) for k in ("kepmag", "kp") if _finite(meta.get(k)) is not None), None)
    inputs = {"target": {**{k: t.get(k) for k in ("target_id", "gaia_id", "kic", "ra", "dec", "gmag")},
                         "crowdsap": _finite(meta.get("crowdsap")), "psffrac": _finite(meta.get("psffrac")),
                         "kepmag": _finite(kepmag)},
              "signal_id": sid, "dossier_generated": data.get("generated"), "ephemeris": data.get("ephemeris"),
              "star": data.get("star"), "neighbours": neighbours, "neighbour_params_note": params_note,
              "leak": _leak(data), "neighbour_kic": kic, "quarters": quarters, "tpf_files": files,
              "tpf_kept": [q for q in quarters if q not in fetched], "tpf_fetched": fetched, "tpf_missing": missing,
              "lightcurve": {"file": "lightcurve.csv", "window_durations": int(WINDOW_DURATIONS), "n": len(rows),
                             "columns": ["time_bkjd", "flux_rel", "err_rel", "quarter"], "clipped": clipped,
                             "clipped_in_transit": clipped_in, "clipped_out_of_transit": clipped - clipped_in,
                             "clip_mad": CLIP_MAD, "clip_mad_kind": "raw (5 MAD is about 3.4 sigma)",
                             "clip_half_width": half_width},
              "fingerprint": inputs_fingerprint(data)}
    (out_dir / "inputs.json").write_text(jsonutil.dumps(inputs, indent=1), encoding="utf-8")
    return out_dir


# ---------------------------------------------------------------------------------------------------- import

def _probability(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and 0.0 <= x <= 1.0


def fpp_without_stp(fpp: float, p_stp: float) -> float | None:
    """The FPP with the planet-on-a-bound-companion scenario set aside, (FPP - P_STP) / (1 - P_STP), held in 0 to 1
    (ruling o): the like-for-like number beside SkySift's, whose model has no such scenario. None when P_STP is 1."""
    if p_stp >= 1.0:
        return None
    return min(1.0, max(0.0, (fpp - p_stp) / (1.0 - p_stp)))


def summarise(runs: list[dict]) -> dict:
    """The median and the 16th and 84th percentiles (numpy's linear rule) of each of ``METRICS`` over the runs that
    are not degenerate, a run without the value (no FPP without STP when P_STP is 1) left out of that one, keyed as
    ``SUMMARY_KEYS``; None for a metric no run gives. The tool computes the same (its copy is tested against this)."""
    good = [r for r in runs if not r.get("degenerate")]
    out: dict = {}
    for m in METRICS:
        vals = [float(r[m]) for r in good if r.get(m) is not None]
        q = [float(x) for x in np.percentile(vals, [50, 16, 84])] if vals else [None, None, None]
        out.update({m: q[0], f"{m}_p16": q[1], f"{m}_p84": q[2]})
    return out


def _close(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-12)


def _validate_run(i: int, r) -> None:
    if not isinstance(r, dict) or any(k not in r for k in RUN_KEYS):
        raise BadResult(f"run {i + 1} must hold {', '.join(RUN_KEYS)}")
    if not isinstance(r["seed"], int) or isinstance(r["seed"], bool):
        raise BadResult(f"run {i + 1}'s seed must be an integer")
    if not isinstance(r["degenerate"], bool):
        raise BadResult(f"run {i + 1}'s degenerate must be true or false")
    for key in ("fpp", "nfpp", "p_stp", "p_ptp"):
        if not _probability(r[key]):
            raise BadResult(f"run {i + 1}'s {key} must be a probability between 0 and 1, not {r[key]!r}")
    if r["fpp_no_stp"] is not None and not _probability(r["fpp_no_stp"]):
        raise BadResult(f"run {i + 1}'s fpp_no_stp must be a probability between 0 and 1 or null")
    if not _close(r["fpp_no_stp"], fpp_without_stp(r["fpp"], r["p_stp"])):
        raise BadResult(f"run {i + 1}'s fpp_no_stp is not (fpp - p_stp) / (1 - p_stp)")


def validate_result(result) -> None:
    """Raise BadResult naming the first thing wrong with a result: a missing key, another tool, fewer than
    ``N_RUNS`` runs or a run list of another length, a run without its probabilities (each between 0 and 1) or with
    an FPP without STP that is not (FPP - P_STP) / (1 - P_STP), a degenerate count that is not the runs', every run
    degenerate, a median or percentile that is not the non-degenerate runs' (``summarise``), an inputs hash that is
    not a SHA-256, fewer than ``MIN_DRAWS`` draws per run (``settings.N``: a smoke result), no field-star population
    (``settings.field_stars`` without a row count above 0), or a run made with ``--allow-old-export``
    (``settings.allow_old_export`` true)."""
    if not isinstance(result, dict):
        raise BadResult("the result file does not hold a JSON object")
    lacking = [k for k in RESULT_KEYS if k not in result]
    if lacking:
        raise BadResult(f"the result file lacks {', '.join(lacking)}")
    if result["tool"] != TOOL:
        raise BadResult(f"the result's tool is {result['tool']!r}, not {TOOL!r}")
    n = result["n_runs"]
    if not isinstance(n, int) or isinstance(n, bool) or n < N_RUNS:
        raise BadResult(f"n_runs must be an integer of at least {N_RUNS} (ruling s), not {n!r}")
    runs = result["runs"]
    if not isinstance(runs, list) or len(runs) != n:
        raise BadResult(f"runs must list {n} runs")
    for i, r in enumerate(runs):
        _validate_run(i, r)
    degenerate = sum(1 for r in runs if r["degenerate"])
    if isinstance(result["n_degenerate"], bool) or result["n_degenerate"] != degenerate:
        raise BadResult(f"n_degenerate {result['n_degenerate']!r} is not the {degenerate} degenerate runs")
    if degenerate == n:
        raise BadResult("every run was degenerate, so there is no median to quote")
    expected = summarise(runs)
    for key in SUMMARY_KEYS:
        if result[key] is not None and not _probability(result[key]):
            raise BadResult(f"{key} must be a probability between 0 and 1, not {result[key]!r}")
        if not _close(result[key], expected[key]):
            raise BadResult(f"{key} {result[key]!r} is not the non-degenerate runs' value {expected[key]!r}")
    if not isinstance(result["inputs_sha256"], str) or not _SHA.fullmatch(result["inputs_sha256"]):
        raise BadResult("inputs_sha256 must be the 64-character SHA-256 of the exported inputs")
    for key, kind in (("tool_version", str), ("run_utc", str), ("scenarios", list), ("stars", list),
                      ("caveats", list), ("settings", dict)):
        if not isinstance(result[key], kind):
            raise BadResult(f"{key} must be a {kind.__name__}")
    draws = result["settings"].get("N")
    if not isinstance(draws, int) or isinstance(draws, bool) or draws < MIN_DRAWS:
        raise BadResult(f"settings.N is {draws!r} draws per run, below the {MIN_DRAWS:,} the cross-check needs; a "
                        "result at fewer draws is a smoke test, so run the tool at its default draws")
    stars = result["settings"].get("field_stars")
    n_field = stars.get("n_rows") if isinstance(stars, dict) else None
    if not isinstance(n_field, int) or isinstance(n_field, bool) or n_field <= 0:
        raise BadResult(f"settings.field_stars records {n_field!r} field stars: the result does not show that the "
                        "package's field-star population was used, so its DTP, DEB, BTP and BEB scenarios may have "
                        "been skipped; run the tool again")
    if result["settings"].get("allow_old_export", False) is not False:          # absent: a tool before the flag
        raise BadResult(f"settings.allow_old_export is {result['settings']['allow_old_export']!r}: the result was "
                        "made from an export before the revision of 22 October 2026, on the package's own shares; "
                        "re-export and run the tool again")


def complete(block: dict | None) -> bool:
    """Whether a stored block carries every key of the present result format (one stored before the revision of
    22 October 2026 has a median and a list of runs' FPPs only)."""
    return isinstance(block, dict) and all(k in block for k in RESULT_KEYS)


def classify(fpp: float | None, nfpp: float | None) -> str | None:
    """Giacalone et al. 2021's class of an FPP and an NFPP, a label only (ruling t): "validated" (FPP below 0.015 and
    NFPP below 0.001), "likely planet" (FPP below 0.5 and NFPP below 0.001), "likely nearby false positive" (NFPP
    above 0.1), else "candidate"; None without both numbers. The paper found no validation region at 30-minute
    cadence (its section 4.2), which Kepler long cadence is, so no text calls a candidate validated on this label."""
    if fpp is None or nfpp is None:
        return None
    if fpp < VALIDATED_FPP and nfpp < PLANET_NFPP:
        return CLASSES[0]
    if fpp < LIKELY_PLANET_FPP and nfpp < PLANET_NFPP:
        return CLASSES[1]
    if nfpp > LIKELY_NFP_NFPP:
        return CLASSES[2]
    return CLASSES[3]


def with_current(block: dict | None, data: dict) -> dict | None:
    """The stored block with ``current`` (made for the inputs of ``data``, a dossier's JSON) and ``class``
    (``classify`` of its medians; None for a block of the earlier format) recomputed; None without a block."""
    if not block:
        return None
    cls = classify(block.get("fpp"), block.get("nfpp")) if complete(block) else None
    return {**block, "current": block.get("inputs_sha256") == inputs_fingerprint(data), "class": cls}


def import_result(store, target_id: str, path: Path) -> dict:
    """Check the tool's result file and store it on the target's dossier row with ``current`` (made for the stored
    dossier's inputs or not) and ``class``. Returns the stored block. Raises NoDossier and BadResult (nothing is
    stored)."""
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
    """(whether the live gate is blocked, the reason): no block, a block stored by an earlier version of the tool,
    or a block made for an earlier dossier (ruling t keeps only the missing-or-stale rule). The numbers never block:
    Giacalone et al.'s classes are labels, and a disagreement with SkySift's estimate is a sentence, not a refusal."""
    if not block:
        return True, "no independent cross-check"
    if not complete(block):
        return True, "the cross-check was stored by an earlier version of the tool; run it again"
    if block.get("current") is not True:
        return True, "the cross-check was made for an earlier dossier"
    return False, None


def probability_text(x: float) -> str:
    """Two significant figures in prose: 0.012, 0.0035, and below a thousandth "1 in N" with N at two significant
    figures (1 in 10,000), as the pack writes the false-alarm probability; 0 as 0."""
    if x == 0:
        return "0"
    if x < 1e-3:
        n = 1.0 / x
        return f"1 in {round(n, 1 - math.floor(math.log10(n))):,.0f}"
    return f"{x:.{max(0, 1 - math.floor(math.log10(x)))}f}"


def _p(x) -> str:
    return "not defined" if x is None else probability_text(x)


def range_text(block: dict, metric: str) -> str:
    """"<16th percentile> to <84th percentile>" of ``metric`` in prose (``probability_text``)."""
    return f"{_p(block.get(f'{metric}_p16'))} to {_p(block.get(f'{metric}_p84'))}"


CLASS_PHRASES = {
    "validated": ("the medians fall inside the paper's validation lines (FPP below 0.015, NFPP below 0.001), which "
                  "the paper could not set at the 30-minute cadence of these data (its section 4.2), so the signal "
                  "is not called validated here"),
    "likely planet": "the medians fall in the paper's class 'likely planet' (FPP below 0.5, NFPP below 0.001)",
    "likely nearby false positive": ("the medians fall in the paper's class 'likely nearby false positive' (NFPP "
                                     "above 0.1)"),
    "candidate": "the medians fall in none of the paper's classes, so the signal stays a candidate",
}


def dominant_scenario(block: dict) -> dict | None:
    """The false-positive scenario (any but TP, PTP and DTP) with the largest probability in the median run's table:
    {"id", "scenario", "prob"}, or None without one."""
    rows = [s for s in block.get("scenarios") or [] if isinstance(s, dict) and _finite(s.get("prob")) is not None
            and s.get("scenario") not in NOT_FALSE_POSITIVES]
    if not rows:
        return None
    best = max(rows, key=lambda s: float(s["prob"]))
    return {"id": str(best.get("ID")), "scenario": str(best.get("scenario")), "prob": float(best["prob"])}


def dominant_sentence(block: dict) -> str | None:
    """When the median full FPP is at or above 0.5 (ruling t), the scenario behind it in words, named on its star
    when it is a nearby one, with its probability in the median run; None otherwise."""
    fpp, top = block.get("fpp"), dominant_scenario(block)
    if fpp is None or fpp < DOMINANT_ABOVE or top is None:
        return None
    words = SCENARIO_WORDS.get(top["scenario"], top["scenario"])
    star = f" (TIC {top['id']})" if top["scenario"] in NEARBY_SCENARIOS else ""
    return (f"Its full false-positive probability is at or above {DOMINANT_ABOVE} mainly through {words}{star}, the "
            f"{top['scenario']} scenario, at a probability of {probability_text(top['prob'])} in the median run.")


def owner_flag(data: dict) -> str | None:
    """The README's line for the owner when a current cross-check's median full FPP is at or above 0.5 (ruling t):
    the median with its 16th to 84th percentile range (ruling s), the dominant scenario and what to do; None
    otherwise."""
    block = data.get("cross_check")
    if not complete(block) or block.get("current") is not True:
        return None
    why = dominant_sentence(block)
    if why is None:
        return None
    return (f"TRICERATOPS puts the median full false-positive probability at {probability_text(block['fpp'])} "
            f"(16th to 84th percentile {range_text(block, 'fpp')}). "
            f"{why} Read the cross-check (data/cross_check.json) before the upload; it does not block the draft.")


def comparison_sentence(data: dict, tex: bool = False) -> str | None:
    """For the pack's texts: the method (the TRICERATOPS version, the KBonus light curve, Kepler limb darkening,
    the package's Gaia DR3 field-star population; ruling u), the medians of the full FPP, the FPP without STP and the
    NFPP each with its 16th to 84th percentile range and the run count (ruling s), the class of the medians on
    Giacalone et al.'s scale (ruling t), the dominant scenario when the full FPP is at or above 0.5, and SkySift's own
    FPP set beside the FPP without STP, the like-for-like number (ruling o), with one sentence on why when the two
    differ by more than a factor of three. None without a current cross-check of the present format. With ``tex`` a
    LaTeX ``Raw`` citing ``giacalone2021``. Trimmed in Task 8 fix round 1 so the note keeps inside the RNAAS limit
    with the TESS statement: both FPPs, their ranges, the class and the limb darkening (round 2) stay."""
    block = data.get("cross_check")
    if not complete(block) or block.get("current") is not True:
        return None
    n, deg = block["n_runs"], block["n_degenerate"]
    left_out = f", {deg} degenerate run{'s' if deg != 1 else ''} left out" if deg else ""
    head = f"TRICERATOPS {block['tool_version']} "
    rest = (", run on the KBonus light curve with Kepler limb darkening and Gaia DR3 field stars, gives "
            f"a median false-positive probability of {_p(block['fpp'])} (16th to 84th percentile "
            f"{range_text(block, 'fpp')}), {_p(block['fpp_no_stp'])} ({range_text(block, 'fpp_no_stp')}) without a "
            "planet transiting an unresolved bound companion (STP), absent from SkySift's model, and a nearby "
            f"false-positive probability of {_p(block['nfpp'])} ({range_text(block, 'nfpp')}) over {n} "
            f"runs{left_out}; {CLASS_PHRASES[classify(block['fpp'], block['nfpp'])]}.")
    dominant = dominant_sentence(block)
    if dominant:
        rest += f" {dominant}"
    own = (data.get("fpp") or {}).get("value")
    whose = ("SkySift's own estimate" if (data.get("fpp") or {}).get("signed_off")
             else "SkySift's own estimate (provisional until its factor tables are reviewed)")
    theirs = block["fpp_no_stp"] if block["fpp_no_stp"] is not None else block["fpp"]
    if isinstance(own, (int, float)) and not isinstance(own, bool) and math.isfinite(own):
        hi, lo = max(own, theirs), min(own, theirs)
        ratio = 1.0 if hi == 0 else hi / lo if lo > 0 else math.inf          # both 0 agree
        if ratio > RATIO_SENTENCE_ABOVE:
            apart = "far" if math.isinf(ratio) else f"a factor of {ratio:.1f}"
            rest += f" {whose} is {probability_text(own)}, {apart} from the value without STP: {DIFFERENT_QUESTIONS}."
        else:
            rest += f" {whose} is {probability_text(own)}."
    if tex:
        return Raw(tex_escape(head) + r"\citep{giacalone2021}" + tex_escape(rest))
    return head + CITATION + rest
