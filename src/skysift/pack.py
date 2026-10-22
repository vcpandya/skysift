"""The submission pack of a transit candidate (dossiers spec 5.9, ``skysift pack``).

No portal accepts a new Kepler candidate from outside a mission team, so the pack holds what each route needs, in the
order its README gives them: 1 freeze the record on Zenodo, 2 upload to ExoFOP-TESS, 3 write a Research Note of the
AAS, 4 tell the NASA Exoplanet Archive, 5 answer follow-up requests. ``build_pack`` writes one folder and a zip of it
from the target's stored dossier (``dossier_data.json``) and the light curve the loader serves:

- ``README.md`` (``render_readme``): the five steps, each with the account it needs, the files to attach, the text to
  write and what the engine did or cannot do; an unapproved candidate gets a warning as its first line.
- ``note/note.tex`` (``render_note``): an AASTeX v7 Research Note draft with its abstract and ``figure1``, inside the
  RNAAS limits (``NOTE_LIMITS``: 1,500 words with title, headings, caption and references, 150 of them for the
  abstract, one figure or one table); ``note_words`` counts it.
- ``data/``: the whitened light curve, the ephemeris, parameters and dilution numbers with errors
  (``ephemeris_rows``, ``param_rows``, ``dilution_rows``), the per-transit times (``timing_rows``), the transit shape
  with its bootstrap errors (``shape_rows``), the stellar provenance (``provenance_rows``), the per-event depths, the
  periodogram summary, the rung metrics and the stored TRICERATOPS cross-check (``cross_check.json``, when there is
  one); ``note/figure1_data.csv`` is the data behind the note's figure. The row builders live in
  ``skysift.pack_rows``.
  ``figures/``: fold, odd and even, periodogram and one centroid panel per season, as SVG, plus PNG when matplotlib
  imports. ``dossier.html``: the standalone export. ``provenance/``: versions, the query log, the calibration counts
  and the SHA-256 of every file. ``exofop/``: copies named ``<target>_<yyyymmdd>_<content>``, the upload note and
  ExoFOP's two bulk parameter files (``skysift.exofop``). ``archive_email.txt`` and
  ``zenodo_metadata.json`` for steps 4 and 1.

Building a pack never changes a status, never regenerates the dossier and never downloads anything: a light curve
released by retention raises ``dossier.LightCurveReleased``. Local absolute paths are cut to names relative to the
data folder, since the pack is meant for a public record. The only network use is the injected Zenodo client, called
after every file is written, which makes a draft and never publishes. Both probabilities appear separately in every
text, the false-positive one marked provisional while its factor tables are not signed off. Every value with an
error is printed beside its own error (``skysift.pack_text``), and a missing error is printed as "(no error;
<reason>)" once per text."""
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
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from skysift import calibrate as cal
from skysift import crosscheck as cc
from skysift import dossier as ds
from skysift import fpp as fpp_model
from skysift import jsonutil
from skysift import tess as tess_module
from skysift.exofop import (
    EXOFOP_PLANET_TEMPLATE,
    EXOFOP_STELLAR_TEMPLATE,
    data_tag,
    render_exofop_planet,
    render_exofop_stellar,
)
from skysift.lc.loader import LightCurveLoader
from skysift.pack_rows import (
    CROWDSAP_DEFINITION,
    PSFFRAC_DEFINITION,
    SHAPE_ROWS,
    _metrics,
    _test,
    dilution_rows,
    ephemeris_rows,
    figure_data_rows,
    julian_date,
    next_transits,
    param_rows,
    provenance_rows,
    shape_rows,
    star_characterized,
    timing_rows,
)
from skysift.pack_text import (
    Raw,
    _bjd,
    _f,
    _joined,
    _num,
    date_words,
    ephemeris_sentence,
    pm_digits,
    pm_text,
    prior_claims,
    quantities,
    tex_escape,
    value_text,
)
from skysift.store.db import SignalRow, Store
from skysift.zenodo import KBONUS_DOI, ZenodoClient, ZenodoError, metadata_from, probability_sentences

PACK_VERSION = "3"         # 3: errors in every text and file, ExoFOP files, provenance, dilution (22 Oct 2026)
# the five steps, in the README's order; the dashboard's step list (ui.routes.dossiers.PACK_STEPS) is built from these
STEPS = (("1", "Freeze the record"), ("2", "Upload to ExoFOP-TESS"), ("3", "Write it up"),
         ("4", "Tell the archive"), ("5", "Answer follow-up questions"))
STEP_TEXTS = {
    "1": "Deposit the pack on Zenodo for a citable DOI. The engine can make the draft; you publish it.",
    "2": "Attach the dossier, the ephemeris and the note to the star's ExoFOP page now, and the stellar parameters "
         "when the star is characterised; the planet parameters wait for a community TOI, which needs a peer-reviewed "
         "paper. This needs your own ExoFOP account.",
    "3": "Turn the Research Note draft into a submission to the AAS, with an arXiv endorsement if needed.",
    "4": "Send the NASA Exoplanet Archive help desk the one-paragraph note once a refereed paper on the candidate is "
         "accepted; the Research Note alone does not open this route.",
    "5": "Reply to observers who ask for the ephemeris or the data, from the pack's own files.",
}
DOI_PLACEHOLDER = "10.5281/zenodo.XXXXXXX"
RNAAS_DOI_PLACEHOLDER = "10.3847/2515-5172/XXXXX"
PAPER_DOI_PLACEHOLDER = "PAPER-DOI-TO-FILL"         # the refereed paper the archive email waits for
SOFTWARE_DOI_PLACEHOLDER = "10.5281/zenodo.SOFTWARE"
SOFTWARE = "numpy, scipy, astropy, matplotlib"
CROSS_CHECK_SOFTWARE = "triceratops"         # named only when a cross-check is stored
FACILITIES = "Kepler, Gaia, MAST, Exoplanet Archive"
CODE_SENTENCE = (f"the SkySift code at the stated commit is archived at doi:{SOFTWARE_DOI_PLACEHOLDER} "
                 "(to be minted)")
# the limits of the transit model, in the words of the referee pack spec (section 6)
MODEL_CLAUSE = ("a uniform-disc model without limb darkening on a circular orbit, averaged over the 29.4-minute "
                "cadence")
SANDBOX_DOI_PREFIX = "10.5072/"             # Zenodo sandbox DOIs never resolve
SANDBOX_DOI_NOTE = "sandbox DOI, replace before submission"
ZIP_SUFFIX = "_skysift_pack.zip"
# the Research Notes of the AAS limits (AAS preparation guidelines, read 21 October 2026)
NOTE_LIMITS = {"total": 1500, "abstract": 150, "figures_or_tables": 1}
TEMPLATES = Path(__file__).parent / "templates" / "pack"
AASTEX_FRAME = Path(__file__).parent / "data" / "rnaas" / "aastex_template.tex"
CITATION_FILE = Path(__file__).resolve().parents[2] / "CITATION.cff"     # present in a source checkout only
MARKER = "provenance/versions.json"         # a folder holding this file is a pack and may be rebuilt in place
EXOFOP_URL = "https://exofop.ipac.caltech.edu/tess/"
EXOFOP_HELP_URL = "https://exofop.ipac.caltech.edu/tess/help.php"
HELPDESK_URL = "https://exoplanetarchive.ipac.caltech.edu/support/helpdesk"
_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,31}")
_CONTENT = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
_EXT = re.compile(r"[a-z0-9]{1,8}")
_TARGET = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}")

# figure colours: fixed hex values, the figures are files read outside the dashboard
INK, MUTED, GRID, POINT, MODEL, EVEN, DOTS = "#111827", "#6b7280", "#e5e7eb", "#1f5f8b", "#c2410c", "#b45309", "#9ca3af"
FONT = "IBM Plex Sans, Helvetica, Arial, sans-serif"


class NoDossier(LookupError):
    """The target has no generated dossier (or its data file is gone): the pack is built from it."""

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else "no dossier"


class PackError(ValueError):
    """A request the pack refuses before writing anything: a bad TIC id, tag or output folder, a folder that is not a
    pack, or a live Zenodo draft for a candidate no reviewer has approved. The CLI reports it as a usage error."""


@dataclass
class PackReport:
    target_id: str
    out_dir: Path
    zip_path: Path
    files: list[str]                # paths relative to out_dir, sorted
    approved: bool
    zenodo: dict | None             # {"deposition_id", "draft_url", "sandbox", "doi"} when a draft was made
    stale_reason: str | None = None
    note_words: dict | None = None  # note_words() of the written note

    def as_dict(self) -> dict:
        return jsonutil.sanitize({"target_id": self.target_id, "out_dir": str(self.out_dir),
                                  "zip_path": str(self.zip_path), "files": self.files, "approved": self.approved,
                                  "zenodo": self.zenodo, "stale_reason": self.stale_reason,
                                  "note_words": self.note_words})


# ---------------------------------------------------------------------------------------------------- formatting


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


_TEX_COMMENT = re.compile(r"(?<!\\)%.*")
_FRONT_LINE = re.compile(r"^\s*\\(author|affiliation|email|correspondingauthor|keywords|includegraphics|usepackage)\b"
                         r".*$", re.MULTILINE)
_ENV = re.compile(r"\\(begin|end)\{[^}]*\}(\[[^\]]*\]|\{\})?")
_CITE = re.compile(r"\\cite[pt]?((?:\[[^\]]*\]){0,2})\{([^}]*)\}")     # natbib's optional pre- and postnotes
_DATASET = re.compile(r"\\dataset\[([^\]]*)\]\{[^}]*\}")
_BIBITEM = re.compile(r"\\bibitem\[[^\]]*\]\{[^}]*\}")
_COMMAND = re.compile(r"\\[a-zA-Z]+\*?(\[[^\]]*\])?")


def _cite_words(m: re.Match) -> str:
    """A citation as the reader sees it: its notes' words, then three per key (author, et al., year)."""
    notes = re.sub(r"[\[\]]", " ", m.group(1))
    return f" {notes}" + " cite et-al year" * len(m.group(2).split(","))


def _count(tex: str) -> int:
    """Words as the reader sees them: a citation as three (author, et al., year), a command's name dropped and its
    argument kept (section titles and the caption count), tokens without a letter or digit not counted."""
    tex = _CITE.sub(_cite_words, tex)
    tex = _DATASET.sub(lambda m: f" {m.group(1)} ", tex)
    tex = _BIBITEM.sub(" ", _ENV.sub(" ", tex))
    tex = _COMMAND.sub(" ", tex.replace("\\&", " & ").replace("~", " "))
    tex = re.sub(r"[{}$]", " ", tex)
    return sum(1 for w in tex.split() if re.search(r"[A-Za-z0-9]", w))


def _between(tex: str, env: str) -> tuple[str, str]:
    """(inside the environment, the text with it cut out); ("", tex) when it is absent."""
    m = re.search(rf"\\begin\{{{env}\}}(.*?)\\end\{{{env}\}}", tex, re.DOTALL)
    return (m.group(1), tex[:m.start()] + tex[m.end():]) if m else ("", tex)


