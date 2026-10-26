"""The TRICERATOPS false-positive cross-check (referee pack spec 4, revised by its rulings (n) to (v) of 22 October
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
(ruling t). ``comparison_sentence`` prints the medians with their 16th to 84th percentiles for the pack.

The export and the result are schema 3 (spec 12.4). Round 3 (spec 12.1) replaced the tool's flux shares, which split
the contamination budget by brightness and the package's own box fractions without the Kepler pixel's geometry, with
the Kepler-pixel overlap at rung 5's fitted PSF width (``rung5_psf_sigma``, exported with the inputs), the budget a
cap. A result made before that carries no ``schema`` key, reads as schema 2 and is refused, and a stored block of
that format is not ``complete``.

Round 3 (spec 12.2) adds a third variant beside the first run set, which stands unchanged: the neighbours that rung 5's
difference images exclude removed (``pixel_excluded``, exported with the inputs; ``EXCLUDE_RULE``). Giacalone et al.
2021 allow removing a nearby star once a centroid analysis rules it out, and this is that run. It does not make the
cross-check circular, since the first set, which removes no star, is always quoted beside it and never replaced: the
texts print four numbers, the FPP and the FPP without STP of each set. A result without the variant is refused."""
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
# Giacalone et al. 2021 (abstract, section 4.1, Figure 12): the lines of its classes, labels only (ruling t)
VALIDATED_FPP, LIKELY_PLANET_FPP, PLANET_NFPP, LIKELY_NFP_NFPP = 0.015, 0.5, 1e-3, 0.1
CLASSES = ("validated", "likely planet", "likely nearby false positive", "candidate")
DOMINANT_ABOVE = 0.5            # a median full FPP at or above this is explained by its dominant scenario
METRICS = ("fpp", "fpp_no_stp", "nfpp", "p_stp", "p_ptp")
SUMMARY_KEYS = tuple(f"{m}{s}" for m in METRICS for s in ("", "_p16", "_p84"))
EXPORT_SCHEMA = 3               # inputs.json (spec 12.4): rung 5's PSF width for the Kepler-pixel overlap
RESULT_SCHEMA = 3               # result.json (spec 12.4): the shares of spec 12.1; a result without the key is 2
PSF_SIGMA_SOURCE = "rung 5's two-source fit (psf_sigma_pix), mean over the voting seasons with a width"
RESULT_KEYS = ("schema", "tool", "tool_version", "run_utc", "n_runs", "n_degenerate", *SUMMARY_KEYS, "runs",
               "scenarios", "stars", "inputs_sha256", "caveats", "settings", "variant_excluded")
EXCLUDE_SIGMA = 3.0                 # spec 12.2: nearer the target than the neighbour by more than this, every season
# rung 5's own neighbour: the Gaia neighbour nearest the stamp separation, within this
STAMP_MATCH_ARCSEC = 1.0
EXCLUDE_RULE = ("a neighbour is excluded when, in every voting season of rung 5, the difference-image centroid lies "
                "nearer the target than the neighbour by more than 3 sigma: for the neighbour rung 5 fitted, its own "
                "vote (separation_sigma, nearer the target); for every other Gaia neighbour (D / 2 - dist_target_pix) "
                "/ (the larger axis error) on the same bisector statistic, D its separation in Kepler pixels: the "
                "numerator is a lower bound on the centroid's distance from the neighbour whatever the direction; the "
                "denominator is the larger axis error")
# the package's limiting separation for an unresolved bound companion without a contrast curve (TRICERATOPS 1.1.0,
# marginal_likelihoods: lnprior_bound_TP(..., 2.2)); the tool passes none, and its COMPANION_ARCSEC is pinned to this
COMPANION_ARCSEC = 2.2
# the flux shares' source in a result the tool made by spec 12.1 (its ``OVERLAP`` starts with this; a test pins both)
OVERLAP_SOURCE = "Kepler-pixel overlap"
# the texts' wording when every neighbour the difference images exclude is absent from the package's star list
NOT_IN_STAR_LIST = ("none of the neighbours the difference images exclude is in TRICERATOPS's star list, so the "
                    "pixel-excluded run equals the first")
# the pixel-excluded run set of the result (spec 12.2), stored beside the first
VARIANT_KEYS = ("n_runs", "n_degenerate", *SUMMARY_KEYS, "runs", "scenarios", "removed", "same_as_all", "seeds",
                "median_run_seed")
