"""The submission pack of a transit candidate (dossiers spec 5.9, ``skysift pack``).

No portal accepts a new Kepler candidate from outside a mission team, so the pack holds what each route needs, in the
order its README gives them: 1 freeze the record on Zenodo, 2 upload to ExoFOP-TESS, 3 write a Research Note of the
AAS, 4 tell the NASA Exoplanet Archive, 5 answer follow-up requests. ``build_pack`` writes one folder and a zip of it
from the target's stored dossier (``dossier_data.json``) and the light curve the loader serves:

- ``README.md`` (``render_readme``): the five steps, each with the account it needs, the files to attach, the text to
  write and what the engine did or cannot do; an unapproved candidate gets a warning as its first line.
- ``note/note.tex`` (``render_note``): an AASTeX Research Note draft under 1,000 words, with ``figure1``.
- ``data/``: the whitened light curve, the ephemeris and parameters with errors, the per-event depths, the periodogram
  summary and the rung metrics. ``figures/``: fold, odd and even, periodogram and one centroid panel per season, as
  SVG, plus PNG when matplotlib imports. ``dossier.html``: the standalone export. ``provenance/``: versions, the query
  log, the calibration counts and the SHA-256 of every file. ``exofop/``: copies under ExoFOP's naming rule and the
  upload note. ``archive_email.txt`` and ``zenodo_metadata.json`` for steps 4 and 1.

Building a pack never changes a status, never regenerates the dossier and never downloads anything: a light curve
released by retention raises ``dossier.LightCurveReleased``. The only network use is the injected Zenodo client, which
makes a draft and never publishes. Both probabilities appear separately in every text, the false-positive one marked
provisional while its factor tables are not signed off."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import platform
import re
import shutil
import sys
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from skysift import calibrate as cal
from skysift import dossier as ds
from skysift import jsonutil
from skysift.lc.loader import LightCurveLoader
from skysift.store.db import Store
from skysift.zenodo import KBONUS_DOI, ZenodoClient, metadata_from, probability_sentences

PACK_VERSION = "1"
# the five steps, in the README's order; the dashboard's step list (ui.routes.dossiers.PACK_STEPS) is built from these
STEPS = (("1", "Freeze the record"), ("2", "Upload to ExoFOP-TESS"), ("3", "Write it up"),
         ("4", "Tell the archive"), ("5", "Answer follow-up questions"))
STEP_TEXTS = {
    "1": "Deposit the pack on Zenodo for a citable DOI. The engine can make the draft; you publish it.",
    "2": "Attach the dossier, the ephemeris and the note under the star's TIC id. This needs your own ExoFOP account.",
    "3": "Turn the Research Note draft into a submission to the AAS, with an arXiv endorsement if needed.",
    "4": "Send the NASA Exoplanet Archive help desk the one-paragraph note once it is published.",
    "5": "Reply to observers who ask for the ephemeris or the data, from the pack's own files.",
}
DOI_PLACEHOLDER = "10.5281/zenodo.XXXXXXX"
RNAAS_DOI_PLACEHOLDER = "10.3847/2515-5172/XXXXX"
NOTE_WORD_LIMIT = 1000
TEMPLATES = Path(__file__).parent / "templates" / "pack"
AASTEX_FRAME = Path(__file__).parent / "data" / "rnaas" / "aastex_template.tex"
CITATION_FILE = Path(__file__).resolve().parents[2] / "CITATION.cff"
MARKER = "provenance/versions.json"         # a folder holding this file is a pack and may be rebuilt in place
EXOFOP_URL = "https://exofop.ipac.caltech.edu/tess/"
HELPDESK_URL = "https://exoplanetarchive.ipac.caltech.edu/cgi-bin/Helpdesk/nph-genTicket"
_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,31}")
_CONTENT = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
_EXT = re.compile(r"[a-z0-9]{1,8}")

# figure colours: fixed hex values, the figures are files read outside the dashboard
INK, MUTED, GRID, POINT, MODEL, EVEN, DOTS = "#111827", "#6b7280", "#e5e7eb", "#1f5f8b", "#c2410c", "#b45309", "#9ca3af"
FONT = "IBM Plex Sans, Helvetica, Arial, sans-serif"


class NoDossier(LookupError):
    """The target has no generated dossier (or its data file is gone): the pack is built from it."""

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else "no dossier"


class Raw(str):
    """Text that is already LaTeX: the note's template prints it as it is."""


@dataclass
class PackReport:
    target_id: str
    out_dir: Path
    zip_path: Path
    files: list[str]                # paths relative to out_dir, sorted
    approved: bool
    zenodo: dict | None             # {"deposition_id", "draft_url", "sandbox", "doi"} when a draft was made
    stale_reason: str | None = None

    def as_dict(self) -> dict:
        return jsonutil.sanitize({"target_id": self.target_id, "out_dir": str(self.out_dir),
                                  "zip_path": str(self.zip_path), "files": self.files, "approved": self.approved,
                                  "zenodo": self.zenodo, "stale_reason": self.stale_reason})


# ---------------------------------------------------------------------------------------------------- formatting

def _num(x) -> float | None:
    if isinstance(x, bool) or x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _decimals(err: float | None, default: int = 2) -> int:
    """Decimals that show an error to one significant figure, two when it starts with 1 or 2 (0.016, 0.08, 73)."""
    if err is None or err <= 0:
        return default
    lead = err / 10 ** math.floor(math.log10(err))
    return max(0, -math.floor(math.log10(err)) + (1 if lead < 3 else 0))


def _f(x, nd: int = 2, comma: bool = False) -> str | None:
    x = _num(x)
    if x is None:
        return None
    return f"{x:,.{nd}f}" if comma else f"{x:.{nd}f}"


def value_text(v: dict | None, tex: bool = False) -> str | None:
    """A stored ``{"median", "minus", "plus"}`` as "0.509 +0.017/-0.016" (or "2.61 ± 0.08" when symmetric), or as
    LaTeX "0.509$^{+0.017}_{-0.016}$" with ``tex``; None without a median."""
    if not isinstance(v, dict) or _num(v.get("median")) is None:
        return None
    m, lo, hi = _num(v.get("median")), _num(v.get("minus")), _num(v.get("plus"))
    errs = [e for e in (lo, hi) if e is not None and e > 0]
    nd = _decimals(min(errs)) if errs else 2
    mid = f"{m:,.{nd}f}"                    # 3,653 K, thousands as the dossier prints them
    if not errs:
        return Raw(mid) if tex else mid
    lo, hi = lo or 0.0, hi or 0.0
    if f"{lo:.{nd}f}" == f"{hi:.{nd}f}":
        return Raw(f"{mid} $\\pm$ {hi:,.{nd}f}") if tex else f"{mid} ± {hi:,.{nd}f}"
    if tex:
        return Raw(f"{mid}$^{{+{hi:,.{nd}f}}}_{{-{lo:,.{nd}f}}}$")
    return f"{mid} +{hi:,.{nd}f}/-{lo:,.{nd}f}"


_TEX = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{",
        "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}", "±": r"$\pm$", "<": r"$<$", ">": r"$>$",
        "≤": r"$\leq$", "≥": r"$\geq$", "×": r"$\times$", "°": r"$^\circ$", "−": "-", "–": "--", "—": ", "}
_TEX_RE = re.compile("|".join(re.escape(k) for k in _TEX))


def tex_escape(text: str) -> str:
    return _TEX_RE.sub(lambda m: _TEX[m.group(0)], str(text))


def _tex_finalize(value):
    if value is None:
        return ""
    if isinstance(value, Raw):
        return value
    return tex_escape(value) if isinstance(value, str) else tex_escape(str(value))


def _md_finalize(value):
    return "" if value is None else value


def _env(tex: bool) -> Environment:
    common = {"loader": FileSystemLoader(str(TEMPLATES)), "trim_blocks": True, "lstrip_blocks": True,
              "keep_trailing_newline": True, "undefined": StrictUndefined, "autoescape": False}
    if tex:
        return Environment(block_start_string=r"\BLOCK{", block_end_string="}", variable_start_string=r"\VAR{",
                           variable_end_string="}", comment_start_string="((#", comment_end_string="#))",
                           finalize=_tex_finalize, **common)
    return Environment(finalize=_md_finalize, **common)


def date_words(when) -> str:
    if isinstance(when, str):
        try:
            when = datetime.fromisoformat(when)
        except ValueError:
            return when
    return f"{when.day} {when:%B %Y}" if isinstance(when, datetime) else "an unknown date"