def note_words(tex: str) -> dict:
    """The rendered note's words between ``\\begin{document}`` and ``\\end{document}`` (comments stripped first, so a
    mention in the frame's comment is never the start): ``abstract``, ``body`` (title, headings, text, caption),
    ``references`` (the reference list) and ``total``, the number the RNAAS limit of 1,500 applies to. The author
    block and keywords are not counted."""
    text = "\n".join(_TEX_COMMENT.sub("", line) for line in tex.splitlines())
    m = re.search(r"\\begin\{document\}(.*?)\\end\{document\}", text, re.DOTALL)
    text = _FRONT_LINE.sub(" ", m.group(1) if m else text)
    abstract, text = _between(text, "abstract")
    refs, text = _between(text, "thebibliography")
    out = {"abstract": _count(abstract), "body": _count(text), "references": _count(refs)}
    out["total"] = out["abstract"] + out["body"] + out["references"]
    return out


def note_problems(words: dict, tex: str = "") -> list[str]:
    """What breaks the RNAAS limits (``NOTE_LIMITS``), in words; empty when the note fits."""
    out = []
    if words["total"] > NOTE_LIMITS["total"]:
        out.append(f"the note has {words['total']:,} words, over the limit of {NOTE_LIMITS['total']:,}")
    if words["abstract"] > NOTE_LIMITS["abstract"]:
        out.append(f"the abstract has {words['abstract']} words, over the limit of {NOTE_LIMITS['abstract']}")
    displays = len(re.findall(r"\\begin\{(figure|table|deluxetable)\*?\}", tex))
    if displays > NOTE_LIMITS["figures_or_tables"]:
        out.append(f"the note has {displays} figures and tables; RNAAS takes one")
    return out


# ---------------------------------------------------------------------------------------------------- the numbers


def _fpp_class(data: dict) -> str | None:
    for s in (data.get("fpp") or {}).get("scenarios") or []:
        if s.get("name") == "PL":
            return (s.get("factors") or {}).get("class")
    return None


def references(data: dict) -> dict[str, bool]:
    """Which works the dossier's numbers rest on, so the note cites exactly those."""
    star, planet = data.get("star") or {}, data.get("planet") or {}
    route = star.get("route") or "none"
    star_ok = star_characterized(data)
    cls = _fpp_class(data)
    notes = json.dumps((data.get("fpp") or {}).get("scenarios") or [])
    tess = set(tess_module.note_cites(data.get("tess")))       # Christ et al. 2019 alone (fix round 2, N1)
    return {"kbonus": True, "kbonushlsp": True, "morton2012": True,
            "dressing2015": cls == "M", "fressin2013": cls != "M" or "Fressin" in notes,
            "pecaut2013": star_ok and route in ("mann", "mamajek") and value_text(star.get("teff")) is not None,
            "mann2015": star_ok and route == "mann",
            "mann2019": star_ok and route == "mann" and value_text(star.get("mass")) is not None,
            "lindegren2021": star_ok and star.get("distance_pc") is not None,
            "giacalone2021": cross_check_sentence(data) is not None,
            **{key: key in tess for key, _words in tess_module.METHOD_CITES if key != "giacalone2021"},
            "bryson2013": self_referenced(offset_block(data)),
            "deeg2015": _timed(data), "holczer2016": _timed(data), "schmitt2014": _bootstrapped(data),
            "kopparapu2013": star_ok and _hz_where(planet) is not None}


BIBLIOGRAPHY = (
    ("battley2021", r"{Battley} et~al.(2021)",
     r"Battley, M.~P., Kunimoto, M., Armstrong, D.~J., \& Pollacco, D. 2021, MNRAS, 503, 4092"),
    ("bryson2013", r"{Bryson} et~al.(2013)",
     r"Bryson, S.~T., Jenkins, J.~M., Gilliland, R.~L., et~al. 2013, PASP, 125, 889"),
    ("christ2019", r"{Christ} et~al.(2019)", r"Christ, C.~N., Montet, B.~T., \& Fabrycky, D.~C. 2019, AJ, 157, 235"),
    ("deeg2015", r"{Deeg}(2015)", r"Deeg, H.~J. 2015, arXiv:1503.05368"),
    ("dressing2015", r"{Dressing} \& {Charbonneau}(2015)",
     r"Dressing, C.~D., \& Charbonneau, D. 2015, ApJ, 807, 45"),
    ("fressin2013", r"{Fressin} et~al.(2013)", r"Fressin, F., Torres, G., Charbonneau, D., et~al. 2013, ApJ, 766, 81"),
    ("giacalone2021", r"{Giacalone} et~al.(2021)",
     r"Giacalone, S., Dressing, C.~D., Jensen, E.~L.~N., et~al. 2021, AJ, 161, 24"),
    ("guerrero2021", r"{Guerrero} et~al.(2021)",
     r"Guerrero, N.~M., Seager, S., Huang, C.~X., et~al. 2021, ApJS, 254, 39"),
    ("holczer2016", r"{Holczer} et~al.(2016)", r"Holczer, T., et~al. 2016, ApJS, 225, 9"),
    ("kbonus", r"{Mart{\'\i}nez-Palomera} et~al.(2023a)",
     r"Mart{\'\i}nez-Palomera, J., Hedges, C., \& Dotson, J. 2023a, AJ, 166, 265, doi:10.3847/1538-3881/ad0727"),
    ("kbonushlsp", r"{Mart{\'\i}nez-Palomera} et~al.(2023b)",
     (r"Mart{\'\i}nez-Palomera, J., Hedges, C., \& Dotson, J. 2023b, KBonus Kepler Background (KBONUS-BKG) "
      r"high-level science product, MAST, doi:10.17909/7jbr-w430")),
    ("kopparapu2013", r"{Kopparapu} et~al.(2013)",
     (r"Kopparapu, R.~K., Ramirez, R., Kasting, J.~F., et~al. 2013, ApJ, 765, 131 (erratum: 2013, ApJ, 770, 82, "
      r"whose coefficients are used)")),
    ("lindegren2021", r"{Lindegren} et~al.(2021)",
     r"Lindegren, L., Bastian, U., Biermann, M., et~al. 2021, A\&A, 649, A4"),
    ("mann2015", r"{Mann} et~al.(2015)", r"Mann, A.~W., Feiden, G.~A., Gaidos, E., et~al. 2015, ApJ, 804, 64"),
    ("mann2019", r"{Mann} et~al.(2019)", r"Mann, A.~W., Dupuy, T., Kraus, A.~L., et~al. 2019, ApJ, 871, 63"),
    ("morton2012", r"{Morton}(2012)", r"Morton, T.~D. 2012, ApJ, 761, 6"),
    ("pecaut2013", r"{Pecaut} \& {Mamajek}(2013)", r"Pecaut, M.~J., \& Mamajek, E.~E. 2013, ApJS, 208, 9"),
    ("ricker2015", r"{Ricker} et~al.(2015)",
     r"Ricker, G.~R., Winn, J.~N., Vanderspek, R., et~al. 2015, JATIS, 1, 014003"),
    ("schmitt2014", r"{Schmitt} et~al.(2014)", r"Schmitt, J.~R., et~al. 2014, AJ, 148, 28"),
    ("stassun2018", r"{Stassun} et~al.(2018)",
     r"Stassun, K.~G., Oelkers, R.~J., Pepper, J., et~al. 2018, AJ, 156, 102"),
    ("stassun2019", r"{Stassun} et~al.(2019)",
     r"Stassun, K.~G., Oelkers, R.~J., Paegert, M., et~al. 2019, AJ, 158, 138"),
    ("sullivan2015", r"{Sullivan} et~al.(2015)",
     r"Sullivan, P.~W., Winn, J.~N., Berta-Thompson, Z.~K., et~al. 2015, ApJ, 809, 77"),
    ("twicken2025", r"{Twicken} et~al.(2025)",
     r"Twicken, J.~D., Jenkins, J.~M., Caldwell, D.~A., et~al. 2025, RNAAS, 9, 132, doi:10.3847/2515-5172/addec4"),
)


RECOVERY_WORDS = ("SkySift found it again in a blind sweep, so this is an independent recovery and vetting of "
                  "{that}, not a new candidate.")


def _family(author: str) -> str:
    parts = author.split()
    return parts[-1] if parts else author


def prior_keys(data: dict) -> list[tuple[dict, str]]:
    """Each prior claim with its bibliography key, the author's family name and the year ("mehta2026"), a letter
    added when the key is taken by another claim or by ``BIBLIOGRAPHY``."""
    taken = {key for key, _label, _text in BIBLIOGRAPHY}
    out = []
    for claim in prior_claims(data):
        base = re.sub(r"[^a-z]", "", _family(claim["author"]).lower()) + str(claim.get("date") or "")[:4]
        key, n = base, 1
        while key in taken:
            key, n = f"{base}{chr(ord('a') + n)}", n + 1
        taken.add(key)
        out.append((claim, key))
    return out


def prior_bib(data: dict) -> list[tuple[str, Raw, str]]:
    """The bibliography items of the prior claims' Zenodo records: (key, natbib label, plain text the template
    escapes), as "Mehta, K. 2026, <title>, Zenodo, doi:<doi>"."""
    out = []
    for claim, key in prior_keys(data):
        family, year = _family(claim["author"]), str(claim["date"])[:4]
        initials = " ".join(f"{p[0]}." for p in claim["author"].split()[:-1])
        who = f"{family}, {initials}" if initials else family
        out.append((key, Raw(f"{{{tex_escape(family)}}}({tex_escape(year)})"),
                    f"{who} {year}, {claim['title']}, Zenodo, doi:{claim['doi']}"))
    return out


def prior_claim_sentence(data: dict, tex: bool = False) -> str | None:
    """Who reported the signal first, when and where (the DOI of the public record), and that SkySift's result is an
    independent blind recovery and vetting of that claim, never a new candidate. None without a prior claim. With
    ``tex`` a ``Raw`` citing each record (``prior_bib``); the plain text names the record's title instead."""
    pairs = prior_keys(data)
    if not pairs:
        return None
    parts: list = []
    for i, (claim, key) in enumerate(pairs):
        record = f"Zenodo, {date_words(claim['date'])}, doi:{claim['doi']}"
        if i:
            parts.append(", and ")
        parts.append(Raw(rf"\citet{{{key}}}") if tex else claim["author"])
        said = "reported this signal first" if i == 0 else "reported it again"
        parts.append(f" {said} ({record})" if tex else f' {said}, in "{claim["title"]}" ({record})')
    parts.append(". " + RECOVERY_WORDS.format(that="that claim" if len(pairs) == 1 else "those claims"))
    return _joined(tex, *parts)


def priority_sentence(data: dict) -> str | None:
    """README step 2: the prior claim is the record of priority, and what the upload says about it."""
    claims = prior_claims(data)
    if not claims:
        return None
    first = claims[0]
    return (f"the prior claim ({first['author']}, {date_words(first['date'])}, doi:{first['doi']}) is the record of "
            "priority. The upload note presents this result as an independent recovery and vetting of that claim, "
            "and the planet file's paper and notes fields name its DOI.")