RUN_KEYS = ("seed", "fpp", "fpp_no_stp", "nfpp", "p_stp", "p_ptp", "best_scenario", "degenerate")
CITATION = "(Giacalone et al. 2021)"
# spec 11.2: the paper's classes do not apply at Kepler's 30-minute cadence, so the texts name none
NO_CLASS_CLAUSE = "in none of its validation classes, which do not apply at 30-minute cadence"
# the Research Note's form (Task 13, the word limit; addendum cut 8); every other text keeps NO_CLASS_CLAUSE
NO_CLASS_SHORT = "no validation class applies at 30-minute cadence"
# fix wave round 4, polish (a): TRICERATOPS takes the target star from the TIC, so the plain texts say the FPP rests
# on it (the README key gives the longer form, ``pack.KEY_TIC_STAR``; the note is unchanged)
TIC_STAR = "The FPP rests on the TIC's stellar parameters."
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
    G, its KBonus CROWDSAP (the leakage budget), rung 5's fitted PSF width (``rung5_psf_sigma``, the overlap's width;
    round 3 review M3) and the Gaia neighbours (source id, G and separation, sorted by id), each number at the
    dossier's 8 significant figures."""
    eph = data.get("ephemeris") or {}
    inputs = (data.get("fpp") or {}).get("inputs") or {}
    target = data.get("target") or {}
    gmag = target.get("gmag")
    neighbours = sorted([str(n.get("source_id")), _round(n.get("gmag")), _round(n.get("sep_arcsec"))]
                        for n in inputs.get("neighbours") or [])
    key = {"period_days": _round(eph.get("period_days")), "t0_bkjd": _round(eph.get("t0_bkjd")),
           "depth_ppm": _round(eph.get("depth_ppm")), "duration_h": _round(eph.get("duration_h")),
           "gmag": _round(gmag if gmag is not None else inputs.get("gmag")), "neighbours": neighbours,
           "crowdsap": _round(_finite((target.get("meta") or {}).get("crowdsap"))),
           "psf_sigma_pix": _round(rung5_psf_sigma(data)["sigma_pix"])}
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


def _pixel_exclusion(data: dict, ds) -> dict | None:
    """``inputs.json``'s ``pixel_exclusion`` (spec 11.2): the radius about the Gaia-referenced difference centroid
    inside which a background source is not excluded (``dossier.exclusion_radius``), its rule and the centroid's
    offset from the star; None without a centroid block or its error."""
    co = data.get("centroid_offset")
    radius = _finite((co or {}).get("exclusion_radius_arcsec")) if isinstance(co, dict) else None
    radius = ds.exclusion_radius(co) if radius is None else radius
    if radius is None:
        return None
    return {"radius_arcsec": radius, "rule": co.get("exclusion_rule") or ds.EXCLUSION_RULE,
            "centre_offset_arcsec": _finite(co.get("catalogue_referenced_arcsec"))}


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


def rung5_psf_sigma(data: dict) -> dict:
    """Rung 5's fitted PSF width in Kepler pixels, the width of the tool's Kepler-pixel overlap (spec 12.1): the mean
    of ``psf_sigma_pix`` (the two-source fit's Gaussian width) over the seasons that vote (``dossier.season_votes``)
    with a finite width above 0, in stored order; a season without a width is left out, never read as zero. Returns
    {"sigma_pix": the mean | None, "seasons": their season numbers, "per_season": {str(season): width}, "source":
    ``PSF_SIGMA_SOURCE``, "reason": None | "rung 5 has not run" | "rung 5 has no voting season with a fitted PSF
    width"}."""
    from skysift import dossier as ds
    out = {"sigma_pix": None, "seasons": [], "per_season": {}, "source": PSF_SIGMA_SOURCE, "reason": None}
    metrics = ds._metrics(data.get("tests") or [], "rung5_pixels")
    if not metrics:
        return {**out, "reason": "rung 5 has not run"}
    widths = []
    for s in ds._pixel_seasons(metrics):
        width = ds._num(s, "psf_sigma_pix")
        if ds.season_votes(s) and width is not None and width > 0:
            widths.append(width)
            out["seasons"].append(s.get("season"))
            out["per_season"][str(s.get("season"))] = width
    if not widths:
        return {**out, "reason": "rung 5 has no voting season with a fitted PSF width"}
    return {**out, "sigma_pix": float(np.mean(widths))}


def stamp_neighbour(neighbours: list[dict], seasons: list[dict]) -> str | None:
    """The source id of the Gaia neighbour rung 5 fitted: the stamp separation is the median over ``seasons`` (the
    voting ones) of |pos_neighbour - pos_target| in arcsec (``priors.PIXEL_ARCSEC``), and the neighbour whose
    ``sep_arcsec`` is nearest it within ``STAMP_MATCH_ARCSEC`` is rung 5's, a tie going to the brighter G. None
    without a season holding both positions or without a neighbour that close."""
    from skysift import dossier as ds
    from skysift.priors import PIXEL_ARCSEC
    seps = []
    for s in seasons:
        pt, pn = ds._pair(s, "pos_target"), ds._pair(s, "pos_neighbour")
        if pt is not None and pn is not None:
            seps.append(float(np.hypot(*(pn - pt))) * PIXEL_ARCSEC)
    if not seps:
        return None
    stamp = float(np.median(seps))
    near = []
    for n in neighbours:
        sep, g = _finite(n.get("sep_arcsec")), _finite(n.get("gmag"))
        if sep is not None and abs(sep - stamp) <= STAMP_MATCH_ARCSEC:
            near.append((abs(sep - stamp), math.inf if g is None else g, str(n.get("source_id"))))
    return min(near)[2] if near else None


def larger_axis_sigma(season: dict) -> float | None:
    """The larger of a rung 5 season's two centroid axis errors (Task 15 fix round 2, M1): along the stamp
    neighbour's axis (``centroid_sigma_axis_pix``) and across it, which the per-axis mean ``centroid_sigma_pix`` (the
    root of half the covariance's trace) gives as sqrt(2 centroid_sigma_pix^2 - centroid_sigma_axis_pix^2), the trace
    being the same on any pair of perpendicular axes. None without both."""
    from skysift import dossier as ds
    axis, mean = ds._num(season, "centroid_sigma_axis_pix"), ds._num(season, "centroid_sigma_pix")
    if axis is None or mean is None:
        return None
    return max(axis, math.sqrt(max(2.0 * mean * mean - axis * axis, 0.0)))