def note_words(tex: str) -> int:
    """Words of the note after ``\\begin{document}``, LaTeX commands with their first argument left out (the count
    the RNAAS limit is checked with)."""
    body = tex.split(r"\begin{document}", 1)[-1]
    return len(re.sub(r"\\[a-zA-Z]+(\[[^\]]*\])?(\{[^}]*\})?", " ", body).split())


# ---------------------------------------------------------------------------------------------------- the numbers

def _metrics(data: dict, rung: str) -> dict:
    return next((t.get("metrics") or {} for t in data.get("tests") or [] if t.get("rung") == rung), {})


def _test(data: dict, rung: str) -> dict | None:
    return next((t for t in data.get("tests") or [] if t.get("rung") == rung), None)


def _fpp_class(data: dict) -> str | None:
    for s in (data.get("fpp") or {}).get("scenarios") or []:
        if s.get("name") == "PL":
            return (s.get("factors") or {}).get("class")
    return None


def references(data: dict) -> dict[str, bool]:
    """Which works the dossier's numbers rest on, so the note cites exactly those."""
    star, planet = data.get("star") or {}, data.get("planet") or {}
    route = star.get("route") or "none"
    star_ok = route != "none" and value_text(star.get("radius")) is not None      # as numbers()["star_ok"]
    cls = _fpp_class(data)
    notes = json.dumps((data.get("fpp") or {}).get("scenarios") or [])
    return {"kbonus": True, "morton2012": True,
            "dressing2015": cls == "M", "fressin2013": cls != "M" or "Fressin" in notes,
            "pecaut2013": star_ok and route in ("mann", "mamajek"), "mann2015": star_ok and route == "mann",
            "mann2019": star_ok and route == "mann",
            "lindegren2021": star_ok and star.get("distance_pc") is not None,
            "kopparapu2013": star_ok and _hz_where(planet) is not None}


BIBLIOGRAPHY = (
    ("dressing2015", r"{Dressing} \& {Charbonneau}(2015)",
     r"Dressing, C.~D., \& Charbonneau, D. 2015, ApJ, 807, 45"),
    ("fressin2013", r"{Fressin} et~al.(2013)", r"Fressin, F., Torres, G., Charbonneau, D., et~al. 2013, ApJ, 766, 81"),
    ("kbonus", r"{Mart{\'\i}nez-Palomera} et~al.(2023)",
     (r"Mart{\'\i}nez-Palomera, J., et~al. 2023, KBonus Kepler Background high-level science product, MAST, "
      r"\dataset[doi:10.17909/7jbr-w430]{https://doi.org/10.17909/7jbr-w430}")),
    ("kopparapu2013", r"{Kopparapu} et~al.(2013)",
     (r"Kopparapu, R.~K., Ramirez, R., Kasting, J.~F., et~al. 2013, ApJ, 765, 131 (erratum: 2013, ApJ, 770, 82, "
      r"whose coefficients are used)")),
    ("lindegren2021", r"{Lindegren} et~al.(2021)",
     r"Lindegren, L., Bastian, U., Biermann, M., et~al. 2021, A\&A, 649, A4"),
    ("mann2015", r"{Mann} et~al.(2015)", r"Mann, A.~W., Feiden, G.~A., Gaidos, E., et~al. 2015, ApJ, 804, 64"),
    ("mann2019", r"{Mann} et~al.(2019)", r"Mann, A.~W., Dupuy, T., Kraus, A.~L., et~al. 2019, ApJ, 871, 63"),
    ("morton2012", r"{Morton}(2012)", r"Morton, T.~D. 2012, ApJ, 761, 6"),
    ("pecaut2013", r"{Pecaut} \& {Mamajek}(2013)", r"Pecaut, M.~J., \& Mamajek, E.~E. 2013, ApJS, 208, 9"),
)


def _vetting_sentences(data: dict) -> list[str]:
    """Rungs 1 to 4 in words, from their stored metrics; a rung that did not pass says so with its reason."""
    out = []
    ev = _metrics(data, "rung1_events")
    n_cov, n_data = ev.get("n_covered"), ev.get("n_events_with_data")
    if n_cov is not None and n_data:
        s = f"Of the {n_data} predicted transits with data, {n_cov} are well covered"
        share = _num(ev.get("ses_max_over_mes"))
        s += (f", and no single transit carries more than {share * 100:.0f} percent of the signal." if share is not None
              else ".")
        out.append(s)
    sh = _metrics(data, "rung2_shape")
    odd, even, par = _f(sh.get("odd_ppm"), 0, True), _f(sh.get("even_ppm"), 0, True), _f(sh.get("parity_sigma"), 1)
    if odd and even:
        s = f"The odd and even transits have depths of {odd} and {even} ppm" + (f" ({par} sigma apart)" if par else "")
        sec, rms = _f(sh.get("secondary_ppm"), 0, True), _f(sh.get("control_rms_ppm"), 0, True)
        s += (f", and no secondary eclipse shows at phase 0.5 ({sec} ppm against a scatter of {rms} ppm)."
              if sec and rms else ".")
        out.append(s)
    if sh.get("fit_ok") is True and _num(sh.get("fit_k")) is not None:
        b = _f(sh.get("fit_b"), 2)
        out.append(f"A transit fit gives a radius ratio of {_f(sh.get('fit_k'), 4)}"
                   + (f" and an impact parameter of {b}." if b else "."))
    st = _metrics(data, "rung3_stability")
    if st:
        n_seasons = st.get("n_seasons", len(st.get("season_depths") or {}))
        chi2, dof, p = _f(st.get("chi2"), 1), st.get("dof"), _f(st.get("p_value"), 2)
        if n_seasons and chi2 is not None and dof is not None:
            out.append(f"The depth agrees across {n_seasons} seasons (chi-squared {chi2} for {dof} degrees of freedom"
                       + (f", p = {p})." if p else ")."))
    nv = _metrics(data, "rung4_novelty")
    if nv:
        names = {"cumulative": "the Kepler cumulative KOI table", "q1_q17_dr25_tce": "the Q1 to Q17 DR25 TCE list",
                 "toi": "the TESS TOI list", "kepler_eb": "the Kepler eclipsing binary catalogue"}
        hits = [f"{int(nv[k])} in {v}" for k, v in names.items() if _num(nv.get(k))]
        if hits:
            out.append(f"Catalogue matches: {', '.join(hits)}.")
        elif any(k in nv for k in names):
            out.append("It matches no entry in " + ", ".join(list(names.values())[:-1]) + f" or {names['kepler_eb']}.")
    words = {w.get("rung"): w for w in data.get("why") or [] if isinstance(w, dict)}
    for t in data.get("tests") or []:
        if t.get("rung") != "rung5_pixels" and t.get("status") != "pass":
            w = words.get(t.get("rung")) or {}
            out.append(f"The check \"{w.get('name') or t.get('rung')}\" gave {t.get('status')}: "
                       f"{t.get('reason') or w.get('sentence') or 'no reason stored'}.")
    return out


def pixel_sentence(data: dict) -> str:
    """Section 04 in words: whether the pixel check ran, and where the difference-image centroid sits."""
    row = _test(data, "rung5_pixels")
    if row is None:
        return ("The pixel check has not run for this signal, so the dip could still come from a neighbouring star "
                "whose light falls in the same photometry.")
    if row.get("status") == "undetermined":
        return f"The pixel check gave no verdict ({row.get('reason') or 'no reason stored'})."
    seasons = [s for s in (row.get("metrics") or {}).get("seasons") or [] if isinstance(s, dict)]
    if not seasons:
        return f"The pixel check {'passed' if row.get('status') == 'pass' else 'failed'} without per-season numbers."
    near = sum(1 for s in seasons if s.get("nearer") == "target")
    text = (f"Difference images (out of transit minus in transit) place the lost light nearer this star than the "
            f"brightest neighbour in {near} of {len(seasons)} observing seasons.")
    dt = [d for s in seasons if (d := _num(s.get("dist_target_pix"))) is not None]
    dn = [d for s in seasons if (d := _num(s.get("dist_neighbour_pix"))) is not None]
    if dt and dn:
        text += (f" The centroid sits a median {np.median(dt):.2f} pixels from this star and {np.median(dn):.2f} "
                 "pixels from the neighbour.")
    off = ds.centroid_offset(row.get("metrics") or {})
    if off is not None:
        text += f" Averaged over the seasons it is {off[0]:.2f} ± {off[1]:.2f} arcsec from the star."
    pairs = [(s["depth_target_ppm"], s["depth_neighbour_ppm"]) for s in seasons
             if isinstance(s.get("depth_target_ppm"), list) and isinstance(s.get("depth_neighbour_ppm"), list)
             and s["depth_target_ppm"] and s["depth_neighbour_ppm"]]
    if pairs:
        t = float(np.median([p[0][0] for p in pairs]))
        n = float(np.median([p[1][0] for p in pairs]))
        text += (f" A two-source fit gives a median depth of {t:,.0f} ppm on this star and {n:,.0f} ppm on the "
                 "neighbour.")
    if row.get("status") == "fail":
        text += " The pixel check failed: the dip may sit on the neighbour."
    return text


