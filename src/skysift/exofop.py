"""ExoFOP's pipe-delimited bulk parameter files of a submission pack (moved out of ``skysift.pack``): the planet
and stellar templates with the column lists of the live template files, filled from the dossier JSON."""
from __future__ import annotations

import re

from skysift.pack_rows import logg_value, provenance_rows, star_characterized
from skysift.pack_text import _bjd, _errors, _num, _pm_parts

# ExoFOP's bulk templates (ExoFOP help, read 22 October 2026), pipe-delimited, one row per target
EXOFOP_PLANET_TEMPLATE = "params_planet_{date}_001.txt"
EXOFOP_STELLAR_TEMPLATE = "params_stellar_{date}_001.txt"
# (the referee checklist, section 4: the column lists of the live template files, 22 October 2026)
EXOFOP_PLANET_COLUMNS = ("target", "flag", "disp", "discovery", "candname", "period", "period_unc", "epoch",
                         "epoch_unc", "depth", "depth_unc", "duration", "duration_unc", "inc", "inc_unc", "imp",
                         "imp_unc", "r_planet", "r_planet_err", "ar_star", "ar_star_err", "radius", "radius_unc",
                         "mass", "mass_unc", "temp", "temp_unc", "insol", "insol_unc", "dens", "dens_unc", "sma",
                         "sma_unc", "ecc", "ecc_unc", "arg_peri", "arg_peri_err", "time_peri", "time_peri_unc", "vsa",
                         "vsa_unc", "tag", "group", "prop_period", "paper", "notes")
EXOFOP_STELLAR_COLUMNS = ("target", "tel", "telsize", "inst", "sp_type", "teff", "teff_unc", "logg", "logg_unc",
                          "radius", "radius_unc", "logrhk", "logrhk_unc", "sindex", "sindex_unc", "halpha",
                          "halpha_unc", "vsini", "vsini_unc", "rotper", "rotper_unc", "met", "met_unc", "mass",
                          "mass_unc", "dens", "dens_unc", "lum", "lum_unc", "obs_time", "rv", "rv_unc", "dist",
                          "dist_unc", "age", "age_unc", "snr", "fit", "tag", "group", "prop_period", "notes")
EXOFOP_DISCOVERY = "SkySift (Kepler)"      # a blank discovery means TESS, so a Kepler candidate names its project
EXOFOP_NOTES_MAX = 120
TIC_PLACEHOLDER = "<TIC-TO-FILL>"


def data_tag(date: str, tag: str, target: str) -> str:
    """The suggested ExoFOP Data Tag, ``YYYYMMDD_username_description``, with the username to fill in; ``target`` is
    the pack's checked target name (``pack._safe_target``)."""
    return f"{date}_<username>_{tag}-{target}"


def _cell(value) -> str:
    """One field of an ExoFOP bulk file: empty for a missing value, never a pipe or a line break."""
    return "" if value is None else re.sub(r"[|\r\n]+", " ", str(value)).strip()


def _pipe_file(columns: tuple[str, ...], values: dict) -> str:
    """ExoFOP's pipe-delimited bulk layout: a comment line naming the columns, then the one row."""
    return "# " + "|".join(columns) + "\n" + "|".join(_cell(values.get(c)) for c in columns) + "\n"


def _plain(value, minus=None, plus=None, nd: int = 2) -> tuple[str | None, str | None]:
    """(value, its one error) for a bulk file: no thousands separator, the larger side of an asymmetric error."""
    lo, hi = _num(minus), _num(plus)
    err = max(e for e in (lo, hi) if e is not None) if (lo is not None or hi is not None) else None
    parts = _pm_parts(value, err, err, nd)
    return (parts[0], parts[2]) if parts else (None, None)


def _pair(values: dict, name: str, unc: str, block, nd: int, errors_only: bool = False) -> None:
    """A stored value dict into ``values[name]`` and its larger half-width into ``values[unc]``; with
    ``errors_only`` neither unless the block carries both errors. An error never stands without its value."""
    if not isinstance(block, dict):
        return
    lo, hi = _num(block.get("minus")), _num(block.get("plus"))
    if errors_only and (lo is None or hi is None):
        return
    values[name], values[unc] = _plain(block.get("median"), lo, hi, nd)


def _candname(data: dict) -> str:
    """The SkySift name of the signal: ``SkySift-<target id>-s<signal id>``."""
    target, sig = data.get("target") or {}, data.get("signal") or {}
    return f"SkySift-{target.get('target_id') or 'target'}-s{sig.get('id')}"


