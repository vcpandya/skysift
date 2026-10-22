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
three durations of each transit) and ``tpf/``, copies of the stored pixel files of the neighbour KIC whose stamps hold
the star.

Construction route. ``triceratops.target`` is built with ``mission="Kepler"``, the target's RA and Dec (so the
package finds the TIC id by position and skips the KIC lookup) and ``sectors=[]``, so its per-quarter loop, which
would download each quarter's pixel file through lightkurve by KIC id (and fails for a star Kepler never targeted),
never runs. The constructor still asks MAST for the TIC stars within 10 Kepler pixels and Gaia for the field-star
population (it writes ``<ID>_gaia_background.csv``, so the tool works in the ``--out`` folder). The tool then sets
``sectors``, ``TESS_images``, ``col0s``, ``row0s`` and ``pix_coords`` itself from the stored pixel files
(``quarter_images``, ``pixel_coords``), with the package's own arithmetic: the time-mean flux image padded with NaN to
22 x 22, ``col0 = 1CRV4P - n_cols_before`` and each star at ``col0 + WCS column + n_cols_before``, the WCS from the
APERTURE header. The aperture per quarter is the 3 x 3 box around the target (the KBonus PSF photometry's footprint);
``calc_depths`` then ``calc_probs(exptime=0.0204, N=1_000_000, parallel=False, dilution_corrected=True)`` run
``--runs`` times with seeds ``--seed`` to ``--seed + runs - 1``.

The TIC stars are cross-matched with the dossier's Gaia neighbours by separation within 1 arcsec
(``match_neighbours``); a neighbour the TIC lacks is added with ``add_star`` and a Tmag estimated from G
(``tmag_from_g``) at the target's pixel position, as the package places an added star, since the dossier stores its
separation but no position; the result's caveats name it.

The helpers above the guarded ``import triceratops`` need numpy and astropy only, so the engine's tests import them.

    python -I tools/triceratops_check.py --inputs <export dir> --out <result.json> [--runs 10] [--seed 1]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import warnings
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS, FITSFixedWarning

TOOL = "triceratops"                # skysift.crosscheck.TOOL (the engine is not importable from this interpreter)
SEARCH_RADIUS = 10                  # Kepler pixels, the package's default
N_PIX = 2 * SEARCH_RADIUS + 2       # the padded image side, as the package builds it
EXPTIME_DAYS = 0.0204               # Kepler long cadence
N_DRAWS = 1_000_000
CAVEATS = (
    ("TRICERATOPS shares the light in the aperture by TIC TESS magnitudes (Tmag), not Kepler magnitudes; its limb "
     "darkening comes from its Kepler table."),
    "The light in the aperture is modelled as a Gaussian PSF of 0.75 pixels over the 3 x 3 box around the target.",
    "The priors and the field-star population are the package's own, not SkySift's.",
    "The result varies from run to run, so the median of the runs is quoted and every run is kept.",
)


def _finite(x) -> float | None:
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
    raises ValueError, since the stars could not be placed), "pad" (columns and rows of padding before the stamp) and "wcs" (from the APERTURE header)."""
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


def read_lightcurve(path: Path) -> dict[str, np.ndarray]:
    table = np.loadtxt(path, delimiter=",", skiprows=1, ndmin=2)
    return {"time": table[:, 0], "flux": table[:, 1], "err": table[:, 2], "quarter": table[:, 3].astype(int)}


def star_rows(stars: list[dict], matched: list[dict], added: dict[str, float]) -> list[dict]:
    """The stars the run used ({"id", "tmag", "sep_arcsec", "flux_ratio", "tdepth", "gaia_source_id",
    "tmag_estimated"}) from TRICERATOPS's ``stars`` rows after ``calc_depths``."""
    gaia_of = {m["tic_id"]: m["source_id"] for m in matched}

    def num(x):
        return None if x is None or not np.isfinite(float(x)) else float(x)
    return [{"id": str(s["ID"]), "tmag": num(s.get("Tmag")), "sep_arcsec": num(s.get("sep (arcsec)")),
             "flux_ratio": num(s.get("fluxratio")), "tdepth": num(s.get("tdepth")),
             "gaia_source_id": str(s["ID"]) if str(s["ID"]) in added else gaia_of.get(str(s["ID"])),
             "tmag_estimated": str(s["ID"]) in added} for s in stars]


def assemble_result(runs: list[dict], stars: list[dict], settings: dict, inputs: dict, version: str) -> dict:
    """The result file: the median FPP and NFPP over ``runs`` (each {"seed", "fpp", "nfpp", "degenerate",
    "scenarios"}) with every run's values, the scenario table of the run nearest the median FPP, the stars, the
    inputs' fingerprint, the caveats and the settings (with the seeds), keyed as ``skysift.crosscheck.RESULT_KEYS``."""
    fpps, nfpps = [float(r["fpp"]) for r in runs], [float(r["nfpp"]) for r in runs]
    fpp = statistics.median(fpps)
    nearest = min(range(len(runs)), key=lambda i: abs(fpps[i] - fpp))
    caveats = list(CAVEATS) + list(settings.get("notes") or [])
    degenerate = sum(1 for r in runs if r.get("degenerate"))
    if degenerate:
        caveats.append(f"{degenerate} of {len(runs)} runs had degenerate scenario evidences (FPP_degenerate).")
    return {"tool": TOOL, "tool_version": version, "run_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "n_runs": len(runs), "fpp": fpp, "nfpp": statistics.median(nfpps), "fpp_runs": fpps, "nfpp_runs": nfpps,
            "scenarios": runs[nearest]["scenarios"], "stars": stars, "inputs_sha256": inputs["fingerprint"],
            "caveats": caveats, "settings": {**{k: v for k, v in settings.items() if k != "notes"},
                                             "seeds": [r["seed"] for r in runs],
                                             "median_run_seed": runs[nearest]["seed"]}}