def _hz_where(planet: dict) -> str | None:
    if _num(planet.get("hz_inner_au")) is None:
        return None
    if planet.get("in_habitable_zone"):
        return "inside the conservative habitable zone"
    if planet.get("in_optimistic_habitable_zone"):
        return "inside the optimistic habitable zone but not the conservative one"
    return "outside the habitable zone"


def param_rows(data: dict) -> list[dict]:
    """The fit, star and planet parameters as rows {parameter, value, minus, plus, unit, source, text}."""
    rows = []
    sh = _metrics(data, "rung2_shape")

    def add(name, v, unit, source=None):
        if isinstance(v, dict):
            if _num(v.get("median")) is None:
                return
            text = value_text(v)
            rows.append({"parameter": name, "value": _num(v.get("median")), "minus": _num(v.get("minus")),
                         "plus": _num(v.get("plus")), "unit": unit, "source": source or v.get("source") or "",
                         "text": f"{text} {unit}".strip()})
        elif _num(v) is not None:
            rows.append({"parameter": name, "value": _num(v), "minus": None, "plus": None, "unit": unit,
                         "source": source or "", "text": f"{_num(v):g} {unit}".strip()})
    if _num(sh.get("depth_ppm")) is not None:
        e = _num(sh.get("depth_err_ppm"))
        add("fitted depth", {"median": sh["depth_ppm"], "minus": e, "plus": e}, "ppm", "rung 2 box depth")
    if sh.get("fit_ok") is True:
        add("radius ratio", sh.get("fit_k"), "", "rung 2 transit fit (uniform disc)")
        add("impact parameter", sh.get("fit_b"), "", "rung 2 transit fit (uniform disc)")
    star, planet = data.get("star") or {}, data.get("planet") or {}
    if star.get("spectral_type"):
        rows.append({"parameter": "spectral type", "value": star["spectral_type"], "minus": None, "plus": None,
                     "unit": "", "source": (star.get("teff") or {}).get("source") or "",
                     "text": star["spectral_type"]})
    add("stellar effective temperature", star.get("teff"), "K")
    add("stellar radius", star.get("radius"), "solar radii")
    add("stellar mass", star.get("mass"), "solar masses")
    add("stellar density", star.get("density_gcc"), "g/cm3")
    add("distance", star.get("distance_pc"), "pc")
    add("planet radius", planet.get("rp_rearth"), "Earth radii")
    add("semi-major axis", planet.get("a_au"), "AU")
    add("scaled semi-major axis a/R*", planet.get("a_over_r"), "")
    add("equilibrium temperature", planet.get("teq_k"), "K")
    add("insolation", planet.get("insolation_earth"), "Earth = 1")
    add("transit probability", planet.get("transit_probability"), "")
    for key, name in (("hz_inner_au", "habitable zone inner edge (runaway greenhouse)"),
                      ("hz_outer_au", "habitable zone outer edge (maximum greenhouse)")):
        add(name, planet.get(key), "AU", "Kopparapu et al. 2013, erratum coefficients")
    return rows


def ephemeris_rows(data: dict) -> list[dict]:
    eph = data.get("ephemeris") or {}
    names = (("period", "period_days", "d"), ("mid-transit time", "t0_bkjd", "BKJD (BJD - 2454833)"),
             ("mid-transit time", "t0_bjd", "BJD_TDB"), ("duration", "duration_h", "h"),
             ("depth", "depth_ppm", "ppm"), ("signal detection efficiency", "sde", ""),
             ("signal-to-noise ratio", "snr", ""), ("transits", "n_events", ""))
    return [{"parameter": p, "value": eph.get(k), "minus": None, "plus": None, "unit": u,
             "source": "box search (no formal error)" if k in ("period_days", "t0_bkjd", "t0_bjd") else "box search",
             "text": f"{eph.get(k)} {u}".strip()} for p, k, u in names if eph.get(k) is not None]


def numbers(data: dict, tex: bool = False) -> dict:
    """Everything the four texts print, as text (``tex``: LaTeX-ready, values with errors as ``Raw`` math)."""
    target, eph, sig = data.get("target") or {}, data.get("ephemeris") or {}, data.get("signal") or {}
    star, planet, prov = data.get("star") or {}, data.get("planet") or {}, data.get("provenance") or {}
    plots, sh = data.get("plots") or {}, _metrics(data, "rung2_shape")
    window = ((sig.get("extra") or {}).get("search_window") or {})
    lo, hi = _num(window.get("min_period_days")), _num(window.get("max_period_days"))
    fap_text, fpp_text = probability_sentences(data)
    caveats = [c for c in data.get("caveats") or [] if isinstance(c, dict) and c.get("title")]
    duration = _num(eph.get("duration_h"))
    rp, a, teq = planet.get("rp_rearth"), planet.get("a_au"), planet.get("teq_k")
    host = ", ".join(x for x in (
        f"{star['spectral_type']} dwarf" if star.get("spectral_type") else None,
        f"Teff {value_text(star.get('teff'))} K" if value_text(star.get("teff")) else None,
        f"R {value_text(star.get('radius'))} Rsun" if value_text(star.get("radius")) else None,
        f"{value_text(star.get('distance_pc'))} pc" if value_text(star.get("distance_pc")) else None) if x)
    star_ok = star.get("route") not in (None, "none") and value_text(star.get("radius")) is not None
    return {
        "name": target.get("name") or target.get("target_id"), "target_id": target.get("target_id"),
        "gaia_id": target.get("gaia_id"), "kic": target.get("kic"), "ra": _f(target.get("ra"), 5),
        "dec": _f(target.get("dec"), 5), "gmag": _f(target.get("gmag"), 1),
        "summary": data.get("summary") or "", "signal_id": sig.get("id"),
        "generated_words": date_words(data.get("generated") or ""),
        "search_version": prov.get("search_version") or sig.get("detector_version"),
        "ladder_version": prov.get("ladder_version") or sig.get("ladder_version"),
        "period": _f(eph.get("period_days"), 5), "period_title": _f(eph.get("period_days"), 3),
        "t0_bjd": _f(eph.get("t0_bjd"), 4), "t0_bkjd": _f(eph.get("t0_bkjd"), 4),
        "duration": _f(duration, 2), "depth": _f(eph.get("depth_ppm"), 0, True), "sde": _f(eph.get("sde"), 1),
        "snr": _f(eph.get("snr"), 1), "n_events": eph.get("n_events"),
        "window": f"{lo:g} to {hi:g} days" if lo is not None and hi is not None else None,
        "vetting": _vetting_sentences(data), "pixel_text": pixel_sentence(data),
        "pixel_plain": pixel_sentence(data),
        "star_ok": star_ok, "spectral_type": star.get("spectral_type"),
        "teff": value_text(star.get("teff"), tex), "radius": value_text(star.get("radius"), tex),
        "mass": value_text(star.get("mass"), tex), "distance": value_text(star.get("distance_pc"), tex),
        "fit_k": _f(sh.get("fit_k"), 4) if sh.get("fit_ok") is True else None,
        "rp": value_text(rp, tex), "a": value_text(a, tex), "teq": value_text(teq, tex),
        "insolation": value_text(planet.get("insolation_earth"), tex),
        "rp_text": value_text(rp), "a_text": value_text(a), "teq_text": value_text(teq), "host_text": host or "no "
        "stellar parameters", "hz_where": _hz_where(planet), "hz_inner": _f(planet.get("hz_inner_au"), 3),
        "hz_outer": _f(planet.get("hz_outer_au"), 3),
        "fap_text": fap_text, "fpp_text": fpp_text, "caveats": caveats,
        "caveat_text": " ".join(f"{c['title'].rstrip('.')}." for c in caveats) or "No caveat is recorded.",
        "window_h": _f(plots.get("window_h"), 1), "bin_minutes": _f(duration * 60 / 8, 0) if duration else None,
        "model_words": "transit fit" if plots.get("model_kind") == "fit" else "box model of the detection",
        "param_rows": param_rows(data), "commands": prov.get("commands") or [],
    }


