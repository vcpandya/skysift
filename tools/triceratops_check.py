"""Run TRICERATOPS (Giacalone et al. 2021) on one exported SkySift dossier and write its false-positive probability.

This runs under its own interpreter, never the engine's: TRICERATOPS 1.1.0 needs numpy 1.26 and scipy 1.13, and the
engine stands on numpy 2. Make the environment once, from the repository root:

    uv venv .venv-triceratops --python 3.12
    uv pip install --native-tls -p .venv-triceratops/Scripts/python.exe triceratops==1.1.0

then export, run and import (the ``-I`` keeps the inputs folder off ``sys.path``):

    skysift crosscheck --target kic-006146676 --export cc/kic-006146676
    .venv-triceratops/Scripts/python.exe -I tools/triceratops_check.py --inputs cc/kic-006146676 --out cc/result.json
    skysift crosscheck --target kic-006146676 --import cc/result.json

The export (``skysift.crosscheck.export_inputs``) holds ``inputs.json``, ``lightcurve.csv`` (the whitened flux within
three durations of each transit, points beyond 5 MAD of a box model clipped) and ``tpf/``, copies of the stored pixel
files of the neighbour KIC whose stamps hold the star.

Construction route. ``triceratops.target`` is built with ``mission="Kepler"`` (so the limb darkening comes from the
package's Kepler table), the target's RA and Dec (so the package finds the TIC id by position and skips the KIC
lookup) and ``sectors=[]``, so its per-quarter loop, which would download each quarter's pixel file through lightkurve
by KIC id (and fails for a star Kepler never targeted), never runs. The constructor still asks MAST for the TIC stars
within 10 Kepler pixels and Gaia for the field-star population (it writes ``<ID>_gaia_background.csv``, so the tool
works in the ``--out`` folder). The tool then sets ``sectors``, ``TESS_images``, ``col0s``, ``row0s`` and
``pix_coords`` itself from the stored pixel files (``quarter_images``, ``pixel_coords``), with the package's own
arithmetic: the time-mean flux image padded with NaN to 22 x 22, ``col0 = 1CRV4P - n_cols_before`` and each star at
``col0 + WCS column + n_cols_before``, the WCS from the APERTURE header.

The TIC stars are cross-matched with the dossier's Gaia neighbours by separation within 1 arcsec
(``match_neighbours``); a neighbour the TIC lacks is added with ``add_star`` and a Tmag estimated from G
(``tmag_from_g``) at the target's pixel position, as the package places an added star, since the dossier stores its
separation but no position; the result's caveats name it. A TIC row without a Teff, radius or mass (the package fills
it from photometry) and an added star take the Gaia DR3 or dwarf-table values the export carries, set with
``update_star`` one parameter at a time; ``params_source`` records per star which source won (``param_updates``).

Flux shares (ruling n of 22 October 2026). ``calc_depths`` runs on the 3 x 3 box around the target, which gives the
package's Gaussian-box fractions; the tool then overwrites ``fluxratio`` and ``tdepth`` (``leakage_shares``).
CROWDSAP C of the target's KBonus file is the target's fraction of the aperture light, so the neighbours' light in
its PSF light curve over the target's own is bounded by the contamination budget B = (1 - C) / C (ruling n as amended
after the Task 9 review), shared among them in proportion to the box fractions re-weighted into the Kepler band with
Gaia G as the proxy (ruling r). Then fluxratio_target = 1 / (1 + B) = C, fluxratio_i = share_i / (1 + B) =
(1 - C) g_i / sum g_j, and the depth star i would need is the on-target depth over share_i (0 above 1, as the package
does). A neighbour the TIC lacks sits at the target's pixel, so its box fraction is weighted by the Gaussian box
fraction at its stored separation along a pixel row (``offset_box_factor``). No star is removed and no scenario
dropped.

The export must carry what the revision added (the target's CROWDSAP in (0, 1] and PSFFRAC key, each neighbour's
parameter sources, the clip count; ``export_problems``): the tool refuses an older export with exit 2 unless
``--allow-old-export`` is given, and then the package's box shares stand and the result records the flag.

Runs (ruling s). ``calc_probs(exptime=0.0204, N=1_000_000, parallel=True, dilution_corrected=True)`` runs ``--runs``
times (20 by default, the import's minimum) with seeds ``--seed`` to ``--seed + runs - 1``. The package's parallel
path is numpy vectorisation over the draws, not multiprocessing, so nothing changes on Windows or under ``-I``. It is
only about 1.5 times as fast as the serial loop (22 October 2026, KIC 6146676 at 20,000 draws: 19 s against 29 s), so
20 runs at 1e6 draws take about 5 hours per signal. With ``--parallel-check`` (the default) the first seed is run
through the serial loop and the parallel path at ``--check-draws`` draws, and the parallel path again at the next
``CHECK_SEEDS - 1`` seeds; the settings keep both FPPs and both NFPPs with the draw count and say whether the FPPs are
equal to three decimals and whether the NFPPs lie within the Monte Carlo noise at that draw count, measured as the
seed-to-seed standard deviation of the parallel NFPP (``parallel_check_record``). Should the parallel path fail, the
tool says so and runs the rest serially. Each run stores its FPP, FPP without STP, NFPP, P(STP), P(PTP), best scenario and
whether it was degenerate; the medians and the 16th and 84th percentiles are over the runs that are not.

Field stars. The constructor's Gaia DR3 query for the field-star population returns None when it fails, and the
package then skips the DTP, DEB, DEBx2P, BTP, BEB and BEBx2P scenarios without a word, so the tool refuses to start
(exit 2) without a readable, non-empty population file. It writes the rows sorted to the result's own copy,
``<out stem>.population.csv``, and points the package at it (``sorted_population``), so the same seed draws the same
field stars across starts and two runs in one folder never share a file; ``settings.field_stars`` records the copy's
path, row count and hash. A result file that already exists stops the tool at once with exit 0 ("result exists"), so
a launcher rerun never redoes or overwrites it.

Checkpoint. After the check and after every run the tool writes ``<out stem>.partial.json`` (``partial_path``): the
runs so far, the check, the parallel state and what defines a run (``checkpoint_key``): the settings, the field-star
rows' hash, the hash of the final ``stars`` table after the parameter and share steps (``stars_digest``), and the
hashes of ``lightcurve.csv`` and ``inputs.json`` as files, since a resume rebuilds all of them. ``--resume`` refuses a
checkpoint whose key differs (naming what differs) or that holds more runs than ``--runs``, and goes on from the next
seed; with no checkpoint it starts fresh and ``settings.resume_note`` says so, so a launcher can always pass it. A
checkpoint found without ``--resume`` stops the tool (exit 2), so a relaunch never overwrites it. The result file is
written once at the end and the partial file removed. ``--draws`` (default 1e6) sets the draws per run, so ``--runs 2
--draws 2000 --check-draws 2000`` runs ``main`` end to end in minutes; below 1e6 the tool warns that the result is a
smoke test, and the import refuses it, as it refuses a result made with ``--allow-old-export``.

The helpers above the guarded ``import triceratops`` need numpy and astropy only, so the engine's tests import them.

    python -I tools/triceratops_check.py --inputs <export dir> --out <result.json> [--runs 20] [--seed 1]
        [--draws 1000000] [--parallel-check | --no-parallel-check] [--check-draws 20000] [--resume]
        [--allow-old-export]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time as clock
import warnings
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS, FITSFixedWarning

TOOL = "triceratops"                # skysift.crosscheck.TOOL (the engine is not importable from this interpreter)
MIN_RUNS = 20                       # skysift.crosscheck.N_RUNS: the import refuses fewer
SEARCH_RADIUS = 10                  # Kepler pixels, the package's default
N_PIX = 2 * SEARCH_RADIUS + 2       # the padded image side, as the package builds it
EXPTIME_DAYS = 0.0204               # Kepler long cadence
N_DRAWS = 1_000_000                 # skysift.crosscheck.MIN_DRAWS: the import refuses fewer draws per run
RADIUS_MISMATCH = 0.2               # a mass belonging to a radius this far from the package's is left to the package
REPLACE_ATTEMPTS = 5                # a checkpoint held open by another process: tries before warning
STARS_COLUMNS = ("ID", "Tmag", "ra", "dec", "Teff", "rad", "mass", "plx", "fluxratio", "tdepth")
CHECK_DRAWS = 20_000                # the serial-against-parallel check: the same estimator, so a few draws suffice
CHECK_SEEDS = 4                     # parallel seeds at those draws, whose NFPP scatter is the Monte Carlo noise
FPP_DECIMALS = 3                    # the serial and parallel FPPs are compared to this many decimals
NOISE_SIGMAS = 3.0                  # an NFPP gap within this many seed-to-seed standard deviations is noise
METRICS = ("fpp", "fpp_no_stp", "nfpp", "p_stp", "p_ptp")          # skysift.crosscheck.METRICS
RUN_KEYS = ("seed", "fpp", "fpp_no_stp", "nfpp", "p_stp", "p_ptp", "best_scenario", "degenerate")
PARAMS = (("Teff", "teff"), ("rad", "radius"), ("mass", "mass"))  # the package's column, the export's key
PIXEL_ARCSEC = 4.0                  # the package's Kepler pixel
PSF_SIGMA_PIX = 0.75                # the package's Gaussian PSF (calc_depths)
BUDGET, BOX = "contamination budget", "package Gaussian box"
CAVEATS = (
    ("The light is shared in the Kepler band with Gaia G as the proxy: the package's own shares use TIC TESS "
     "magnitudes (Tmag), which understate a G-type neighbour's share beside a red target in the Kepler band. The "
     "package's bound-companion and background-star dilution stay in the TESS band, a known approximation with a "
     "direction each way: a bound companion of a cool dwarf is cooler still, so the TESS band overstates its light "
     "and the dilution it causes, while background field stars are mostly bluer than the target, so the TESS band "
     "understates theirs. The limb darkening comes from the package's Kepler table."),
    ("No high-resolution imaging exists, so the package lets an unresolved bound companion sit anywhere within "
     "2.2 arcsec of the target; a planet transiting such a companion (STP) counts towards the full FPP and is the "
     "open false-positive channel. SkySift's model has no such scenario, so the FPP without STP, (FPP - P_STP) / "
     "(1 - P_STP), is quoted beside the full FPP."),
    ("Giacalone et al. 2021's classes are labels only: the paper found no validation region at 30-minute cadence "
     "(its section 4.2), which Kepler long cadence is, so no candidate is called validated by this tool."),
    ("The priors are the package's own, not SkySift's, and the field-star population is the Gaia DR3 field-star "
     "population TRICERATOPS 1.1.0 uses by default, not the 2021 paper's TRILEGAL one."),
    ("The result varies from run to run, so the median and the 16th to 84th percentiles over the runs that are not "
     "degenerate are quoted, and every run is kept."),
    ("The dossier's Gaia neighbours are matched to the TIC stars by their separation from the target only, since the "
     "dossier stores no position for them."),
    ("No star is removed on the strength of SkySift's own centroid test, which would make the cross-check "
     "circular."),
)
OUT_OF_TRANSIT_MIN = 50             # fewer out-of-transit points: the robust scatter is taken over every point


def _finite(x) -> float | None:
    if isinstance(x, bool) or x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def usable_neighbours(gaia_neighbours) -> tuple[list[dict], list[dict]]:
    """(the neighbours with a finite separation and G, the others), so a neighbour the dossier stores without
    either is left out of the match and named in the caveats instead."""
    usable = [n for n in gaia_neighbours if _finite(n.get("sep_arcsec")) is not None and _finite(n.get("gmag"))
              is not None]
    return usable, [n for n in gaia_neighbours if n not in usable]


def match_neighbours(tic_rows, gaia_neighbours, arcsec: float = 1.0) -> tuple[list[dict], list[dict]]:
    """Pair each Gaia neighbour of the dossier ({"source_id", "sep_arcsec", "gmag", "bp_rp"}) with the TIC star
    (TRICERATOPS's ``stars`` rows other than the target: "ID", "sep (arcsec)", "GAIAmag") whose separation from the
    target is within ``arcsec`` of its own, the nearest in separation and then in G (a TIC star without a G ranks
    last), each TIC star used once. A neighbour without a finite separation or G is skipped (``usable_neighbours``
    names it). Returns (matched: {"source_id", "tic_id", "dsep_arcsec"}, the usable neighbours left unmatched)."""
    free = [r for r in tic_rows if _finite(r.get("sep (arcsec)")) is not None]
    matched, missing = [], []
    for n in sorted(usable_neighbours(gaia_neighbours)[0], key=lambda n: float(n["sep_arcsec"])):
        sep, g = float(n["sep_arcsec"]), float(n["gmag"])
        near = [r for r in free if abs(float(r["sep (arcsec)"]) - sep) <= arcsec]
        if not near:
            missing.append(n)
            continue
        best = min(near, key=lambda r: (abs(float(r["sep (arcsec)"]) - sep),
                                        abs(_finite(r.get("GAIAmag")) - g) if _finite(r.get("GAIAmag")) is not None
                                        else math.inf))
        free.remove(best)
        matched.append({"source_id": str(n["source_id"]), "tic_id": str(best["ID"]),
                        "dsep_arcsec": abs(float(best["sep (arcsec)"]) - sep)})
    return matched, missing


def tmag_from_g(g: float, bp_rp: float | None) -> float:
    """An estimated TESS magnitude from Gaia G: G - 0.5 for a red star (BP-RP above 1.5), G - 0.3 otherwise or
    without a colour."""
    return float(g) - (0.5 if bp_rp is not None and bp_rp > 1.5 else 0.3)


def quarter_images(tpf_paths) -> list[dict]:
    """One dict per pixel file, by quarter, as TRICERATOPS builds them for a Kepler target: "quarter", "image" (the
    time-mean FLUX padded with NaN to ``N_PIX`` square), "col0" and "row0" (the CCD column and row of the padded
    image's first pixel, ``1CRV4P`` / ``2CRV4P`` of the TARGETTABLES header less the padding; a file without them
    raises ValueError, since the stars could not be placed), "pad" (columns and rows of padding before the stamp)
    and "wcs" (from the APERTURE header)."""
    out = []
    for path in tpf_paths:
        with fits.open(path, memmap=False) as h:
            quarter = int(h[0].header["QUARTER"])
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)         # an all-NaN pixel: its mean is NaN
                image = np.nanmean(np.asarray(h[1].data["FLUX"], float), axis=0)
            lacking = [k for k in ("1CRV4P", "2CRV4P") if k not in h[1].header]
            if lacking:
                raise ValueError(f"{Path(path).name}: no {' or '.join(lacking)} in the TARGETTABLES header")
            col_ref, row_ref = float(h[1].header["1CRV4P"]), float(h[1].header["2CRV4P"])
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", FITSFixedWarning)
                wcs = WCS(h[2].header, naxis=2)
        rows_before, cols_before = (max(0, (N_PIX - n) // 2) for n in image.shape)
        rows_after, cols_after = (max(0, N_PIX - n - b) for n, b in zip(image.shape, (rows_before, cols_before),
                                                                         strict=True))
        image = np.pad(image, ((rows_before, rows_after), (cols_before, cols_after)), constant_values=np.nan)
        out.append({"quarter": quarter, "image": image, "col0": col_ref - cols_before, "row0": row_ref - rows_before,
                    "pad": (cols_before, rows_before), "wcs": wcs})
    return sorted(out, key=lambda q: q["quarter"])


def pixel_coords(q: dict, ra, dec) -> np.ndarray:
    """(n, 2) CCD (column, row) of each star in quarter ``q`` (the target first), as the package computes
    ``pix_coords``; a star without a finite position takes the target's, as ``add_star`` places an added star."""
    out = np.zeros((len(ra), 2))
    for i, (a, d) in enumerate(zip(ra, dec, strict=True)):
        if not (np.isfinite(a) and np.isfinite(d)):
            out[i] = out[0]
            continue
        c, r = q["wcs"].all_world2pix(float(a), float(d), 0)
        out[i] = (q["col0"] + float(c) + q["pad"][0], q["row0"] + float(r) + q["pad"][1])
    return out


def aperture_3x3(target_pix) -> np.ndarray:
    """The 3 x 3 box of (column, row) pixels around the target's rounded position, in the package's layout."""
    c, r = np.round(np.asarray(target_pix, float))
    return np.array([np.repeat(np.arange(c - 1, c + 2), 3), np.tile(np.arange(r - 1, r + 2), 3)]).T


def missing_stellar(row) -> list[str]:
    """The columns among mass, rad, Teff and plx that the target's ``stars`` row lacks (None or not finite). Without
    them ``calc_probs`` stops at the target and leaves every scenario equally likely, a result that means nothing."""
    return [k for k in ("mass", "rad", "Teff", "plx") if _finite(row.get(k)) is None]


def robust_flux_err(time, flux, period: float, t0: float, duration_days: float) -> tuple[float, str]:
    """(per-point error, which points) for ``flux_err_0``: 1.4826 times the MAD of the relative flux more than one
    duration from mid-transit, the rule ``skysift.ephemeris`` times the transits with, or of every point when fewer
    than ``OUT_OF_TRANSIT_MIN`` lie out of transit. The exported pipeline error is smaller than the scatter of the
    whitened flux the tool fits, and the scenario likelihoods scale with one over its square."""
    time, flux = np.asarray(time, float), np.asarray(flux, float)
    ok = np.isfinite(time) & np.isfinite(flux)
    phase = (time - t0 + period / 2) % period - period / 2
    out = ok & (np.abs(phase) > duration_days)
    which = "out of transit (more than one duration from mid-transit)"
    if out.sum() < OUT_OF_TRANSIT_MIN:
        out, which = ok, f"every point (fewer than {OUT_OF_TRANSIT_MIN} out of transit)"
    f = flux[out]
    return float(1.4826 * np.median(np.abs(f - np.median(f)))), which


def flux_err_caveat(robust: float, pipeline: float, which: str) -> str:
    """The caveat naming the per-point error handed to TRICERATOPS and the pipeline error it replaces."""
    return (f"The per-point error given to TRICERATOPS is 1.4826 times the median absolute deviation of the whitened "
            f"flux {which}, {robust:.6f} in relative flux, not the KBonus pipeline error ({pipeline:.6f}), which is "
            "smaller than the scatter of the flux it fits.")


def read_lightcurve(path: Path) -> dict[str, np.ndarray]:
    table = np.loadtxt(path, delimiter=",", skiprows=1, ndmin=2)
    return {"time": table[:, 0], "flux": table[:, 1], "err": table[:, 2], "quarter": table[:, 3].astype(int)}


# ---------------------------------------------------------------------------------------------------- flux shares

def offset_box_factor(sep_arcsec) -> float:
    """The package's Gaussian-box fraction of a star ``sep_arcsec`` from the target along a pixel row, over that of a
    star on the target, both with the target at the centre of the 3 x 3 box: [Phi((1.5 - d) / s) - Phi((-1.5 - d) /
    s)] / [Phi(1.5 / s) - Phi(-1.5 / s)] with d the separation in Kepler pixels and s the package's PSF sigma. An added
    star is placed at the target's pixel, as ``add_star`` places it, so its box fraction is that of a star at 0 arcsec;
    this factor puts it back at its stored separation (1 without one)."""
    sep = _finite(sep_arcsec)
    if sep is None:
        return 1.0
    d, s = abs(sep) / PIXEL_ARCSEC, PSF_SIGMA_PIX

    def phi(x: float) -> float:
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
    return (phi((1.5 - d) / s) - phi((-1.5 - d) / s)) / (phi(1.5 / s) - phi(-1.5 / s))


def leakage_shares(stars, crowdsap, gmags, tdepth: float, box_factors=None) -> dict:
    """The flux shares that replace the package's (ruling n). ``stars``: TRICERATOPS's rows after ``calc_depths``,
    the target first, each with "ID", "Tmag" and "fluxratio" (the package's Gaussian-box fraction of the box light,
    shared by Tmag); ``gmags``: Gaia G per row (None where unknown); ``tdepth``: the on-target depth (fraction).

    ``box_factors`` (one per row, 1 where None): a multiplier on each box fraction, ``offset_box_factor`` for a star
    the tool added at the target's pixel.

    Each box fraction is re-weighted into the Kepler band with G as the proxy (ruling r), by 10^(-0.4 ((G_i - T_i) -
    (G_t - T_t))), a star without G keeping the target's offset, and the re-weighted fractions g_i normalised.
    CROWDSAP C is the target's fraction of the aperture light, so with a C in (0, 1] the neighbours' light over the
    target's is at most B = (1 - C) / C (ruling n as amended), shared as share_i = L_i F_i / F_t = B g_i / sum_j g_j
    over the neighbours; then fluxratio_target = 1 / (1 + B) = C, fluxratio_i = share_i / (1 + B) = (1 - C) g_i /
    sum_j g_j, the target keeps ``tdepth`` and star i needs tdepth / share_i, set to 0 above 1 or without a share, as
    the package does. Without one the package's box stands: its fluxratio, the share fluxratio_i / fluxratio_target it
    implies and its depths.

    Returns {"source": "contamination budget" | "package Gaussian box", "budget": B = (1 - C) / C or None,
    "contaminating_fraction": 1 - C or None (the neighbours' fraction of the aperture light), "crowdsap", "rows":
    [{"id", "g_package", "box_factor", "band", "kepler_flux" (F_i / F_t from G, None without it), "g", "share" (None
    for the target), "fluxratio", "tdepth", "source"}]}."""
    factors = [1.0 if f is None else float(f) for f in (box_factors or [None] * len(stars))]
    pkg = [_finite(s.get("fluxratio")) or 0.0 for s in stars]
    tmag = [_finite(s.get("Tmag")) for s in stars]
    g = [_finite(x) for x in gmags]
    gt, tt = g[0], tmag[0]
    rows, weights = [], []
    for i, s in enumerate(stars):
        has_g = None not in (gt, tt, g[i], tmag[i])
        weights.append(pkg[i] * factors[i] * (10 ** (-0.4 * ((g[i] - tmag[i]) - (gt - tt))) if has_g else 1.0))
        rows.append({"id": str(s.get("ID")), "g_package": pkg[i], "box_factor": factors[i],
                     "band": "Gaia G" if has_g else "Tmag",
                     "kepler_flux": 10 ** (-0.4 * (g[i] - gt)) if None not in (g[i], gt) else None})
    total = sum(weights)
    for r, w in zip(rows, weights, strict=True):
        r["g"] = w / total if total > 0 else 0.0
    c = _finite(crowdsap)
    if c is not None and 0.0 < c <= 1.0:
        budget = (1.0 - c) / c
        others = sum(r["g"] for r in rows[1:])
        for i, r in enumerate(rows):
            if i == 0:
                r.update(share=None, fluxratio=c, tdepth=float(tdepth))
                continue
            share = budget * r["g"] / others if others > 0 else 0.0
            need = tdepth / share if share > 0 else 0.0
            r.update(share=share, fluxratio=(1.0 - c) * r["g"] / others if others > 0 else 0.0,
                     tdepth=need if need <= 1 else 0.0)
            r["source"] = BUDGET
        rows[0]["source"] = BUDGET
        return {"source": BUDGET, "budget": budget, "contaminating_fraction": 1.0 - c, "crowdsap": c, "rows": rows}
    for i, r in enumerate(rows):
        need = tdepth * pkg[0] / pkg[i] if pkg[i] > 0 else 0.0
        r.update(share=None if i == 0 else (pkg[i] / pkg[0] if pkg[0] > 0 else None), fluxratio=pkg[i],
                 tdepth=need if need <= 1 else 0.0, source=BOX)
    return {"source": BOX, "budget": None, "contaminating_fraction": None, "crowdsap": c, "rows": rows}


def leakage_caveat(shares: dict, crowdsap) -> str:
    """The caveat on the flux shares the run used (``leakage_shares``)."""
    if shares["source"] == BUDGET:
        return (f"CROWDSAP {float(crowdsap):.4f} of the target's KBonus file is the target's fraction of the aperture "
                "light, so the light the neighbours leak into its KBonus PSF light curve is at most (1 - CROWDSAP) / "
                f"CROWDSAP = {shares['budget']:.4f} of the target's own (the target's flux ratio is CROWDSAP), shared "
                "among them by the package's Gaussian-box fractions in the Kepler band (Gaia G as the proxy). The "
                "PSF fit removes that light to first order, so each share, and the nearby false-positive probability "
                "with it, is an upper bound.")
    return ("The export carries no CROWDSAP, so the package's Gaussian-box shares of the 3 x 3 box light are kept; "
            "they describe aperture photometry, not the KBonus PSF fit, and overstate the neighbours' share.")


def leak_caveat(leak: dict | None) -> str | None:
    """The caveat on rung 5's leak test, reported as a consistency check (ruling n); None without one."""
    if not leak or _finite(leak.get("depth_ppm")) is None:
        return None
    err = _finite(leak.get("err_ppm"))
    depth = f"{leak['depth_ppm']:.0f}" + (f" +- {err:.0f}" if err is not None else "")
    whose = f"KIC {leak['kic']}" if leak.get("kic") else "the neighbour"
    return (f"SkySift's leak test measures the dip in the neighbour's own aperture ({depth} ppm in {whose}), the "
            "opposite direction to the leak into this star's light curve, so it is a consistency check and not a "
            "share.")


def clip_caveat(lightcurve: dict) -> str | None:
    """The caveat on the export's 5 MAD clip (ruling p), a raw MAD (one MAD is 0.6745 sigma, so 5 MAD is about 3.4
    sigma), with the clipped points inside and outside the transit when the export counts them; None for an export
    without the count."""
    n = lightcurve.get("clipped")
    if not isinstance(n, int):
        return None
    mad = lightcurve.get("clip_mad", 5.0)
    rule = f"{mad:g} raw median absolute deviations (about {mad * 0.6745:.1f} sigma)"
    if n == 0:
        return f"No point of the exported window lay further than {rule} from a box model of the transit."
    inside, outside = lightcurve.get("clipped_in_transit"), lightcurve.get("clipped_out_of_transit")
    split = (f" ({inside} in transit, {outside} out of transit)" if isinstance(inside, int)
             and isinstance(outside, int) else "")
    return (f"{n} point{'s' if n != 1 else ''} further than {rule} from a box model of the transit were clipped "
            f"from the exported window{split}.")


def export_problems(inputs: dict) -> list[str]:
    """What an export lacks of the cross-check revision of 22 October 2026: the target's CROWDSAP (in (0, 1]) and its
    PSFFRAC key, each neighbour's ``params_source`` and ``mass_radius`` (an export before 9b2a0e4 set masses by the
    older evolved rule) and the light curve's clip count. [] for a current export."""
    out = []
    tgt = inputs.get("target") or {}
    c = _finite(tgt.get("crowdsap"))
    if "crowdsap" not in tgt:
        out.append("the target has no crowdsap key")
    elif c is None or not 0.0 < c <= 1.0:
        out.append(f"the target's CROWDSAP {tgt.get('crowdsap')!r} is not in (0, 1]")
    if "psffrac" not in tgt:
        out.append("the target has no psffrac key")
    for key, words in (("params_source", "parameter sources"),
                       ("mass_radius", "mass_radius (the radius each mass belongs to)")):
        lacking = [str(n.get("source_id")) for n in inputs.get("neighbours") or [] if key not in n]
        if lacking:
            out.append(f"neighbour{'s' if len(lacking) != 1 else ''} {', '.join(lacking)} carry no {words}")
    if not isinstance((inputs.get("lightcurve") or {}).get("clipped"), int):
        out.append("the light curve has no clip count")
    return out


# ---------------------------------------------------------------------------------------------------- parameters

def param_updates(row: dict, given: dict | None, added: bool) -> tuple[list[tuple[str, float]], dict]:
    """(the ``update_star`` calls, the source of each parameter) for one ``stars`` row (ruling q): a Teff, radius or
    mass the TIC row lacks (the package lists it in ``params_estimated`` when it filled it from photometry, or it is
    not finite), and every one of an added star, takes the export's value (``given``: {"teff", "radius", "mass",
    "params_source", "mass_radius"}) where it has one; otherwise the TIC value ("TIC"), the package's estimate
    ("TRICERATOPS estimate from photometry") or nothing ("none (the package assumes solar values)") stands. A mass
    that belongs to a radius (``mass_radius``, else the export's radius) more than ``RADIUS_MISMATCH`` from the radius
    the package will use (the TIC's when it is kept) is left to the package, its source saying why, so a star never
    carries one source's mass on another's radius."""
    est = row.get("params_estimated")
    estimated = {e.strip().split(" ")[0] for e in est.split(",")} if isinstance(est, str) and est else set()
    updates, sources = [], {}
    used_radius = None
    for col, key in PARAMS:
        if not (added or col in estimated or _finite(row.get(col)) is None):
            sources[col] = "TIC"
            if col == "rad":
                used_radius = _finite(row.get(col))
            continue
        value = _finite((given or {}).get(key))
        said = ((given or {}).get("params_source") or {}).get(key)
        stands = ("TRICERATOPS estimate from photometry" if col in estimated
                  else "none (the package assumes solar values)")
        if col == "rad":
            used_radius = value if value is not None else _finite(row.get(col))
        belongs = _finite((given or {}).get("mass_radius", (given or {}).get("radius")))
        if col == "mass" and value is not None and None not in (used_radius, belongs) and belongs > 0 and \
                abs(used_radius - belongs) / belongs > RADIUS_MISMATCH:
            sources[col] = (f"{said or 'the export'}, left to the package: that mass belongs to radius {belongs:.3f}, "
                            f"more than {RADIUS_MISMATCH:.0%} from the radius {used_radius:.3f} the package uses; "
                            f"{stands}")
            continue
        if value is not None:
            updates.append((col, value))
            sources[col] = said or "the export"
            continue
        sources[col] = f"{said}: {stands}" if said else stands
    return updates, sources


# ---------------------------------------------------------------------------------------------------- runs

def _unit(x) -> tuple[float, bool]:
    """(x held in 0 to 1, whether it was finite)."""
    x = _finite(x)
    return (0.0, False) if x is None else (min(1.0, max(0.0, x)), True)


def fpp_without_stp(fpp: float, p_stp: float) -> float | None:
    """(FPP - P_STP) / (1 - P_STP) held in 0 to 1; None when P_STP is 1 (``skysift.crosscheck.fpp_without_stp``)."""
    if p_stp >= 1.0:
        return None
    return min(1.0, max(0.0, (fpp - p_stp) / (1.0 - p_stp)))


def run_record(seed: int, fpp, nfpp, degenerate, scenarios: list[dict]) -> dict:
    """One run as the result keeps it (ruling s): the seed, the FPP, the FPP without STP, the NFPP, P(STP) and
    P(PTP) summed from the scenario table, the most probable scenario {"id", "scenario", "prob"} and whether the
    package flagged it degenerate; a value that is not finite marks the run degenerate (it is then left out of the
    medians), and rounding below 0 or above 1 is held at the edge. ``scenarios`` (the package's ``probs`` rows) is
    kept for the median run's table."""
    fpp, ok1 = _unit(fpp)
    nfpp, ok2 = _unit(nfpp)
    p_stp, ok3 = _unit(sum(_finite(s.get("prob")) or 0.0 for s in scenarios if s.get("scenario") == "STP"))
    p_ptp, ok4 = _unit(sum(_finite(s.get("prob")) or 0.0 for s in scenarios if s.get("scenario") == "PTP"))
    rated = [s for s in scenarios if _finite(s.get("prob")) is not None]
    best = max(rated, key=lambda s: float(s["prob"])) if rated else None
    return {"seed": int(seed), "fpp": fpp, "fpp_no_stp": fpp_without_stp(fpp, p_stp), "nfpp": nfpp, "p_stp": p_stp,
            "p_ptp": p_ptp,
            "best_scenario": None if best is None else {"id": str(best.get("ID")), "scenario": str(best["scenario"]),
                                                        "prob": float(best["prob"])},
            "degenerate": bool(degenerate) or not (ok1 and ok2 and ok3 and ok4), "scenarios": scenarios}


def summarise(runs: list[dict]) -> dict:
    """The median and the 16th and 84th percentiles of each metric over the runs that are not degenerate, a copy of
    ``skysift.crosscheck.summarise`` (the engine's tests check that the two agree)."""
    good = [r for r in runs if not r.get("degenerate")]
    out: dict = {}
    for m in METRICS:
        vals = [float(r[m]) for r in good if r.get(m) is not None]
        q = [float(x) for x in np.percentile(vals, [50, 16, 84])] if vals else [None, None, None]
        out.update({m: q[0], f"{m}_p16": q[1], f"{m}_p84": q[2]})
    return out


def _clamp(x) -> float:
    """A probability held in 0 to 1 (the package's FPP can be a rounding error below 0); 0 when not finite."""
    return _unit(x)[0]


def _tiny(x: float, spec: str) -> str:
    """``x`` in ``spec``, or 0 below 1e-12, where the package's sums are rounding."""
    return "0" if x < 1e-12 else format(x, spec)


def parallel_check_record(seed: int, n: int, serial: dict, parallel: dict, seed_nfpps: list[float]) -> dict:
    """The serial-against-parallel check of one seed at ``n`` draws (ruling s): both FPPs and both NFPPs, the draw
    count, and the comparison stated in words, never as one pass flag. The FPPs are compared to ``FPP_DECIMALS``
    decimals (an absolute difference below half a unit in the last). The NFPP is a sum over the nearby-star
    scenarios, whose evidences rest on the few draws that fit, so at a small draw count it carries Monte Carlo noise of
    its own: the noise is measured as the sample standard deviation of the parallel NFPP over ``seed_nfpps`` (the
    parallel path at ``n`` draws and consecutive seeds, the first being ``seed``), and a gap within ``NOISE_SIGMAS``
    of those is noise. Every value is held in 0 to 1 and a value below 1e-12 is printed as 0."""
    serial = {k: _clamp(serial[k]) for k in ("fpp", "nfpp")}
    parallel = {k: _clamp(parallel[k]) for k in ("fpp", "nfpp")}
    seed_nfpps = [_clamp(x) for x in seed_nfpps]
    fpp_gap, nfpp_gap = abs(serial["fpp"] - parallel["fpp"]), abs(serial["nfpp"] - parallel["nfpp"])
    fpp_equal = fpp_gap < 0.5 * 10 ** -FPP_DECIMALS
    sd = float(np.std(seed_nfpps, ddof=1)) if len(seed_nfpps) > 1 else None
    in_sd = nfpp_gap / sd if sd else None
    fpp_words = (f"the FPPs ({_tiny(serial['fpp'], '.4g')} serial, {_tiny(parallel['fpp'], '.4g')} parallel) are "
                 f"{'' if fpp_equal else 'not '}equal to {FPP_DECIMALS} decimals")
    nfpps = f"the NFPPs ({_tiny(serial['nfpp'], '.3g')} serial, {_tiny(parallel['nfpp'], '.3g')} parallel)"
    if in_sd is None:
        nfpp_words = f"{nfpps} cannot be set against the Monte Carlo noise, which needs two seeds that differ"
    else:
        nfpp_words = (f"{nfpps} differ by {in_sd:.1f} standard deviations of the Monte Carlo noise ({sd:.2g}, the "
                      f"seed-to-seed scatter of the parallel NFPP over {len(seed_nfpps)} seeds at {n:,} draws), so "
                      f"the gap is {'within' if in_sd <= NOISE_SIGMAS else 'beyond'} that noise")
    return {"seed": seed, "N": n, "fpp_serial": serial["fpp"], "fpp_parallel": parallel["fpp"],
            "nfpp_serial": serial["nfpp"], "nfpp_parallel": parallel["nfpp"], "fpp_difference": fpp_gap,
            "nfpp_difference": nfpp_gap, "nfpp_parallel_seeds": [float(x) for x in seed_nfpps],
            "nfpp_noise_sd": sd, "nfpp_difference_in_sd": in_sd,
            "comparison": f"At {n:,} draws and seed {seed}, {fpp_words}, and {nfpp_words}."}


def star_rows(stars: list[dict], matched: list[dict], added: dict[str, float], shares: dict, gmags: list,
              params_source: dict) -> list[dict]:
    """The stars the run used, from TRICERATOPS's ``stars`` rows after the shares were written: id, Tmag, G,
    separation, the flux ratio and required depth used, the package's box fraction, the Kepler-band fraction, the
    Kepler-band flux relative to the target, the box factor (1 for a TIC star at its own position, the Gaussian factor
    at its separation for an added one), the share and its source, the Teff, radius and mass with their sources,
    the Gaia id and whether the Tmag is an estimate."""
    gaia_of = {m["tic_id"]: m["source_id"] for m in matched}

    def num(x):
        return _finite(x)
    out = []
    for s, sh, g in zip(stars, shares["rows"], gmags, strict=True):
        sid = str(s["ID"])
        out.append({"id": sid, "tmag": num(s.get("Tmag")), "gmag": num(g), "sep_arcsec": num(s.get("sep (arcsec)")),
                    "flux_ratio": num(s.get("fluxratio")), "tdepth": num(s.get("tdepth")),
                    "g_package": sh["g_package"], "g_kepler": sh["g"], "kepler_flux": sh["kepler_flux"],
                    "band": sh["band"], "box_factor": sh["box_factor"], "share": sh["share"],
                    "share_source": sh["source"],
                    "teff": num(s.get("Teff")), "rad": num(s.get("rad")), "mass": num(s.get("mass")),
                    "params_source": params_source.get(sid),
                    "gaia_source_id": sid if sid in added else gaia_of.get(sid), "tmag_estimated": sid in added})
    return out


def assemble_result(runs: list[dict], stars: list[dict], settings: dict, inputs: dict, version: str) -> dict:
    """The result file, keyed as ``skysift.crosscheck.RESULT_KEYS``: the run count, the degenerate count, the
    medians and 16th and 84th percentiles over the runs that are not degenerate (``summarise``), every run
    (``RUN_KEYS``), the scenario table of the non-degenerate run nearest the median FPP, the stars, the inputs'
    fingerprint, the caveats (``CAVEATS``, the settings' notes and the degenerate count) and the settings with the
    seeds."""
    summary = summarise(runs)
    good = [i for i, r in enumerate(runs) if not r["degenerate"]] or list(range(len(runs)))
    centre = summary["fpp"] if summary["fpp"] is not None else 0.0
    nearest = min(good, key=lambda i: abs(runs[i]["fpp"] - centre))
    degenerate = sum(1 for r in runs if r["degenerate"])
    caveats = list(CAVEATS) + list(settings.get("notes") or [])
    if degenerate:
        caveats.append(f"{degenerate} of {len(runs)} runs had degenerate scenario evidences (FPP_degenerate) and are "
                       "left out of the medians.")
    return {"tool": TOOL, "tool_version": version, "run_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "n_runs": len(runs), "n_degenerate": degenerate, **summary,
            "runs": [{k: r[k] for k in RUN_KEYS} for r in runs], "scenarios": runs[nearest]["scenarios"],
            "stars": stars, "inputs_sha256": inputs["fingerprint"], "caveats": caveats,
            "settings": {**{k: v for k, v in settings.items() if k != "notes"}, "seeds": [r["seed"] for r in runs],
                         "median_run_seed": runs[nearest]["seed"]}}


# ---------------------------------------------------------------------------------------------------- checkpoint

def partial_path(out: Path) -> Path:
    """The checkpoint beside the result file: ``<out stem>.partial.json``."""
    out = Path(out)
    return out.with_name(out.stem + ".partial.json")


def file_sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def field_star_summary(fname) -> dict:
    """{"file", "n_rows", "sha256"} of the package's field-star population (``t.trilegal_fname``): the hash is over
    the header and the rows sorted, so the order the query returns them in does not matter. Raises ValueError when
    the package has no population (its query failed or returned nothing), the file cannot be read or it holds no
    star."""
    if not isinstance(fname, str) or not fname:
        raise ValueError("the package has no field-star population")
    try:
        lines = Path(fname).read_text(encoding="utf-8").splitlines()
    except OSError as e:
        raise ValueError(f"the field-star population {fname} cannot be read ({type(e).__name__}: {e})") from None
    rows = sorted(line for line in lines[1:] if line.strip())
    if not rows:
        raise ValueError(f"the field-star population {fname} holds no star")
    digest = hashlib.sha256("\n".join([lines[0], *rows]).encode("utf-8")).hexdigest()
    return {"file": Path(fname).name, "n_rows": len(rows), "sha256": digest}


def population_path(out: Path) -> Path:
    """The result's own copy of the field-star population: ``<out stem>.population.csv``."""
    out = Path(out)
    return out.with_name(out.stem + ".population.csv")


def sorted_population(src, dest: Path) -> dict:
    """Write the package's field-star population ``src`` to ``dest`` with its rows sorted and the header first,
    through a temporary file and a rename, and return ``field_star_summary`` of the copy with "file" its absolute path
    and "source_file" the package's file name. The package draws field stars by row index with the seeded generator,
    and the Gaia query returns its rows in no fixed order, so a sorted copy makes the same seed draw the same stars
    across starts; a copy per result means two runs in one folder (two signals of one KIC) never share the file the
    package re-reads on every run. Raises ValueError as ``field_star_summary``."""
    field_star_summary(src)
    lines = Path(src).read_text(encoding="utf-8").splitlines()
    rows = sorted(line for line in lines[1:] if line.strip())
    dest = Path(dest).resolve()
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_text("\n".join([lines[0], *rows]) + "\n", encoding="utf-8")
    os.replace(tmp, dest)
    return {**field_star_summary(str(dest)), "file": str(dest), "source_file": Path(src).name}


def stars_digest(rows) -> str:
    """A SHA-256 of the ``stars`` table the runs use (``STARS_COLUMNS``, each number at 8 significant figures), taken
    after the parameter and share steps."""
    def cell(x):
        f = _finite(x)
        return None if f is None else f"{f:.8g}"
    table = [[str(r.get("ID")), *(cell(r.get(c)) for c in STARS_COLUMNS[1:])] for r in rows]
    return hashlib.sha256(json.dumps(table).encode("utf-8")).hexdigest()


def checkpoint_key(fingerprint: str, *, seed: int, draws: int, check_draws: int, parallel_check: bool,
                   flux_err: float, shares: dict, quarters, allow_old_export: bool, version: str,
                   field_stars: dict | None = None, stars_sha256: str | None = None,
                   lightcurve_sha256: str | None = None, inputs_json_sha256: str | None = None) -> dict:
    """What a resumed run must share with the runs already made: the inputs' fingerprint, the first seed, the draws
    per run, the check's draws, the exposure time, the flux error, the flux shares, the quarters, whether an older
    export was allowed, the package version, and the hashes of what a resume rebuilds: the field-star population
    (``field_star_summary``), the final ``stars`` table (``stars_digest``), ``lightcurve.csv`` and ``inputs.json``."""
    return {"inputs_sha256": fingerprint, "first_seed": int(seed), "N": int(draws),
            "check_draws": int(check_draws) if parallel_check else None, "parallel_check": bool(parallel_check),
            "exptime_days": EXPTIME_DAYS, "flux_err_rel_robust": float(flux_err),
            "flux_shares": {k: shares.get(k) for k in ("source", "budget", "crowdsap")},
            "quarters": [int(q) for q in quarters], "allow_old_export": bool(allow_old_export),
            "tool_version": str(version), "field_stars_n": (field_stars or {}).get("n_rows"),
            "field_stars_sha256": (field_stars or {}).get("sha256"), "stars_sha256": stars_sha256,
            "lightcurve_sha256": lightcurve_sha256, "inputs_json_sha256": inputs_json_sha256}


def new_state() -> dict:
    """The state a checkpoint keeps: the runs so far, the serial-against-parallel record, the parallel path's
    failure, whether the parallel path is in use, and the run numbers a resume started at."""
    return {"runs": [], "check": None, "failure": None, "parallel": True, "resumes": []}


def write_partial(path: Path, key: dict, state: dict, *, attempts: int = REPLACE_ATTEMPTS, pause: float = 0.2) -> bool:
    """Write the checkpoint through a temporary file and a rename, so a stop in the middle of a write leaves the last
    whole checkpoint. On Windows the rename fails while another process (an indexer, a virus scan, a reader) holds
    the checkpoint open, so it is tried ``attempts`` times ``pause`` seconds apart, and then the tool warns and goes
    on, the next run's checkpoint trying again. Returns whether the checkpoint was written."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"key": key, **state}, indent=1, allow_nan=False), encoding="utf-8")
    for k in range(attempts):
        try:
            os.replace(tmp, path)
            return True
        except PermissionError:
            if k + 1 < attempts:
                clock.sleep(pause)
    print(f"warning: the checkpoint {path} is held open by another process and was not replaced; the run goes on "
          "and the next checkpoint tries again", file=sys.stderr)
    return False


def checkpoint_runs(path: Path) -> int:
    """The number of runs a checkpoint holds. Raises OSError or ValueError when it cannot be read as one."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("runs"), list):
        raise ValueError(f"{path} is not a checkpoint of this tool")  # noqa: TRY004  (one error for a bad file)
    return len(data["runs"])


def resume_state(path: Path, key: dict) -> dict:
    """The state of the checkpoint at ``path`` (``new_state``'s keys). Raises OSError when it cannot be read and
    ValueError when it is not a checkpoint, when its key differs from ``key`` (naming what differs), or when its runs
    are not the consecutive seeds from the first."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("key"), dict) or not isinstance(data.get("runs"), list):
        raise ValueError(f"{path} is not a checkpoint of this tool")  # noqa: TRY004  (one error for a bad file)
    old = data["key"]
    differ = sorted(k for k in set(key) | set(old) if old.get(k) != key.get(k))
    if differ:
        raise ValueError(f"the checkpoint {path} was made with other settings ({', '.join(differ)} differ), so its "
                         "runs cannot be continued; remove it or run without --resume")
    runs = data["runs"]
    for k, r in enumerate(runs):
        if not isinstance(r, dict) or any(x not in r for x in (*RUN_KEYS, "scenarios")):
            raise ValueError(f"run {k + 1} of the checkpoint {path} lacks a key")
        if r["seed"] != key["first_seed"] + k:
            raise ValueError(f"run {k + 1} of the checkpoint {path} has seed {r['seed']}, not "
                             f"{key['first_seed'] + k}")
    state = new_state()
    state.update(runs=runs, check=data.get("check"), failure=data.get("failure"),
                 parallel=bool(data.get("parallel", True)), resumes=list(data.get("resumes") or []))
    return state


try:                                # only under .venv-triceratops; the helpers above work without it
    import triceratops.triceratops as tr
except ImportError:
    tr = None


def _probs(t) -> list[dict]:
    return json.loads(t.probs[["ID", "scenario", "prob"]].to_json(orient="records"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--inputs", required=True, help="the folder skysift crosscheck --export wrote")
    ap.add_argument("--out", required=True, help="the result file to write")
    ap.add_argument("--runs", type=int, default=MIN_RUNS,
                    help=f"runs with consecutive seeds (default {MIN_RUNS}; the import refuses fewer)")
    ap.add_argument("--seed", type=int, default=1, help="the first run's seed (default 1)")
    ap.add_argument("--parallel-check", action=argparse.BooleanOptionalAction, default=True,
                    help="first run the first seed through the serial loop and the parallel path and record both "
                         "(default on)")
    ap.add_argument("--check-draws", type=int, default=CHECK_DRAWS,
                    help=f"draws for the serial-against-parallel check (default {CHECK_DRAWS:,})")
    ap.add_argument("--draws", type=int, default=N_DRAWS, help=f"draws per run (default {N_DRAWS:,})")
    ap.add_argument("--resume", action="store_true",
                    help="continue from the checkpoint <out stem>.partial.json made with the same inputs and settings")
    ap.add_argument("--allow-old-export", action="store_true",
                    help="run an export that lacks what the revision of 22 October 2026 added (the result says so)")
    a = ap.parse_args(argv)
    if a.draws < 1 or a.check_draws < 1:
        print("--draws and --check-draws must be positive", file=sys.stderr)
        return 2
    if a.draws < N_DRAWS:
        print(f"--draws {a.draws:,} is below {N_DRAWS:,}: smoke only, the import will refuse it", file=sys.stderr)
    inputs_dir, out = Path(a.inputs).resolve(), Path(a.out).resolve()
    if out.exists():                    # a launcher rerun never redoes, or overwrites, a finished result
        print(f"result exists; remove it to rerun ({out})", file=sys.stderr)
        return 0
    if tr is None:
        print("TRICERATOPS is not installed in this interpreter; run this tool with "
              ".venv-triceratops/Scripts/python.exe (see its docstring)", file=sys.stderr)
        return 2
    from importlib.metadata import version

    partial = partial_path(out)
    if partial.exists() and not a.resume:
        print(f"checkpoint exists; pass --resume or remove it ({partial})", file=sys.stderr)
        return 2
    if a.resume and partial.exists():
        try:
            held = checkpoint_runs(partial)
        except (OSError, ValueError) as e:
            print(f"cannot resume: {type(e).__name__}: {e}", file=sys.stderr)
            return 2
        if held > a.runs:
            print(f"the checkpoint {partial} holds {held} runs, more than --runs {a.runs}; pass --runs {held} or more "
                  "so none of them is lost", file=sys.stderr)
            return 2
    try:
        inputs = json.loads((inputs_dir / "inputs.json").read_text(encoding="utf-8"))
        lc = read_lightcurve(inputs_dir / inputs["lightcurve"]["file"])
        images = quarter_images([inputs_dir / f for f in inputs["tpf_files"]])
    except (OSError, ValueError, KeyError) as e:        # a missing or unreadable export file, a stamp without 1CRV4P
        print(f"the export in {inputs_dir} cannot be read: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    if not images:
        print("the export holds no pixel files, so there is no image to place the stars on", file=sys.stderr)
        return 2
    problems = export_problems(inputs)
    if problems and not a.allow_old_export:
        print(f"the export in {inputs_dir} lacks what the cross-check revision of 22 October 2026 added "
              f"({'; '.join(problems)}); re-export it with skysift crosscheck --export, or pass --allow-old-export "
              "to run it on the package's own values (the result then says so)", file=sys.stderr)
        return 2
    if a.runs < MIN_RUNS:
        print(f"{a.runs} runs: skysift crosscheck --import refuses a result of fewer than {MIN_RUNS} (ruling s), so "
              "this one is a smoke test only", file=sys.stderr)
    tgt, eph = inputs["target"], inputs["ephemeris"]
    out.parent.mkdir(parents=True, exist_ok=True)
    os.chdir(out.parent)                        # the constructor writes the field-star file in the working folder
    t = tr.target(ID=int(tgt.get("kic") or inputs["neighbour_kic"]), sectors=[], search_radius=SEARCH_RADIUS,
                  mission="Kepler", ra=tgt["ra"], dec=tgt["dec"])
    try:
        field_stars = sorted_population(getattr(t, "trilegal_fname", None), population_path(out))
        t.trilegal_fname = field_stars["file"]          # calc_probs reads it on every run: the sorted, own copy
    except ValueError as e:
        print(f"{e}: the package's Gaia DR3 query for the field-star population is the likely cause (an outage or a "
              "refusal), and without the population it would skip the DTP, DEB, DEBx2P, BTP, BEB and BEBx2P "
              "scenarios, so no run is started", file=sys.stderr)
        return 2
    first = t.stars.iloc[0]
    offset = math.hypot((first["ra"] - tgt["ra"]) * math.cos(math.radians(tgt["dec"])), first["dec"] - tgt["dec"])
    if offset * 3600 > 2.0:
        print(f"the TIC star nearest the target is {offset * 3600:.1f} arcsec away; not the same star",
              file=sys.stderr)
        return 1
    lacking = missing_stellar(first)
    if lacking:
        print(f"the TIC row of the target has no {', '.join(lacking)}; TRICERATOPS cannot weigh the scenarios "
              "without them, so no result is written", file=sys.stderr)
        return 1
    neighbours = inputs["neighbours"]
    matched, missing = match_neighbours(t.stars.iloc[1:].to_dict("records"), neighbours)
    added, factors, notes = {}, {}, []
    if problems:
        notes.append(f"The export lacks what the revision of 22 October 2026 added ({'; '.join(problems)}); it was "
                     "run with --allow-old-export, so the package's own values stand where it lacks them.")
    for n in usable_neighbours(neighbours)[1]:
        notes.append(f"Gaia DR3 {n.get('source_id')} is stored without a separation or G, so it was left out of the "
                     "match with the TIC list.")
    for n in missing:
        sid = str(n["source_id"])
        added[sid], factors[sid] = tmag_from_g(n["gmag"], n.get("bp_rp")), offset_box_factor(n["sep_arcsec"])
        t.add_star(ID=sid, Tmag=added[sid], bound=False)
        notes.append(f"Gaia DR3 {sid} ({n['sep_arcsec']:.1f} arcsec, G {n['gmag']:.2f}) is not in the TIC list; it "
                     f"was added with an estimated Tmag of {added[sid]:.2f} at the target's pixel position, since the "
                     "dossier stores no position for it, and its share of the contamination budget is weighted by "
                     f"the package's Gaussian box fraction at its stored separation along a pixel row (x "
                     f"{factors[sid]:.3f} of a star on the target).")
    if inputs.get("neighbour_params_note"):
        notes.append(f"{inputs['neighbour_params_note']}.")
    # the stellar parameters a TIC row lacks, and those of an added star, from the export (ruling q)
    by_source = {str(n.get("source_id")): n for n in neighbours}
    gaia_of = {m["tic_id"]: m["source_id"] for m in matched}
    params_source = {}
    for i, row in enumerate(t.stars.to_dict("records")):
        sid = str(row["ID"])
        gaia = sid if sid in added else gaia_of.get(sid)
        updates, sources = param_updates(row, by_source.get(gaia) if (gaia and i) else None, added=sid in added)
        for col, value in updates:
            t.update_star(sid, col, value)
        params_source[sid] = sources
        set_from = sorted({sources[c] for c, _v in updates})
        if updates:
            why = "the star is not in the TIC" if sid in added else "the TIC row lacks them"
            notes.append(f"{'Gaia DR3 ' + gaia if gaia else 'TIC ' + sid}: {', '.join(c for c, _v in updates)} set "
                         f"from {'; '.join(set_from)}, since {why}.")
    ra, dec = t.stars["ra"].to_numpy(float), t.stars["dec"].to_numpy(float)
    t.sectors = np.array([q["quarter"] for q in images])
    t.TESS_images = [q["image"] for q in images]
    t.col0s, t.row0s = [q["col0"] for q in images], [q["row0"] for q in images]
    t.pix_coords = [pixel_coords(q, ra, dec) for q in images]
    depth = float(eph["depth_ppm"]) / 1e6
    t.calc_depths(tdepth=depth, all_ap_pixels=[aperture_3x3(pc[0]) for pc in t.pix_coords], dilution_corrected=True)
    # the flux shares: the KBonus contamination budget in the Kepler band (rulings n and r)
    rows = t.stars.to_dict("records")
    gmags = []
    for i, row in enumerate(rows):
        sid = str(row["ID"])
        gaia = sid if sid in added else gaia_of.get(sid)
        g = tgt.get("gmag") if i == 0 else (by_source.get(gaia) or {}).get("gmag") if gaia else None
        gmags.append(_finite(g) if _finite(g) is not None else _finite(row.get("GAIAmag")))
    shares = leakage_shares(rows, tgt.get("crowdsap"), gmags, depth,
                            box_factors=[factors.get(str(r["ID"])) for r in rows])
    t.stars["fluxratio"] = [r["fluxratio"] for r in shares["rows"]]
    t.stars["tdepth"] = [r["tdepth"] for r in shares["rows"]]
    notes.append(leakage_caveat(shares, tgt.get("crowdsap")))
    for text in (leak_caveat(inputs.get("leak")), clip_caveat(inputs["lightcurve"])):
        if text:
            notes.append(text)
    kp, g_t = _finite(tgt.get("kepmag")), _finite(tgt.get("gmag"))
    if kp is None:
        notes.append("The target's KIC Kp is not stored, so the Gaia G proxy for the Kepler band is not checked "
                     "against it.")
    period, t0 = float(eph["period_days"]), float(eph["t0_bkjd"])
    phase = (lc["time"] - t0 + period / 2) % period - period / 2
    flux_err, which = robust_flux_err(lc["time"], lc["flux"], period, t0, float(eph["duration_h"]) / 24.0)
    pipeline_err = float(np.median(lc["err"]))
    notes.append(flux_err_caveat(flux_err, pipeline_err, which))

    def probs(seed: int, n_draws: int, parallel: bool):
        np.random.seed(seed)
        t.calc_probs(time=phase, flux_0=lc["flux"], flux_err_0=flux_err, P_orb=period, N=n_draws,
                     parallel=parallel, exptime=EXPTIME_DAYS, dilution_corrected=True, verbose=0)

    tool_version = version("triceratops")
    key = checkpoint_key(inputs["fingerprint"], seed=a.seed, draws=a.draws, check_draws=a.check_draws,
                         parallel_check=a.parallel_check, flux_err=flux_err, shares=shares,
                         quarters=[q["quarter"] for q in images], allow_old_export=a.allow_old_export,
                         version=tool_version, field_stars=field_stars,
                         stars_sha256=stars_digest(t.stars.to_dict("records")),
                         lightcurve_sha256=file_sha256(inputs_dir / inputs["lightcurve"]["file"]),
                         inputs_json_sha256=file_sha256(inputs_dir / "inputs.json"))
    state, resume_note = new_state(), None
    if a.resume and not partial.exists():
        resume_note = "--resume was given but no checkpoint existed, so the run started fresh"
        print(resume_note, file=sys.stderr)
    elif a.resume:
        try:
            state = resume_state(partial, key)
        except (OSError, ValueError) as e:
            print(f"cannot resume: {type(e).__name__}: {e}", file=sys.stderr)
            return 2
        state["resumes"].append(len(state["runs"]) + 1)
        print(f"resuming from {partial} at run {len(state['runs']) + 1} (seed {a.seed + len(state['runs'])})",
              file=sys.stderr)
    if a.parallel_check and state["check"] is None and not state["runs"] and state["failure"] is None:
        answers = {}
        for mode in (False, True):
            started = clock.monotonic()
            try:
                probs(a.seed, a.check_draws, mode)
            except Exception as e:  # the parallel path failing is reported, then the serial loop is used
                if not mode:
                    raise
                state["failure"], state["parallel"] = f"{type(e).__name__}: {e}", False
                break
            answers[mode] = {"fpp": float(t.FPP), "nfpp": float(t.NFPP)}
            print(f"check seed {a.seed} at {a.check_draws:,} draws, {'parallel' if mode else 'serial'}: FPP "
                  f"{t.FPP:.4g}, NFPP {t.NFPP:.4g} ({clock.monotonic() - started:.1f} s)", file=sys.stderr)
        if len(answers) == 2:
            seed_nfpps = [answers[True]["nfpp"]]
            for extra in range(1, CHECK_SEEDS):          # the Monte Carlo noise of the NFPP at this draw count
                probs(a.seed + extra, a.check_draws, True)
                seed_nfpps.append(float(t.NFPP))
                print(f"check seed {a.seed + extra} at {a.check_draws:,} draws, parallel: NFPP {t.NFPP:.4g}",
                      file=sys.stderr)
            state["check"] = parallel_check_record(a.seed, a.check_draws, answers[False], answers[True], seed_nfpps)
        write_partial(partial, key, state)
    runs = state["runs"]
    for k in range(len(runs), a.runs):
        seed, started = a.seed + k, clock.monotonic()
        try:
            probs(seed, a.draws, state["parallel"])
        except Exception as e:  # as above: the parallel path failing switches to the serial loop
            if not state["parallel"]:
                raise
            state["failure"], state["parallel"] = f"{type(e).__name__}: {e}", False
            probs(seed, a.draws, False)
        runs.append(run_record(seed, t.FPP, t.NFPP, t.FPP_degenerate, _probs(t)))
        write_partial(partial, key, state)
        print(f"run {k + 1}/{a.runs} (seed {seed}, {'parallel' if state['parallel'] else 'serial'}): FPP "
              f"{t.FPP:.3g}, NFPP {t.NFPP:.3g} ({clock.monotonic() - started:.1f} s)", file=sys.stderr)
    check, failure, parallel = state["check"], state["failure"], state["parallel"]
    if check:
        notes.append(f"The serial loop against the parallel path: {check['comparison']}")
    if failure:
        notes.append(f"The package's parallel path failed ({failure}), so the serial loop was used from then on.")
    first_row = t.stars.iloc[0]
    star = inputs.get("star") or {}
    settings = {"N": a.draws, "exptime_days": EXPTIME_DAYS, "search_radius_pix": SEARCH_RADIUS,
                "aperture": "3 x 3 around the target", "dilution_corrected": True, "parallel": parallel,
                "parallel_failure": failure, "parallel_check": check, "mission": "Kepler",
                "limb_darkening": "the package's Kepler table", "field_star_population": "Gaia DR3 (package default)",
                "field_stars": field_stars, "resume_note": resume_note,
                "quarters": [q["quarter"] for q in images], "matched_neighbours": matched, "notes": notes,
                "flux_err_rel_robust": flux_err, "flux_err_rel_pipeline": pipeline_err, "flux_err_source": which,
                "flux_shares": {k: shares[k] for k in ("source", "budget", "contaminating_fraction", "crowdsap")},
                "allow_old_export": a.allow_old_export, "export_problems": problems,
                "resumed_at_runs": state["resumes"],
                "psffrac": _finite(tgt.get("psffrac")), "leak": inputs.get("leak"),
                "lightcurve_clipped": inputs["lightcurve"].get("clipped"),
                "kepmag_target": kp, "g_minus_kp_target": None if kp is None or g_t is None else g_t - kp,
                "target_params": {"used": {c: _finite(first_row.get(c)) for c in ("Teff", "rad", "mass", "plx")},
                                  "source": params_source.get(str(first_row["ID"])),
                                  "skysift": {k: (star.get(k) or {}).get("median") for k in ("teff", "radius",
                                                                                             "mass")}}}
    stars = star_rows(t.stars.to_dict("records"), matched, added, shares, gmags, params_source)
    result = assemble_result(runs, stars, settings, inputs, tool_version)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(result, indent=1, allow_nan=False), encoding="utf-8")
    os.replace(tmp, out)
    partial.unlink(missing_ok=True)
    fpp, nfpp = result["fpp"], result["nfpp"]
    print(f"median FPP {fpp if fpp is None else f'{fpp:.3g}'}, NFPP {nfpp if nfpp is None else f'{nfpp:.3g}'} over "
          f"{result['n_runs'] - result['n_degenerate']} runs; wrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