def hand_built_sentence(data: dict) -> str | None:
    """The pixel section's one sentence on the hand-built study of the same pixels
    (``dossier.hand_built_localisation``): the largest season-by-season difference in the centroid's distance from
    this star, and where the study is. It says "agrees" only when every season's difference is below its combined
    error, else "differs ... by up to"."""
    block = data.get("hand_built_localisation")
    diff = _num(block.get("max_diff_pix")) if isinstance(block, dict) else None
    if diff is None:
        return None
    n = block.get("n_seasons")
    seasons = f"{n} season{'s' if n != 1 else ''}" if isinstance(n, int) else "the seasons that voted"
    if block.get("agrees") is True:
        return (f"Season by season the centroid's distance from this star agrees with the hand-built localisation of "
                f"the same pixels within {max(diff, 0.01):.2f} px over {seasons} ({block.get('source')}).")
    err = _num(block.get("combined_err_pix"))
    if block.get("agrees") is False and err is not None and err > 0:
        return (f"Season by season the centroid's distance from this star differs from the hand-built localisation "
                f"of the same pixels by up to {diff:.2f} px over {seasons}, more than the seasons' combined error "
                f"({block.get('source')}).")
    return (f"Season by season the centroid's distance from this star differs from the hand-built localisation of the "
            f"same pixels by up to {diff:.2f} px over {seasons} (the error is not available; {block.get('source')}).")


def _timed(data: dict) -> bool:
    """Whether the period and epoch come from the linear fit of the per-transit times (the timing block is ok)."""
    timing = data.get("timing")
    return isinstance(timing, dict) and timing.get("ok") is True


def _bootstrapped(data: dict) -> bool:
    """Whether the shape errors come from the bootstrap over transits (the shape block is ok)."""
    shape = data.get("shape")
    return isinstance(shape, dict) and shape.get("ok") is True


def methods_sentence(data: dict, tex: bool = False) -> str | None:
    """How the errors were made, in one sentence for the note's signal section (ruling h): the linear fit of the
    per-transit mid-times with the reference epoch at the centre of the series (Deeg 2015), the per-transit times by
    template fit (Holczer et al. 2016), the shape errors from a bootstrap over transits quoted as the 16th to 84th
    percentile spread about the bootstrap median (Schmitt et al. 2014), and that the fold, the per-event depths and
    the bootstrap use the search period and epoch (M4). None when neither block is ok."""
    timed, boot = _timed(data), _bootstrapped(data)
    if not (timed or boot):
        return None

    def cite(key: str, words: str):
        return Raw(rf"\citep{{{key}}}") if tex else f"({words})"
    parts: list = []
    if timed:
        parts += [("The period and the reference time come from a weighted linear fit of the per-transit "
                   "mid-times, its reference epoch at the centre of the series "), cite("deeg2015", "Deeg 2015"),
                  ", each transit timed by a fit of the transit template ", cite("holczer2016", "Holczer et al. 2016")]
    if boot:
        parts += ["; the" if timed else "The", " shape errors come from a bootstrap over transits ",
                  cite("schmitt2014", "Schmitt et al. 2014"),
                  ", quoted as the 16th to 84th percentile spread of the resamples about their median"]
    parts.append(". The fold, the per-event depths and the bootstrap use the search period and epoch"
                 + ("; the quoted ephemeris is the linear fit." if timed else "."))
    return _joined(tex, *parts)


def cross_check_sentence(data: dict, tex: bool = False) -> str | None:
    """The TRICERATOPS cross-check beside SkySift's own estimate (``crosscheck.comparison_sentence``), for the four
    texts; None without a cross-check made for this dossier's inputs, which the texts then leave out."""
    return cc.comparison_sentence(data, tex=tex)


def tess_text(data: dict, tex: bool = False) -> str:
    """The TESS statement for the note, after the cross-check sentence (spec 10.8): ``tess.note_sentence`` alone,
    pointing to the README for the noise model and thresholds and citing Christ et al. 2019 only (fix round 2,
    N1: the methods sentence and its references cost the note about 180 words); "TESS coverage was not assessed."
    without the inputs."""
    return tess_module.note_sentence(data.get("tess"), tex)


def plain_reference(key: str) -> str:
    """A ``BIBLIOGRAPHY`` entry as plain text, for the README."""
    text = next(text for k, _label, text in BIBLIOGRAPHY if k == key)
    return text.replace(r"\&", "&").replace("~", " ")


def tess_methods_text(data: dict) -> str | None:
    """The README's TESS section: the methods sentence (each choice credited to its source, spec 10.9), the
    thresholds and noise models, and the full references of the works it cites; None without a counted sector."""
    method = tess_module.methods_text(data.get("tess"))
    if method is None:
        return None
    refs = "; ".join(plain_reference(key) for key, _words in tess_module.METHOD_CITES)
    return f"{method} References: {refs}."


def shape_sentence(data: dict, tex: bool = False) -> str | None:
    """The transit fit in words with the model's limits (``MODEL_CLAUSE``): the radius ratio and T14 with their
    bootstrap errors, b and a/R* with theirs or, when the fit is degenerate, alone with the reason; without a
    bootstrap the values alone (the duration names the reason in the same text). None without a rung 2 fit."""
    sh = _metrics(data, "rung2_shape")
    if sh.get("fit_ok") is not True:
        return None
    rows = {r["key"]: r for r in shape_rows(data)}
    if not rows:                            # a dossier before version 3: rung 2's point values of k and b
        rows = {key: {"value": _num(sh.get(point)), "minus": None, "plus": None, "nd": nd}
                for _name, key, _unit, nd, point in SHAPE_ROWS[:2] if _num(sh.get(point)) is not None}
    if "k" not in rows:
        return None

    offset = any(isinstance(c, dict) and c.get("key") == "shape_offset" for c in data.get("caveats") or [])

    def val(key: str):
        r = rows[key]
        text = pm_text(r["value"], r["minus"], r["plus"], nd=r["nd"], tex=tex)
        boot = _num(r.get("boot_median"))
        if offset and key == "k" and boot is not None and r["minus"] is not None:
            nd = pm_digits(r["minus"], r["plus"])
            return _joined(tex, text, f" (bootstrap median {boot:.{nd}f})")
        return text
    if (data.get("shape") or {}).get("ok") is not True:
        parts = [f"Fitting {MODEL_CLAUSE}, gives a radius ratio of ", val("k")]
        if "b" in rows:
            parts += [" and an impact parameter of ", val("b")]
        return _joined(tex, *parts, ", without errors for the reason given with the duration.")
    parts = [f"Fitting {MODEL_CLAUSE}, gives a radius ratio of ", val("k")]
    if "t14_h" in rows:
        parts += [" and a duration (T14) of ", val("t14_h"), " hours"]
    if "b" in rows and "a_over_r" in rows:
        if rows["b"]["minus"] is None:
            parts += ["; the impact parameter is ", val("b"), " and a/R* is ", val("a_over_r"),
                      f" ({rows['b']['note']})"]
        else:
            parts += [", with an impact parameter of ", val("b"), " and a/R* of ", val("a_over_r")]
    return _joined(tex, *parts, ".")


def _odd_even(data: dict, tex: bool) -> tuple[str, str] | None:
    parity = data.get("parity")
    if not isinstance(parity, dict):
        return None
    odd, even = parity.get("odd") or {}, parity.get("even") or {}
    texts = [pm_text(p.get("depth_ppm"), p.get("err_ppm"), p.get("err_ppm"), nd=0, comma=True, tex=tex)
             for p in (odd, even)]
    if None in texts:
        return None
    return _joined(tex, texts[0], " ppm"), _joined(tex, texts[1], " ppm")


def parity_sentence(data: dict, tex: bool = False) -> str | None:
    """The odd and even depths with their errors and the difference in sigma (the parity block), with rung 2's own
    comparison when the two differ by more than 0.5 sigma; rung 2's depths without errors for an older dossier."""
    sh = _metrics(data, "rung2_shape")
    pair = _odd_even(data, tex)
    rung = _num(sh.get("parity_sigma"))
    if pair is None:
        odd, even, par = _f(sh.get("odd_ppm"), 0, True), _f(sh.get("even_ppm"), 0, True), _f(rung, 1)
        if not (odd and even):
            return None
        return f"The odd and even transits have depths of {odd} and {even} ppm" + (f" ({par} sigma apart)." if par
                                                                                    else ".")
    diff = _num((data.get("parity") or {}).get("difference_sigma"))
    parts = ["The odd and even transits have depths of ", pair[0], " and ", pair[1]]
    if diff is not None:
        parts.append(f" ({diff:.1f} sigma apart")
        if rung is not None and abs(rung - diff) > 0.5:
            parts.append(f"; the vetting rung's own comparison gives {rung:.1f} sigma")
        parts.append(")")
    return _joined(tex, *parts, ".")


ALBEDO_NONE = "no constraint on the geometric albedo"


def albedo_text(data: dict) -> str:
    """What the secondary limit says about the geometric albedo, as a clause; "" without a transit fit. A limit above
    1, or one from a degenerate fit, constrains nothing (ruling k): the block's ``albedo_note``, or for a dossier made
    before the note the stored limit and the shape block's flag."""
    sec = data.get("secondary") or {}
    a = _num(sec.get("albedo_limit"))
    degenerate = (data.get("shape") or {}).get("degenerate") is True
    if sec.get("albedo_note") or (a is not None and (a > 1 or degenerate)):
        return f", {ALBEDO_NONE}"
    return "" if a is None else f", which keeps the geometric albedo below {a:.2f}"


def secondary_sentence(data: dict, tex: bool = False) -> str | None:
    """The phase 0.5 depth with its error, its 3-sigma upper limit, the albedo clause and the strongest dip at any
    other phase (the secondary block); rung 2's depth against the scatter for an older dossier; None without
    either."""
    sec = data.get("secondary")
    if not isinstance(sec, dict) or _num(sec.get("depth_ppm")) is None:
        sh = _metrics(data, "rung2_shape")
        d, rms = _f(sh.get("secondary_ppm"), 0, True), _f(sh.get("control_rms_ppm"), 0, True)
        return (f"No secondary eclipse shows at phase 0.5 ({d} ppm against a scatter of {rms} ppm)." if d and rms
                else None)
    parts = ["At phase 0.5 the depth is ",
             pm_text(sec["depth_ppm"], sec.get("err_ppm"), sec.get("err_ppm"), nd=0, comma=True, tex=tex), " ppm"]
    limit = _f(sec.get("limit_3sigma_ppm"), 0, True)
    if limit:
        parts.append(f", a 3-sigma upper limit of {limit} ppm{albedo_text(data)}")
    top, phase, sigma = (_f(sec.get("strongest_ppm"), 0, True), _f(sec.get("strongest_phase"), 2),
                         _f(sec.get("strongest_sigma"), 1))
    if top and phase and sigma:
        parts.append(f", and the strongest dip at any other phase is {top} ppm at phase {phase} ({sigma} sigma)")
    return _joined(tex, *parts, ".")


def _vetting_sentences(data: dict, tex: bool = False) -> list[str]:
    """Rungs 1 to 4 in words, from their stored metrics and the dossier's error blocks (the transit fit, odd and even,
    the secondary); a rung that did not pass says so with its reason. With ``tex`` the sentences with errors are
    ``Raw`` LaTeX."""
    out = []
    ev = _metrics(data, "rung1_events")
    n_cov, n_data = ev.get("n_covered"), ev.get("n_events_with_data")
    if n_cov is not None and n_data:
        s = f"Of the {n_data} predicted transits with data, {n_cov} are well covered"
        share = _num(ev.get("ses_max_over_mes"))
        s += (f", and no single transit carries more than {share * 100:.0f} percent of the signal." if share is not None
              else ".")
        out.append(s)
    out += [s for s in (shape_sentence(data, tex), parity_sentence(data, tex), secondary_sentence(data, tex)) if s]
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