def archive_paragraph(data: dict, doi: str, tic: str | None) -> str:
    n = numbers(data)
    where = f" (Gaia DR3 {n['gaia_id']}, RA {n['ra']} deg, Dec {n['dec']} deg)" if n["gaia_id"] else ""
    rp = f", which gives a planet radius of {n['rp_text']} Earth radii" if n["rp_text"] else ""
    exofop = f"under TIC {tic}" if tic else "under the star's TIC id"
    return (f"I write to report a transit candidate on {n['name']}{where}, found in the KBonus Kepler Background "
            f"light curves and published as a Research Note of the AAS (doi:{RNAAS_DOI_PLACEHOLDER}). The signal "
            f"repeats every {n['period']} days with a depth of {n['depth']} ppm and a duration of {n['duration']} "
            f"hours{rp}. {n['fap_text']} {n['fpp_text']} The light curve, ephemeris, vetting metrics and figures are "
            f"frozen on Zenodo (doi:{doi}) and uploaded to ExoFOP-TESS {exofop}. I would be glad to see it "
            "considered for the archive's tables, and I can send anything else you need.")


# ---------------------------------------------------------------------------------------------------- the texts

def _authors(citation: dict | None) -> list[dict]:
    out = []
    for a in (citation or {}).get("authors") or []:
        if not isinstance(a, dict):
            continue
        given, family = a.get("given-names"), a.get("family-names")
        name = " ".join(x for x in (given, family) if x) or a.get("name")
        orcid = re.search(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]", str(a.get("orcid") or ""))
        if name:
            out.append({"name": str(name), "orcid": orcid.group(0) if orcid else None})
    return out or [{"name": "AUTHOR NAME TO FILL IN", "orcid": None}]


def render_note(data: dict, doi_placeholder: str = DOI_PLACEHOLDER, *, citation: dict | None = None) -> str:
    """The AASTeX Research Note draft (RNAAS option): the frame in ``data/rnaas/aastex_template.tex`` and the body
    from ``note.tex.j2``, under 1,000 words, citing exactly the works the dossier used."""
    refs = references(data)
    bib = [(key, Raw(label), Raw(text)) for key, label, text in BIBLIOGRAPHY if refs.get(key)]
    body = _env(tex=True).get_template("note.tex.j2").render(
        n=numbers(data, tex=True), refs=refs, bib=bib, doi=doi_placeholder, authors=_authors(citation))
    return AASTEX_FRAME.read_text(encoding="utf-8") + "\n" + body


EXOFOP_CONTENTS = (("lightcurve", "data/lightcurve.csv",
                    "the detrended light curve (time BKJD, relative flux and error in ppm, quarter)"),
                   ("ephemeris", "data/ephemeris.csv", "the ephemeris and parameters with their errors"),
                   ("dossier", "dossier.html", "the standalone dossier with every vetting number"),
                   ("fold", "figures/fold.{ext}", "the folded transit with the model"),
                   ("oddeven", "figures/oddeven.{ext}", "the odd and even transits"),
                   ("periodogram", "figures/periodogram.{ext}", "the periodogram"),
                   ("centroids", "figures/centroids.{ext}", "the per-season difference images and centroids"))

FILE_NOTES = {
    "README.md": "these instructions",
    "note/note.tex": "the Research Note draft (AASTeX, RNAAS)",
    "note/figure1.svg": "the note's figure (the folded transit)",
    "note/figure1.pdf": "the note's figure as PDF, for pdflatex",
    "note/figure1.png": "the note's figure as PNG",
    "data/lightcurve.csv": "the whitened light curve the search ran on: time BKJD, relative flux in ppm, error in "
                           "ppm, quarter",
    "data/ephemeris.csv": "period, mid-transit time, duration, depth and the star and planet parameters with errors",
    "data/ephemeris.json": "the same with the dossier's ephemeris, star and planet blocks",
    "data/events.csv": "every transit: epoch, mid-time, quarter, depth, error, coverage, single-event statistic",
    "data/periodogram.json": "the periodogram envelope, its peaks and the alias and systematic periods",
    "data/rung_metrics.json": "every vetting rung's status, reason and metrics",
    "dossier.html": "the standalone dossier (opens offline in any browser)",
    "archive_email.txt": "the paragraph for the NASA Exoplanet Archive help desk (step 4)",
    "zenodo_metadata.json": "the Zenodo metadata, for a deposit made by hand (step 1)",
    "provenance/versions.json": "pack, dossier, search and ladder versions and the git commit",
    "provenance/queries.json": "every catalogue and data request for this target, with URL, time, status and SHA-256",
    "provenance/hashes.json": "the SHA-256 of every other file in the pack",
    "provenance/calibration.json": "the null-search counts behind the false-alarm probability",
    "exofop/note.txt": "the text for the ExoFOP upload's notes field (step 2)",
}


def exofop_name(tic: str | None, date: str, tag: str, content: str, ext: str) -> str:
    """ExoFOP's file name ``<TIC>_<UTC date>_<observer tag>_<content>.<ext>``; ``TIC-unknown`` when the TIC id is
    not known yet. A "TIC" prefix and spaces are dropped; anything but digits in the id raises ValueError."""
    if tic is None:
        tid = "TIC-unknown"
    else:
        tid = re.sub(r"^TIC", "", str(tic).strip(), flags=re.IGNORECASE).strip()
        if not tid.isdigit():
            raise ValueError(f"a TIC id is digits, not {tic!r}")
    if not re.fullmatch(r"\d{8}", date):
        raise ValueError(f"the date is yyyymmdd, not {date!r}")
    for value, rule, what in ((tag, _TAG, "observer tag"), (content, _CONTENT, "content"), (ext, _EXT, "extension")):
        if not rule.fullmatch(value):
            raise ValueError(f"not a valid {what}: {value!r}")
    return f"{tid}_{date}_{tag}_{content}.{ext}"


def _exofop_list(tic, date, tag, png: bool, pixels_figure: bool) -> list[tuple[str, str, str]]:
    """(ExoFOP name, pack source, what it is) for the files step 2 attaches: each figure once, as PNG when the PNGs
    were drawn, else as SVG."""
    out = []
    for content, source, what in EXOFOP_CONTENTS:
        if content == "centroids" and not pixels_figure:
            continue
        ext = ("png" if png else "svg") if "{ext}" in source else source.rsplit(".", 1)[1]
        out.append((exofop_name(tic, date, tag, content, ext), source.format(ext=ext), what))
    return out


def _fields(data: dict, f: dict) -> dict:
    f = dict(f)
    f.setdefault("approved", False)
    f.setdefault("stale_reason", None)
    f.setdefault("tic", None)
    f.setdefault("date", datetime.now(UTC).strftime("%Y%m%d"))
    f.setdefault("tag", "skysift")
    f.setdefault("png", False)
    f.setdefault("zenodo", None)
    f.setdefault("pixels_figure", bool(data.get("pixels")))
    f.setdefault("zip_name", "the pack's zip")
    f.setdefault("files", [])
    f.setdefault("built_words", date_words(datetime.now(UTC)))
    f.setdefault("pack_version", PACK_VERSION)
    doi = (f.get("zenodo") or {}).get("doi")
    f.setdefault("doi_shown", doi or DOI_PLACEHOLDER)
    if "note_words" not in f:
        f["note_words"] = note_words(render_note(data, f["doi_shown"]))
    f.setdefault("exofop", [(name, what) for name, _src, what in
                            _exofop_list(f["tic"], f["date"], f["tag"], f["png"], f["pixels_figure"])])
    f.setdefault("file_notes", [(name, FILE_NOTES.get(name) or _figure_note(name)) for name in sorted(f["files"])
                                if not name.startswith("exofop/") or name == "exofop/note.txt"])
    f.setdefault("exofop_url", EXOFOP_URL)
    f.setdefault("helpdesk_url", HELPDESK_URL)
    return f