def season_margin(season: dict, sep_arcsec: float | None, stamp: bool) -> tuple[float | None, str, str | None]:
    """(the margin in sigma by which the season's difference centroid lies nearer the target than the neighbour, its
    basis, why it is None). For rung 5's own neighbour (``stamp``) the season's vote: ``separation_sigma``, positive
    when ``nearer`` is "target" and negative when "neighbour" ("rung 5 vote"). For any other neighbour the
    "geometric bound" (D / 2 - ``dist_target_pix``) / (the larger axis error, ``larger_axis_sigma``), D the separation
    in Kepler pixels: a point within ``dist_target_pix`` of the target is at least D / 2 - dist_target_pix nearer the
    target than the bisector of any star D away, whatever its direction, and the larger of the two axis errors bounds
    the error along that star's axis (Task 15 fix round 2, M1)."""
    from skysift import dossier as ds
    from skysift.priors import PIXEL_ARCSEC
    n = season.get("season")
    if stamp:
        value, nearer = ds._num(season, "separation_sigma"), season.get("nearer")
        if value is None or nearer not in ("target", "neighbour"):
            return None, "rung 5 vote", f"season {n} has no rung 5 vote against this neighbour"
        return (abs(value) if nearer == "target" else -abs(value)), "rung 5 vote", None
    dist, err = ds._num(season, "dist_target_pix"), larger_axis_sigma(season)
    if dist is None or err is None or err <= 0 or sep_arcsec is None:
        return None, "geometric bound", f"season {n} has no centroid distance or error"
    return (sep_arcsec / PIXEL_ARCSEC / 2 - dist) / err, "geometric bound", None