def _pixel_votes(row: dict | None) -> tuple[list[dict], list[dict]]:
    """(voting, silent) seasons of the pixel rung. As in the dossier, a vote not recorded is not a vote."""
    seasons = [s for s in ((row or {}).get("metrics") or {}).get("seasons") or [] if isinstance(s, dict)]
    return [s for s in seasons if s.get("voting") is True], [s for s in seasons if s.get("voting") is not True]


def _seasons_phrase(near: int, voting: list[dict], silent: list[dict]) -> str:
    """"the dip is nearer this star than its brightest neighbour in N of M ...": what the difference images show."""
    kind = "seasons that voted" if silent else "observing seasons"
    return f"the dip is nearer this star than its brightest neighbour in {near} of {len(voting)} {kind}"


def _silent_sentence(silent: list[dict]) -> str:
    """The seasons that did not vote, each with its stored reason."""
    def label(s: dict) -> str:
        return str(s.get("season")) if s.get("season") is not None else "?"
    if not silent:
        return ""
    if len(silent) == 1:
        s = silent[0]
        return f"Season {label(s)} did not vote ({s.get('non_voting_reason') or 'no reason stored'})."
    names = ", ".join(label(s) for s in silent[:-1]) + f" and {label(silent[-1])}"
    why = "; ".join(f"season {label(s)}: {s.get('non_voting_reason') or 'no reason stored'}" for s in silent)
    return f"Seasons {names} did not vote ({why})."


def _first(value) -> float | None:
    """The value of a stored ``[value, error]`` pair, or None when the pair or its value is missing."""
    return _num(value[0]) if isinstance(value, list) and value else None


def offset_block(data: dict) -> dict | None:
    """The dossier's ``centroid_offset`` block, or for a dossier made before it the block the stored rung 5 seasons
    give (``dossier.centroid_offset``); None without a voting season."""
    block = data.get("centroid_offset")
    if isinstance(block, dict):
        return block
    row = _test(data, "rung5_pixels")
    return ds.centroid_offset(row.get("metrics") or {}) if row else None


def self_referenced(block: dict | None) -> bool:
    """The offset block measured every voting season against the star's own direct-image centroid (its
    ``reference``), so Bryson et al.'s rule applies; the catalogue position plus the fit's common shift is not their
    estimator, and a block from before ``reference`` was read is judged by that string too."""
    return (isinstance(block, dict) and _num(block.get("sigma")) is not None
            and block.get("reference") == ds.SELF_REFERENCES["direct"])


SAME_ERROR_RULE = "Both errors are the larger of the seasons' combined error and their scatter."


def offset_sentence(data: dict, tex: bool = False) -> str | None:
    """The pixel offset as Bryson et al. 2013 quote it (ruling e): the difference-image centroid against the star's own
    out-of-transit image, with its error and sigma, judged by their rule (above 3 sigma and above 0.2 arcsec), and the
    distance from the Gaia position with the one clause that it carries the WCS zero point and the registration. None
    without the block; with ``tex`` a ``Raw`` citing ``bryson2013``."""
    block = offset_block(data)
    if not block:
        return None
    cat, cat_err = block.get("catalogue_referenced_arcsec"), block.get("catalogue_referenced_err_arcsec")
    gaia = ["the distance from the Gaia position (", pm_text(cat, cat_err, cat_err, tex=tex),
            " arcsec) carries the WCS zero point and the registration."]
    off, err, sigma = (_num(block.get(k)) for k in ("self_referenced_arcsec", "self_referenced_err_arcsec", "sigma"))
    if off is None or err is None or sigma is None:
        return _joined(tex, "Averaged over the seasons that voted, the centroid sits ",
                       pm_text(cat, cat_err, cat_err, tex=tex), " arcsec from the Gaia position projected through each "
                       "stamp's WCS, a distance that carries the WCS zero point and the registration.")
    if not self_referenced(block):
        return _joined(tex, f"Against {block.get('reference') or 'the fitted position of the star'} (the star's "
                       "direct-image centroid is not stored for every season) the difference-image centroid is "
                       "offset by ", pm_text(off, err, err, tex=tex), f" arcsec ({sigma:.1f} sigma), not assessed "
                       "from the star's own image; ", *gaia, " ", SAME_ERROR_RULE)
    cite = Raw(r"\citet{bryson2013}") if tex else "Bryson et al. (2013)"
    level, k = _num(block.get("level_arcsec")) or 0.2, _num(block.get("sigma_level")) or 3.0
    verdict = "an offset" if block.get("counts") else "not an offset"
    return _joined(tex, "Against the star's own out-of-transit image the difference-image centroid is offset by ",
                   pm_text(off, err, err, tex=tex), f" arcsec ({sigma:.1f} sigma), {verdict} by the rule of ", cite,
                   f", which asks for more than {k:g} sigma and more than {level:g} arcsec; ", *gaia, " ",
                   SAME_ERROR_RULE)


def pixel_sentence(data: dict, tex: bool = False) -> str:
    """Section 04 in words: whether the pixel check ran, where the difference-image centroid sits in the seasons
    that voted (``offset_sentence``), and which seasons did not vote and why. With ``tex`` a ``Raw``."""
    row = _test(data, "rung5_pixels")
    if row is None:
        return ("The pixel check has not run for this signal, so the dip could still come from a neighbouring star "
                "whose light falls in the same photometry.")
    if row.get("status") == "undetermined":
        return f"The pixel check gave no verdict ({row.get('reason') or 'no reason stored'})."
    voting, silent = _pixel_votes(row)
    if not voting and silent:
        return f"The pixel check gave no verdict: no season voted. {_silent_sentence(silent)}"
    if not voting:
        return f"The pixel check {'passed' if row.get('status') == 'pass' else 'failed'} without per-season numbers."
    near = sum(1 for s in voting if s.get("nearer") == "target")
    text = f"In difference images (out of transit minus in transit) {_seasons_phrase(near, voting, silent)}."
    seasons = voting
    dt = [d for s in seasons if (d := _num(s.get("dist_target_pix"))) is not None]
    dn = [d for s in seasons if (d := _num(s.get("dist_neighbour_pix"))) is not None]
    if dt and dn:
        floor = ds.floor_pix(seasons)
        carried = "" if floor is None else f", its error carrying a measured systematic floor of {floor:.3f} px"
        text += (f" The centroid sits a median {np.median(dt):.2f} pixels from this star and {np.median(dn):.2f} "
                 f"pixels from the neighbour{carried}.")
    cut = ds.partial_box_sentence(seasons)
    if cut:
        text += f" {cut}"
    offset = offset_sentence(data, tex)
    parts: list = [text, " ", offset] if offset else [text]
    text = ""
    pairs = [(t, n) for s in seasons
             if (t := _first(s.get("depth_target_ppm"))) is not None
             and (n := _first(s.get("depth_neighbour_ppm"))) is not None]
    if pairs:
        t = float(np.median([p[0] for p in pairs]))
        n = float(np.median([p[1] for p in pairs]))
        text += (f" A two-source fit gives a median depth of {t:,.0f} ppm on this star and {n:,.0f} ppm on the "
                 "neighbour.")
    hand = hand_built_sentence(data)
    if hand:
        text += f" {hand}"
    if silent:
        text += f" {_silent_sentence(silent)}"
    if row.get("status") == "fail":
        text += " The pixel check failed: the dip may sit on the neighbour."
    return _joined(tex, *parts, text)


def _hz_where(planet: dict) -> str | None:
    if _num(planet.get("hz_inner_au")) is None:
        return None
    if planet.get("in_habitable_zone"):
        return "inside the conservative habitable zone"
    if planet.get("in_optimistic_habitable_zone"):
        return "inside the optimistic habitable zone but not the conservative one"
    return "outside the habitable zone"


def dilution_sentence(data: dict, tex: bool = False) -> str | None:
    """The note's dilution sentence: CROWDSAP and PSFFRAC and the leak depth with its error; None without any."""
    rows = {r["key"]: r for r in dilution_rows(data)}
    words = {"crowdsap": "a target light fraction in the aperture (CROWDSAP)",
             "psffrac": "a PSF flux fraction inside the aperture (PSFFRAC)"}
    named = [f"{words[k]} of {rows[k]['text']}" for k in ("crowdsap", "psffrac") if k in rows]
    parts = [f"The KBonus light curve gives this star {' and '.join(named)}"] if named else []
    leak = rows.get("leak")
    if leak:
        whose = f" (KIC {leak['kic']})" if leak["kic"] else ""
        parts += [", and in" if parts else "In",
                  f" the optimal-aperture flux of the neighbour{whose} the dip at this ephemeris is ",
                  pm_text(leak["value"], leak["minus"], leak["plus"], nd=0, comma=True, tex=tex),
                  " ppm"]
    return _joined(tex, *parts, ".") if parts else None


def skysift_version() -> str:
    from importlib import metadata as md
    try:
        return md.version("skysift")
    except md.PackageNotFoundError:
        return "unknown"


def software_commit(data: dict) -> str:
    """The commit the dossier was generated at, else the checkout's own now, in the dossier's format
    (``dossier.git_commit``, "unknown" without git)."""
    return (data.get("provenance") or {}).get("git_commit") or ds.git_commit()


def search_version(data: dict) -> str | None:
    """The search version the dossier's signal was found with (its stored detector version)."""
    return (data.get("signal") or {}).get("detector_version") or (data.get("provenance") or {}).get("search_version")


def software_line(data: dict) -> str:
    """The note's software statement: "SkySift <version> (search version <v>, commit <sha>), numpy, ...", the
    version from the package metadata, the search version and the commit from the dossier; triceratops only when a
    cross-check is stored."""
    tools = SOFTWARE + (f", {CROSS_CHECK_SOFTWARE}" if data.get("cross_check") else "")
    return (f"SkySift {skysift_version()} (search version {search_version(data) or 'unknown'}, commit "
            f"{software_commit(data)}), {tools}")