try:                                # only under .venv-triceratops; the helpers above work without it
    import triceratops.triceratops as tr
except ImportError:
    tr = None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--inputs", required=True, help="the folder skysift crosscheck --export wrote")
    ap.add_argument("--out", required=True, help="the result file to write")
    ap.add_argument("--runs", type=int, default=10, help="runs with consecutive seeds (default 10)")
    ap.add_argument("--seed", type=int, default=1, help="the first run's seed (default 1)")
    a = ap.parse_args(argv)
    if tr is None:
        print("TRICERATOPS is not installed in this interpreter; run this tool with "
              ".venv-triceratops/Scripts/python.exe (see its docstring)", file=sys.stderr)
        return 2
    from importlib.metadata import version

    inputs_dir, out = Path(a.inputs).resolve(), Path(a.out).resolve()
    inputs = json.loads((inputs_dir / "inputs.json").read_text(encoding="utf-8"))
    lc = read_lightcurve(inputs_dir / inputs["lightcurve"]["file"])
    images = quarter_images([inputs_dir / f for f in inputs["tpf_files"]])
    if not images:
        print("the export holds no pixel files, so there is no image to place the stars on", file=sys.stderr)
        return 2
    tgt, eph = inputs["target"], inputs["ephemeris"]
    out.parent.mkdir(parents=True, exist_ok=True)
    os.chdir(out.parent)                        # the constructor writes the field-star file in the working folder
    t = tr.target(ID=int(tgt.get("kic") or inputs["neighbour_kic"]), sectors=[], search_radius=SEARCH_RADIUS,
                  mission="Kepler", ra=tgt["ra"], dec=tgt["dec"])
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
    matched, missing = match_neighbours(t.stars.iloc[1:].to_dict("records"), inputs["neighbours"])
    added, notes = {}, []
    for n in usable_neighbours(inputs["neighbours"])[1]:
        notes.append(f"Gaia DR3 {n.get('source_id')} is stored without a separation or G, so it was left out of the "
                     "match with the TIC list.")
    for n in missing:
        added[str(n["source_id"])] = tmag_from_g(n["gmag"], n.get("bp_rp"))
        t.add_star(ID=str(n["source_id"]), Tmag=added[str(n["source_id"])], bound=False)
        notes.append(f"Gaia DR3 {n['source_id']} ({n['sep_arcsec']:.1f} arcsec, G {n['gmag']:.2f}) is not in the "
                     f"TIC list; it was added with an estimated Tmag of {added[str(n['source_id'])]:.2f} at the "
                     "target's pixel position, since the dossier stores no position for it.")
    ra, dec = t.stars["ra"].to_numpy(float), t.stars["dec"].to_numpy(float)
    t.sectors = np.array([q["quarter"] for q in images])
    t.TESS_images = [q["image"] for q in images]
    t.col0s, t.row0s = [q["col0"] for q in images], [q["row0"] for q in images]
    t.pix_coords = [pixel_coords(q, ra, dec) for q in images]
    t.calc_depths(tdepth=float(eph["depth_ppm"]) / 1e6, all_ap_pixels=[aperture_3x3(pc[0]) for pc in t.pix_coords],
                  dilution_corrected=True)
    period, t0 = float(eph["period_days"]), float(eph["t0_bkjd"])
    phase = (lc["time"] - t0 + period / 2) % period - period / 2
    runs = []
    for k in range(a.runs):
        np.random.seed(a.seed + k)
        t.calc_probs(time=phase, flux_0=lc["flux"], flux_err_0=float(np.median(lc["err"])), P_orb=period,
                     N=N_DRAWS, parallel=False, exptime=EXPTIME_DAYS, dilution_corrected=True, verbose=0)
        runs.append({"seed": a.seed + k, "fpp": float(t.FPP), "nfpp": float(t.NFPP),
                     "degenerate": bool(t.FPP_degenerate),
                     "scenarios": json.loads(t.probs[["ID", "scenario", "prob"]].to_json(orient="records"))})
        print(f"run {k + 1}/{a.runs} (seed {a.seed + k}): FPP {t.FPP:.3g}, NFPP {t.NFPP:.3g}", file=sys.stderr)
    settings = {"N": N_DRAWS, "exptime_days": EXPTIME_DAYS, "search_radius_pix": SEARCH_RADIUS,
                "aperture": "3 x 3 around the target", "dilution_corrected": True, "parallel": False,
                "quarters": [q["quarter"] for q in images], "matched_neighbours": matched, "notes": notes}
    stars = star_rows(t.stars.to_dict("records"), matched, added)
    result = assemble_result(runs, stars, settings, inputs, version("triceratops"))
    out.write_text(json.dumps(result, indent=1, allow_nan=False), encoding="utf-8")
    print(f"median FPP {result['fpp']:.3g}, NFPP {result['nfpp']:.3g}; wrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