def _figure_note(name: str) -> str:
    stem = Path(name).stem
    what = {"fold": "the folded transit with the model", "oddeven": "the odd and even transits side by side",
            "periodogram": "the periodogram with the signal and its aliases marked",
            "centroids": "one difference image per season with the star, the neighbour and the centroid"}
    return what.get(stem, "a pack file")


def render_readme(data: dict, report_fields: dict) -> str:
    """The README: the five steps in order (``STEPS``), each with its account, files, text and what the engine did
    or cannot do. ``report_fields`` carries the build's own values (approved, tic, date, tag, files, png, zenodo,
    stale_reason, zip_name, pixels_figure); missing ones take defaults."""
    f = _fields(data, report_fields)
    return _env(tex=False).get_template("README.md.j2").render(
        n=numbers(data), f=f, titles=dict(STEPS),
        archive_paragraph=archive_paragraph(data, f["doi_shown"], f["tic"]))


def _render_plain(name: str, data: dict, f: dict) -> str:
    f = _fields(data, f)
    return _env(tex=False).get_template(name).render(n=numbers(data), f=f,
                                                     archive_paragraph=archive_paragraph(data, f["doi_shown"],
                                                                                         f["tic"]))


# ---------------------------------------------------------------------------------------------------- SVG figures

def _ticks(lo: float, hi: float, n: int = 5) -> tuple[list[float], float]:
    if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
        lo, hi = (lo if math.isfinite(lo) else 0.0), (lo if math.isfinite(lo) else 0.0) + 1.0
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw * 0.999)
    out, v = [], math.ceil(lo / step) * step
    while v <= hi + step * 1e-9:
        out.append(round(v, 12))
        v += step
    return out, step


def _label(v: float, step: float) -> str:
    nd = max(0, -math.floor(math.log10(step)) + (1 if step / 10 ** math.floor(math.log10(step)) == 2.5 else 0))
    text = f"{v:,.{nd}f}"
    return "0" if text.strip("-0.,") == "" else text


def _esc(text) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _finite(pairs) -> list:
    return [p for p in pairs if all(_num(v) is not None for v in p[:2])]


def _panel(spec: dict, x0: float, y0: float, w: float, h: float, pid: str) -> list[str]:
    """One plot: frame, ticks, grid, labels, then each series (points, errorbars, line) clipped to the frame."""
    ml, mb, mt, mr = 62, 44, 30, 14
    px, py, pw, ph = x0 + ml, y0 + mt, w - ml - mr, h - mt - mb
    xlog = spec.get("xlog", False)
    xs, ys = [], []
    for s in spec["series"]:
        for p in _finite(s["xy"]):
            xs.append(p[0])
            e = p[2] if len(p) > 2 and _num(p[2]) is not None else 0.0
            ys += [p[1] - e, p[1] + e]
    xs = [x for x in xs if not xlog or x > 0]
    xlo, xhi = spec.get("xlim") or ((min(xs), max(xs)) if xs else (0.0, 1.0))
    ylo, yhi = spec.get("ylim") or ((min(ys), max(ys)) if ys else (0.0, 1.0))
    if not spec.get("ylim"):
        pad = 0.06 * ((yhi - ylo) or 1.0)
        ylo, yhi = ylo - pad, yhi + pad
    if xhi <= xlo:
        xhi = xlo + 1.0
    lx = (lambda v: math.log10(v)) if xlog else (lambda v: v)
    xa, xb = lx(xlo), lx(xhi)

    def X(v):
        return round(px + (lx(v) - xa) / ((xb - xa) or 1.0) * pw, 2)

    def Y(v):
        return round(py + (yhi - v) / ((yhi - ylo) or 1.0) * ph, 2)

    out = [f'<g class="{spec.get("klass", "panel")}">',
           f'<clipPath id="clip-{pid}"><rect x="{px}" y="{py}" width="{pw}" height="{ph}"/></clipPath>',
           f'<rect x="{px}" y="{py}" width="{pw}" height="{ph}" fill="none" stroke="{MUTED}" stroke-width="1"/>']
    yt, ystep = _ticks(ylo, yhi)
    for v in yt:
        out.append(f'<line x1="{px}" x2="{px + pw}" y1="{Y(v)}" y2="{Y(v)}" stroke="{GRID}" stroke-width="1"/>')
        out.append(f'<text x="{px - 6}" y="{Y(v) + 4}" text-anchor="end" font-size="11" fill="{INK}">'
                   f'{_label(v, ystep)}</text>')
    if xlog:
        xt = [10.0 ** k for k in range(math.ceil(xa), math.floor(xb) + 1)]
        xlabels = [f"{v:g}" for v in xt]
    else:
        xt, xstep = _ticks(xlo, xhi)
        xlabels = [_label(v, xstep) for v in xt]
    for v, lab in zip(xt, xlabels, strict=True):
        out.append(f'<line x1="{X(v)}" x2="{X(v)}" y1="{py + ph}" y2="{py + ph + 4}" stroke="{MUTED}"/>')
        out.append(f'<text x="{X(v)}" y="{py + ph + 17}" text-anchor="middle" font-size="11" fill="{INK}">{lab}</text>')
    out.append(f'<text x="{px + pw / 2}" y="{py + ph + 36}" text-anchor="middle" font-size="12" fill="{INK}">'
               f'{_esc(spec["xlabel"])}</text>')
    out.append(f'<text transform="translate({x0 + 16} {py + ph / 2}) rotate(-90)" text-anchor="middle" font-size="12" '
               f'fill="{INK}">{_esc(spec["ylabel"])}</text>')
    out.append(f'<text x="{px}" y="{y0 + 18}" font-size="13" font-weight="600" fill="{INK}">{_esc(spec["title"])}'
               "</text>")
    out.append(f'<g clip-path="url(#clip-{pid})">')
    for vx, colour, dash in spec.get("vlines") or []:
        if xlo <= vx <= xhi:
            out.append(f'<line x1="{X(vx)}" x2="{X(vx)}" y1="{py}" y2="{py + ph}" stroke="{colour}" stroke-width="1.2"'
                       + (f' stroke-dasharray="{dash}"' if dash else "") + "/>")
    for s in spec["series"]:
        pts = [p for p in _finite(s["xy"]) if not xlog or p[0] > 0]
        colour = s.get("color", POINT)
        if s["kind"] == "line" and pts:
            d = "M" + " L".join(f"{X(p[0])} {Y(p[1])}" for p in pts)
            out.append(f'<path d="{d}" fill="none" stroke="{colour}" stroke-width="{s.get("width", 1.6)}" '
                       'stroke-linejoin="round"/>')
        elif s["kind"] == "points":
            r = s.get("r", 1.0)
            out.append(f'<g fill="{colour}">' + "".join(f'<circle cx="{X(p[0])}" cy="{Y(p[1])}" r="{r}"/>'
                                                         for p in pts) + "</g>")
        elif s["kind"] == "errorbars":
            parts = []
            for p in pts:
                e = p[2] if len(p) > 2 and _num(p[2]) is not None else 0.0
                parts.append(f'<line x1="{X(p[0])}" x2="{X(p[0])}" y1="{Y(p[1] - e)}" y2="{Y(p[1] + e)}"/>')
                parts.append(f'<circle cx="{X(p[0])}" cy="{Y(p[1])}" r="{s.get("r", 2.4)}"/>')
            out.append(f'<g fill="{colour}" stroke="{colour}" stroke-width="1">' + "".join(parts) + "</g>")
    out.append("</g>")
    labelled = [s for s in spec["series"] if s.get("label")]
    for i, s in enumerate(labelled):
        ly = py + 14 + i * 15
        out.append(f'<rect x="{px + pw - 150}" y="{ly - 8}" width="10" height="10" fill="{s.get("color", POINT)}"/>'
                   f'<text x="{px + pw - 135}" y="{ly + 1}" font-size="11" fill="{INK}">{_esc(s["label"])}</text>')
    out.append("</g>")
    return out


def _svg(title: str, desc: str, width: int, height: int, body: list[str]) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" '
            f'height="{height}" role="img" font-family="{FONT}"><title>{_esc(title)}</title><desc>{_esc(desc)}</desc>'
            f'<rect width="{width}" height="{height}" fill="#ffffff"/>' + "".join(body) + "</svg>\n")


def _model(plots: dict) -> list:
    return [(p[0], p[1]) for p in plots.get("model") or []]