def render_exofop_planet(data: dict, date: str, tic: str | None, tag: str) -> str:
    """``exofop/params_planet_<date>_001.txt``, ExoFOP's planet template (``EXOFOP_PLANET_COLUMNS``): the target
    ``TIC<tic>.01`` (``TIC<TIC-TO-FILL>.01`` without a TIC id), flag ``newctoi`` with its discovery and candidate
    name, disposition ``PC``, the ephemeris with its errors (epoch in full BJD_TDB), Rp/R*, a/R* and b only when the
    bootstrap gives them errors, the planet's radius, temperature, insolation and orbit with theirs, ``prop_period``
    0 and the paper URL and notes as placeholders. Every uncertainty is the larger half-width; empty fields are what
    SkySift does not have."""
    eph, planet, shape = data.get("ephemeris") or {}, data.get("planet") or {}, data.get("shape") or {}
    e = _errors(eph)
    period = _plain(eph.get("period_days"), e["period"], e["period"], 5)
    epoch = _plain(_bjd(eph, "t0"), e["t0"], e["t0"], 4)
    depth = _plain(eph.get("depth_ppm"), e["depth"], e["depth"], 0)
    duration = _plain(eph.get("duration_h"), e["duration_minus"], e["duration_plus"], 2)
    values = {"target": f"TIC{tic or TIC_PLACEHOLDER}.01", "flag": "newctoi", "disp": "PC",
              "discovery": EXOFOP_DISCOVERY, "candname": _candname(data),
              "period": period[0], "period_unc": period[1], "epoch": epoch[0], "epoch_unc": epoch[1],
              "depth": depth[0], "depth_unc": depth[1], "duration": duration[0], "duration_unc": duration[1],
              "tag": tag, "prop_period": 0, "paper": "<PAPER-URL-TO-FILL>", "notes": "<NOTES-TO-FILL>"}
    if shape.get("ok") is True:
        for name, unc, key, nd in (("imp", "imp_unc", "b", 2), ("r_planet", "r_planet_err", "k", 4),
                                   ("ar_star", "ar_star_err", "a_over_r", 1)):
            _pair(values, name, unc, shape.get(key), nd, errors_only=True)
    for name, unc, key, nd in (("radius", "radius_unc", "rp_rearth", 2), ("temp", "temp_unc", "teq_k", 0),
                               ("insol", "insol_unc", "insolation_earth", 2), ("sma", "sma_unc", "a_au", 4)):
        _pair(values, name, unc, planet.get(key), nd)
    return _pipe_file(EXOFOP_PLANET_COLUMNS, values)


def stellar_note(data: dict) -> str:
    """The stellar file's note, at most ``EXOFOP_NOTES_MAX`` characters: each parameter's method from
    ``provenance_rows`` (cut before its numbers), parameters with one method named together, in the order Teff,
    radius, mass, distance, density; a method that would pass the limit is left out."""
    short = {"effective temperature": "Teff", "radius": "R", "mass": "M", "distance": "dist", "density": "density"}
    methods = {r["parameter"]: r["method"].split(" at ")[0].split(",")[0].strip() for r in provenance_rows(data)
               if r["parameter"] in short and r["method"]}
    groups: dict[str, list[str]] = {}
    for parameter, name in short.items():
        if parameter in methods:
            groups.setdefault(methods[parameter], []).append(name)
    note = ""
    for method, names in groups.items():
        part = f"{', '.join(names)}: {method}"
        if len(f"{note}; {part}" if note else part) <= EXOFOP_NOTES_MAX:
            note = f"{note}; {part}" if note else part
    return note


def render_exofop_stellar(data: dict, date: str, tic: str | None, tag: str) -> str | None:
    """``exofop/params_stellar_<date>_001.txt``, ExoFOP's stellar template (``EXOFOP_STELLAR_COLUMNS``): the target
    ``KIC<kic>`` (an accepted prefix, so no TIC is needed; ``TIC<tic>`` or the placeholder without a KIC), the
    spectral type, Teff, log g, radius, mass, density and distance with their uncertainties (the larger half-width),
    ``prop_period`` 0 and a note naming each parameter's method (``stellar_note``). None when the star has no
    parameters (``star_characterized``), so no file is written."""
    if not star_characterized(data):
        return None
    star, kic = data.get("star") or {}, (data.get("target") or {}).get("kic")
    values = {"target": f"KIC{int(kic)}" if kic else f"TIC{tic or TIC_PLACEHOLDER}",
              "sp_type": star.get("spectral_type"), "tag": tag, "prop_period": 0, "notes": stellar_note(data)}
    for name, block, nd in (("teff", star.get("teff"), 0), ("logg", logg_value(star), 3),
                            ("radius", star.get("radius"), 3), ("mass", star.get("mass"), 3),
                            ("dens", star.get("density_gcc"), 2), ("dist", star.get("distance_pc"), 0)):
        _pair(values, name, f"{name}_unc", block, nd)
    return _pipe_file(EXOFOP_STELLAR_COLUMNS, values)