def numbers(data: dict, tex: bool = False, after_bjd: float | None = None) -> dict:
    """Everything the four texts print, as text (``tex``: LaTeX-ready, values with errors as ``Raw`` math). The
    ephemeris keys are ``quantities``; ``next_transits`` are the three after ``after_bjd`` (default now)."""
    target, sig = data.get("target") or {}, data.get("signal") or {}
    star, planet, prov = data.get("star") or {}, data.get("planet") or {}, data.get("provenance") or {}
    eph, plots, sh = data.get("ephemeris") or {}, data.get("plots") or {}, _metrics(data, "rung2_shape")
    timing = data.get("timing") if isinstance(data.get("timing"), dict) else {}
    timed = timing.get("ok") is True
    window = ((sig.get("extra") or {}).get("search_window") or {})
    lo, hi = _num(window.get("min_period_days")), _num(window.get("max_period_days"))
    fap_text, fpp_text = probability_sentences(data)
    caveats = [c for c in data.get("caveats") or [] if isinstance(c, dict) and c.get("title")]
    rp, a, teq = planet.get("rp_rearth"), planet.get("a_au"), planet.get("teq_k")
    host = ", ".join(x for x in (
        f"{star['spectral_type']} dwarf" if star.get("spectral_type") else None,
        f"Teff {value_text(star.get('teff'))} K" if value_text(star.get("teff")) else None,
        f"R {value_text(star.get('radius'))} Rsun" if value_text(star.get("radius")) else None,
        f"{value_text(star.get('distance_pc'))} pc" if value_text(star.get("distance_pc")) else None) if x)
    star_ok = star_characterized(data)
    q = quantities(data, tex)
    pair = _odd_even(data, tex)
    sec = data.get("secondary") if isinstance(data.get("secondary"), dict) else {}
    dilution = {r["key"]: r for r in dilution_rows(data)}
    leak = dilution.get("leak")
    return {
        "name": target.get("name") or target.get("target_id"), "target_id": target.get("target_id"),
        "gaia_id": target.get("gaia_id"), "kic": target.get("kic"), "ra": _f(target.get("ra"), 5),
        "dec": _f(target.get("dec"), 5), "gmag": _f(target.get("gmag"), 1),
        "summary": data.get("summary") or "", "signal_id": sig.get("id"),
        "generated_words": date_words(data.get("generated") or ""),
        "search_version": search_version(data),
        "ladder_version": prov.get("ladder_version") or sig.get("ladder_version"),
        **q, "period_title": _f(eph.get("period_days"), 3), "t0_err": q["t0_bjd_err"], "t_ref_err": q["t_ref_bjd_err"],
        "ephemeris_text": ephemeris_sentence(data),
        "timed": timed, "methods_text": methods_sentence(data, tex),
        "rms_min": _f(timing.get("rms_min"), 1) if timed else None,
        "n_used": timing.get("n_used") if timed else None, "n_measured": timing.get("n_measured") if timed else None,
        "next_transits": next_transits(data, julian_date(datetime.now(UTC)) if after_bjd is None else after_bjd),
        "sde": _f(eph.get("sde"), 1), "snr": _f(eph.get("snr"), 1), "n_events": eph.get("n_events"),
        "window": f"{lo:g} to {hi:g} days" if lo is not None and hi is not None else None,
        "vetting": _vetting_sentences(data, tex), "pixel_text": pixel_sentence(data, tex),
        "offset_text": offset_sentence(data),
        "pixel_plain": pixel_sentence(data),
        "odd_text": pair[0] if pair else None, "even_text": pair[1] if pair else None,
        "parity_sigma_text": _f((data.get("parity") or {}).get("difference_sigma"), 1) if pair else None,
        "secondary_text": secondary_sentence(data, tex), "albedo_text": albedo_text(data),
        "secondary_pm": pm_text(sec.get("depth_ppm"), sec.get("err_ppm"), sec.get("err_ppm"), nd=0, comma=True),
        "secondary_limit": _f(sec.get("limit_3sigma_ppm"), 0, True),
        "shape_text": shape_sentence(data, tex),
        "crowdsap": (dilution.get("crowdsap") or {}).get("text"),
        "psffrac": (dilution.get("psffrac") or {}).get("text"),
        "leak_text": leak["text"] if leak else None, "dilution_text": dilution_sentence(data, tex),
        "dilution_rows": list(dilution.values()),
        "software_line": software_line(data), "facilities": FACILITIES, "code_sentence": CODE_SENTENCE,
        "commit": software_commit(data),
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
        "cross_check_sentence": cross_check_sentence(data, tex),
        "cross_check_block": cc.verdict(data.get("cross_check"))[1],
        "cross_check_flag": cc.owner_flag(data),
        "tess_text": tess_text(data, tex), "tess_row": tess_module.readme_row(data.get("tess")),
        "tess_exofop": tess_module.exofop_line(data.get("tess")), "tess_methods": tess_methods_text(data),
        "abstract": abstract_text(data),
        "prior_text": prior_claim_sentence(data, tex), "prior_plain": prior_claim_sentence(data),
        "priority_text": priority_sentence(data),
        "caveat_text": " ".join(f"{c['title'].rstrip('.')}." for c in caveats) or "No caveat is recorded.",
        "window_h": _f(plots.get("window_h"), 1), "bin_minutes": bin_minutes(data),
        "model_words": "transit fit" if plots.get("model_kind") == "fit" else "box model of the detection",
        "param_rows": param_rows(data), "commands": prov.get("commands") or [],
    }


def bin_minutes(data: dict) -> str | None:
    """The fold's bin width in minutes as the caption prints it: the stored ``plots.bin_minutes`` (an eighth of the
    search duration), else, for a dossier made before it, the same eighth of the signal's search duration."""
    stored = _num((data.get("plots") or {}).get("bin_minutes"))
    if stored is None:
        dur = _num((data.get("signal") or {}).get("duration"))
        stored = dur * 1440.0 / 8 if dur else None
    return None if stored is None else f"{stored:.1f}".removesuffix(".0")


def abstract_text(data: dict) -> str:
    """The note's abstract (RNAAS requires one, at most 150 words): the signal, where the pixels put it, the planet
    when the star is characterised, both probabilities and the candidate status, each from the stored numbers."""
    target, eph = data.get("target") or {}, data.get("ephemeris") or {}
    name = target.get("name") or target.get("target_id")
    claims = prior_claims(data)
    first = (f"; {claims[0]['author']} reported the signal first on {date_words(claims[0]['date'])} "
             f"(doi:{claims[0]['doi']})") if claims else ""
    what = "an independent recovery of a transit candidate" if claims else "a transit candidate"
    parts = [(f"This note reports {what} on {name}, a star that Kepler recorded only in the background "
              f"of other targets' pixels{first}."),
             (f"In its KBonus Kepler Background light curve a dip of {_f(eph.get('depth_ppm'), 0, True)} ppm lasting "
              f"{_f(eph.get('duration_h'), 2)} hours repeats every {_f(eph.get('period_days'), 5)} days over "
              f"{eph.get('n_events')} transits.")]
    row = _test(data, "rung5_pixels")
    voting, silent = _pixel_votes(row)
    if row is None:
        parts.append("The pixel check has not run, so a neighbouring star could still cause the dip.")
    elif row.get("status") == "undetermined" or not voting:
        parts.append("The pixel check gave no verdict on which star dims.")
    elif row.get("status") == "fail":
        parts.append("In difference images the dip is nearer a neighbouring star than this one.")
    else:
        near = sum(1 for s in voting if s.get("nearer") == "target")
        parts.append(f"In difference images {_seasons_phrase(near, voting, silent)}{ds.partial_box_clause(voting)}.")
    rp, a = (data.get("planet") or {}).get("rp_rearth"), (data.get("planet") or {}).get("a_au")
    if isinstance(rp, dict) and _num(rp.get("median")) is not None:
        orbit = f" on a {_num(a.get('median')):.3f} AU orbit" if isinstance(a, dict) and _num(a.get("median")) else ""
        parts.append(f"With the stellar radius from Gaia photometry the planet would be about "
                     f"{_num(rp['median']):.1f} Earth radii{orbit}.")
    fap, fpp = data.get("fap") or {}, data.get("fpp") or {}
    against = (f" against {fap['n']:,} null searches of this source" if fap.get("calibrated") and fap.get("n")
               else "")
    tail = ", provisional until its factor tables are reviewed" if not fpp.get("signed_off") else ""
    if fpp.get("neighbours_known") is False:
        fpp_part = ("the false-positive probability is not computed, since the Gaia neighbour list needed for the "
                    "background binary term is missing")
    else:
        fpp_part = (f"a scenario estimate puts the false-positive probability at {fpp.get('words') or 'not computed'}"
                    f"{tail}")
    parts.append(f"The false-alarm probability{against} is {fap.get('words') or 'not computed'}, and {fpp_part}.")
    parts.append("It remains a candidate until independent follow-up confirms it.")
    return " ".join(parts)


def archive_paragraph(data: dict, doi: str, tic: str | None) -> str:
    n = numbers(data)
    where = f" (Gaia DR3 {n['gaia_id']}, RA {n['ra']} deg, Dec {n['dec']} deg)" if n["gaia_id"] else ""
    rp = f", which gives a planet radius of {n['rp_text']} Earth radii" if n["rp_text"] else ""
    exofop = f"under TIC {tic}" if tic else f"on the {n['name']} page"
    cross = f" {n['cross_check_sentence']}" if n["cross_check_sentence"] else ""
    claims = prior_claims(data)
    if claims:
        c = claims[0]
        opening = (f"I write about a transit candidate on {n['name']}{where}, first reported by {c['author']} on "
                   f"{date_words(c['date'])} (doi:{c['doi']}). SkySift's independent recovery and vetting of it is "
                   f"now in an accepted refereed paper (doi:{PAPER_DOI_PLACEHOLDER}), since the archive takes "
                   "parameters from such a paper and not from the Research Note of the AAS that reported the "
                   f"recovery (doi:{RNAAS_DOI_PLACEHOLDER}). SkySift (search version {n['search_version']}, commit "
                   f"{n['commit']}) found it again in a blind sweep of the KBonus Kepler Background light curves. ")
    else:
        opening = (f"I write about a transit candidate on {n['name']}{where}, now in an accepted refereed paper "
                   f"(doi:{PAPER_DOI_PLACEHOLDER}), since the archive takes parameters from such a paper and not "
                   f"from the Research Note of the AAS that first reported it (doi:{RNAAS_DOI_PLACEHOLDER}). SkySift "
                   f"(search version {n['search_version']}, commit {n['commit']}) found it in the KBonus Kepler "
                   "Background light curves. ")
    return opening + (
            f"The ephemeris is {n['ephemeris_text']}{rp}. {n['fap_text']} {n['fpp_text']}{cross} The light curve, "
            f"ephemeris, transit times, vetting metrics and figures are frozen on Zenodo (doi:{doi}) and uploaded to "
            f"ExoFOP-TESS {exofop}. I would be glad to see it considered for the archive's tables, and I can send "
            "anything else you need.")


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


def doi_shown(doi: str | None) -> str:
    """The DOI as the plain texts print it: the placeholder without one, and a sandbox DOI (``10.5072/``, which never
    resolves) only with the words that it must be replaced."""
    if not doi:
        return DOI_PLACEHOLDER
    return f"{doi}, a {SANDBOX_DOI_NOTE}" if doi.startswith(SANDBOX_DOI_PREFIX) else doi