def fold_svg(data: dict) -> str:
    plots, name = data.get("plots") or {}, (data.get("target") or {}).get("name") or "the target"
    w = _num(plots.get("window_h")) or 12.0
    spec = {"title": f"{name}: folded on {numbers(data)['period']} d", "xlabel": "hours from mid-transit",
            "ylabel": "relative flux (ppm)", "xlim": (-w, w),
            "series": [{"kind": "points", "xy": plots.get("fold_points") or [], "color": DOTS, "r": 0.8},
                       {"kind": "errorbars", "xy": [(b[0], b[1], b[2]) for b in plots.get("fold_bins") or []],
                        "color": POINT, "label": "binned flux"},
                       {"kind": "line", "xy": _model(plots), "color": MODEL,
                        "label": "transit fit" if plots.get("model_kind") == "fit" else "box model"}]}
    ys = [b[1] - (b[2] or 0) for b in plots.get("fold_bins") or [] if _num(b[1]) is not None]
    ys += [b[1] + (b[2] or 0) for b in plots.get("fold_bins") or [] if _num(b[1]) is not None]
    ys += [p[1] for p in _model(plots) if _num(p[1]) is not None]
    if ys:
        pad = 0.15 * ((max(ys) - min(ys)) or 1.0)
        spec["ylim"] = (min(ys) - pad, max(ys) + pad)       # the scatter is clipped, the bins and model set the scale
    return _svg(f"{name} folded transit", "The whitened flux folded on the period: grey dots are the points, blue "
                "the bins with their standard errors, orange the model.", 760, 380, _panel(spec, 0, 0, 760, 380, "f"))


def oddeven_svg(data: dict) -> str:
    plots, sh = data.get("plots") or {}, _metrics(data, "rung2_shape")
    w = _num(plots.get("window_h")) or 12.0
    sets = {"odd": plots.get("odd_bins") or [], "even": plots.get("even_bins") or []}
    ys = [v for b in [*sets["odd"], *sets["even"]] if _num(b[1]) is not None
          for v in (b[1] - (b[2] or 0), b[1] + (b[2] or 0))] + [p[1] for p in _model(plots)]
    ylim = (min(ys) - 0.08 * (max(ys) - min(ys) or 1), max(ys) + 0.08 * (max(ys) - min(ys) or 1)) if ys else None
    body = []
    for i, (kind, colour) in enumerate((("odd", POINT), ("even", EVEN))):
        depth = _f(sh.get(f"{kind}_ppm"), 0, True)
        spec = {"title": f"{kind.capitalize()} transits" + (f": {depth} ppm" if depth else ""),
                "xlabel": "hours from mid-transit", "ylabel": "relative flux (ppm)", "xlim": (-w, w), "ylim": ylim,
                "series": [{"kind": "errorbars", "xy": [(b[0], b[1], b[2]) for b in sets[kind]], "color": colour},
                           {"kind": "line", "xy": _model(plots), "color": MODEL}]}
        body += _panel(spec, i * 400, 0, 400, 360, f"oe{i}")
    return _svg("Odd and even transits", "The odd-numbered and even-numbered transits folded apart, with the same "
                "model on both: an eclipsing binary at twice the period shows two different depths.", 800, 360, body)


def periodogram_svg(data: dict) -> str:
    plots, eph = data.get("plots") or {}, data.get("ephemeris") or {}
    lines = plots.get("spectrum_lines") or {}
    vlines = [(a, MUTED, "4 3") for a in lines.get("aliases") or []]
    if _num(eph.get("period_days")):
        vlines.append((float(eph["period_days"]), MODEL, None))
    spec = {"title": "Signal detection efficiency against trial period", "xlabel": "period (days)",
            "ylabel": "SDE", "xlog": True, "vlines": vlines,
            "series": [{"kind": "line", "xy": plots.get("spectrum") or [], "color": POINT, "width": 1.0}]}
    return _svg("Periodogram", "The search's SDE envelope; the solid orange line marks the signal's period, the dashed "
                "lines half, twice and three times it.", 760, 340, _panel(spec, 0, 0, 760, 340, "p"))


def _mix(frac: float) -> str:
    """White to the point colour by ``frac`` in 0..1."""
    frac = max(0.0, min(1.0, frac))
    base = (0x1f, 0x5f, 0x8b)
    return "#" + "".join(f"{round(255 + (c - 255) * frac):02x}" for c in base)


def centroids_svg(pixels: list[dict]) -> str:
    """One panel per season: the difference image as S/N (white to blue, a crossed grey cell outside the stored
    aperture), the star as a ring, the neighbour as a square and the centroid as a cross, row 0 at the bottom."""
    vmax = max([1.0] + [v for s in pixels for row in s.get("snr") or [] for v in row if _num(v) is not None])
    cs, pad, top, gap = 22, 14, 34, 18
    panels, x = [], 0
    height = 0
    for s in pixels:
        snr = s.get("snr") or []
        nr, nc = len(snr), len(snr[0]) if snr else 0
        w, h = nc * cs + 2 * pad, nr * cs + 2 * pad + top + 22
        height = max(height, h)

        def X(col, x0=x):
            return round(x0 + pad + (col + 0.5) * cs, 2)

        def Y(row, nr=nr):
            return round(top + pad + (nr - 0.5 - row) * cs, 2)
        g = ['<g class="season-panel">',
             (f'<text x="{x + pad}" y="20" font-size="13" font-weight="600" fill="{INK}">'
              f'Season {s.get("season")}</text>')]
        for r in range(nr):
            for c in range(nc):
                v = _num(snr[r][c])
                cx, cy = x + pad + c * cs, top + pad + (nr - 1 - r) * cs
                fill = "#f3f4f6" if v is None else _mix(v / vmax if v > 0 else 0.0)
                g.append(f'<rect x="{cx + 1}" y="{cy + 1}" width="{cs - 2}" height="{cs - 2}" fill="{fill}">'
                         f'<title>{"no valid pixel" if v is None else f"S/N {v:.1f}"}</title></rect>')
                if v is None:
                    g.append(f'<line x1="{cx + 5}" y1="{cy + cs - 5}" x2="{cx + cs - 5}" y2="{cy + 5}" '
                             f'stroke="{DOTS}"/>')
        pt, pn, cen = s.get("pos_target") or [], s.get("pos_neighbour") or [], s.get("centroid") or []
        if len(pn) == 2 and None not in pn:
            g.append(f'<rect x="{X(pn[0]) - 7}" y="{Y(pn[1]) - 7}" width="14" height="14" fill="none" stroke="{INK}" '
                     'stroke-width="1.6"/>')
        if len(pt) == 2 and None not in pt:
            g.append(f'<circle cx="{X(pt[0])}" cy="{Y(pt[1])}" r="8" fill="none" stroke="{MODEL}" stroke-width="2"/>')
        if len(cen) == 2 and None not in cen:
            cx, cy = X(cen[0]), Y(cen[1])
            g.append(f'<path d="M{cx - 5} {cy - 5} L{cx + 5} {cy + 5} M{cx - 5} {cy + 5} L{cx + 5} {cy - 5}" '
                     f'stroke="{INK}" stroke-width="2"/>')
        near = {"target": "nearer this star", "neighbour": "nearer the neighbour"}.get(s.get("nearer"), "no verdict")
        g.append(f'<text x="{x + pad}" y="{top + pad * 2 + nr * cs + 6}" font-size="11" fill="{INK}">'
                 f'{_esc(near)}</text></g>')
        panels += g
        x += w + gap
    width = max(1, x - gap)
    return _svg("Difference images by season", f"Each panel is one observing season's difference image as S/N, from "
                f"white (no lost light) to blue (S/N {vmax:.0f}). The ring marks this star, the square the "
                "neighbour, the cross the difference-image centroid.", width, height or 100, panels)


# ---------------------------------------------------------------------------------------------------- PNG figures

def _matplotlib():
    """matplotlib's Figure class, or None when it cannot be imported (the SVGs are then the figures)."""
    try:
        from matplotlib.figure import Figure
    except Exception:  # noqa: BLE001  (missing, or broken by its own dependencies)
        return None
    return Figure


def _arr(rows, cols) -> list[np.ndarray]:
    good = [r for r in rows or [] if all(_num(r[c]) is not None for c in cols)]
    return [np.array([float(r[c]) for r in good]) for c in cols]