def pixel_excluded(data: dict) -> dict:
    """``inputs.json``'s ``pixel_excluded`` (spec 12.2): which Gaia neighbours (``fpp.inputs.neighbours``) rung 5's
    difference images exclude by ``EXCLUDE_RULE``. A neighbour is excluded when rung 5 has a voting season and every
    voting season's margin (``season_margin``; rung 5's vote for the neighbour it fitted, ``stamp_neighbour``) is a
    number above ``EXCLUDE_SIGMA``; otherwise its reason names the first season that falls short, or "no separation
    stored". Returns {"rule", "sigma", "pixel_arcsec", "seasons" (the voting season numbers), "stamp_source_id",
    "neighbours": [{"source_id", "sep_arcsec", "gmag", "excluded", "margins": [{"season", "margin_sigma", "basis"}],
    "min_margin_sigma", "reason"}], "excluded_ids" (in the stored order), "reason": None | "rung 5 has not run" |
    "rung 5 has no voting season"}."""
    from skysift import dossier as ds
    from skysift.priors import PIXEL_ARCSEC
    neighbours = ((data.get("fpp") or {}).get("inputs") or {}).get("neighbours") or []
    metrics = ds._metrics(data.get("tests") or [], "rung5_pixels")
    seasons = [s for s in ds._pixel_seasons(metrics) if ds.season_votes(s)]
    reason = ("rung 5 has not run" if not metrics else "rung 5 has no voting season" if not seasons else None)
    stamp = stamp_neighbour(neighbours, seasons)
    rows = []
    for nb in neighbours:
        sid, sep = str(nb.get("source_id")), _finite(nb.get("sep_arcsec"))
        is_stamp = sid == stamp
        margins, why = [], reason
        if why is None and sep is None and not is_stamp:
            why = "no separation stored"
        if why is None:
            for s in seasons:
                m, basis, lack = season_margin(s, sep, is_stamp)
                margins.append({"season": s.get("season"), "margin_sigma": m, "basis": basis})
                if why is None and lack is not None:
                    why = lack
                elif why is None and m <= EXCLUDE_SIGMA:
                    why = f"season {s.get('season')} margin {m:.2f} sigma, not above {EXCLUDE_SIGMA:g}"
        numbers = [m["margin_sigma"] for m in margins if m["margin_sigma"] is not None]
        rows.append({"source_id": sid, "sep_arcsec": sep, "gmag": _finite(nb.get("gmag")), "excluded": why is None,
                     "margins": margins, "min_margin_sigma": min(numbers) if numbers else None, "reason": why})
    return {"rule": EXCLUDE_RULE, "sigma": EXCLUDE_SIGMA, "pixel_arcsec": PIXEL_ARCSEC,
            "seasons": [s.get("season") for s in seasons], "stamp_source_id": stamp, "neighbours": rows,
            "excluded_ids": [r["source_id"] for r in rows if r["excluded"]], "reason": reason}


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
    from skysift.sources.kbonus_bkg import CATALOGUE, CROWDSAP_COLUMN
    inputs = {"schema": EXPORT_SCHEMA,
              "target": {**{k: t.get(k) for k in ("target_id", "gaia_id", "kic", "ra", "dec", "gmag")},
                         "crowdsap": _finite(meta.get("crowdsap")), "crowdsap_column": CROWDSAP_COLUMN,
                         "crowdsap_catalogue": CATALOGUE, "psffrac": _finite(meta.get("psffrac")),
                         "kepmag": _finite(kepmag)},
              "signal_id": sid, "dossier_generated": data.get("generated"), "ephemeris": data.get("ephemeris"),
              "star": data.get("star"), "neighbours": neighbours, "neighbour_params_note": params_note,
              "leak": _leak(data), "rung5_psf_sigma": rung5_psf_sigma(data),
              "pixel_exclusion": _pixel_exclusion(data, ds), "pixel_excluded": pixel_excluded(data),
              "neighbour_kic": kic,
              "quarters": quarters, "tpf_files": files,
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


def _validate_variant(result: dict) -> None:
    """Raise BadResult naming the first thing wrong with the result's pixel-excluded run set (spec 12.2): not an
    object of ``VARIANT_KEYS``, another run count than the first set's, a run that fails the first set's checks, seeds
    other than the first set's, a degenerate count or a median or percentile that is not its runs', every run
    degenerate, a ``removed`` list that is not of {tic_id, source_id} objects, or a ``same_as_all`` that is not a bool
    or is true over runs that differ from the first set's, or a ``not_in_star_list`` (optional: the excluded
    neighbours the package's star list lacks, Task 15 fix round 2, M3) that is not a list of source ids."""
    v = result["variant_excluded"]
    if not isinstance(v, dict) or any(k not in v for k in VARIANT_KEYS):
        raise BadResult(f"variant_excluded must be an object holding {', '.join(VARIANT_KEYS)}")
    n = result["n_runs"]
    if isinstance(v["n_runs"], bool) or v["n_runs"] != n:
        raise BadResult(f"variant_excluded.n_runs {v['n_runs']!r} is not the first set's {n}")
    runs = v["runs"]
    if not isinstance(runs, list) or len(runs) != n:
        raise BadResult(f"variant_excluded.runs must list {n} runs")
    for i, r in enumerate(runs):
        try:
            _validate_run(i, r)
        except BadResult as e:
            raise BadResult(f"variant_excluded {e}") from None
    first = [r["seed"] for r in result["runs"]]
    if v["seeds"] != first or [r["seed"] for r in runs] != first:
        raise BadResult("variant_excluded's seeds are not the first set's")
    degenerate = sum(1 for r in runs if r["degenerate"])
    if isinstance(v["n_degenerate"], bool) or v["n_degenerate"] != degenerate:
        raise BadResult(f"variant_excluded.n_degenerate {v['n_degenerate']!r} is not the {degenerate} degenerate "
                        "runs")
    if degenerate == n:
        raise BadResult("every run of variant_excluded was degenerate, so there is no median to quote")
    expected = summarise(runs)
    for key in SUMMARY_KEYS:
        if not _close(v[key], expected[key]):
            raise BadResult(f"variant_excluded.{key} {v[key]!r} is not the non-degenerate runs' value "
                            f"{expected[key]!r}")
    removed = v["removed"]
    if not isinstance(removed, list) or not all(isinstance(r, dict) and isinstance(r.get("tic_id"), str)
                                                and isinstance(r.get("source_id"), str) for r in removed):
        raise BadResult("variant_excluded.removed must list {tic_id, source_id} objects")
    same = v["same_as_all"]
    differ = [{k: r[k] for k in RUN_KEYS} for r in runs] != [{k: r[k] for k in RUN_KEYS} for r in result["runs"]]
    if not isinstance(same, bool) or (same and differ):
        raise BadResult("variant_excluded says same_as_all but its runs differ from the first set's"
                        if isinstance(same, bool) else "variant_excluded.same_as_all must be true or false")
    absent = v.get("not_in_star_list", [])
    if not isinstance(absent, list) or not all(isinstance(x, str) for x in absent):
        raise BadResult("variant_excluded.not_in_star_list must list Gaia DR3 source ids")


def validate_result(result) -> None:
    """Raise BadResult naming the first thing wrong with a result: a schema other than ``RESULT_SCHEMA`` (a result
    without the key is schema 2, made before round 3 without the Kepler-pixel shares), a missing key, another tool,
    fewer than ``N_RUNS`` runs or a run list of another length, a run without its probabilities (each between 0 and
    1) or with an FPP without STP that is not (FPP - P_STP) / (1 - P_STP), a degenerate count that is not the runs',
    every run degenerate, a median or percentile that is not the non-degenerate runs' (``summarise``), an inputs hash
    that is not a SHA-256, fewer than ``MIN_DRAWS`` draws per run (``settings.N``: a smoke result), no field-star
    population (``settings.field_stars`` without a row count above 0), a run made with ``--allow-old-export``
    (``settings.allow_old_export`` true), or a pixel-excluded run set that fails ``_validate_variant``."""
    if not isinstance(result, dict):
        raise BadResult("the result file does not hold a JSON object")
    tail = ("it was made before round 3 (spec 12) without the Kepler-pixel shares; run the tool again")
    if "schema" not in result:
        raise BadResult(f"the result is schema 2 (no schema key), not {RESULT_SCHEMA}: {tail}")
    schema = result["schema"]
    if not (isinstance(schema, int) and not isinstance(schema, bool) and schema == RESULT_SCHEMA):
        raise BadResult(f"the result is schema {schema!r}, not {RESULT_SCHEMA}: {tail}")
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
    _validate_variant(result)


def complete(block: dict | None) -> bool:
    """Whether a stored block carries every key of the present result format (one stored before the revision of
    22 October 2026 has a median and a list of runs' FPPs only, and one of round 2 has no ``schema``)."""
    return isinstance(block, dict) and all(k in block for k in RESULT_KEYS)


def stale_format_text(block: dict | None) -> str | None:
    """The page's reason for a stored block of an earlier result format (the round 3 final review, M5), naming the
    keys of ``RESULT_KEYS`` it lacks (three at most, then how many more): "This cross-check was stored by an earlier
    version of the tool: it has no schema and no variant_excluded; run it again." None for a complete block or
    without one."""
    if not isinstance(block, dict) or complete(block):
        return None
    missing = [k for k in RESULT_KEYS if k not in block]
    named = " and no ".join(missing[:3]) + (f", and {len(missing) - 3} more" if len(missing) > 3 else "")
    return f"This cross-check was stored by an earlier version of the tool: it has no {named}; run it again."


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
    """The stored block with ``current`` (made for the inputs of ``data``, a dossier's JSON), ``class`` (``classify``
    of its medians) and ``class_excluded`` (of the pixel-excluded set's medians) recomputed, each class None for a
    block of an earlier format; None without a block."""
    if not block:
        return None
    full = complete(block)
    v = block.get("variant_excluded") if full else None
    cls = classify(block.get("fpp"), block.get("nfpp")) if full else None
    cls_ex = classify(v.get("fpp"), v.get("nfpp")) if isinstance(v, dict) else None
    return {**block, "current": block.get("inputs_sha256") == inputs_fingerprint(data), "class": cls,
            "class_excluded": cls_ex}


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
        return True, "no TRICERATOPS cross-check"
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


# the page's class text (round-2 final review, minor 7): naming a class and withdrawing it in one breath read as a
# verdict, so the page keeps only the clause that no class applies
CLASS_LABEL = "No validation class applies at 30-minute cadence (Giacalone et al. 2021)."


def class_label_text(cls: str | None) -> str | None:
    """The dossier page's sentence on Giacalone et al.'s classes: ``CLASS_LABEL``, that none applies at 30-minute
    cadence, whatever class the medians fall in; None without a class (a block of the earlier format)."""
    return CLASS_LABEL if cls else None


def dominant_scenario(block: dict) -> dict | None:
    """The false-positive scenario (any but TP, PTP and DTP) with the largest probability in the median run's table
    of ``block``, the first run set or the pixel-excluded one (``variant_excluded``): {"id", "scenario", "prob"}, or
    None without one."""
    rows = [s for s in block.get("scenarios") or [] if isinstance(s, dict) and _finite(s.get("prob")) is not None
            and s.get("scenario") not in NOT_FALSE_POSITIVES]
    if not rows:
        return None
    best = max(rows, key=lambda s: float(s["prob"]))
    return {"id": str(best.get("ID")), "scenario": str(best.get("scenario")), "prob": float(best["prob"])}


def dominant_sentence(block: dict, short: bool = False) -> str | None:
    """When the median full FPP is at or above 0.5 (ruling t), the scenario behind it in words, named on its star
    when it is a nearby one, with its probability in the median run; None otherwise. ``short`` is the note's form:
    "The full false-positive probability comes mainly from the NEB scenario, <words> (TIC N), at 0.55 in the median
    run."."""
    fpp, top = block.get("fpp"), dominant_scenario(block)
    if fpp is None or fpp < DOMINANT_ABOVE or top is None:
        return None
    words = SCENARIO_WORDS.get(top["scenario"], top["scenario"])
    star = f" (TIC {top['id']})" if top["scenario"] in NEARBY_SCENARIOS else ""
    if short:
        return (f"The full false-positive probability comes mainly from the {top['scenario']} scenario, "
                f"{words}{star}, at {probability_text(top['prob'])} in the median run.")
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


def _current(data: dict) -> dict | None:
    block = data.get("cross_check")
    return block if complete(block) and block.get("current") is True else None


def is_current(data: dict) -> bool:
    """Whether the dossier carries a current cross-check of the present format (the texts print its numbers)."""
    return _current(data) is not None


def _variant(block: dict) -> dict | None:
    v = block.get("variant_excluded")
    return v if isinstance(v, dict) else None


def removed_phrase(variant: dict) -> str:
    """"the one neighbour the difference images exclude", or "the <n> neighbours the difference images exclude"."""
    n = len(variant.get("removed") or [])
    return ("the one neighbour the difference images exclude" if n == 1
            else f"the {n} neighbours the difference images exclude")


def _removed_list(variant: dict) -> str:
    """Each removed neighbour in the export's order: "Gaia DR3 <id> at <sep> arcsec, smallest margin <m> sigma"."""
    parts = []
    for r in variant.get("removed") or []:
        sep, m = _finite(r.get("sep_arcsec")), _finite(r.get("min_margin_sigma"))
        text = f"Gaia DR3 {r.get('source_id')}"
        text += f" at {sep:.2f} arcsec" if sep is not None else ""
        text += f", smallest margin {m:.3g} sigma" if m is not None else ""
        parts.append(text)
    return "; ".join(parts)


def excluded_text(block: dict, ranges: bool) -> str:
    """The pixel-excluded set's numbers in prose, for ``comparison_sentence``. With ``ranges`` (the README, the ExoFOP
    note, the archive email and the page): "With <removed_phrase> removed (<each neighbour>), <n> seeded runs give
    <FPP> (16th to 84th percentile <range>), <FPP without STP> (<range>) without STP and a nearby false-positive
    probability of <NFPP> (<range>)."; without, the Research Note's clause inside the parenthesis of its cross-check
    sentence (Task 16's cut 2 and Task 15 fix round 1, cut B8): "; <FPP>, and <FPP without STP> without STP, with
    <removed_phrase> removed". A set that removed nothing (``same_as_all``) says why (``NOT_IN_STAR_LIST`` when the
    difference images exclude neighbours the star list lacks, Task 15 fix round 2, M3)."""
    v = _variant(block)
    if v is None:
        return ""
    if v.get("same_as_all") and v.get("not_in_star_list"):
        return f"{NOT_IN_STAR_LIST[:1].upper()}{NOT_IN_STAR_LIST[1:]}." if ranges else f"; {NOT_IN_STAR_LIST}"
    if v.get("same_as_all"):
        return ("The difference images exclude no neighbour, so no run set with neighbours removed differs from this "
                "one." if ranges else "; the same with no neighbour removed, since the difference images exclude none")
    phrase = removed_phrase(v)
    if not ranges:
        return f"; {_p(v['fpp'])}, and {_p(v['fpp_no_stp'])} without STP, with {phrase} removed"
    listed = _removed_list(v)
    return (f"With {phrase} removed{f' ({listed})' if listed else ''}, {v['n_runs']} seeded runs give {_p(v['fpp'])} "
            f"(16th to 84th percentile {range_text(v, 'fpp')}), {_p(v['fpp_no_stp'])} "
            f"({range_text(v, 'fpp_no_stp')}) without STP and a nearby false-positive probability of "
            f"{_p(v['nfpp'])} ({range_text(v, 'nfpp')}).")


def own_evidence_sentence(data: dict, short: bool = False) -> str | None:
    """Which of the two FPPs rests on SkySift's own evidence (Task 15 addendum 2), in one form for every text (the
    round 3 final review, I2, which replaced the "split" of the round-2 quota and holds whether the cap bound or not):
    "The pixel-excluded value (<FPP>, mainly <scenario>) rests on our own evidence, the difference-image centroids;
    the all-neighbours value (<FPP>) rests on neighbour shares set by the PRF overlap rule within the catalogue's
    crowding budget, a bound rather than a measurement." ``short`` is kept for the callers and changes nothing. None
    without a current, complete cross-check, or when the difference images exclude no neighbour (``same_as_all``: the
    two sets are one)."""
    block = _current(data)
    v = _variant(block) if block else None
    if v is None or v.get("same_as_all"):
        return None
    top = dominant_scenario(v)             # Task 15 fix round 2, M2: the excluded set's dominant scenario
    mainly = f", mainly {top['scenario']}" if top else ""
    return (f"The pixel-excluded value ({_p(v['fpp'])}{mainly}) rests on our own evidence, the difference-image "
            f"centroids; the all-neighbours value ({_p(block['fpp'])}) rests on neighbour shares set by the PRF overlap "
            "rule within the catalogue's crowding budget, a bound rather than a measurement.")


def leakage_basis(data: dict) -> str | None:
    """The leakage budget's basis in three parts, in this order (Task 15 addendum 2), for the README and the page:
    the budget (1 - C) / C from the catalogue's crowding metric (its column and catalogue named), the split by the
    PRF overlap rule at rung 5's width, and whether the cap bound. None without a current, complete cross-check whose
    shares are the Kepler-pixel overlap (``settings.flux_shares``)."""
    from skysift.sources.kbonus_bkg import CATALOGUE, CROWDSAP_COLUMN
    block = _current(data)
    shares = ((block or {}).get("settings") or {}).get("flux_shares") or {}
    c, budget = _finite(shares.get("crowdsap")), _finite(shares.get("budget"))
    total, sigma = _finite(shares.get("weight_sum")), _finite(shares.get("sigma_pix"))
    if block is None or not str(shares.get("source") or "").startswith(OVERLAP_SOURCE) or None in (
            c, budget, total, sigma):
        return None
    crowd = (data.get("star") or {}).get("crowdsap") or {}
    return budget_basis(c, budget, total, bool(shares.get("capped")), sigma, crowd.get("column") or CROWDSAP_COLUMN,
                        crowd.get("catalogue") or CATALOGUE)


def cap_bound_clause(total: float, budget: float) -> str:
    """The cap's state when it binds, in every text's one wording (the round 3 final review, I1): "the cap bound (the
    weights sum to 0.202 against the budget 0.168), so the shares are scaled to the budget". The tool, which does not
    import the engine, keeps a copy that a test pins to this one."""
    return (f"the cap bound (the weights sum to {total:.3f} against the budget {budget:.3f}), so the shares are scaled "
            "to the budget")


def budget_basis(c: float, budget: float, total: float, capped: bool, sigma: float, column: str,
                 catalogue: str) -> str:
    """The leakage budget's three-part basis (Task 15 addendum 2), worded once (fix round 2, M6) for the README, the
    page (``leakage_basis``) and the comparison note (``tools/leakage_note.py``): (1 - C) / C from the catalogue's
    crowding metric C, its ``column`` of the ``catalogue``; the split by the PRF overlap rule at rung 5's width
    ``sigma`` (Kepler pixels); and whether the cap bound, the overlap-weighted fluxes summing to ``total``. The
    tool's caveat says the same three parts in its own copy, since the tool does not import the engine."""
    cap = (cap_bound_clause(total, budget) if capped else
           f"the overlap-weighted fluxes sum to {total:.4f}, below the budget of {budget:.4f}, so the cap did not "
           "bind")
    return ("The leakage budget behind the nearby false-positive probability has three parts. First, it comes from "
            f"the catalogue, not from SkySift: (1 - C) / C = {budget:.4f}, with C = {c:.4f} the KBonus crowding "
            f"metric for this extraction (the {column} column of the {catalogue}), the catalogue's own estimate that "
            f"{100 * (1 - c):.0f} percent of the fitted flux belongs to other sources, PRF wings included. Second, "
            "its split among the neighbours follows the PRF overlap rule: each neighbour's share is its Kepler-pixel "
            f"overlap at rung 5's PSF width ({sigma:.3f} Kepler pixels) times its Kepler-band flux relative to the "
            f"target's, as an absolute fraction of the target's flux. Third, {cap}.")


def dominant_excluded_sentence(data: dict, tex: bool = False, short: bool = False) -> str | None:
    """The scenario that carries the pixel-excluded set's false-positive weight (``dominant_scenario`` of its median
    run's table), and that the signal stays a candidate. For STP, the region TRICERATOPS computed (the round 3 final
    review, M6): "(TRICERATOPS places such companions within 2.2 arcsec; the pixels exclude background sources beyond
    0.83 arcsec, which does not bear on a bound companion)", with ``COMPANION_ARCSEC`` and the exclusion radius
    (``centroid_offset.exclusion_radius_arcsec``; without it the first clause alone). ``short`` is the Research
    Note's form. With ``tex`` a LaTeX ``Raw``. None without a current, complete cross-check or without such a
    scenario."""
    block = _current(data)
    v = _variant(block) if block else None
    top = dominant_scenario(v) if v else None
    if top is None:
        return None
    radius = _finite((data.get("centroid_offset") or {}).get("exclusion_radius_arcsec")) \
        if isinstance(data.get("centroid_offset"), dict) else None
    pixels = (f"; the pixels exclude background sources beyond {radius:.2f} arcsec, which does not bear on a bound "
              "companion" if radius is not None else "")
    where = f"(TRICERATOPS places such companions within {COMPANION_ARCSEC:g} arcsec{pixels})"
    if short and v.get("same_as_all") and dominant_sentence(block) is not None:
        return None        # Task 15 fix round 2, M4: the first set's sentence has named the same scenario
    lead = ("With no neighbour removed the two values coincide; the weight is mainly" if short and v.get("same_as_all")
            else "The remaining weight is mainly" if short else
            "The false-positive weight sits mainly on" if v.get("same_as_all") else
            "With those neighbours removed the false-positive weight sits mainly on")
    if top["scenario"] == "STP":
        what = (f"a planet on an unresolved bound companion {where}" if short else
                f"a planet transiting an unresolved bound companion {where}")
    else:
        star = f" (TIC {top['id']})" if top["scenario"] in NEARBY_SCENARIOS else ""
        what = f"{SCENARIO_WORDS.get(top['scenario'], top['scenario'])}{star}"
    text = f"{lead} {top['scenario']}, {what}, so the signal stays a candidate."
    return Raw(tex_escape(text)) if tex else text


def headline(data: dict, tex: bool = False, named: bool = True, short: bool = False) -> str | None:
    """The false-positive probability every public text leads with (spec 11.2): the TRICERATOPS median full FPP,
    its value without STP and ``NO_CLASS_CLAUSE``, citing Giacalone et al. 2021; None without a complete, current
    cross-check. ``named`` False drops "a false-positive probability of", for a text whose label already names it
    ("False-positive probability: TRICERATOPS 1.1.0 gives 0.012 ..."). With ``tex`` a LaTeX ``Raw`` citing
    ``giacalone2021``. Spec 12.2: after the first set's two numbers come the pixel-excluded set's two, "and <FPP>
    (<FPP without STP> without it) with <removed_phrase> removed". ``short`` is the note's abstract, which carries
    the two FPPs alone (Task 15 fix round 1, cut A1; the no-STP values are in the body): "<FPP>, or <FPP> with
    <removed_phrase> removed (no validation class applies at 30-minute cadence; Giacalone et al. 2021)"
    (``NO_CLASS_SHORT``)."""
    block = _current(data)
    if block is None:
        return None
    what = "a false-positive probability of " if named else ""
    v = _variant(block)
    if short and not tex:
        if v is None:
            middle = ""
        elif v.get("same_as_all"):
            middle = (f", and {NOT_IN_STAR_LIST}" if v.get("not_in_star_list") else
                      ", the same with no neighbour removed, since the difference images exclude none")
        else:
            middle = f", or {_p(v['fpp'])} with {removed_phrase(v)} removed"
        return (f"TRICERATOPS {block['tool_version']} gives {what}{_p(block['fpp'])}{middle} ({NO_CLASS_SHORT}; "
                "Giacalone et al. 2021)")
    if v is None:
        middle = ""
    elif v.get("same_as_all"):
        middle = (f", and {NOT_IN_STAR_LIST}" if v.get("not_in_star_list") else
                  ", and the same with no neighbour removed, since the difference images exclude none")
    else:
        middle = f", and {_p(v['fpp'])} ({_p(v['fpp_no_stp'])} without it) with {removed_phrase(v)} removed"
    words = (f"TRICERATOPS {block['tool_version']} gives {what}{_p(block['fpp'])} "
             f"({_p(block['fpp_no_stp'])} without a planet on an unresolved bound companion){middle}, "
             f"{NO_CLASS_CLAUSE}")
    if tex:
        return Raw(tex_escape(words) + r" \citep{giacalone2021}")
    return f"{words} {CITATION}"


def comparison_sentence(data: dict, tex: bool = False, after_headline: bool = False,
                        short: bool = False) -> str | None:
    """For the pack's texts: the method (the TRICERATOPS version, the KBonus light curve, Kepler limb darkening,
    the package's Gaia DR3 field-star population; ruling u), the medians of the full FPP, the FPP without STP and the
    NFPP each with its 16th to 84th percentile range and the run count (ruling s), ``NO_CLASS_CLAUSE`` where the
    class on Giacalone et al.'s scale stood (spec 11.2) and the
    dominant scenario when the full FPP is at or above 0.5. SkySift's own estimate is not set beside it here: the
    texts print it after, as the in-house sentence (``zenodo.in_house_text``). ``after_headline`` gives the form for
    a text that has just printed ``headline`` (the README, the ExoFOP note and the archive email): it refers back to
    those medians and adds their ranges, the NFPP, the run count and the method, so the tool's median is printed
    once. None without a current cross-check of the present format. With ``tex`` a LaTeX ``Raw`` citing
    ``giacalone2021``. ``short``, the Research Note's form since Task 13 (the word limit), gives the method, the
    three medians and the run count, ending "(no validation class applies at 30-minute cadence)" (``NO_CLASS_SHORT``;
    the sentence already cites the paper), and the dominant scenario in its short form; the README's
    ``after_headline`` sentence keeps the ranges. Spec 12.2: every form then gives the pixel-excluded set
    (``excluded_text``: with its ranges and the neighbours removed, or in the note its two medians alone); the
    note's and the ``after_headline`` forms then say which FPP rests on SkySift's own evidence
    (``own_evidence_sentence``, Task 15 addendum 2; the page prints it beside its own variant line), and the
    ``after_headline`` form ends on ``dominant_excluded_sentence``."""
    block = _current(data)
    if block is None:
        return None
    n, deg = block["n_runs"], block["n_degenerate"]
    left_out = f", {deg} degenerate run{'s' if deg != 1 else ''} left out" if deg else ""
    dominant = dominant_sentence(block)
    if after_headline:
        text = (f"The TRICERATOPS values are medians over {n} runs{left_out}, on the KBonus light curve with Kepler "
                f"limb darkening and Gaia DR3 field stars (16th to 84th percentile {range_text(block, 'fpp')}, and "
                f"{range_text(block, 'fpp_no_stp')} without STP, a scenario absent from SkySift's model); the nearby "
                f"false-positive probability is {_p(block['nfpp'])} ({range_text(block, 'nfpp')}).")
        text += f" {dominant}" if dominant else ""
        # fix wave round 4, polish (a); after the dominant sentence, so its "Its" keeps the FPP as antecedent (N2)
        text += f" {TIC_STAR}"
        text += f" {excluded_text(block, True)}" if _variant(block) else ""
        own = own_evidence_sentence(data)
        text += f" {own}" if own else ""
        why = dominant_excluded_sentence(data)
        text += f" {why}" if why else ""
        return Raw(tex_escape(text)) if tex else text
    head = f"TRICERATOPS {block['tool_version']} "
    if short:              # the Research Note (Task 13, the word limit): the README carries the ranges
        variant = excluded_text(block, False) if _variant(block) else ""
        rest = (", run on the KBonus light curve with Kepler limb darkening and Gaia DR3 field stars, gives a median "
                f"false-positive probability of {_p(block['fpp'])} ({_p(block['fpp_no_stp'])} without a planet "
                f"transiting an unresolved bound companion, STP, absent from SkySift's model{variant}) and a nearby "
                f"false-positive probability of {_p(block['nfpp'])} over {n} runs{left_out} ({NO_CLASS_SHORT}).")
        if dominant:
            rest += f" {dominant_sentence(block, short=True)}"
        own = own_evidence_sentence(data, short=True)
        rest += f" {own}" if own else ""
        if tex:
            return Raw(tex_escape(head) + r"\citep{giacalone2021}" + tex_escape(rest))
        return head + CITATION + rest
    rest = (", run on the KBonus light curve with Kepler limb darkening and Gaia DR3 field stars, gives "
            f"a median false-positive probability of {_p(block['fpp'])} (16th to 84th percentile "
            f"{range_text(block, 'fpp')}), {_p(block['fpp_no_stp'])} ({range_text(block, 'fpp_no_stp')}) without a "
            "planet transiting an unresolved bound companion (STP), absent from SkySift's model, and a nearby "
            f"false-positive probability of {_p(block['nfpp'])} ({range_text(block, 'nfpp')}) over {n} "
            f"runs{left_out}, {NO_CLASS_CLAUSE}.")
    if dominant:
        rest += f" {dominant}"
    rest += f" {TIC_STAR}"                    # fix wave round 4, polish (a) and N2: after the dominant sentence
    rest += f" {excluded_text(block, True)}" if _variant(block) else ""
    if tex:
        return Raw(tex_escape(head) + r"\citep{giacalone2021}" + tex_escape(rest))
    return head + CITATION + rest