def render_note(data: dict, doi_placeholder: str = DOI_PLACEHOLDER, *, citation: dict | None = None) -> str:
    """The Research Note draft: the AASTeX v7 frame in ``data/rnaas/aastex_template.tex`` and the body from
    ``note.tex.j2`` (title, abstract, sections, one figure, references), citing exactly the works the dossier used.
    A sandbox DOI is printed with the words that it must be replaced before submission."""
    refs = references(data)
    bib = sorted([(key, Raw(label), Raw(text)) for key, label, text in BIBLIOGRAPHY if refs.get(key)]
                 + prior_bib(data), key=lambda item: item[0])
    doi = doi_placeholder or DOI_PLACEHOLDER
    note = f", a {SANDBOX_DOI_NOTE}" if doi.startswith(SANDBOX_DOI_PREFIX) else ""
    body = _env(tex=True).get_template("note.tex.j2").render(
        n=numbers(data, tex=True), refs=refs, bib=bib, doi=doi, doi_note=note, authors=_authors(citation))
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
    "note/note.tex": "the Research Note draft (AASTeX v7)",
    "note/figure1.svg": "the note's figure (the folded transit)",
    "note/figure1.pdf": "the note's figure as PDF, for pdflatex",
    "note/figure1.png": "the note's figure as PNG",
    "data/lightcurve.csv": "the whitened light curve the search ran on: time BKJD, relative flux in ppm, error in "
                           "ppm, quarter",
    "data/ephemeris.csv": "period, mid-transit times, duration, depth, the star and planet parameters and the "
                          "dilution numbers, each with its errors and source",
    "data/ephemeris.json": "the same with the dossier's ephemeris, star and planet blocks",
    "data/transit_times.csv": "every timed transit: epoch, mid-time in BKJD and BJD_TDB, error in days, reduced "
                              "chi-square, whether the linear ephemeris used it and why not",
    "data/shape.csv": "radius ratio, impact parameter, a/R*, T14 and the depths with their bootstrap errors, and why "
                      "an error is missing",
    "data/stellar_provenance.csv": "each stellar parameter with its errors, method, inputs and reference",
    "note/figure1_data.csv": "the data behind the note's figure: phase in hours, binned flux, error and count, and "
                             "the model, in ppm",
    f"exofop/{EXOFOP_STELLAR_TEMPLATE}": "the stellar parameters in ExoFOP's bulk template (upload now)",
    "data/events.csv": "every transit: epoch, mid-time, quarter, depth, error, coverage, single-event statistic",
    "data/periodogram.json": "the periodogram envelope, its peaks and the alias and systematic periods",
    "data/rung_metrics.json": "every vetting rung's status, reason and metrics",
    "data/cross_check.json": "the independent TRICERATOPS cross-check: every run's false-positive probability with "
                             "and without STP, nearby false-positive probability, P(STP) and P(PTP), their medians "
                             "and 16th to 84th percentiles, the median run's scenario table, the stars with their "
                             "flux shares and parameter sources, the inputs it was made for and the caveats",
    "dossier.html": "the standalone dossier (opens offline in any browser)",
    "archive_email.txt": "the paragraph for the NASA Exoplanet Archive help desk (step 4)",
    "zenodo_metadata.json": "the Zenodo metadata, for a deposit made by hand (step 1)",
    "provenance/versions.json": "pack, dossier, search and ladder versions, the git commit and where the creators "
                                "came from",
    "provenance/queries.json": "every catalogue and data request for this target, with URL, time, status and SHA-256",
    "provenance/hashes.json": "the SHA-256 of every other file in the pack",
    "provenance/calibration.json": "the null-search counts behind the false-alarm probability",
    "exofop/note.txt": "the text for the ExoFOP upload's description (step 2)",
}


def _safe_target(target_id: str) -> str:
    if not _TARGET.fullmatch(str(target_id)):
        raise PackError(f"not a target id: {target_id!r}")
    return str(target_id).replace(":", "-")


def exofop_name(target_id: str, date: str, content: str, ext: str) -> str:
    """The pack's name for a file it offers for ExoFOP, ``<target>_<UTC yyyymmdd>_<content>.<ext>``. ExoFOP enforces
    no file name (an upload is grouped by the Data Tag set on its form); this one says what the file is and which
    build it came from. Raises PackError for a bad target id, date, content or extension."""
    if not re.fullmatch(r"\d{8}", date):
        raise PackError(f"the date is yyyymmdd, not {date!r}")
    for value, rule, what in ((content, _CONTENT, "content"), (ext, _EXT, "extension")):
        if not rule.fullmatch(value):
            raise PackError(f"not a valid {what}: {value!r}")
    return f"{_safe_target(target_id)}_{date}_{content}.{ext}"


def check_tic(tic: str | None) -> str | None:
    """The TIC id as digits ("TIC 169822650" gives "169822650"), or None; PackError for anything else."""
    if tic is None:
        return None
    digits = re.sub(r"^TIC", "", str(tic).strip(), flags=re.IGNORECASE).strip()
    if not digits.isdigit():
        raise PackError(f"a TIC id is digits, not {tic!r}")
    return digits


def check_tag(tag: str) -> str:
    if not _TAG.fullmatch(tag or ""):
        raise PackError(f"the tag is 1 to 32 letters, digits and hyphens, starting with a letter or digit, not "
                        f"{tag!r}")
    return tag


def _exofop_list(target_id, date, png: bool, pixels_figure: bool) -> list[tuple[str, str, str]]:
    """(file name, pack source, what it is) for the files step 2 attaches: each figure once, as PNG when the PNGs
    were drawn, else as SVG."""
    out = []
    for content, source, what in EXOFOP_CONTENTS:
        if content == "centroids" and not pixels_figure:
            continue
        ext = ("png" if png else "svg") if "{ext}" in source else source.rsplit(".", 1)[1]
        out.append((exofop_name(target_id, date, content, ext), source.format(ext=ext), what))
    return out


def gate_problems(raw: dict) -> list[str]:
    """Why a live Zenodo draft is refused on the stored dossier JSON (with its stored cross-check merged in as
    ``cross_check``), one sentence per problem: the ephemeris carries no error (no timing block, or its fit is not
    ok), the transit shape carries no error (no shape block, or it is not ok), and ``crosscheck.verdict``'s reason
    (no cross-check, one stored by an earlier version of the tool or one made for an earlier dossier; its numbers
    never block, ruling t), and a stored
    commit that is dirty or unknown (the software statement could not name archived code; ruling g), a pixel verdict
    made under an older pixel rule (``dossier.pixel_rule_stale``), a pixel rerun that disagrees with the reviewer
    (``dossier.pixel_verdict_disagrees``), prior claims that were not checked (``dossier.prior_claims_unchecked``)
    and a rendered Research Note over the RNAAS limit of 1,500 words (``note_words``; the TESS statement itself never
    blocks). Empty when the dossier may go to zenodo.org."""
    out = []
    for key, what in (("timing", "the ephemeris"), ("shape", "the transit shape")):
        block = raw.get(key)
        if not isinstance(block, dict):
            out.append(f"{what} carries no error (no {key} block)")
        elif block.get("ok") is not True:
            out.append(f"{what} carries no error ({block.get('reason') or 'no reason stored'})")
    blocked, reason = cc.verdict(raw.get("cross_check"))
    if blocked:
        out.append(reason)
    commit = str((raw.get("provenance") or {}).get("git_commit") or "unknown")
    if commit == "unknown" or commit.endswith("-dirty"):
        out.append("the stored commit is dirty or unknown")
    stale = ds.pixel_rule_stale(raw)
    if stale:
        out.append(f"{stale}; the recheck reruns the pixel step first")
    if ds.pixel_verdict_disagrees(raw):
        out.append("the pixel rerun disagrees with the reviewer's decision; a reviewer looks again first")
    words = note_words(render_note(raw))["total"]
    if words > NOTE_LIMITS["total"]:
        out.append(f"the Research Note has {words:,} words, over the RNAAS limit of {NOTE_LIMITS['total']:,}; "
                   "shorten it first")
    if ds.prior_claims_unchecked(raw):
        out.append("prior claims on the signal were not checked (no docs/candidates folder)")
    return out


def independent_periods(period: float, others, tolerance: float = 0.02) -> list[float]:
    """The periods among ``others`` that are independent of ``period``: not within ``tolerance`` (relative) of it,
    nor of 2 or 3 times it, nor of a half or a third of it. Sorted, each once."""
    ratios = (1.0, 2.0, 3.0, 1 / 2, 1 / 3)
    out: list[float] = []
    for p in others:
        p = _num(p)
        if p is None or p <= 0 or p in out:
            continue
        if not any(abs(p - period * r) <= tolerance * period * r for r in ratios):
            out.append(p)
    return sorted(out)


def other_signal_periods(store, target_id: str, signal_id: int) -> list[float]:
    """The periods of the target's other stage 2 signals (store status ``candidate_stage2``); [] without a store or
    when the signal table cannot be read."""
    if store is None:
        return []
    try:
        with store.session() as s:
            return [float(p) for p in s.scalars(select(SignalRow.period).where(
                SignalRow.target_id == target_id, SignalRow.id != signal_id,
                SignalRow.final_status == "candidate_stage2"))]
    except SQLAlchemyError:
        return []


def other_signals_sentence(periods: list[float]) -> str:
    """The README's sentence for a star with another independent stage 2 signal; "" without one."""
    if not periods:
        return ""
    listed = [f"{p:.3f}" for p in periods]
    what = (f"another stage 2 signal at {listed[0]} days" if len(listed) == 1
            else f"other stage 2 signals at {', '.join(listed[:-1])} and {listed[-1]} days")
    return (f"This star also has {what}, independent of this one; this pack covers one candidate only, so that "
            "signal needs its own dossier and pack.")


def zip_name(target_id: str, date: str) -> str:
    """The pack's zip, ``<target>_<yyyymmdd>_skysift_pack.zip``: the name Zenodo and a download show."""
    return f"{_safe_target(target_id)}_{date}{ZIP_SUFFIX}"


def _fields(data: dict, f: dict) -> dict:
    f = dict(f)
    target_id = (data.get("target") or {}).get("target_id") or "target"
    f.setdefault("approved", False)
    f.setdefault("stale_reason", None)
    f.setdefault("tic", None)
    f.setdefault("date", datetime.now(UTC).strftime("%Y%m%d"))
    f.setdefault("tag", "skysift")
    f.setdefault("png", False)
    f.setdefault("zenodo", None)
    f.setdefault("pixels_figure", bool(data.get("pixels")))
    f.setdefault("zip_name", zip_name(target_id, f["date"]))
    f.setdefault("files", [])
    f.setdefault("built_words", date_words(datetime.now(UTC)))
    f.setdefault("pack_version", PACK_VERSION)
    f.setdefault("creators_source", "no CITATION.cff found, so the creators are placeholders")
    doi = (f.get("zenodo") or {}).get("doi")
    f.setdefault("doi", doi or DOI_PLACEHOLDER)
    f.setdefault("doi_shown", doi_shown(doi))
    if "note_words" not in f:
        tex = render_note(data, f["doi"])
        f["note_words"], f["note_problems"] = note_words(tex), note_problems(note_words(tex), tex)
    f.setdefault("note_problems", note_problems(f["note_words"]))
    f.setdefault("limits", NOTE_LIMITS)
    f.setdefault("tag_seq", 1)
    f.setdefault("data_tag", data_tag(f["date"], f["tag"], _safe_target(target_id), f["tag_seq"]))
    day = datetime.strptime(f["date"], "%Y%m%d").replace(tzinfo=UTC)
    f.setdefault("after_bjd", julian_date(day))
    f.setdefault("after_words", date_words(day))
    f.setdefault("other_signals", "")
    stellar = EXOFOP_STELLAR_TEMPLATE.format(date=f["date"]) if star_characterized(data) else None
    f.setdefault("exofop_stellar", stellar)
    f.setdefault("exofop_planet", EXOFOP_PLANET_TEMPLATE.format(date=f["date"]))
    f.setdefault("exofop", [(name, what) for name, _src, what in
                            _exofop_list(target_id, f["date"], f["png"], f["pixels_figure"])]
                 + ([(stellar, FILE_NOTES[f"exofop/{EXOFOP_STELLAR_TEMPLATE}"])] if stellar else []))
    f.setdefault("file_notes", [(name, FILE_NOTES.get(name) or _figure_note(name)) for name in sorted(f["files"])
                                if not name.startswith("exofop/") or name == "exofop/note.txt"])
    f.setdefault("exofop_url", EXOFOP_URL)
    f.setdefault("exofop_help_url", EXOFOP_HELP_URL)
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
    stale_reason, zip_name, pixels_figure, note_words, creators_source, after_bjd for step 5's transits,
    other_signals); missing ones take defaults."""
    f = _fields(data, report_fields)
    return _env(tex=False).get_template("README.md.j2").render(
        n=numbers(data, after_bjd=f["after_bjd"]), f=f, titles=dict(STEPS), sandbox_note=SANDBOX_DOI_NOTE,
        crowdsap_definition=CROWDSAP_DEFINITION, psffrac_definition=PSFFRAC_DEFINITION,
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
    if xlog and xlo <= 0:                # an empty log axis (a dossier without its periodogram) spans a decade
        xlo, xhi = (xhi / 10.0, xhi) if xhi > 0 else (1.0, 10.0)
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
    """The pack's files, without its own zip (which sits in the folder, so two folders never share one)."""
    return sorted(name for p in out_dir.rglob("*") if p.is_file()
                  and not (name := p.relative_to(out_dir).as_posix()).endswith((ZIP_SUFFIX, ZIP_SUFFIX + ".tmp")))