def _png_figures(Figure, data: dict, figures: Path, note: Path) -> None:
    plots, eph, sh = data.get("plots") or {}, data.get("ephemeris") or {}, _metrics(data, "rung2_shape")
    w = _num(plots.get("window_h")) or 12.0
    mx, my = _arr(plots.get("model"), (0, 1))

    def fold(ax):
        fx, fy = _arr(plots.get("fold_points"), (0, 1))
        bx, by, be = _arr(plots.get("fold_bins"), (0, 1, 2))
        ax.plot(fx, fy, ".", color=DOTS, ms=1.5, zorder=1)
        ax.errorbar(bx, by, be, fmt="o", color=POINT, ms=3, lw=1, zorder=2, label="binned flux")
        ax.plot(mx, my, color=MODEL, lw=1.6, zorder=3,
                label="transit fit" if plots.get("model_kind") == "fit" else "box model")
        vals = np.concatenate([by - be, by + be, my]) if by.size or my.size else np.array([-1.0, 1.0])
        pad = 0.15 * (np.ptp(vals) or 1.0)
        ax.set_ylim(vals.min() - pad, vals.max() + pad)
        ax.set_xlim(-w, w)
        ax.set_xlabel("hours from mid-transit")
        ax.set_ylabel("relative flux (ppm)")
        ax.legend(loc="lower right", fontsize=8, frameon=False)

    fig = Figure(figsize=(7, 3.6), dpi=150, layout="constrained")
    fold(fig.add_subplot())
    fig.savefig(figures / "fold.png")
    fig.savefig(note / "figure1.png")
    fig.savefig(note / "figure1.pdf")

    fig = Figure(figsize=(8, 3.4), dpi=150, layout="constrained")
    axes = fig.subplots(1, 2, sharey=True)
    for ax, kind, colour in ((axes[0], "odd", POINT), (axes[1], "even", EVEN)):
        bx, by, be = _arr(plots.get(f"{kind}_bins"), (0, 1, 2))
        ax.errorbar(bx, by, be, fmt="o", color=colour, ms=3, lw=1)
        ax.plot(mx, my, color=MODEL, lw=1.4)
        depth = _f(sh.get(f"{kind}_ppm"), 0, True)
        ax.set_title(f"{kind.capitalize()} transits" + (f": {depth} ppm" if depth else ""), fontsize=10)
        ax.set_xlim(-w, w)
        ax.set_xlabel("hours from mid-transit")
    axes[0].set_ylabel("relative flux (ppm)")
    fig.savefig(figures / "oddeven.png")

    fig = Figure(figsize=(7, 3.2), dpi=150, layout="constrained")
    ax = fig.add_subplot()
    px, py = _arr(plots.get("spectrum"), (0, 1))
    ax.plot(px, py, color=POINT, lw=0.8)
    for a in (plots.get("spectrum_lines") or {}).get("aliases") or []:
        ax.axvline(a, color=MUTED, ls="--", lw=0.8)
    if _num(eph.get("period_days")):
        ax.axvline(float(eph["period_days"]), color=MODEL, lw=1.2)
    ax.set_xscale("log")
    ax.set_xlabel("period (days)")
    ax.set_ylabel("SDE")
    fig.savefig(figures / "periodogram.png")

    pixels = data.get("pixels") or []
    if pixels:
        fig = Figure(figsize=(2.6 * len(pixels), 3.0), dpi=150, layout="constrained")
        axes = np.atleast_1d(fig.subplots(1, len(pixels)))
        vmax = max([1.0] + [v for s in pixels for row in s.get("snr") or [] for v in row if _num(v) is not None])
        for ax, s in zip(axes, pixels, strict=True):
            snr = np.array([[np.nan if v is None else float(v) for v in row] for row in s.get("snr") or [[None]]])
            nr, nc = snr.shape
            ax.imshow(snr, origin="lower", extent=(0, nc, 0, nr), cmap="Blues", vmin=0, vmax=vmax)
            pt, pn, cen = s.get("pos_target") or [], s.get("pos_neighbour") or [], s.get("centroid") or []
            if len(pn) == 2 and None not in pn:
                ax.plot(pn[0] + 0.5, pn[1] + 0.5, "s", mfc="none", mec=INK, ms=9)
            if len(pt) == 2 and None not in pt:
                ax.plot(pt[0] + 0.5, pt[1] + 0.5, "o", mfc="none", mec=MODEL, ms=10, mew=2)
            if len(cen) == 2 and None not in cen:
                ax.plot(cen[0] + 0.5, cen[1] + 0.5, "x", color=INK, ms=8, mew=2)
            ax.set_title(f"Season {s.get('season')}", fontsize=10)
            ax.set_xticks([])
            ax.set_yticks([])
        fig.savefig(figures / "centroids.png")


# ---------------------------------------------------------------------------------------------------- files

def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _csv(path: Path, header: list[str], rows: list[list]) -> None:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(["" if v is None else v for v in r] for r in rows)
    _write(path, buf.getvalue())


def _lightcurve_csv(path: Path, lc, fnorm: np.ndarray) -> None:
    time, flux, err = np.asarray(lc.time, float), np.asarray(lc.flux, float), np.asarray(lc.flux_err, float)
    good = np.isfinite(fnorm) & np.isfinite(time)
    scale = float(np.nanmedian(flux[good & np.isfinite(flux)])) if good.any() else 1.0
    rel_err = np.where(np.isfinite(err), err / (scale or 1.0) * 1e6, np.nan)
    table = np.column_stack([time[good], (fnorm[good] - 1.0) * 1e6, rel_err[good], np.asarray(lc.quarter)[good]])
    buf = io.StringIO()
    np.savetxt(buf, table, fmt=["%.6f", "%.1f", "%.1f", "%d"], delimiter=",", header="time_bkjd,flux_rel_ppm,err_ppm,"
               "quarter", comments="", newline="\n")
    _write(path, buf.getvalue())


def _sha(path: Path) -> dict:
    data = path.read_bytes()
    return {"sha256": hashlib.sha256(data).hexdigest(), "nbytes": len(data)}


def _rel(out_dir: Path) -> list[str]:
    return sorted(p.relative_to(out_dir).as_posix() for p in out_dir.rglob("*") if p.is_file())


def _prepare(out_dir: Path) -> None:
    """An empty folder for the pack: a missing one is made, an earlier pack (it holds ``MARKER``) is replaced, and
    any other non-empty folder is refused, so a pack never deletes files it did not write."""
    if out_dir.exists():
        if not out_dir.is_dir():
            raise FileExistsError(f"{out_dir} is a file, not a folder")
        if any(out_dir.iterdir()):
            try:
                marker = json.loads((out_dir / MARKER).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                marker = None
            if not isinstance(marker, dict) or "pack_version" not in marker:
                raise FileExistsError(f"{out_dir} is not empty and is not a submission pack; choose another --out")
            shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)


def _zip(out_dir: Path, zip_path: Path, top: str) -> None:
    tmp = zip_path.with_name(zip_path.name + ".tmp")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name in _rel(out_dir):
            z.write(out_dir / name, f"{top}/{name}")
    tmp.replace(zip_path)


def load_citation(path: Path = CITATION_FILE) -> dict | None:
    import yaml
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    return data if isinstance(data, dict) else None


def _approval(store: Store, target_id: str, signal_id: int) -> tuple[bool, str | None]:
    """(approved, stale reason): approved when the signal's latest reviewer decision approves it (or its candidate row
    says approved or published); stale when the stored dossier no longer matches the signal's tests, status and
    decision, or another signal became the target's best."""
    sig = ds.pick_signal(store, target_id, signal_id)
    approved = sig.get("decision") == "approve" or sig.get("candidate_status") in ("approved", "published")
    row = store.dossier(target_id) or {}
    fp = ds.fingerprint(store.latest_tests(signal_id), sig["final_status"], sig.get("decision"))
    if ds.pick_signal(store, target_id)["id"] != signal_id:
        return approved, "another signal is now the target's best"
    if row.get("tests_fingerprint") != fp:
        return approved, "the signal's tests, status or reviewer decision changed since it was generated"
    return approved, None


def _calibration(data: dict) -> dict:
    prov = data.get("provenance") or {}
    path = prov.get("calibration_file")
    counts: dict[str, int] = {}
    if path and Path(path).is_file():
        for r in cal.read_rows(Path(path)):
            v = str(r.get("search_version"))
            counts[v] = counts.get(v, 0) + 1
    return {"fap": data.get("fap"), "calibration_file": path, "rows_by_version": counts,
            "rule": "FAP = (k + 1) / (n + 1) over both null kinds of every row; not calibrated below "
                    f"{cal.MIN_NULLS} null searches"}