def _zip(out_dir: Path, zip_path: Path, top: str) -> None:
    tmp = zip_path.with_name(zip_path.name + ".tmp")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name in _rel(out_dir):
            z.write(out_dir / name, f"{top}/{name}")
    tmp.replace(zip_path)


def load_citation(path: Path = CITATION_FILE) -> tuple[dict | None, str]:
    """(citation, where it came from) for the note's authors and the Zenodo creators: the repository's
    ``CITATION.cff`` when present (a source checkout), else the installed package's author metadata (from
    ``pyproject.toml``), else (None, a sentence saying the creators are placeholders)."""
    import yaml
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        data = None
    if isinstance(data, dict) and data.get("authors"):
        return data, "CITATION.cff in the source checkout"
    from importlib import metadata as md
    try:
        meta = md.metadata("skysift")
        fields = [*(meta.get_all("Author") or []), *(meta.get_all("Author-email") or [])]
    except md.PackageNotFoundError:
        fields = []
    names: list[str] = []
    for field in fields:
        for part in str(field).split(","):
            name = re.sub(r"<[^>]*>", "", part).strip().strip('"')
            if name and "@" not in name and name not in names:
                names.append(name)
    if names:
        return {"authors": [{"name": n} for n in names]}, "the installed package's author metadata (pyproject.toml)"
    return None, "no CITATION.cff and no package author metadata, so the creators are placeholders"


def _approval(store: Store, target_id: str, signal_id: int, data_dir) -> tuple[bool, str | None]:
    """(approved, stale reason): approved when the signal's latest reviewer decision approves it (or its candidate row
    says approved or published); stale when the stored dossier no longer matches the signal's tests, status and
    decision, or another signal became the target's best."""
    sig = ds.pick_signal(store, target_id, signal_id)
    approved = sig.get("decision") == "approve" or sig.get("candidate_status") in ("approved", "published")
    row = store.dossier(target_id) or {}
    source = ds._target_dict(store, target_id).get("source")
    fp = ds.fingerprint(store.latest_tests(signal_id), sig["final_status"], sig.get("decision"),
                        ds.calibration_rows(data_dir, source))
    if ds.pick_signal(store, target_id)["id"] != signal_id:
        return approved, "another signal is now the target's best"
    if row.get("tests_fingerprint") != fp:
        return approved, ("the signal's tests, status or reviewer decision, or the null calibration, changed since "
                          "it was generated")
    return approved, None


# every absolute local path cut to a name relative to the data folder, whole or inside a sentence (shared with the
# standalone export)
public_paths = ds.public_paths


def _calibration_path(raw: dict, data_dir: Path) -> Path | None:
    path = (raw.get("provenance") or {}).get("calibration_file")
    if not path:
        return None
    path = Path(path)
    return path if path.is_absolute() else Path(data_dir) / path


def _calibration(raw: dict, data: dict, data_dir: Path) -> dict:
    """The null-search counts behind the FAP. The file is read at its stored path (a relative one against the data
    folder, not the current folder) and named relative to the data folder."""
    path = _calibration_path(raw, data_dir)
    counts: dict[str, int] = {}
    if path is not None and path.is_file():
        for r in cal.read_rows(path):
            v = str(r.get("search_version"))
            counts[v] = counts.get(v, 0) + 1
    return {"fap": data.get("fap"), "calibration_file": (data.get("provenance") or {}).get("calibration_file"),
            "calibration_sha256": _sha(path)["sha256"] if path is not None and path.is_file() else None,
            "rows_by_version": counts,
            "rule": "FAP = (k + 1) / (n + 1) over both null kinds of every row; not calibrated below "
                    f"{cal.MIN_NULLS} null searches"}