def build_pack(store, blob, settings, target_id: str, *, out_dir: Path | None = None, tic: str | None = None,
               observer_tag: str = "skysift", zenodo: ZenodoClient | None = None,
               log: Callable[[str], None] | None = None) -> PackReport:
    """Write the submission pack of ``target_id`` from its stored dossier and zip it to ``<out_dir>.zip``; the default
    ``out_dir`` is ``<data_dir>/packs/<target_id>/<UTC yyyymmdd>/`` (an earlier pack there is replaced). With a
    ``zenodo`` client the deposition is created first, so the note carries its reserved DOI, then filled and given the
    zip; it is never published. Records the folder on the dossiers row and the candidate row. Raises NoDossier,
    ``dossier.LightCurveReleased`` (nothing is downloaded), FileExistsError for a foreign folder, ValueError for a bad
    TIC id or tag and ``zenodo.ZenodoError``."""
    say = log or (lambda _text: None)
    row = store.dossier(target_id)
    if row is None or not row.get("data_path") or not Path(row["data_path"]).is_file():
        raise NoDossier(f"no dossier for {target_id}; run skysift dossier --target {target_id} first")
    data = json.loads(Path(row["data_path"]).read_text(encoding="utf-8"))
    sid = int(row["signal_id"])
    now = datetime.now(UTC)
    date = now.strftime("%Y%m%d")
    exofop_name(tic, date, observer_tag, "lightcurve", "csv")          # a bad TIC id or tag fails before any work
    approved, stale = _approval(store, target_id, sid)
    say("loading the light curve")
    target = ds._target_dict(store, target_id)
    lc = LightCurveLoader(store, blob, source_factory=ds._offline_factory(target_id, sid)).load(target, pin=True)
    fnorm = ds.whitened(lc, data["signal"])
    citation = load_citation()
    deposition, zinfo = None, None
    if zenodo is not None:
        say("creating the Zenodo draft")
        deposition = zenodo.create_deposition()
        zinfo = {"deposition_id": deposition.get("id"), "draft_url": zenodo.draft_url(deposition),
                 "sandbox": zenodo.sandbox, "doi": zenodo.reserved_doi(deposition)}
    out_dir = Path(out_dir) if out_dir is not None else Path(settings.data_dir) / "packs" / target_id / date
    zip_path = out_dir.with_name(out_dir.name + ".zip")
    _prepare(out_dir)
    say(f"writing {out_dir}")
    figures, note, d = out_dir / "figures", out_dir / "note", out_dir / "data"
    for folder in (figures, note, d, out_dir / "provenance", out_dir / "exofop"):
        folder.mkdir(parents=True, exist_ok=True)
    # data
    _lightcurve_csv(d / "lightcurve.csv", lc, fnorm)
    eph_rows = ephemeris_rows(data) + param_rows(data)
    cols = ["parameter", "value", "minus", "plus", "unit", "source"]
    _csv(d / "ephemeris.csv", cols, [[r[c] for c in cols] for r in eph_rows])
    _write(d / "ephemeris.json", jsonutil.dumps({"ephemeris": data.get("ephemeris"), "star": data.get("star"),
                                                 "planet": data.get("planet"),
                                                 "rows": [{c: r[c] for c in cols} for r in eph_rows]}, indent=1))
    ev_cols = ["epoch", "t_mid", "quarter", "depth_ppm", "err_ppm", "coverage", "ses"]
    _csv(d / "events.csv", ["epoch", "t_mid_bkjd", "quarter", "depth_ppm", "err_ppm", "coverage", "ses"],
         [[e.get(c) for c in ev_cols] for e in (data.get("plots") or {}).get("events") or []])
    plots = data.get("plots") or {}
    _write(d / "periodogram.json", jsonutil.dumps({"sde": (data.get("ephemeris") or {}).get("sde"),
                                                   "period_days": (data.get("ephemeris") or {}).get("period_days"),
                                                   "spectrum": plots.get("spectrum"),
                                                   "lines": plots.get("spectrum_lines"),
                                                   "columns": ["period_days", "sde"]}, indent=1))
    _write(d / "rung_metrics.json", jsonutil.dumps(data.get("tests") or [], indent=1))
    # figures
    _write(figures / "fold.svg", fold_svg(data))
    _write(figures / "oddeven.svg", oddeven_svg(data))
    _write(figures / "periodogram.svg", periodogram_svg(data))
    pixels_figure = bool(data.get("pixels"))
    if pixels_figure:
        _write(figures / "centroids.svg", centroids_svg(data["pixels"]))
    shutil.copyfile(figures / "fold.svg", note / "figure1.svg")
    Figure = _matplotlib()
    png = Figure is not None
    if png:
        say("drawing the PNG figures")
        _png_figures(Figure, data, figures, note)
    # the standalone page, provenance, texts
    _write(out_dir / "dossier.html", ds.export_html(data))
    prov = data.get("provenance") or {}
    _write(out_dir / "provenance" / "queries.json", jsonutil.dumps(prov.get("queries") or [], indent=1))
    _write(out_dir / "provenance" / "calibration.json", jsonutil.dumps(_calibration(data), indent=1))
    _write(out_dir / MARKER, jsonutil.dumps(_versions(data, row, now, Figure), indent=1))
    doi = (zinfo or {}).get("doi") or DOI_PLACEHOLDER
    _write(note / "note.tex", render_note(data, doi, citation=citation))
    metadata = metadata_from(data, citation)
    _write(out_dir / "zenodo_metadata.json", jsonutil.dumps(metadata, indent=1))
    fields = {"approved": approved, "stale_reason": stale, "tic": tic, "date": date, "tag": observer_tag,
              "png": png, "zenodo": zinfo, "pixels_figure": pixels_figure, "zip_name": zip_path.name,
              "built_words": date_words(now), "doi_shown": doi}
    for name, source, _what in _exofop_list(tic, date, observer_tag, png, pixels_figure):
        shutil.copyfile(out_dir / source, out_dir / "exofop" / name)
    _write(out_dir / "exofop" / "note.txt", _render_plain("exofop_note.txt.j2", data, fields))
    _write(out_dir / "archive_email.txt", _render_plain("archive_email.txt.j2", data, fields))
    files = sorted({*_rel(out_dir), "README.md", "provenance/hashes.json"})
    _write(out_dir / "README.md", render_readme(data, {**fields, "files": files}))
    hashes = {name: _sha(out_dir / name) for name in _rel(out_dir) if name != "provenance/hashes.json"}
    _write(out_dir / "provenance" / "hashes.json", jsonutil.dumps(hashes, indent=1))
    _zip(out_dir, zip_path, f"{target_id}_pack_{out_dir.name}")
    built = now.replace(tzinfo=None)
    store.set_pack(target_id, str(out_dir), built)
    store.set_candidate_pack(sid, str(out_dir))
    if zenodo is not None:
        say("filling the Zenodo draft and uploading the zip")
        zenodo.set_metadata(deposition, metadata)
        zenodo.upload(deposition, zip_path)
    say(f"wrote {zip_path}")
    return PackReport(target_id, out_dir, zip_path, _rel(out_dir), approved, zinfo, stale)


def _versions(data: dict, row: dict, now: datetime, Figure) -> dict:
    from importlib import metadata as md
    try:
        version = md.version("skysift")
    except md.PackageNotFoundError:
        version = "unknown"
    mpl = None
    if Figure is not None:
        import matplotlib
        mpl = matplotlib.__version__
    prov, sig = data.get("provenance") or {}, data.get("signal") or {}
    return {"pack_version": PACK_VERSION, "dossier_version": data.get("dossier_version"),
            "target_id": (data.get("target") or {}).get("target_id"), "signal_id": sig.get("id"),
            "search_version": sig.get("detector_version"), "ladder_version": prov.get("ladder_version"),
            "git_commit": prov.get("git_commit"), "pack_git_commit": ds._git_commit(), "skysift_version": version,
            "python": platform.python_version(), "numpy": np.__version__, "matplotlib": mpl,
            "platform": sys.platform, "built_utc": now.isoformat(), "dossier_generated": data.get("generated"),
            "dossier_row_generated": ds._iso(row.get("generated")), "kbonus_doi": KBONUS_DOI}