def _check_out(out_dir: Path) -> None:
    """Refuse an output folder before any work: one without a name (a drive root), a file, or a non-empty folder
    that is not an earlier pack (it would be replaced, so a pack never deletes files it did not write)."""
    if not out_dir.name:
        raise PackError(f"{out_dir} has no folder name; give --out a folder such as packs/<target>")
    if not out_dir.exists():
        return
    if not out_dir.is_dir():
        raise PackError(f"{out_dir} is a file, not a folder")
    if any(out_dir.iterdir()):
        try:
            marker = json.loads((out_dir / MARKER).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            marker = None
        if not isinstance(marker, dict) or "pack_version" not in marker:
            raise PackError(f"{out_dir} is not empty and is not a submission pack; choose another --out")


def _building(out_dir: Path) -> Path:
    """The empty folder a pack is written into before it replaces ``out_dir`` (``_check_out`` has passed): a
    hidden sibling, so a build that crashes leaves neither a partial pack nor a folder that blocks the retry."""
    work = out_dir.with_name(f".{out_dir.name}.building")
    if work.exists():
        shutil.rmtree(work)              # only a crashed earlier build of this folder leaves it
    work.mkdir(parents=True)
    return work


def _move_into_place(work: Path, out_dir: Path, say: Callable[[str], None]) -> None:
    """The finished pack in ``work`` replaces ``out_dir`` without ever losing the earlier pack: (1) the earlier pack
    is renamed aside to ``.<name>.old``, (2) the new one is renamed into place, (3) only then is the old one deleted.
    A rename that fails raises PackError with the earlier pack where it was and the new one still in ``work`` (on
    Windows a file held open in the earlier pack, such as the zip during a download, stops step 1). A deletion that
    fails at step 3 only logs a warning: the new pack is in place by then, so nothing is raised."""
    old = out_dir.with_name(f".{out_dir.name}.old")
    keep = (f"the new pack is complete in {work} (the next run builds it again); close whatever holds the files and "
            "run the command again")
    if old.exists():
        try:
            shutil.rmtree(old)           # left by an earlier swap whose step 3 failed
        except OSError as e:
            raise PackError(f"could not delete {old}, left by an earlier build ({e}); the pack at {out_dir} is "
                            f"untouched, and {keep}") from e
    had_old = out_dir.exists()
    if had_old:
        try:
            out_dir.rename(old)
        except OSError as e:
            raise PackError(f"could not move the earlier pack at {out_dir} aside ({e}); it is untouched, and "
                            f"{keep}") from e
    try:
        work.rename(out_dir)
    except OSError as e:
        if not had_old:
            raise PackError(f"could not move the new pack into {out_dir} ({e}); {keep}") from e
        try:
            old.rename(out_dir)
        except OSError as back:
            raise PackError(f"could not move the new pack into {out_dir} ({e}) nor the earlier pack back ({back}): "
                            f"the earlier pack is in {old}, and {keep}") from e
        raise PackError(f"could not move the new pack into {out_dir} ({e}); the earlier pack is back in place, and "
                        f"{keep}") from e
    if had_old:
        try:
            shutil.rmtree(old)
        except OSError as e:
            say(f"warning: the new pack is in {out_dir}, but the earlier one could not be deleted from {old} ({e}); "
                "the next run deletes it, or delete that folder by hand")


def _write_texts(out_dir: Path, zip_path: Path, data: dict, fields: dict, citation: dict | None) -> dict:
    """The four texts, then the hashes and the zip, for the DOI in ``fields`` (written again once a Zenodo draft
    reserved one). Returns the note's word counts."""
    tex = render_note(data, fields["doi"], citation=citation)
    _write(out_dir / "note" / "note.tex", tex)
    words = note_words(tex)
    fields = {**fields, "note_words": words, "note_problems": note_problems(words, tex)}
    _write(out_dir / "exofop" / "note.txt", _render_plain("exofop_note.txt.j2", data, fields))
    _write(out_dir / "archive_email.txt", _render_plain("archive_email.txt.j2", data, fields))
    files = sorted({*_rel(out_dir), "README.md", "provenance/hashes.json"})
    _write(out_dir / "README.md", render_readme(data, {**fields, "files": files}))
    hashes = {name: _sha(out_dir / name) for name in _rel(out_dir) if name != "provenance/hashes.json"}
    _write(out_dir / "provenance" / "hashes.json", jsonutil.dumps(hashes, indent=1))
    _zip(out_dir, zip_path, zip_path.stem)
    return words


def build_pack(store, blob, settings, target_id: str, *, out_dir: Path | None = None, tic: str | None = None,
               observer_tag: str = "skysift", tag_seq: int = 1, zenodo: ZenodoClient | None = None,
               log: Callable[[str], None] | None = None) -> PackReport:
    """Write the submission pack of ``target_id`` from its stored dossier and zip it inside the folder as
    ``<target>_<yyyymmdd>_skysift_pack.zip``; the default folder is ``<data_dir>/packs/<target_id>/<UTC yyyymmdd>/`` (an
    earlier pack there is replaced only once the new one is complete: it is written in a hidden sibling folder and
    swapped in by renames, so a crash or a held file leaves the earlier pack and nothing that blocks a retry).
    Everything is checked and written first; only then, with a ``zenodo`` client, is the draft created, its reserved DOI
    written into the texts (a sandbox DOI with the words that it must be replaced), the metadata filled and the zip
    uploaded. The draft is never published, and a live draft is refused for a candidate no reviewer has approved,
    while the FPP is provisional (``fpp.SIGNED_OFF`` False), for a stale dossier and for one whose ephemeris or
    shape carries no error or whose TRICERATOPS cross-check is missing, stored by an earlier version of the tool or
    made for an earlier dossier (``gate_problems``), so a provisional number never reaches a live record. The README
    names the target's other stage 2 signals whose periods are independent of this one (``independent_periods``).
    Records the folder on the dossiers row and the candidate row.

    Raises NoDossier, ``dossier.LightCurveReleased`` (nothing is downloaded), PackError (bad TIC id, tag or folder; a
    live draft before approval, before the FPP sign-off, for a stale dossier, or for one ``gate_problems`` refuses,
    naming every problem) before anything is written, and ``zenodo.ZenodoError`` naming the built pack and any
    unfinished draft when Zenodo fails."""
    say = log or (lambda _text: None)
    tic, observer_tag = check_tic(tic), check_tag(observer_tag)
    if not (isinstance(tag_seq, int) and 1 <= tag_seq <= 99999):
        raise PackError(f"the Data Tag sequence is a number from 1 to 99999, not {tag_seq!r}")
    _safe_target(target_id)
    row = store.dossier(target_id)
    if row is None or not row.get("data_path") or not Path(row["data_path"]).is_file():
        raise NoDossier(f"no dossier for {target_id}; run skysift dossier --target {target_id} first")
    raw = json.loads(Path(row["data_path"]).read_text(encoding="utf-8"))
    raw["cross_check"] = cc.with_current(row.get("cross_check"), raw)      # imported after the JSON was written
    sid = int(row["signal_id"])
    now = datetime.now(UTC)
    date = now.strftime("%Y%m%d")
    out_dir = Path(out_dir if out_dir is not None else Path(settings.data_dir) / "packs" / target_id / date).resolve()
    _check_out(out_dir)
    approved, stale = _approval(store, target_id, sid, settings.data_dir)
    if zenodo is not None and not zenodo.sandbox and not approved:
        raise PackError(f"{target_id} is not yet approved by a reviewer, so no draft is made on zenodo.org; use "
                        "--zenodo for a sandbox rehearsal")
    if zenodo is not None and not zenodo.sandbox and not fpp_model.SIGNED_OFF:
        raise PackError("the false-positive estimate is provisional until spec 4.2's factor tables are signed off, "
                        "so no draft is made on zenodo.org; use --zenodo for a sandbox rehearsal")
    if zenodo is not None and not zenodo.sandbox and not (raw.get("fpp") or {}).get("signed_off"):
        raise PackError(f"the stored dossier of {target_id} was written before the false-positive sign-off, so no "
                        f"draft is made on zenodo.org; run skysift dossier --target {target_id} --force first")
    if zenodo is not None and not zenodo.sandbox and stale:
        raise PackError(f"the stored dossier of {target_id} is stale ({stale}), so no draft is made on zenodo.org; "
                        f"run skysift dossier --target {target_id} first")
    problems = gate_problems(raw)
    if zenodo is not None and not zenodo.sandbox and problems:
        raise PackError(f"the stored dossier of {target_id} is not ready for zenodo.org: {'; '.join(problems)}. No "
                        "draft is made there; use --zenodo for a sandbox rehearsal")
    period = _num((raw.get("ephemeris") or {}).get("period_days")) or _num((raw.get("signal") or {}).get("period"))
    others = independent_periods(period, other_signal_periods(store, target_id, sid)) if period else []
    say("loading the light curve")
    target = ds._target_dict(store, target_id)
    lc = LightCurveLoader(store, blob, source_factory=ds._offline_factory(target_id, sid)).load(target, pin=True)
    fnorm = ds.whitened(lc, raw["signal"])
    citation, creators_source = load_citation()
    if citation is None:
        say("no CITATION.cff or package author found; the creators are placeholders")
    data = ds.public_claims(public_paths(raw, settings.data_dir))     # the pack embeds and uploads this JSON
    final = out_dir
    out_dir = _building(final)
    try:
        zip_file, metadata, fields, words = _write_pack(
            out_dir, data, raw, row, lc, fnorm, target_id, date, now, settings, citation, creators_source,
            {"approved": approved, "stale_reason": stale, "tic": tic, "tag": observer_tag, "tag_seq": tag_seq,
             "other_signals": other_signals_sentence(others)}, say)
    except BaseException:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise
    _move_into_place(out_dir, final, say)
    out_dir, zip_path = final, final / zip_file
    store.set_pack(target_id, str(out_dir), now.replace(tzinfo=None))
    store.set_candidate_pack(sid, str(out_dir))
    say(f"wrote {zip_path}")
    zinfo = None
    if zenodo is not None:
        deposition = None
        try:
            say("creating the Zenodo draft")
            deposition = zenodo.create_deposition()
            zinfo = {"deposition_id": deposition.get("id"), "draft_url": zenodo.draft_url(deposition),
                     "sandbox": zenodo.sandbox, "doi": zenodo.reserved_doi(deposition)}
            doi = zinfo["doi"] or DOI_PLACEHOLDER
            words = _write_texts(out_dir, zip_path, data, {**fields, "zenodo": zinfo, "doi": doi,
                                                           "doi_shown": doi_shown(zinfo["doi"])}, citation)
            say("filling the Zenodo draft and uploading the zip")
            zenodo.set_metadata(deposition, metadata)
            zenodo.upload(deposition, zip_path)
        except Exception as e:  # any failure after the files exist is reported with the draft
            if deposition is None:
                raise ZenodoError(f"{e}; the pack at {out_dir} was built without a Zenodo draft") from e
            raise ZenodoError(f"{e}; the pack at {out_dir} was built, but the unpublished Zenodo draft "
                              f"{deposition.get('id')} ({zenodo.draft_url(deposition)}) is unfinished: discard it on "
                              "Zenodo, then run the command again") from e
    return PackReport(target_id, out_dir, zip_path, _rel(out_dir), approved, zinfo, stale, words)


def _write_pack(out_dir: Path, data: dict, raw: dict, row: dict, lc, fnorm, target_id: str, date: str,
                now: datetime, settings, citation: dict | None, creators_source: str, review: dict, say):
    """Every file of the pack into ``out_dir`` (the building folder) and its zip there, with the DOI placeholder.
    Returns (zip file name, Zenodo metadata, the text fields, the note's word counts)."""
    zip_path = out_dir / zip_name(target_id, date)
    say("writing the pack")
    figures, note, d = out_dir / "figures", out_dir / "note", out_dir / "data"
    for folder in (figures, note, d, out_dir / "provenance", out_dir / "exofop"):
        folder.mkdir(parents=True, exist_ok=True)
    # data
    _lightcurve_csv(d / "lightcurve.csv", lc, fnorm)
    eph_rows = ephemeris_rows(data) + param_rows(data) + dilution_rows(data)
    cols = ["parameter", "value", "minus", "plus", "unit", "source"]
    _csv(d / "ephemeris.csv", cols, [[r[c] for c in cols] for r in eph_rows])
    for name, header, table in (
            ("transit_times.csv", ["epoch", "t_bkjd", "t_bjd", "err_days", "chi2_red", "used", "reason"],
             timing_rows(data)),
            ("shape.csv", ["parameter", "value", "minus", "plus", "unit", "boot_median", "note"], shape_rows(data)),
            ("stellar_provenance.csv", ["parameter", "value", "minus", "plus", "unit", "method", "inputs",
                                        "reference"], provenance_rows(data))):
        _csv(d / name, header, [[r[c] for c in header] for r in table])
    _csv(note / "figure1_data.csv", ["phase_h", "flux_ppm", "err_ppm", "n", "model_ppm"], figure_data_rows(data))
    eph = data.get("ephemeris") or {}
    eph = {**eph, **{f"{k}_bjd": _bjd(eph, k) for k in ("t0", "t_ref") if f"{k}_bjd" in eph}}
    _write(d / "ephemeris.json", jsonutil.dumps({"ephemeris": eph, "star": data.get("star"),
                                                 "planet": data.get("planet"),
                                                 "rows": [{c: r[c] for c in cols} for r in eph_rows]}, indent=1))
    ev_cols = ["epoch", "t_mid", "quarter", "depth_ppm", "err_ppm", "coverage", "ses"]
    plots = data.get("plots") or {}
    _csv(d / "events.csv", ["epoch", "t_mid_bkjd", "quarter", "depth_ppm", "err_ppm", "coverage", "ses"],
         [[e.get(c) for c in ev_cols] for e in plots.get("events") or []])
    _write(d / "periodogram.json", jsonutil.dumps({"sde": (data.get("ephemeris") or {}).get("sde"),
                                                   "period_days": (data.get("ephemeris") or {}).get("period_days"),
                                                   "spectrum": plots.get("spectrum"),
                                                   "lines": plots.get("spectrum_lines"),
                                                   "columns": ["period_days", "sde"]}, indent=1))
    _write(d / "rung_metrics.json", jsonutil.dumps(data.get("tests") or [], indent=1))
    if data.get("cross_check"):
        _write(d / "cross_check.json", jsonutil.dumps(data["cross_check"], indent=1))
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
    # the standalone page, provenance, the ExoFOP copies
    _write(out_dir / "dossier.html", ds.export_html(data))
    prov = data.get("provenance") or {}
    _write(out_dir / "provenance" / "queries.json", jsonutil.dumps(prov.get("queries") or [], indent=1))
    _write(out_dir / "provenance" / "calibration.json",
           jsonutil.dumps(_calibration(raw, data, settings.data_dir), indent=1))
    _write(out_dir / MARKER, jsonutil.dumps(_versions(data, row, now, Figure, zip_path.name, creators_source),
                                            indent=1))
    metadata = metadata_from(data, citation, prior=prior_claim_sentence(data))
    _write(out_dir / "zenodo_metadata.json", jsonutil.dumps(metadata, indent=1))
    for name, source, _what in _exofop_list(target_id, date, png, pixels_figure):
        shutil.copyfile(out_dir / source, out_dir / "exofop" / name)
    tag = data_tag(date, review["tag"], _safe_target(target_id), review.get("tag_seq", 1))
    _write(out_dir / "exofop" / EXOFOP_PLANET_TEMPLATE.format(date=date),
           render_exofop_planet(data, date, review["tic"], tag))
    stellar = render_exofop_stellar(data, date, review["tic"], tag)
    if stellar is not None:
        _write(out_dir / "exofop" / EXOFOP_STELLAR_TEMPLATE.format(date=date), stellar)
    fields = {**review, "date": date, "png": png, "zenodo": None, "pixels_figure": pixels_figure,
              "after_bjd": julian_date(now), "after_words": date_words(now),
              "zip_name": zip_path.name, "built_words": date_words(now), "doi": DOI_PLACEHOLDER,
              "doi_shown": DOI_PLACEHOLDER, "creators_source": creators_source}
    words = _write_texts(out_dir, zip_path, data, fields, citation)
    return zip_path.name, metadata, fields, words


def _versions(data: dict, row: dict, now: datetime, Figure, zip_file: str, creators_source: str) -> dict:
    version = skysift_version()
    mpl = None
    if Figure is not None:
        import matplotlib
        mpl = matplotlib.__version__
    prov, sig = data.get("provenance") or {}, data.get("signal") or {}
    return {"pack_version": PACK_VERSION, "dossier_version": data.get("dossier_version"),
            "target_id": (data.get("target") or {}).get("target_id"), "signal_id": sig.get("id"),
            "search_version": sig.get("detector_version"), "ladder_version": prov.get("ladder_version"),
            "git_commit": prov.get("git_commit"), "pack_git_commit": ds.git_commit(), "skysift_version": version,
            "python": platform.python_version(), "numpy": np.__version__, "matplotlib": mpl,
            "platform": sys.platform, "built_utc": now.isoformat(), "dossier_generated": data.get("generated"),
            "dossier_row_generated": ds._iso(row.get("generated")), "kbonus_doi": KBONUS_DOI,
            "zip_name": zip_file, "creators_source": creators_source}
