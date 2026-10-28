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
from pathlib import Path, PurePosixPath

import numpy as np
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from skysift import calibrate as cal
from skysift import crosscheck as cc
from skysift import dossier as ds
from skysift import fpp as fpp_model
from skysift import jsonutil
from skysift import stars as stars_module
from skysift import tess as tess_module
from skysift.ephemeris import BKJD_OFFSET, SES_MIN, SHAPE_PRIOR_DRAW, predict_from
from skysift.exofop import (
    EXOFOP_STELLAR_TEMPLATE,
    data_tag,
    planet_file_name,
    render_exofop_planet,
    render_exofop_stellar,
)
from skysift.exofop import prior_fields as exofop_prior_fields
from skysift.lc.loader import LightCurveLoader
from skysift.lc.transit_model import A_LABEL_FITTED, A_LABEL_STELLAR, CONSTRAINED_DELTA_CHI2, UNCONSTRAINED
from skysift.pack_rows import (
    B_PRIOR_LABEL,
    CROWDSAP_DEFINITION,
    PSFFRAC_DEFINITION,
    SHAPE_ROWS,
    _metrics,
    _test,
    crowdsap_text,
    dilution_rows,
    ephemeris_rows,
    event_rows,
    figure_data_rows,
    julian_date,
    next_transits,
    param_rows,
    provenance_rows,
    shape_csv_rows,
    shape_rows,
    star_characterized,
    timing_rows,
)
from skysift.pack_text import (
    RELATIONSHIP_VERBS,
    RELATIONSHIP_WORDS,
    Raw,
    _bjd,
    _f,
    _joined,
    _num,
    aperture_clause,
    date_words,
    ephemeris_sentence,
    pm_digits,
    pm_text,
    prior_claims,
    quantities,
    reason_words,
    tex_escape,
    value_text,
)
from skysift.store.db import SignalRow, Store
from skysift.zenodo import (
    IN_HOUSE,  # noqa: F401  (re-exported: the in-house wording, spec 11.2)
    KBONUS_DOI,
    ZenodoClient,
    ZenodoError,
    fap_rate_clause,
    in_house_text,
    merge_metadata,
    metadata_from,
    probability_sentences,
)

# 5: round 3 (spec 12): the pixel-excluded variant and the dust-map extinction; 6: a second candidate in one pack
# (the _02 files, the planet files _001 and _002, one README table per candidate, versions.json "candidates")
PACK_VERSION = "6"
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
SOFTWARE_PACKAGES = ("numpy", "scipy", "astropy", "matplotlib")    # each named with its version (spec 11.6)
CROSS_CHECK_SOFTWARE = "triceratops"         # named only when a cross-check is stored
FACILITIES = "Kepler, Gaia, MAST, Exoplanet Archive"
CODE_SENTENCE = "the SkySift code at the stated commit is archived at doi:{doi}"
CODE_DOI_PENDING = " (to be minted)"
# the author block (spec 11.6): docs/author.json, which Vikram fills in; nobody types the fields for him
AUTHOR_FILE = Path(__file__).resolve().parents[2] / "docs" / "author.json"
AUTHOR_FIELDS = ("name", "affiliation", "email", "orcid")
_ORCID = re.compile(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]")
# a pack built with --final carries none of these in its public texts
FINAL_PLACEHOLDERS = ("XXXXXXX", "to be minted", "TO FILL IN", SOFTWARE_DOI_PLACEHOLDER)
FINAL_TEXTS = ("note/note.tex", "README.md", "exofop/note.txt", "archive_email.txt", "zenodo_metadata.json")
FINAL_NEEDS_LIVE = "--final needs --zenodo-live: the record's DOI is reserved on zenodo.org"
# the follow-up clause names the telescope class only for a deep enough dip on a bright enough star
FOLLOWUP_REACH = ", within reach of a 1 to 2 m telescope at a few hundred ppm per 10 minutes"
FOLLOWUP_REACH_DEPTH_PPM, FOLLOWUP_REACH_GMAG = 1000.0, 17.0
# the limits of the transit model, in the words of the referee pack spec (section 6)
MODEL_CLAUSE = "a quadratic limb-darkened model on a circular orbit, integrated over the {exposure}-minute exposure"
KEPLER_EXPOSURE_MIN = 29.4      # the clause's exposure when the shape block does not record one
MODEL_CITE_AFTER = "limb-darkened model"     # the Claret and Bloemen 2011 citation follows these words
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
_SOFTWARE_TAG = re.compile(r"\\software\{[^}]*\}")


def _cite_words(m: re.Match) -> str:
    """A citation as the reader sees it: its notes' words, then three per key (author, et al., year)."""
    notes = re.sub(r"[\[\]]", " ", m.group(1))
    return f" {notes}" + " cite et-al year" * len(m.group(2).split(","))


def _count(tex: str) -> int:
    """Words as the reader sees them: a citation as three (author, et al., year), a command's name dropped and its
    argument kept (section titles and the caption count), tokens without a letter or digit not counted. A word
    processor counts "Giacalone et al. 2021" as 4 and "Mehta 2026" as 2, so on the live notes (10 to 12 citations)
    this count runs 6 to 8 words under a word processor's (fix round 3 review, M-5); the 1,480-word margin of the
    limit test covers that."""
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
    block, the keywords and the ``\\software{}`` tag are not counted (AAS counts text, not the tag; spec 11.6)."""
    text = "\n".join(_TEX_COMMENT.sub("", line) for line in tex.splitlines())
    text = _SOFTWARE_TAG.sub(" ", text)
    m = re.search(r"\\begin\{document\}(.*?)\\end\{document\}", text, re.DOTALL)
    text = _FRONT_LINE.sub(" ", m.group(1) if m else text)
    abstract, text = _between(text, "abstract")
    refs, text = _between(text, "thebibliography")
    out = {"abstract": _count(abstract), "body": _count(text), "references": _count(refs)}
    out["total"] = out["abstract"] + out["body"] + out["references"]
    return out


def note_limit_problems(words: dict, tex: str = "") -> list[tuple[str, str]]:
    """What breaks the RNAAS limits (``NOTE_LIMITS``) as (limit, words) pairs, the limit a ``NOTE_LIMITS`` key, so a
    caller picks limits by key, not by matching the words (the Task 11 re-review); empty when the note fits."""
    out = []
    if words["total"] > NOTE_LIMITS["total"]:
        out.append(("total", f"the note has {words['total']:,} words, over the limit of {NOTE_LIMITS['total']:,}"))
    if words["abstract"] > NOTE_LIMITS["abstract"]:
        out.append(("abstract",
                    f"the abstract has {words['abstract']} words, over the limit of {NOTE_LIMITS['abstract']}"))
    displays = len(re.findall(r"\\begin\{(figure|table|deluxetable)\*?\}", tex))
    if displays > NOTE_LIMITS["figures_or_tables"]:
        out.append(("figures_or_tables", f"the note has {displays} figures and tables; RNAAS takes one"))
    return out


def note_problems(words: dict, tex: str = "") -> list[str]:
    """What breaks the RNAAS limits (``NOTE_LIMITS``), in words; empty when the note fits."""
    return [text for _limit, text in note_limit_problems(words, tex)]


# ---------------------------------------------------------------------------------------------------- the numbers


def _fpp_class(data: dict) -> str | None:
    for s in (data.get("fpp") or {}).get("scenarios") or []:
        if s.get("name") == "PL":
            return (s.get("factors") or {}).get("class")
    return None


def references(data: dict) -> dict[str, bool]:
    """Which works the dossier's numbers rest on, so the note cites exactly those."""
    star = data.get("star") or {}
    route = star.get("route") or "none"
    star_ok = star_characterized(data)
    tess = set(tess_module.note_cites(data.get("tess")))       # Christ et al. 2019 alone (fix round 2, N1)
    return {"kbonus": True, "kbonushlsp": True,
            # the in-house method and its three works are the README's since Task 13 (the word limit; addendum cut 1)
            "morton2012": False, "dressing2015": False, "fressin2013": False,
            # the colour table and the parallax zero point are the README's since Task 13 (addendum cut 9)
            "pecaut2013": False, "lindegren2021": False,
            "mann2015": star_ok and route == "mann",
            "mann2019": star_ok and route == "mann" and value_text(star.get("mass")) is not None,
            "giacalone2021": cross_check_sentence(data) is not None,
            **{key: key in tess for key, _words in tess_module.METHOD_CITES if key != "giacalone2021"},
            "bryson2013": self_referenced(offset_block(data)),
            # the errors method and its three works are in the README since fix round 3; the note points there
            "deeg2015": False, "holczer2016": False, "schmitt2014": False,
            "claret2011": shape_sentence(data) is not None and bool((data.get("shape") or {}).get("comparison")),
            # spec 11.6: Kepler in the signal sentence, Gaia DR3 in the star sentence; the habitable zone and its
            # Kopparapu et al. edges are the README's
            "borucki2010": True, "gaia2016": star_ok, "gaia2023": star_ok,
            # Task 13 fix round 1 (I-1): the GSP-Phot reddening prints with the cool-dwarf bias it carries
            "andrae2023": "Andrae" in (reddening_text(data) or ""),
            # spec 12.3: the note cites the map of the adopted 3D value, or the full column of its planet-radius bracket;
            # Task 17 fix round 1 (M7, ruling 2): the adopted value's full-column check cites its source too
            "green2019": reddening_state(data) == RED_ADOPTED,
            "schlafly2011": rp_bracket_text(data) is not None or column_fit(data) is not None}


BIBLIOGRAPHY = (
    ("andrae2023", r"{Andrae} et~al.(2023)",
     r"Andrae, R., Fouesneau, M., Sordo, R., et~al. 2023, A\&A, 674, A27, doi:10.1051/0004-6361/202243462"),
    ("battley2021", r"{Battley} et~al.(2021)",
     r"Battley, M.~P., Kunimoto, M., Armstrong, D.~J., \& Pollacco, D. 2021, MNRAS, 503, 4092"),
    ("borucki2010", r"{Borucki} et~al.(2010)", r"Borucki, W.~J., Koch, D., Basri, G., et~al. 2010, Science, 327, 977"),
    ("bryson2013", r"{Bryson} et~al.(2013)",
     r"Bryson, S.~T., Jenkins, J.~M., Gilliland, R.~L., et~al. 2013, PASP, 125, 889"),
    ("cardelli1989", r"{Cardelli} et~al.(1989)",
     r"Cardelli, J.~A., Clayton, G.~C., \& Mathis, J.~S. 1989, ApJ, 345, 245"),
    ("christ2019", r"{Christ} et~al.(2019)", r"Christ, C.~N., Montet, B.~T., \& Fabrycky, D.~C. 2019, AJ, 157, 235"),
    ("claret2011", r"{Claret} \& {Bloemen}(2011)", r"Claret, A., \& Bloemen, S. 2011, A\&A, 529, A75"),
    ("coughlin2017", r"{Coughlin}(2017)",
     (r"Coughlin, J.~L. 2017, Planet Detection Metrics: Robovetter Completeness and Effectiveness for Data Release "
      r"25, KSCI-19114-002 (NASA Ames Research Center)")),
    ("deeg2015", r"{Deeg}(2015)", r"Deeg, H.~J. 2015, arXiv:1503.05368"),
    ("dressing2015", r"{Dressing} \& {Charbonneau}(2015)",
     r"Dressing, C.~D., \& Charbonneau, D. 2015, ApJ, 807, 45"),
    ("fitzpatrick2019", r"{Fitzpatrick} et~al.(2019)",
     r"Fitzpatrick, E.~L., Massa, D., Gordon, K.~D., Bohlin, R., \& Clayton, G.~C. 2019, ApJ, 886, 108"),
    ("fressin2013", r"{Fressin} et~al.(2013)", r"Fressin, F., Torres, G., Charbonneau, D., et~al. 2013, ApJ, 766, 81"),
    ("fulton2017", r"{Fulton} et~al.(2017)",
     r"Fulton, B.~J., Petigura, E.~A., Howard, A.~W., et~al. 2017, AJ, 154, 109"),
    ("gaia2016", r"{Gaia Collaboration} et~al.(2016)",
     r"Gaia Collaboration, Prusti, T., de Bruijne, J.~H.~J., et~al. 2016, A\&A, 595, A1"),
    ("gaia2023", r"{Gaia Collaboration} et~al.(2023)",
     r"Gaia Collaboration, Vallenari, A., Brown, A.~G.~A., et~al. 2023, A\&A, 674, A1"),
    ("giacalone2021", r"{Giacalone} et~al.(2021)",
     r"Giacalone, S., Dressing, C.~D., Jensen, E.~L.~N., et~al. 2021, AJ, 161, 24"),
    ("green2019", r"{Green} et~al.(2019)",
     r"Green, G.~M., Schlafly, E., Zucker, C., Speagle, J.~S., \& Finkbeiner, D. 2019, ApJ, 887, 93"),
    ("guerrero2021", r"{Guerrero} et~al.(2021)",
     r"Guerrero, N.~M., Seager, S., Huang, C.~X., et~al. 2021, ApJS, 254, 39"),
    ("holczer2016", r"{Holczer} et~al.(2016)", r"Holczer, T., et~al. 2016, ApJS, 225, 9"),
    ("kipping2010", r"{Kipping}(2010)", r"Kipping, D.~M. 2010, MNRAS, 408, 1758"),
    ("kirk2016", r"{Kirk} et~al.(2016)", r"Kirk, B., Conroy, K., Pr{\v s}a, A., et~al. 2016, AJ, 151, 68"),
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
    ("riello2021", r"{Riello} et~al.(2021)", r"Riello, M., De Angeli, F., Evans, D.~W., et~al. 2021, A\&A, 649, A3"),
    ("rogers2015", r"{Rogers}(2015)", r"Rogers, L.~A. 2015, ApJ, 801, 41"),
    ("schlafly2011", r"{Schlafly} \& {Finkbeiner}(2011)", r"Schlafly, E.~F., \& Finkbeiner, D.~P. 2011, ApJ, 737, 103"),
    # Task 17 fix round 2 (N2): the README key's scale sentences name these two; the note cites neither
    ("schlafly2016", r"{Schlafly} et~al.(2016)",
     r"Schlafly, E.~F., Meisner, A.~M., Stutz, A.~M., et~al. 2016, ApJ, 821, 78"),
    ("schlegel1998", r"{Schlegel} et~al.(1998)", r"Schlegel, D.~J., Finkbeiner, D.~P., \& Davis, M. 1998, ApJ, 500, 525"),
    ("schmitt2014", r"{Schmitt} et~al.(2014)", r"Schmitt, J.~R., et~al. 2014, AJ, 148, 28"),
    ("seager2003", r"{Seager} \& {Mall{\'e}n-Ornelas}(2003)",
     r"Seager, S., \& Mall{\'e}n-Ornelas, G. 2003, ApJ, 585, 1038"),
    ("skrutskie2006", r"{Skrutskie} et~al.(2006)",
     r"Skrutskie, M.~F., Cutri, R.~M., Stiening, R., et~al. 2006, AJ, 131, 1163"),
    ("stassun2018", r"{Stassun} et~al.(2018)",
     r"Stassun, K.~G., Oelkers, R.~J., Pepper, J., et~al. 2018, AJ, 156, 102"),
    ("stassun2019", r"{Stassun} et~al.(2019)",
     r"Stassun, K.~G., Oelkers, R.~J., Paegert, M., et~al. 2019, AJ, 158, 138"),
    ("sullivan2015", r"{Sullivan} et~al.(2015)",
     r"Sullivan, P.~W., Winn, J.~N., Berta-Thompson, Z.~K., et~al. 2015, ApJ, 809, 77"),
    ("thompson2018", r"{Thompson} et~al.(2018)",
     r"Thompson, S.~E., Coughlin, J.~L., Hoffman, K., et~al. 2018, ApJS, 235, 38"),
    ("twicken2025", r"{Twicken} et~al.(2025)",
     r"Twicken, J.~D., Jenkins, J.~M., Caldwell, D.~A., et~al. 2025, RNAAS, 9, 132, doi:10.3847/2515-5172/addec4"),
)


# spec 11.1: words no public text may carry (case-insensitive); "independent" is kept for the vetting ladder alone
FORBIDDEN_WORDS = (r"\bblind\b", r"\bindependent recovery\b")
COMPARED = ("period", "epoch", "depth")         # the claim's quantities set against the dossier's, in this order


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
    """Who reported the signal first, when and where (the DOI of the public record), and how SkySift's result stands
    to that claim: the earliest claim's relationship (``RELATIONSHIP_WORDS``) and its development note (spec 11.1).
    None without a prior claim. With ``tex`` a ``Raw`` citing each record (``prior_bib``) with the claim's short
    development clause (``development_note_short``, Task 13 fix round 1, the word limit) when it has one; the plain
    text names the record's title instead and keeps the full note."""
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
    first = pairs[0][0]
    relationship = RELATIONSHIP_WORDS.get(first.get("relationship") or "")
    note = (tex and first.get("development_note_short")) or first.get("development_note") or ""
    if relationship:
        parts.append(f"; SkySift's result is {relationship}" + (f": {note}." if note else "."))
    else:
        parts.append(f"; {note}." if note else ".")
    return _joined(tex, *parts)


def priority_sentence(data: dict) -> str | None:
    """README step 2: the prior claim is the record of priority, and what the upload says about it."""
    claims = prior_claims(data)
    if not claims:
        return None
    first = claims[0]
    relationship = RELATIONSHIP_WORDS.get(first.get("relationship") or "")
    how = f" ({relationship})" if relationship else ""
    return (f"the prior claim ({first['author']}, {date_words(first['date'])}, doi:{first['doi']}) is the record of "
            f"priority. The upload note presents this result as SkySift's vetting of that claim's signal{how}, and "
            "the planet file's paper and notes fields name its DOI.")


def _against(ours: float, ours_err: float | None, theirs: float, theirs_err: float | None) -> dict:
    """One compared quantity: both values, both errors and the difference in sigma over the errors present (None
    when neither has one)."""
    errs = [e for e in (ours_err, theirs_err) if e is not None and e > 0]
    sigma = abs(ours - theirs) / math.hypot(*errs) if errs else None
    return {"ours": ours, "ours_err": ours_err, "theirs": theirs, "theirs_err": theirs_err, "sigma": sigma}


def _claim_epoch(timing: dict, t0_bkjd: float) -> int | None:
    """The epoch of the linear ephemeris nearest the claim's mid-transit time: from the reference epoch when the
    timing is ok, from the first transit otherwise; None without the numbers."""
    period = _num(timing.get("period"))
    if not period:
        return None
    if timing.get("ok") is True:
        t_ref, ref = _num(timing.get("t_ref")), timing.get("epoch_ref")
        if t_ref is None or not isinstance(ref, int) or _num(timing.get("t_ref_err")) is None:
            return None
        return ref + round((t0_bkjd - t_ref) / period)
    t0 = _num(timing.get("t0"))
    return None if t0 is None else round((t0_bkjd - t0) / period)


def prior_comparison(data: dict) -> dict | None:
    """The earliest prior claim set against the dossier (spec 11.1): ``period``, ``epoch`` (the claim's mid-transit
    time against ours propagated to the same epoch, ``ephemeris.predict_from``, with that ``epoch``) and ``depth``,
    each {"ours", "ours_err", "theirs", "theirs_err", "sigma"} with sigma the difference over the errors present
    added in quadrature; ``fpp`` {"theirs", "tool", "ours", "ours_tool"}, ours the TRICERATOPS median when a current
    cross-check exists, else the in-house value; and ``one_sided``, the quantities compared with our error alone. A
    quantity the claim does not give (or the dossier lacks) is left out. None without a claim."""
    claims = prior_claims(data)
    if not claims:
        return None
    claim, eph = claims[0], data.get("ephemeris") or {}
    timing = data.get("timing") if isinstance(data.get("timing"), dict) else {}
    out: dict = {}
    period = _num(eph.get("period_days"))
    if claim["period_days"] is not None and period is not None:
        out["period"] = _against(period, _num(eph.get("period_err_days")), claim["period_days"],
                                 claim["period_err_days"])
    if claim["t0_bjd"] is not None:
        theirs = claim["t0_bjd"] - BKJD_OFFSET
        epoch = _claim_epoch(timing, theirs)
        if epoch is not None:
            ours, err = predict_from(timing, epoch)
            out["epoch"] = {**_against(ours, err, theirs, claim["t0_err_days"]), "epoch": epoch}
    depth = _num(eph.get("depth_ppm"))
    if claim["depth_ppm"] is not None and depth is not None:
        out["depth"] = _against(depth, _num(eph.get("depth_ppm_err")), claim["depth_ppm"], claim["depth_err_ppm"])
    if claim["fpp"] is not None:
        block = data.get("cross_check")
        if cc.complete(block) and block.get("current") is True:
            ours, tool = _num(block.get("fpp")), "TRICERATOPS"
        else:
            ours, tool = _num((data.get("fpp") or {}).get("value")), "SkySift's four-scenario estimate"
        out["fpp"] = {"theirs": claim["fpp"], "tool": claim["fpp_tool"], "ours": ours, "ours_tool": tool}
    out["one_sided"] = [k for k in COMPARED if k in out and out[k]["ours_err"] is not None
                        and out[k]["theirs_err"] is None]
    return out


def _listed(items: list[str], last: str = "and") -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} {last} {items[-1]}"


def _difference(name: str, row: dict) -> str:
    """The difference of a quantity neither side gives an error for, in its unit."""
    diff = abs(row["ours"] - row["theirs"])
    if name == "depth":
        return f"{diff:,.0f} ppm, with no error on either"
    if name == "epoch":
        return f"{diff * 1440:.1f} min, with no error on either"
    return f"{diff:.6f} d, with no error on either"


def comparison_text(data: dict, tex: bool = False) -> str | None:
    """The one sentence comparing SkySift's numbers with the earliest prior claim's (spec 11.1,
    ``prior_comparison``): each difference in sigma to one decimal, the claim's depth and false-positive probability
    as the claim gives them (another work's numbers, so not rounded by ``pm_text``'s two-figure rule), which
    differences use our error alone, and our false-positive probability beside the claim's. None without a claim or
    without anything to compare. With ``tex`` a ``Raw`` without the false-positive clause, which the README keeps
    (Task 13, the word limit)."""
    cmp = prior_comparison(data)
    if cmp is None:
        return None
    rows = [(k, cmp[k]) for k in COMPARED if k in cmp]
    sentence = ""
    if rows:
        said = []
        for i, (name, row) in enumerate(rows):
            # the note says "the mid-transit at epoch E" (Task 17 fix round 1, a cut for the reddening sentence's words)
            what = (f"the mid-transit{'' if tex else ' time'} at epoch {row['epoch']}" if name == "epoch"
                    else f"the {name}")
            by = _difference(name, row) if row["sigma"] is None else f"{row['sigma']:.1f} sigma"
            said.append(f"{what} {'differs by' if i == 0 else 'by'} {by}")
        notes = []
        if "depth" in cmp:
            d = cmp["depth"]
            err = f" ± {d['theirs_err']:,g}" if d["theirs_err"] is not None else ""
            notes.append(f"{d['theirs']:,g}{err} ppm there")
        if cmp["one_sided"]:
            names = [f"the {k}" for k in cmp["one_sided"]]
            if tex:         # the note's form (Task 13, the word limit): "the period and epoch use our error alone",
                # one article for the list (Task 17 fix round 1, a cut for the reddening sentence's words)
                listed = _listed([names[0], *cmp["one_sided"][1:]])
                notes.append(f"{listed} use{'' if len(names) > 1 else 's'} our error alone")
            else:
                uses = "those use" if len(names) > 1 else "that uses"
                notes.append(f"the claim gives no error on {_listed(names, 'or')}, so {uses} ours alone")
        which = "that claim" if len(prior_claims(data)) == 1 else "the first claim"
        sentence = f"Against {which} {_listed(said)}" + (f" ({'; '.join(notes)})" if notes else "")
    if "fpp" in cmp and not tex:      # the note leaves the FPP clause to the README (Task 13, brief Step 15 item 3)
        f = cmp["fpp"]
        theirs = f"{f['theirs']:,g}" + (f" ({f['tool']})" if f["tool"] else "")
        ours = None if f["ours"] is None else cc.probability_text(f["ours"])
        # the tool is named once on each side: "our TRICERATOPS 0.012", "our 0.0016 (SkySift's ... estimate)"
        ours = (None if ours is None else f"TRICERATOPS {ours}" if f["ours_tool"] == "TRICERATOPS"
                else f"{ours} ({f['ours_tool']})")
        clause = (f"false-positive probability of {theirs} sits beside our {ours}" if ours is not None
                  else f"false-positive probability is {theirs}")
        sentence = f"{sentence}; its {clause}" if sentence else f"The claim's {clause}"
    if not sentence:
        return None
    return _joined(tex, sentence + ".")


HAND_BUILT_FILE = "data/hand_built_localisation.json"    # the study's results file, copied into the pack


def hand_built_sentence(data: dict, source: str | None = None) -> str | None:
    """The README's one sentence on the hand-built study of the same pixels (``dossier.hand_built_localisation``;
    since spec 11.5 it is not in the note): the largest season-by-season difference in the centroid's distance from
    this star, over how many seasons, and where the study is (``source``, by default the block's own path; the
    README names the pack's copy, ``HAND_BUILT_FILE``). It says "agrees" only when every season's difference is below
    its combined error, else "differs ... by up to". "The centroid agrees with the hand-built localisation within X
    px over N seasons (<source>)."."""
    block = data.get("hand_built_localisation")
    diff = _num(block.get("max_diff_pix")) if isinstance(block, dict) else None
    if diff is None:
        return None
    src = source or block.get("source")
    n = block.get("n_seasons")
    seasons = f"{n} season{'s' if n != 1 else ''}" if isinstance(n, int) else "the seasons that voted"
    if block.get("agrees") is True:
        return (f"The centroid agrees with the hand-built localisation within {max(diff, 0.01):.2f} px over {seasons} "
                f"({src}).")
    err = _num(block.get("combined_err_pix"))
    if block.get("agrees") is False and err is not None and err > 0:
        return (f"The centroid differs from the hand-built localisation by up to {diff:.2f} px over {seasons}, more "
                f"than the seasons' combined error ({src}).")
    return (f"The centroid differs from the hand-built localisation by up to {diff:.2f} px over {seasons} (the error "
            f"is not available; {src}).")


def _timed(data: dict) -> bool:
    """Whether the period and epoch come from the linear fit of the per-transit times (the timing block is ok)."""
    timing = data.get("timing")
    return isinstance(timing, dict) and timing.get("ok") is True


def _bootstrapped(data: dict) -> bool:
    """Whether the shape errors come from the bootstrap over transits (the shape block is ok)."""
    shape = data.get("shape")
    return isinstance(shape, dict) and shape.get("ok") is True


def methods_sentence(data: dict, tex: bool = False) -> str | None:
    """How the errors were made, in one sentence for the README's ephemeris section (ruling h, moved out of the
    note in Task 8 fix round 3; the note keeps ``errors_pointer``): the linear fit of the per-transit mid-times with
    the reference epoch at the centre of the series (Deeg 2015), the per-transit times by template fit (Holczer et
    al. 2016) and none below the single-event S/N cut (``SES_MIN``; M-7 of Task 10's fix round 1), the shape errors
    from a bootstrap over transits (Schmitt et al. 2014), with the density prior's centre drawn on each resample when
    the method says so (I-1), quoted as the 16th to 84th percentile spread about the bootstrap median, and which
    period and epoch the fold, the per-event depths and the bootstrap use (M4; the linear fit since version 5,
    ``folded_on_linear_fit``). None when neither block is ok."""
    timed, boot = _timed(data), _bootstrapped(data)
    if not (timed or boot):
        return None

    def cite(key: str, words: str):
        return Raw(rf"\citep{{{key}}}") if tex else f"({words})"
    parts: list = []
    if timed:
        parts += [("The period and the reference time come from a weighted linear fit of the per-transit "
                   "mid-times, its reference epoch at the centre of the series "), cite("deeg2015", "Deeg 2015"),
                  ", each transit timed by a fit of the transit template ", cite("holczer2016", "Holczer et al. 2016"),
                  f"; transits with a single-event S/N below {SES_MIN:g} are not timed ",
                  cite("holczer2016", "Holczer et al. 2016")]
    if boot:
        drawn = SHAPE_PRIOR_DRAW in str((data.get("shape") or {}).get("method") or "")
        parts += ["; the" if timed else "The", " shape errors come from a bootstrap over transits ",
                  cite("schmitt2014", "Schmitt et al. 2014"), f", {SHAPE_PRIOR_DRAW}" if drawn else "",
                  ", quoted as the 16th to 84th percentile spread of the resamples about their median"]
    if folded_on_linear_fit(data):
        parts.append(". The fold, the per-event depths, odd and even, the bootstrap and the depth stability test use "
                     "the linear-fit period and epoch.")
    else:
        parts.append(". The fold, the per-event depths and the bootstrap use the search period and epoch"
                     + ("; the quoted ephemeris is the linear fit." if timed else "."))
    return _joined(tex, *parts)


def folded_on_linear_fit(data: dict) -> bool:
    """Whether the dossier's folds (the depth, the events, parity, the shape and its bootstrap, rung 3 again) use the
    linear-fit ephemeris (version 5, ``ephemeris.fold_ephemeris``), not the search one."""
    return str((data.get("ephemeris") or {}).get("fold_ephemeris") or "").startswith("linear fit")


def fold_words(data: dict) -> str:
    """The ephemeris the dossier folded on, as the caption and the signal section name it: "linear-fit ephemeris",
    "search ephemeris", or "period" for a dossier before version 5, which does not say."""
    if folded_on_linear_fit(data):
        return "linear-fit ephemeris"
    return "search ephemeris" if (data.get("ephemeris") or {}).get("fold_ephemeris") else "period"


# the note says once which README its pointers mean, in the first of them (Task 8 fix round 4, M-3)
PACK_README = tess_module.PACK_README
# the works ``methods_sentence`` cites, in its order
ERRORS_CITES = (("deeg2015", "timed"), ("holczer2016", "timed"), ("schmitt2014", "boot"))


def errors_pointer(data: dict) -> str | None:
    """The note's stand-in for ``methods_sentence`` (Task 8 fix round 3, no citation): the clause "methods in the
    Zenodo pack's README" that closes the signal section's ephemeris sentence, in brackets, when the ephemeris or the
    shape errors have a method to point to (the round-2 word budget: the sentence naming the methods is the
    README's). It is the note's first pointer, so it names the README as the Zenodo pack's (fix round 4, M-3;
    ``PACK_README``) and the TESS sentence's later pointer says "the README" alone. None when neither block is ok.
    Since Task 15 fix round 1 (cut B6) the note prints neither pointer: its Data availability line names the pack's
    methods; the long TESS sentence still reads this for which README it names."""
    return f"methods in {PACK_README}" if _timed(data) or _bootstrapped(data) else None


def errors_methods_text(data: dict) -> str | None:
    """The README's ephemeris section: ``methods_sentence`` in plain text with the full references of the works it
    cites; None when neither block is ok."""
    method = methods_sentence(data)
    if method is None:
        return None
    used = {"timed": _timed(data), "boot": _bootstrapped(data)}
    refs = "; ".join(plain_reference(key) for key, needs in ERRORS_CITES if used[needs])
    return f"{method} References: {refs}."


def cross_check_sentence(data: dict, tex: bool = False) -> str | None:
    """The TRICERATOPS cross-check (``crosscheck.comparison_sentence``) in full, for the Research Note (the plain texts
    print the headline first and the form that refers back to it, ``cross_check_after``); None without a cross-check
    made for this dossier's inputs, which the texts then leave out."""
    return cc.comparison_sentence(data, tex=tex, short=tex)      # the note's short class clause (addendum cut 8)


def both_tools(data: dict, tex: bool = False) -> str | None:
    """The Research Note's sentence after the two estimates (spec 12.2): what carries the TRICERATOPS weight once
    the neighbours the difference images exclude are removed (``crosscheck.dominant_excluded_sentence``, short),
    when a current cross-check is printed and the pixel rung voted, so that both estimates have counted the pixels;
    None otherwise, and None when the cross-check sentence already says which number rests on SkySift's own evidence
    (``crosscheck.own_evidence_sentence``), which takes its place for the word limit (Task 15 addendum 2)."""
    if cross_check_sentence(data) is None or cc.own_evidence_sentence(data) is not None:
        return None
    voting, _silent = _pixel_votes(_test(data, "rung5_pixels"))
    return cc.dominant_excluded_sentence(data, tex=tex, short=True) if voting else None


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def tess_text(data: dict, tex: bool = False) -> str:
    """The TESS statement for the note, after the cross-check sentence (spec 10.8): ``tess.note_sentence`` alone,
    pointing to the README for the noise model and thresholds and citing Christ et al. 2019 only (fix round 2,
    N1: the methods sentence and its references cost the note about 180 words); "TESS coverage was not assessed."
    without the inputs. Its pointer names the README as the Zenodo pack's when the note has no errors pointer
    before it (fix round 4, M-3)."""
    return tess_module.note_sentence(data.get("tess"), tex, first=errors_pointer(data) is None, short=tex)


def _plain_tex(text: str) -> str:
    return (str(text).replace(r"\&", "&").replace("~", " ").replace(r"{\'\i}", "í").replace(r"{\v s}", "š")
            .replace(r"{\'e}", "é"))


def plain_reference(key: str) -> str:
    """A ``BIBLIOGRAPHY`` entry as plain text, for the README."""
    return _plain_tex(next(text for k, _label, text in BIBLIOGRAPHY if k == key))


def _occurrence_keys(data: dict) -> list[str]:
    """The planet-occurrence works the in-house estimate rests on: Dressing and Charbonneau 2015 for an M dwarf,
    Fressin et al. 2013 otherwise or when the stored scenarios name it."""
    cls = _fpp_class(data)
    notes = json.dumps((data.get("fpp") or {}).get("scenarios") or [])
    return [k for k, used in (("dressing2015", cls == "M"), ("fressin2013", cls != "M" or "Fressin" in notes))
            if used]


IN_HOUSE_METHOD = ("The in-house estimate weighs a planet, a background eclipsing binary and an eclipsing binary on "
                   "the target, after Morton (2012), with planet occurrence from {occurrence}. It has no "
                   "radial-velocity or imaging input.")
OCCURRENCE_WORDS = {"dressing2015": "Dressing and Charbonneau (2015)", "fressin2013": "Fressin et al. (2013)"}


def in_house_method_text(data: dict) -> str:
    """The README's method of the in-house estimate (the note's until Task 13's word budget, addendum cut 1)."""
    return IN_HOUSE_METHOD.format(occurrence=" and ".join(OCCURRENCE_WORDS[k] for k in _occurrence_keys(data)))


SEAGER_CITE = "Seager and Mallén-Ornelas 2003"      # the density from the transit shape
KIPPING_CITE = "Kipping 2010"                        # the model integrated over the exposure


def b_set_by_prior(data: dict) -> bool:
    """Whether the quoted b and a/R* are the density-prior fit's because the data leave them unconstrained
    (``shape.comparison``: the prior fit quoted, ``constrained`` False)."""
    shape = data.get("shape") if isinstance(data.get("shape"), dict) else {}
    cmp = shape.get("comparison") or {}
    return cmp.get("quoted") == "prior" and cmp.get("constrained") is False


def b_upper_bound(row: dict) -> float | None:
    """The 84th percentile of a b the fit cannot pin, or None when the posterior holds b away from zero or carries no
    errors. Unpinned means the 16th percentile prints as zero at the row's decimals (the lower error equals the
    median within rounding), or the median is under 0.01 with the upper error more than ten times it: the posterior
    then piles against b = 0 and its median and lower error say nothing, so the note quotes the bound alone. The
    density-prior clause (``b_set_by_prior``) is the flag the fit sets; this reads the posterior, so a prior-fit b
    that the data do bound (0.46 +0.05/-0.06) keeps its errors."""
    median, minus, plus = (_num(row.get(k)) for k in ("value", "minus", "plus"))
    if median is None or minus is None or plus is None or plus <= 0:
        return None
    nd = row.get("nd", 2)
    pinned = abs(median - minus) < 0.5 * 10 ** -nd or (median < 0.01 and plus > 10 * median)
    bound = median + plus
    return bound if pinned and round(bound, nd) > 0 else None


def b_bound_text(row: dict) -> str | None:
    """"below 0.16 (84th percentile)" for a b ``b_upper_bound`` finds unpinned, at the decimals the row prints b at."""
    bound = b_upper_bound(row)
    return None if bound is None else f"below {bound:.{row.get('nd', 2)}f} (84th percentile)"


def shape_fit_text(data: dict) -> str | None:
    """The README's evidence for the shape call (round-2 final review, I-4): the density-prior fit's b and a/R*, the
    free fit's b, a/R* and stellar density beside the host's, the chi-square difference against the rule of
    ``CONSTRAINED_DELTA_CHI2``, and the reading, with its precedents (Seager and Mallén-Ornelas 2003 for the density
    from the transit shape) and the exposure integration named with Kipping 2010. The note keeps "unconstrained at
    this cadence" without them (the word limit). None unless the prior fit is quoted because the data do not
    constrain b and a/R*."""
    if not b_set_by_prior(data):
        return None
    shape = data["shape"]
    free = (shape.get("fits") or {}).get("free") or {}
    delta = _num(shape["comparison"].get("delta_chi2"))
    b_prior = shape.get("b") or {}
    b_row = {"value": b_prior.get("median"), "minus": b_prior.get("minus"), "plus": b_prior.get("plus"), "nd": 2}
    b_text = b_bound_text(b_row) or pm_text(b_prior.get("median"), b_prior.get("minus"), b_prior.get("plus"))
    a_text = value_text((data.get("planet") or {}).get("a_over_r")) or value_text(shape.get("a_over_r"))
    fb, fa, frho = (_num(free.get(k)) for k in ("b", "a_over_r", "density_gcc"))
    if free.get("ok") is not True or None in (fb, fa, delta) or b_text is None:
        return None
    exposure = _num(shape.get("exposure_min")) or KEPLER_EXPOSURE_MIN
    host = value_text((data.get("star") or {}).get("density_gcc"))
    rho = (f" (a stellar density of {frho:.1f} g/cm3 against the host's {host} g/cm3)"
           if frho is not None and host else "")
    return (f"Shape fit: {B_PRIOR_LABEL} b is {b_text} and a/R* {a_text} {A_LABEL_STELLAR}. The free fit "
            f"gives b of {fb:.2f} and a/R* of {fa:.1f}{rho}, with a chi-square lower by only {delta:.2f}, within the "
            f"rule of {CONSTRAINED_DELTA_CHI2:g}: the data do not constrain them; the quoted values follow the host's "
            f"density ({SEAGER_CITE}). The model is integrated over the {exposure:.1f}-minute exposure, which matters "
            f"at this cadence ({KIPPING_CITE}).")


def fap_method_text(data: dict) -> str | None:
    """The README's clause on the null searches behind the false-alarm probability (round-2 final review, minor 6):
    the inverted and scrambled light curves follow Thompson et al. (2018) and Coughlin (2017); the null stars come
    from the calibration pilot, a selection apart from the sweep (``calibration.null_stars_in_sweep`` of them in it,
    when stored); and which kind of null reached the signal's SDE (``fap.k_by_kind``), all inverted meaning
    systematics, not white noise, set the tail. None without a calibrated FAP."""
    fap, calib = data.get("fap") or {}, data.get("calibration") or {}
    if not (fap.get("calibrated") and fap.get("n")):
        return None
    parts = [("The null searches are the inverted and scrambled light curves of Thompson et al. (2018) and "
              "Coughlin (2017)")]
    stars, inside = calib.get("null_stars"), calib.get("null_stars_in_sweep")
    if isinstance(stars, int) and stars:
        parts.append(f"the {stars:,} null stars come from the calibration pilot, a selection apart from the sweep"
                     + (f" ({inside:,} of them {'is' if inside == 1 else 'are'} in it)" if isinstance(inside, int) else ""))
    k, kinds = fap.get("k"), {kind: n for kind, n in (fap.get("k_by_kind") or {}).items() if n}
    if isinstance(k, int) and k and kinds:
        if len(kinds) == 1:
            kind = next(iter(kinds))
            # fix wave round 4 (M1): one search is singular
            parts.append((f"the 1 null search that reached the signal's SDE is {'an' if kind[:1] in 'aeiou' else 'a'} "
                          f"{kind} light curve" if k == 1 else
                          f"all {k} null searches that reached the signal's SDE are {kind} light curves")
                         + (", so systematics, not white noise, set the tail" if kind == "inverted" else ""))
        else:
            parts.append(f"of the {k} that reached the signal's SDE, "
                         + _listed([f"{n} {'is' if n == 1 else 'are'} {kind}" for kind, n in sorted(kinds.items())]))
    return "; ".join(parts) + "."


# M-1 of the Task 13 review: where the note's wording is shorter than the specification's, the README says so; every
# number and claim is in both
def note_forms(data: dict, nearest_in_note: bool = True) -> str | None:
    """The README's sentence on where the Research Note words a statement more briefly than this README, with the
    same numbers (Task 13 review M-1): the nearest Gaia source by distance and magnitude, a/R* without its label
    where the quoted values follow the host's density, the short class clause, the claim's short development
    note; only the ones this note carries. None when it carries none. The two-candidate note leaves the nearest Gaia
    source out (cut C14), so ``nearest_in_note`` is False there."""
    items = []
    if nearest_in_note and offset_block(data) and (offset_block(data).get("nearest_beyond") or {}).get("source_id"):
        items.append("it names the nearest Gaia source beyond the exclusion radius by its distance and G magnitude "
                     "(its Gaia DR3 id is in the pixel offset paragraph)")
    cmp = (data.get("shape") or {}).get("comparison") if isinstance(data.get("shape"), dict) else None
    if isinstance(cmp, dict) and cmp.get("quoted") == "prior" and cmp.get("constrained") is False:
        items.append('it gives a/R* without the label "from the stellar parameters", the shape sentence saying the '
                     "quoted values follow the host's density")
    if cross_check_sentence(data) is not None:
        items.append(f"it says \"{cc.NO_CLASS_SHORT}\" for the validation classes of TRICERATOPS")
    if tess_module.note_sentence(data.get("tess"), short=True) != tess_module.note_sentence(data.get("tess")):
        items.append("it gives TESS as the sector count, the predicted folded significance and the verdict (the "
                     "windows, the dilution and the noise are in the TESS row above)")
    claims = prior_claims(data)
    if claims and claims[0].get("development_note_short"):
        items.append("it gives the claim's development note as \"" + claims[0]["development_note_short"] + "\"")
    if not items:
        return None
    return ("The Research Note words these more briefly than this README, with the same numbers: "
            + "; ".join(items) + ".")


def readme_works(data: dict) -> list[str]:
    """The works the README cites where the note does not: the in-house method (Morton 2012 and the occurrence
    rates, ``in_house_method_text``); the habitable zone (Kopparapu et al. 2013) and its sub-Neptune caveat (Rogers
    2015; Fulton et al. 2017), when ``hz_text`` is printed; the shape call's precedent (Seager and Mallén-Ornelas
    2003; Kipping 2010) with ``shape_fit_text``; Coughlin 2017 with ``fap_method_text`` (Thompson et al. 2018 is in
    ``README_REFERENCES`` already)."""
    return (["morton2012", *_occurrence_keys(data)] + [k for k, _w in _star_method_keys(data)]
            + (["kopparapu2013", "rogers2015", "fulton2017"] if hz_text(data) else [])
            + (["seager2003", "kipping2010"] if shape_fit_text(data) else [])
            + (["coughlin2017"] if fap_method_text(data) else [])
            + _reddening_keys(data))


def _reddening_keys(data: dict) -> list[str]:
    """The works the README's reddening paragraph and table rest on (spec 12.3): the Gaia (E)DR3 law's Fitzpatrick et
    al. 2019 and Riello et al. 2021 and the A_Ks ratio's Cardelli et al. 1989 when the law was applied, Schlafly and
    Finkbeiner 2011 for a full column the note does not cite, Green et al. 2019 for a 3D value, Andrae et al. 2023
    for the GSP-Phot caveat the table's key prints with a GSP-Phot row (minor 7), and, where the key prints its
    scale sentences (Task 17 fix round 2, N2), Schlafly et al. 2016 and Schlegel et al. 1998 (SFD98)."""
    state, col = reddening_state(data), _column_derived(data)
    if state is None:
        return []
    keys = ((["andrae2023"] if _gsp_row(data) else [])
            + (["cardelli1989", "fitzpatrick2019", "riello2021"] if state == RED_ADOPTED or col is not None else []))
    keys += ["schlafly2011"] if col is not None else []
    keys += ["green2019"] if state == RED_ADOPTED else []
    # fix round 2 (N2): the key's scale sentences name Schlafly et al. 2016 and SFD98 (Schlegel et al. 1998)
    e = (data.get("star") or {}).get("extinction") or {}
    if state == RED_ADOPTED and _map_value(e):
        keys += ["schlafly2016"] + (["schlegel1998"] if col is not None and column_fit(data) else [])
    return keys


def _star_method_keys(data: dict) -> list[tuple[str, str]]:
    """(key, what) of the star's methods the README names in the note's place (addendum cut 9): the dwarf colour
    table of Pecaut and Mamajek 2013 for the temperature and spectral type, and the Lindegren et al. 2021 parallax
    zero point for the distance."""
    star = data.get("star") or {}
    if not star_characterized(data):
        return []
    out = []
    if star.get("route") in ("mann", "mamajek") and value_text(star.get("teff")) is not None:
        out.append(("pecaut2013", ("the temperature and spectral type are read from the colour on the dwarf "
                                   "sequence of Pecaut and Mamajek (2013)")))
    if star.get("distance_pc") is not None:
        out.append(("lindegren2021", "the Gaia parallax is corrected for its zero point (Lindegren et al. 2021)"))
    return out


def star_method_text(data: dict) -> str | None:
    """The README's sentence on the star's colour table and parallax zero point (the note's until addendum cut 9)."""
    parts = [what for _k, what in _star_method_keys(data)]
    return (parts[0][:1].upper() + parts[0][1:] + (f"; {parts[1]}" if len(parts) > 1 else "") + ".") if parts else None


# what each of the README's own references is for (spec 11.6: 2MASS, the matched catalogues, the TIC)
README_REFERENCES = (("skrutskie2006", "2MASS, the Ks magnitude"),
                     ("thompson2018", "the Kepler cumulative KOI table and the Q1 to Q17 DR25 TCE list"),
                     ("guerrero2021", "the TESS TOI list"), ("kirk2016", "the Kepler eclipsing binary catalogue"),
                     ("stassun2019", "the TESS Input Catalog"))


def readme_references(data: dict) -> list[str]:
    """The README's reference list (spec 11.6): the note's bibliography in plain text (``references`` and the prior
    claims), the works the README cites in its place (the habitable zone with Kopparapu et al. 2013, Rogers 2015 and
    Fulton et al. 2017 when it is printed, ``readme_works``), then 2MASS, the matched catalogues and the TIC
    (``README_REFERENCES``), each with what it is for."""
    refs = references(data)
    keys = {k for k, used in refs.items() if used} | set(readme_works(data))
    out = [plain_reference(k) for k, _label, _text in BIBLIOGRAPHY if k in keys]
    out += [_plain_tex(text) for _key, _label, text in prior_bib(data)]
    out += [f"{plain_reference(k)} ({what})" for k, what in README_REFERENCES]
    return sorted(out, key=_by_author)        # by the first author, then the year (Task 13 review M-4)


def _by_author(reference: str) -> tuple[str, str, str]:
    """A reference's sort key: the first author's family name without accents, in lower case ("Martínez-Palomera"
    sorts as "martinez"), then the year with its letter (Mann 2015 before Mann 2019, whoever the co-authors are),
    then the whole text (the Task 13 re-review)."""
    import unicodedata
    plain = "".join(c for c in unicodedata.normalize("NFKD", reference) if not unicodedata.combining(c)).lower()
    year = re.search(r"\b(1[89]|20)\d\d[a-z]?\b", plain)
    return plain.split(",")[0], year.group(0) if year else "", plain


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
    cmp = ((data.get("shape") or {}).get("comparison") or {}) if isinstance(data.get("shape"), dict) else {}

    def val(key: str):
        r = rows[key]
        text = pm_text(r["value"], r["minus"], r["plus"], nd=r["nd"], tex=tex)
        point = _num(r.get("point"))
        if offset and key == "k" and point is not None and r["minus"] is not None:
            nd = pm_digits(r["minus"], r["plus"])
            return _joined(tex, text, f" (full-data fit {point:.{nd}f})")
        return text
    exposure = _num((data.get("shape") or {}).get("exposure_min")) or KEPLER_EXPOSURE_MIN
    head = _model_words(tex, exposure) if cmp else [f"Fitting {LEGACY_MODEL_CLAUSE}"]
    if (data.get("shape") or {}).get("ok") is not True:
        parts = [*head, ", gives a radius ratio of ", val("k")]
        if "b" in rows:
            parts += [" and an impact parameter of ", val("b")]
        return _joined(tex, *parts, ", without errors for the reason given with the duration.")
    parts = [*head, ", gives a radius ratio of ", val("k")]
    # the note's signal sentence quotes the same duration with the same errors, so the note gives T14 once (Task 13)
    if "t14_h" in rows and not (tex and str(val("t14_h")) == str(quantities(data, tex)["duration_pm"])):
        parts += [" and a duration (T14) of ", val("t14_h"), " hours"]
    if "b" in rows and "a_over_r" in rows:
        parts += _geometry_words(data, rows, val, cmp, tex)
    return _joined(tex, *parts, ".")


# the model of a dossier before version 5 (rung 2's uniform disc), in the words of the referee pack spec (section 6)
LEGACY_MODEL_CLAUSE = ("a uniform-disc model without limb darkening on a circular orbit, averaged over the 29.4-minute "
                       "cadence")


def _model_words(tex: bool, exposure_min: float = KEPLER_EXPOSURE_MIN) -> list:
    """"Fitting <MODEL_CLAUSE>" with the measured exposure to 0.1 minute (fix round 1 of Task 10, M-10) and the
    Claret and Bloemen 2011 citation after "limb-darkened model"."""
    before, after = MODEL_CLAUSE.format(exposure=f"{exposure_min:.1f}").split(MODEL_CITE_AFTER, 1)
    cite = Raw(r" \citep{claret2011}") if tex else " (Claret and Bloemen 2011)"
    return [f"Fitting {before}{MODEL_CITE_AFTER}", cite, after]


def _geometry_words(data: dict, rows: dict, val, cmp: dict, tex: bool) -> list:
    """b and one a/R* (spec 11.3). Version 5: a prior fit quoted because the comparison left b and a/R* unconstrained
    (``constrained`` False) says so and gives a/R* from the stellar parameters; a prior fit quoted because the free
    fit failed (``constrained`` None) says that instead (fix round 1 of Task 10, M-2); a quoted free fit gives the
    fitted a/R* and, when the comparison constrained it, its tension with the host's density in sigma; a degenerate
    free b without errors names its reason. Before version 5 the old wording: b and a/R* with their errors, or alone
    with the degeneracy reason."""
    b_note = f" ({rows['b']['note']})" if rows["b"]["minus"] is None and rows["b"].get("note") else ""
    b_val = b_bound_text(rows["b"]) or val("b")        # an unpinned b prints as its bound, not as a median with errors
    if not cmp:
        if rows["b"]["minus"] is None:
            return ["; the impact parameter is ", val("b"), " and a/R* is ", val("a_over_r"), b_note]
        return [", with an impact parameter of ", val("b"), " and a/R* of ", val("a_over_r")]
    if cmp.get("quoted") == "prior":
        stellar = value_text((data.get("planet") or {}).get("a_over_r"), tex)
        a_text = stellar if stellar is not None else val("a_over_r")
        why = UNCONSTRAINED if cmp.get("constrained") is False else FREE_FAILED
        # UNCONSTRAINED already names the host's density, so the note leaves out the label (Task 13)
        label = "" if tex and why == UNCONSTRAINED else f" {A_LABEL_STELLAR}"
        return [f"; b and a/R* are {why}: b is ", b_val, " and a/R* ", a_text, label]
    tension = _num(cmp.get("density_tension_sigma")) if cmp.get("constrained") is True else None
    words = ["; b is ", b_val, " and a/R* ", val("a_over_r"), f" {A_LABEL_FITTED}"]
    if tension is not None:
        words.append(f", {abs(tension):.1f} sigma from the host's density")
    return [*words, b_note]


FREE_FAILED = "from the density-prior fit (the free fit did not converge)"


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


def stacked_parity_text(data: dict, tex: bool = False, where: str = "") -> str | None:
    """The stacked fold of each parity (spec 11.5): "X ± e and Y ± e ppm (Z sigma apart)"; None without it. The
    README's table prints it since Task 13 (the word limit; addendum cut 4), the note the per-event pair."""
    parity = data.get("parity")
    stacked = parity.get("stacked") if isinstance(parity, dict) else None
    if not isinstance(stacked, dict):
        return None
    texts = [pm_text((stacked.get(k) or {}).get("depth_ppm"), (stacked.get(k) or {}).get("err_ppm"),
                     (stacked.get(k) or {}).get("err_ppm"), nd=0, comma=True, tex=tex) for k in ("odd", "even")]
    if None in texts:
        return None
    sd = _num(stacked.get("difference_sigma"))
    return _joined(tex, texts[0], " and ", texts[1], f" ppm{where}",
                   f" ({sd:.1f} sigma apart)" if sd is not None else "")


def parity_sentence(data: dict, tex: bool = False, stacked: bool = True) -> str | None:
    """The odd and even depths of the parity block, the per-event estimator with its errors and the difference in
    sigma, then (``stacked``) the stacked fold of each parity the same way when the block stores it (spec 11.5): "The
    odd and even depths are X ± e and Y ± e ppm per event (Z sigma apart) and X' ± e and Y' ± e ppm on the stacked
    folds (Z' sigma apart)."; rung 2's depths without errors for an older dossier. The note leaves the stacked pair to
    the README (addendum cut 4)."""
    sh = _metrics(data, "rung2_shape")
    pair = _odd_even(data, tex)
    if pair is None:
        odd, even, par = (_f(sh.get("odd_ppm"), 0, True), _f(sh.get("even_ppm"), 0, True),
                          _f(sh.get("parity_sigma"), 1))
        if not (odd and even):
            return None
        return f"The odd and even depths are {odd} and {even} ppm" + (f" ({par} sigma apart)." if par else ".")
    parity = data["parity"]
    diff = _num(parity.get("difference_sigma"))
    odd = pm_text(parity["odd"]["depth_ppm"], parity["odd"]["err_ppm"], parity["odd"]["err_ppm"], nd=0, comma=True,
                  tex=tex)                                  # the unit once, after the even depth
    parts = ["The odd and even depths are ", odd, " and ", pair[1], " per event"]
    if diff is not None:
        parts.append(f" ({diff:.1f} sigma apart)")
    folds = stacked_parity_text(data, tex, " on the stacked folds") if stacked else None
    if folds:
        parts += [" and ", folds]
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


def secondary_sentence(data: dict, tex: bool = False, short: bool = False) -> str | None:
    """The phase 0.5 depth with its error, its 3-sigma upper limit, the albedo clause and the strongest dip at any
    other phase (the secondary block); rung 2's depth against the scatter for an older dossier; None without
    either. "The phase 0.5 depth is X ± e ppm (3-sigma upper limit L ppm), <albedo>; the strongest dip elsewhere is
    D ppm at phase F (S sigma)." since Task 8 fix round 4 (the word limit). Beside the strongest dip, rung 2's
    model-shift margins (``sigma_pos``, the largest positive excursion, and ``sigma_ter``, the tertiary dip) show the
    red noise (spec 11.5): ", against model-shift margins of P sigma for the largest positive excursion and T sigma
    for the tertiary dip"; ``short``, the note's form since Task 13 (addendum cut 6), says ", and positive excursions
    in the same fold reach P sigma" and leaves the tertiary to the README."""
    sec = data.get("secondary")
    if not isinstance(sec, dict) or _num(sec.get("depth_ppm")) is None:
        sh = _metrics(data, "rung2_shape")
        d, rms = _f(sh.get("secondary_ppm"), 0, True), _f(sh.get("control_rms_ppm"), 0, True)
        return (f"No secondary eclipse shows at phase 0.5 ({d} ppm against a scatter of {rms} ppm)." if d and rms
                else None)
    parts = ["The phase 0.5 depth is ",
             pm_text(sec["depth_ppm"], sec.get("err_ppm"), sec.get("err_ppm"), nd=0, comma=True, tex=tex), " ppm"]
    limit = _f(sec.get("limit_3sigma_ppm"), 0, True)
    top, phase, sigma = (_f(sec.get("strongest_ppm"), 0, True), _f(sec.get("strongest_phase"), 2),
                         _f(sec.get("strongest_sigma"), 1))
    if short:       # the note's form since Task 13 fix round 1 (cut 12): one sentence, no "upper", no "in the fold"
        albedo = albedo_text(data)
        if limit:
            parts.append(f" (3-sigma limit {limit} ppm{'' if albedo == f', {ALBEDO_NONE}' else albedo})")
        if top and phase and sigma:
            parts.append(f", the strongest dip elsewhere {top} ppm at phase {phase} ({sigma} sigma)")
            pos = _f(_metrics(data, "rung2_shape").get("sigma_pos"), 1)
            if pos:
                parts.append(f"; positive excursions in the same fold reach {pos} sigma")
        return _joined(tex, *parts, ".")
    if limit:
        parts.append(f" (3-sigma upper limit {limit} ppm){albedo_text(data)}")
    if top and phase and sigma:
        parts.append(f"; the strongest dip elsewhere is {top} ppm at phase {phase} ({sigma} sigma)")
        sh = _metrics(data, "rung2_shape")
        pos, ter = _f(sh.get("sigma_pos"), 1), _f(sh.get("sigma_ter"), 1)
        if pos and ter:
            parts.append(f", against model-shift margins of {pos} sigma for the largest positive excursion and {ter} "
                         "sigma for the tertiary dip")
    return _joined(tex, *parts, ".")


def trend_sentence(data: dict, tex: bool = False, clause: bool = False) -> str | None:
    """The per-event depths' trend against time (the ``depth_trend`` block, spec 11.5), only when it is printed (above
    ``ephemeris.DEPTH_TREND_SIGMA`` with errors scaled by the scatter): "The per-event depth deepens by S ± e ppm per
    year (Z sigma, errors scaled by the scatter)." ("shallows" for a negative slope; a positive slope is a dip that
    grows deeper with time, Task 12 review M-2); None otherwise. ``clause`` is the note's form, a clause that ends
    the quarter-group sentence: ", though the per-event depth deepens by S ± e ppm per year (Z sigma, scaled
    errors)." (addendum cut 7)."""
    trend = data.get("depth_trend")
    if not isinstance(trend, dict) or trend.get("printed") is not True:
        return None
    slope, err, sigma = (_num(trend.get(k)) for k in ("slope_ppm_per_year", "err_ppm_per_year", "sigma"))
    if None in (slope, err, sigma):
        return None
    verb = "deepens" if slope >= 0 else "shallows"
    if clause:
        return _joined(tex, f", though the per-event depth {verb} by ",
                       pm_text(abs(slope), err, err, nd=0, comma=True, tex=tex), f" ppm per year ({sigma:.1f} sigma, "
                       "scaled errors).")
    return _joined(tex, f"The per-event depth {verb} by ", pm_text(abs(slope), err, err, nd=0, comma=True, tex=tex),
                   f" ppm per year ({sigma:.1f} sigma, errors scaled by the scatter).")


def stability_sentence(data: dict) -> str | None:
    """Rung 3 on the fold (the stability block, Task 10), else rung 3's own row, in words: "The depth agrees across N
    quarter groups (quarters modulo 4; chi-squared X for D degrees of freedom, p = P)."; the groups are quarters
    modulo 4, "season" being the Kepler roll season of the pixel check (spec 11.6). None without the numbers."""
    stab = data.get("stability")
    st = (stab.get("metrics") or {}) if isinstance(stab, dict) and stab.get("metrics") else _metrics(
        data, "rung3_stability")
    if not st:
        return None
    n_groups = st.get("n_seasons", len(st.get("season_depths") or {}))
    chi2, dof, p = _f(st.get("chi2"), 1), st.get("dof"), _f(st.get("p_value"), 2)
    if not (n_groups and chi2 is not None and dof is not None):
        return None
    return (f"The depth agrees across {n_groups} quarter groups (quarters modulo 4; chi-squared {chi2} for "
            f"{dof} degrees of freedom" + (f", p = {p})." if p else ")."))


def vetting_texts(data: dict) -> dict:
    """The dossier page's copies of the note's vetting sentences in the README's words (round-2 final review, I-2):
    ``shape``, ``parity``, ``secondary`` and ``stability``, each None when it does not apply."""
    return {"shape": shape_sentence(data), "parity": parity_sentence(data), "secondary": secondary_sentence(data),
            "stability": stability_sentence(data)}


def star_texts(data: dict) -> str | None:
    """The reddening paragraph (``reddening_plain``, spec 12.3) and the note's Gaia flags sentence in plain text, for
    the dossier page (I-2); a dossier without the extinction block keeps the note's GSP-Phot sentence after the flags.
    None without any."""
    flags, plain = gaia_flags_text(data), reddening_plain(data)
    red = None if plain else reddening_text(data)
    parts = [plain, f"{flags[:1].upper()}{flags[1:]}." if flags else None, f"{red}." if red else None]
    return " ".join(p for p in parts if p) or None


def fold_text(data: dict) -> str | None:
    """The figure's fold in words, as the note's caption gives it, for the dossier page (I-2): "Folded on the P day
    <ephemeris>, within W hours of mid-transit, in bins of B minutes."; None without a period."""
    period = quantities(data).get("period")
    if period is None:
        return None
    window, bins = window_hours(data), bin_minutes(data)
    return (f"Folded on the {period} day {fold_words(data)}" + (f", within {window} hours of mid-transit" if window
                                                                else "")
            + (f", in bins of {bins} minutes" if bins else "") + ".")


def _vetting_sentences(data: dict, tex: bool = False) -> list[str]:
    """Rungs 1 to 4 in words, from their stored metrics and the dossier's error blocks (the transit fit, odd and even,
    the secondary); a rung that did not pass says so with its reason. With ``tex`` the sentences with errors are
    ``Raw`` LaTeX."""
    out = []
    ev = _metrics(data, "rung1_events")
    n_cov, n_data = ev.get("n_covered"), ev.get("n_events_with_data")
    if n_cov is not None and n_data:
        s = f"{n_cov} of {n_data} predicted transits with data are well covered"
        share = _num(ev.get("ses_max_over_mes"))
        s += (f"; the largest single-event statistic is {share:.2f} of the multiple-event statistic."
              if share is not None else ".")
        out.append(s)
    out += [s for s in (shape_sentence(data, tex), parity_sentence(data, tex, stacked=not tex),
                        secondary_sentence(data, tex, short=tex)) if s]
    stability = stability_sentence(data)
    if stability:
        out.append(stability)
    trend = trend_sentence(data, tex, clause=tex and bool(out) and out[-1].startswith("The depth agrees"))
    if trend and tex and out and out[-1].startswith("The depth agrees"):
        out[-1] = _joined(tex, out[-1][:-1], trend)            # one clause of that sentence (addendum cut 7)
    elif trend:
        out.append(trend)
    nv = _metrics(data, "rung4_novelty")
    if nv:
        names = {"cumulative": "the Kepler cumulative KOI table", "q1_q17_dr25_tce": "the Q1 to Q17 DR25 TCE list",
                 "toi": "the TESS TOI list", "kepler_eb": "the Kepler eclipsing binary catalogue"}
        hits = [f"{int(nv[k])} in {v}" for k, v in names.items() if _num(nv.get(k))]
        if hits:
            out.append(f"Catalogue matches: {', '.join(hits)}.")
        elif any(k in nv for k in names):
            out.append("It matches no Kepler KOI, DR25 TCE, TESS TOI or Kepler eclipsing binary entry.")
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
    """The seasons that did not vote, each with its stored reason as one clause (``reason_words``) in parentheses: "Season 3 did not
    vote: box cut: ambiguous at 1.8 sigma." (no brackets inside brackets, round-2 final review, minor 10)."""
    def label(s: dict) -> str:
        return str(s.get("season")) if s.get("season") is not None else "?"
    if not silent:
        return ""
    if len(silent) == 1:
        s = silent[0]
        return f"Season {label(s)} did not vote ({reason_words(s.get('non_voting_reason'))})."
    names = ", ".join(label(s) for s in silent[:-1]) + f" and {label(silent[-1])}"
    why = "; ".join(f"season {label(s)} {reason_words(s.get('non_voting_reason'))}" for s in silent)
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


JITTER_FLOOR = "pointing-jitter floor"       # spec 11.5: the measured quarter-to-quarter term of the centroid error
SAME_ERROR_RULE = "Both errors are the larger of the seasons' combined error and their scatter."


def _exclusion_words(block: dict, short: bool) -> list[str]:
    """The exclusion radius clause and the nearest Gaia source beyond it (spec 11.5), [] without a radius."""
    radius = _num(block.get("exclusion_radius_arcsec"))
    radius = ds.exclusion_radius(block) if radius is None else radius
    if radius is None:
        return []
    out = [(f", with an exclusion radius of {radius:.2f} arcsec (max(3 sigma, 0.5 arcsec)) within which a background "
           "source is not excluded")]
    nb = block.get("nearest_beyond")
    sep = _num(nb.get("centroid_sep_arcsec")) if isinstance(nb, dict) else None
    if sep is not None and nb.get("source_id"):
        g = _num(nb.get("gmag"))
        named = "" if short else f" is Gaia DR3 {nb['source_id']}"      # the note: distance and G (Task 13)
        out.append(f"; the nearest Gaia source beyond it{named} {'lies ' if short else ''}at {sep:.1f} arcsec"
                   + ("" if short else " from the centroid") + (f" (G = {g:.1f})" if g is not None else ""))
    return out


def offset_sentence(data: dict, tex: bool = False, short: bool = False) -> str | None:
    """The pixel offset (spec 11.5, after ruling e), in this order: the difference-image centroid against the Gaia
    position, with the one clause that it carries the WCS zero point and the registration, its error and the
    exclusion radius max(3 sigma, 0.5 arcsec) inside which a background source is not excluded, and the nearest Gaia
    source beyond it (``_exclusion_words``); then the offset against the star's own out-of-transit image, as Bryson et
    al. 2013 quote it, with its error and sigma, judged by their rule (above 3 sigma and above 0.2 arcsec), and "its
    reference is pulled by the neighbour" when the direct centroid sits more than ``dossier.PULL_PIX`` from the Gaia
    position; then the error rule. None without the block; with ``tex`` a ``Raw`` citing ``bryson2013``. ``short`` is
    the note's form (fewer connectives); the README, the ExoFOP note and the page keep the long one."""
    block = offset_block(data)
    if not block:
        return None
    cat, cat_err = block.get("catalogue_referenced_arcsec"), block.get("catalogue_referenced_err_arcsec")
    if short:
        # the README's long form says what the reference carries (Task 13, the word limit)
        lead = ["Against the Gaia position the centroid sits ",
                pm_text(cat, cat_err, cat_err, tex=tex), " arcsec away"]
    else:
        lead = [("Against the Gaia position projected through each stamp's WCS, a reference that carries the WCS zero "
                "point and the registration, the difference-image centroid sits "),
                pm_text(cat, cat_err, cat_err, tex=tex), " arcsec away"]
    lead += _exclusion_words(block, short)
    same = "" if short else SAME_ERROR_RULE     # the note leaves the error rule to the README (Task 13)
    off, err, sigma = (_num(block.get(k)) for k in ("self_referenced_arcsec", "self_referenced_err_arcsec", "sigma"))
    if off is None or err is None or sigma is None:
        return _joined(tex, *lead, ".")
    if not self_referenced(block):
        return _joined(tex, *lead, f". Against {block.get('reference') or 'the fitted position of the star'} (the "
                       "star's direct-image centroid is not stored for every season) the difference-image centroid is "
                       "offset by ", pm_text(off, err, err, tex=tex), f" arcsec ({sigma:.1f} sigma), not assessed "
                       "from the star's own image.", f" {same}" if same else "")
    cite = Raw(r"\citet{bryson2013}") if tex else "Bryson et al. (2013)"
    level, k = _num(block.get("level_arcsec")) or 0.2, _num(block.get("sigma_level")) or 3.0
    verdict = "an offset" if block.get("counts") else "not an offset"
    if short:                   # one clause since Task 13 (the word limit; addendum cut 7)
        own = ["; against the star's out-of-transit image the offset is ", pm_text(off, err, err, tex=tex),
               f" arcsec ({sigma:.1f} sigma), {verdict} by the rule of ", cite,
               f" (over {k:g} sigma and {level:g} arcsec)"]
    else:
        own = [". Against the star's own out-of-transit image the difference-image centroid is offset by ",
               pm_text(off, err, err, tex=tex), f" arcsec ({sigma:.1f} sigma), {verdict} by the rule of ", cite,
               f", which asks for more than {k:g} sigma and more than {level:g} arcsec"]
    pull = _num(block.get("direct_pull_pix"))
    if block.get("pulled") is True and pull is not None:
        own.append(f", its reference pulled by the neighbour up to {pull:.2f} px" if short else
                   f"; its reference is pulled by the neighbour, up to {pull:.2f} px from the Gaia position")
    return _joined(tex, *lead, *own, ". " if same else ".", same)


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
    what = "" if tex else " (out of transit minus in transit)"          # the README defines it (Task 13)
    text = f"In difference images{what} {_seasons_phrase(near, voting, silent)}."
    seasons = voting
    dt = [d for s in seasons if (d := _num(s.get("dist_target_pix"))) is not None]
    dn = [d for s in seasons if (d := _num(s.get("dist_neighbour_pix"))) is not None]
    if dt and dn:
        floor = ds.floor_pix(seasons)
        carried = "" if floor is None else f", its error carrying a {JITTER_FLOOR} of {floor:.3f} px"
        if tex:         # the note opens on one sentence (Task 15 fix round 1, cut B1; the abstract has the vote)
            kind = "seasons that voted" if silent else "observing seasons"
            text = (f"In difference images the centroid sits a median {np.median(dt):.2f} pixels from this star and "
                    f"{np.median(dn):.2f} from its brightest neighbour, nearer this star in {near} of "
                    f"{len(voting)} {kind}{carried}.")
        else:
            text += (f" The centroid sits a median {np.median(dt):.2f} pixels from this star and "
                     f"{np.median(dn):.2f} pixels from the neighbour{carried}.")
    cut = ds.partial_box_sentence(seasons, short=tex)
    if cut:
        text += f" {cut}"
    offset = offset_sentence(data, tex, short=tex)          # the note (tex) takes the short form
    parts: list = [text, " ", offset] if offset else [text]
    text = ""
    pairs = [(t, n) for s in seasons
             if (t := _first(s.get("depth_target_ppm"))) is not None
             and (n := _first(s.get("depth_neighbour_ppm"))) is not None]
    if pairs:
        t = float(np.median([p[0] for p in pairs]))
        n = float(np.median([p[1] for p in pairs]))
        text += (f" A two-source fit gives a median depth of {t:,.0f} ppm on this star and {n:,.0f} on the "
                 "neighbour.")
    if silent:
        text += f" {_silent_sentence(silent)}"
    if row.get("status") == "fail":
        text += " The pixel check failed: the dip may sit on the neighbour."
    return _joined(tex, *parts, text)


def gaia_flags_text(data: dict) -> str | None:
    """The star sentence's Gaia clause (spec 11.6): "Gaia gives a RUWE of R and no non-single-star solution" (or "a
    non-single-star solution (flag N)" when the flag is above 0); None when the star block carries neither (a dossier
    made before them). The GSP-Phot reddening has its own sentence with its caveat (``reddening_text``)."""
    star = data.get("star") or {}
    ruwe, nss = _num(star.get("ruwe")), _num(star.get("non_single_star"))
    parts = []
    if ruwe is not None:
        parts.append(f"a RUWE of {ruwe:.2f}")
    if nss is not None:
        parts.append(f"a non-single-star solution (flag {nss:.0f})" if nss > 0 else "no non-single-star solution")
    return "Gaia gives " + " and ".join(parts) if parts else None


REDDENING_BIAS = "a value biased for cool dwarfs"
# spec 12.3 and 12.5: the note's sentence with a 3D value adopted (the controller's ruling of 26 October 2026 and
# Task 17 fix round 1, ruling 2): the value with its 16th to 84th percentile range at three decimals, as the README
# prints them (``EBV_RANGE``, never a zero error), the map, the distance, then one parenthesis: the full column with
# its source as an upper bound (``COLUMN_NOTE``; the scales are the README key's), led for a star outside the map's
# reliable range by the range's end (the side's words, ``stars.range_side``, before it). When the dereddened star
# leaves the Mann relations' range, ``MANN_ADOPTED``. "A sightline average" and the GSP-Phot half stay in the
# README's paragraph and table (Task 16 Step 13, cut 1)
REDDENING_CLAUSE = "the reddening is E(B-V) = {ebv} from Bayestar19{green} at {d} pc"
EBV_RANGE = "{ebv:.3f} ({p16:.3f} to {p84:.3f})"
COLUMN_NOTE = "full column {col}"
MANN_ADOPTED = "; the dereddened parameters are " + stars_module.MANN_OUTSIDE
# Task 17 fix round 1, ruling 3: the README key's words on the scales, for a value the map gave (its sample count
# from the record), and the plain check clause's few words
KEY_SCALE = ("E(B-V) is 0.884 times the map's unit (E(g-r) = 0.901 per unit, Schlafly and Finkbeiner 2011 Table 6; "
             "the 0.884 from the Schlafly et al. 2016 reddening vector, as the Bayestar usage notes give it), from "
             "{samples} with their 16th to 84th percentiles")
KEY_COLUMN_SCALE = ("on the Schlafly and Finkbeiner 2011 scale, 0.86 times SFD98 (Schlegel, Finkbeiner and Davis 1998), "
                    "so the comparison is a check across two scales, not a measurement on one")
# fix wave round 4, polish (a): the README key says which star the FPP rests on (the plain texts' few words are
# ``crosscheck.TIC_STAR``; the note is unchanged)
KEY_TIC_STAR = ("TRICERATOPS draws the target's Teff, radius and mass from the TIC (its own star list), so the dossier's "
                "derived star does not enter the FPP.")
COLUMN_SCALE = "on the Schlafly and Finkbeiner 2011 scale"
EARLIER_ASSUMPTION = f"{stars_module.UNREDDENED} is the earlier assumption"
# the three states the texts follow (spec 12.3 as amended by the controller's addenda of 23 October)
RED_ADOPTED, RED_BRACKET, RED_NOT_AVAILABLE = "adopted", "bracket", "not available"
FULL_COLUMN = "Full column (Schlafly and Finkbeiner 2011)"
REDDENING_COLUMNS = (("estimate", "Estimate"), ("ebv", "E(B-V)"), ("ebprp", "E(BP-RP)"), ("a_g", "A_G"),
                     ("a_ks", "A_Ks"), ("bp_rp0", "(BP-RP)_0"), ("teff", "Teff (K)"), ("radius", "Radius (Rsun)"),
                     ("mass", "Mass (Msun)"), ("insolation", "Insolation (Earth)"),
                     ("rp", "Planet radius (Earth radii)"), ("status", "Status"))
GAIA_LAW_WORDS = ("E(BP-RP), A_G and A_Ks follow from E(B-V) through the Gaia (E)DR3 extinction law at R_V 3.1 "
                  "(Fitzpatrick et al. 2019; Riello et al. 2021), with A_Ks = 0.117 A_0 (Cardelli et al. 1989).")


def reddening_state(data: dict) -> str | None:
    """Which reddening the texts print (spec 12.3 as amended): ``RED_ADOPTED`` for a 3D value (a stored Bayestar19
    value or one stored by hand); ``RED_BRACKET`` without one but with the full-column E(B-V); ``RED_NOT_AVAILABLE``
    with neither; None for a star that is not characterised or a dossier without the block (before version 6)."""
    ext = (data.get("star") or {}).get("extinction")
    if not isinstance(ext, dict) or not star_characterized(data):
        return None
    if ext.get("state") != stars_module.EXT_NOT_AVAILABLE:
        return RED_ADOPTED
    column = ext.get("column")
    ok = isinstance(column, dict) and column.get("status") == stars_module.COLUMN_OK
    return RED_BRACKET if ok else RED_NOT_AVAILABLE


def _column_derived(data: dict) -> dict | None:
    """The full-column block when it carries what the law made of it (``stars.derive``), else None."""
    col = ((data.get("star") or {}).get("extinction") or {}).get("column")
    return col if isinstance(col, dict) and col.get("status") == stars_module.COLUMN_OK and "ebprp" in col else None


def column_ebv_text(col: dict) -> str:
    """The full-column E(B-V) as the plain texts print it (minor 8): "0.674 ± 0.034", with the stored spread inside the
    service's 5 arcmin circle; the value alone without one."""
    std = _num(col.get("ebv_std"))
    return f"{_num(col['ebv']):.3f} ± {std:.3f}" if std else f"{_num(col['ebv']):.3f}"


def _height(data: dict) -> str:
    """"; <clause>" (ruling (c): the star's height above the plane against the dust layer, ``stars.height_fields``)
    for the plain texts' full-column sentence; empty without one."""
    clause = ((data.get("star") or {}).get("extinction") or {}).get("height_clause")
    return f"; {clause}" if clause else ""


def column_fit(data: dict) -> str | None:
    """The full column as the adopted value's check (spec 12.5; the controller's ruling of 26 October 2026):
    "consistent" when the 3D median sits at or below the column's E(B-V) plus its stored std, "inconsistent" above
    it; None outside the adopted state or without a fetched column."""
    if reddening_state(data) != RED_ADOPTED:
        return None
    e = data["star"]["extinction"]
    col = e.get("column")
    if not isinstance(col, dict) or col.get("status") != stars_module.COLUMN_OK or _num(col.get("ebv")) is None:
        return None
    top = _num(col["ebv"]) + (_num(col.get("ebv_std")) or 0.0)
    return "consistent" if float(e["ebv"]) <= top + 1e-9 else "inconsistent"


def _not_there(data: dict) -> str:
    """Why no 3D value is adopted, for the plain texts: the local map's short reason when it was read (Task 17:
    outside its footprint, an unconverged pixel, no finite sample), else the build-time words of Task 16."""
    why = ((data.get("star") or {}).get("extinction") or {}).get("map_reason")
    return f"Bayestar19 not available: {why}" if why else "Bayestar19 not available at build time"


def _distance_check(e: dict) -> str:
    """" (E(B-V) a to b across the distance's 16th to 84th percentiles, d1 to d2 pc)" from a local-map record (the
    sample medians at the Gaia distance's percentiles, Task 17), so the texts say what the distance error does to the
    reddening; "E(B-V) unchanged at the printed precision across ..." when the two ends print alike (fix round 1,
    M3); empty without them."""
    vals = [_num(e.get(k)) for k in ("ebv_at_distance_p16", "ebv_at_distance_p84", "distance_pc_p16",
                                     "distance_pc_p84")]
    if None in vals:
        return ""
    lo, hi = (f"{v:.3f}" for v in sorted(vals[:2]))
    # fix round 1, M3: no "0.009 to 0.009"; the form ``rp_bracket_text`` uses when the two ends print alike
    span = "unchanged at the printed precision" if lo == hi else f"{lo} to {hi}"
    return f" (E(B-V) {span} across the distance's 16th to 84th percentiles, {vals[2]:.0f} to {vals[3]:.0f} pc)"


def _mid_at(v, ref) -> str | None:
    """A value's median alone at the decimals the adopted value's error gives (``pm_digits``), with thousands
    commas: the unreddened and full-column values beside the adopted one. None without a median."""
    if not isinstance(v, dict) or _num(v.get("median")) is None:
        return None
    d = pm_digits(ref.get("minus"), ref.get("plus")) if isinstance(ref, dict) else 2
    return f"{_num(v['median']):,.{d}f}"


def _range_short(e: dict) -> str:
    """", nearer than the map's reliable range" (or beyond, or outside) for the README table's status, without the
    range's end, which the key gives (Task 17 fix round 1, I1); empty where the map constrains the dust."""
    side = stars_module.range_flag(e, end=False)
    return f", {side}" if side else ""


def _map_value(e: dict) -> bool:
    """Whether the adopted value is the map's own (the stored state "adopted", from the local map or Argonaut), so
    its unit and scale are the map's (ruling 3); a value stored by hand is not."""
    return e.get("state", e.get("status")) == stars_module.EXT_ADOPTED


def _adopted_mann(e: dict) -> str:
    """", outside the Mann relations' range, so indicative" when the adopted, dereddened star leaves the relations'
    calibration (``stars.outside_mann_range``, recorded by ``derive``); empty otherwise."""
    return f", {stars_module.MANN_OUTSIDE}" if e.get("outside_mann_range") is True else ""


def _adopted_trio(star: dict, planet: dict, unred: dict, col: dict | None) -> str:
    """The adopted Teff, radius and planet radius, each as "<adopted> (unreddened <value>; full column <value>)"
    (the ruling's form; the full column when it was fetched), the values beside at the adopted value's decimals."""
    parts = []
    for name, key, unit, holder in (("Teff", "teff", " K", star), ("radius", "radius", " Rsun", star),
                                    ("planet radius", "rp_rearth", " Earth radii", planet)):
        adopted = holder.get(key)
        text = value_text(adopted)
        if not text:
            continue
        beside = [f"unreddened {u}" for u in [_mid_at(unred.get(key), adopted)] if u]
        beside += [f"full column {c}" for c in [_mid_at((col or {}).get(key), adopted)] if c and col is not None]
        parts.append(f"{name} {text}{unit}" + (f" ({'; '.join(beside)})" if beside else ""))
    return ", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + parts[-1] if parts else ""


def _gsp_row(data: dict) -> bool:
    """Whether the README's reddening table has a GSP-Phot row (its E(BP-RP) or its Teff). Minor 7: the key's
    GSP-Phot sentence and Andrae et al. 2023 come only with it."""
    star = data.get("star") or {}
    return (_num(star.get("ebpminrp_gspphot")) is not None
            or _num((star.get("gaia_cross_check") or {}).get("teff_gspphot")) is not None)


def _red(v) -> str | None:
    """An extinction value as the texts print it: with its error by the precision rule, three decimals without one."""
    if not isinstance(v, dict) or _num(v.get("median")) is None:
        return None
    if (_num(v.get("minus")) or 0) > 0 or (_num(v.get("plus")) or 0) > 0:
        return value_text(v)
    return f"{_num(v['median']):.3f}"


def _mann_flag(col: dict | None) -> str:
    """", outside the Mann relations' range, so indicative" when the full-column end's dereddened star leaves the
    relations' calibration (``stars.outside_mann_range``, the round 3 final review C1(i)); empty otherwise."""
    return f", {stars_module.MANN_OUTSIDE}" if (col or {}).get("outside_mann_range") is True else ""


def _at_adopted(v, ref) -> str | None:
    """A full-column value with its errors at the decimals the adopted value's error gives (``pm_digits``, the one
    precision rule; the round 3 final review, M3), so the upper end prints as 2.7 beside 2.5, not as 3.59; with its
    own decimals when the adopted value has no error. None without a median."""
    if not isinstance(v, dict) or _num(v.get("median")) is None:
        return None
    if not isinstance(ref, dict):
        return value_text(v)
    d = pm_digits(ref.get("minus"), ref.get("plus"))
    lo, hi = _num(v.get("minus")), _num(v.get("plus"))
    mid = f"{_num(v['median']):,.{d}f}"
    if not ((lo or 0) > 0 or (hi or 0) > 0):
        return mid
    a, b = f"{lo or 0.0:,.{d}f}", f"{hi or 0.0:,.{d}f}"
    return f"{mid} ± {b}" if a == b else f"{mid} +{b}/-{a}"


def rp_bracket_text(data: dict, tex: bool = False) -> str | None:
    """The note's one clause on the planet radius without a 3D value (the controller's addendum 2; the round 3 final
    review, C1), after the adopted radius and its unit: ", 2.5 to 2.7 from unreddened (E(B-V) = 0), adopted, to the
    full-column 0.674 (Schlafly and Finkbeiner 2011)", then ``_mann_flag``; no second unit and no second "E(B-V)"
    (the word limit: the longest live shape stood at 1,481). Both ends at the adopted radius's decimals
    (``pm_digits``), the smaller first; when they print alike, ", unchanged at the printed precision from ...". None outside the bracket state or
    without both radii."""
    if reddening_state(data) != RED_BRACKET:
        return None
    col, rp = _column_derived(data), (data.get("planet") or {}).get("rp_rearth")
    hi = _num(((col or {}).get("rp_rearth") or {}).get("median"))
    if not isinstance(rp, dict) or _num(rp.get("median")) is None or hi is None:
        return None
    d = pm_digits(rp.get("minus"), rp.get("plus"))
    cite = Raw(r" \citep{schlafly2011}") if tex else " (Schlafly and Finkbeiner 2011)"
    a, b = sorted((float(f"{_num(rp['median']):.{d}f}"), float(f"{hi:.{d}f}")))
    span = [", unchanged at the printed precision"] if a == b else [f", {a:.{d}f} to {b:.{d}f}"]
    return _joined(tex, *span, f" from {stars_module.UNREDDENED}, adopted, to the full-column "
                   f"{_num(col['ebv']):.3f}", cite, _mann_flag(col))


def _gsp_clause(star: dict) -> str | None:
    red = _num(star.get("ebpminrp_gspphot"))
    return f"GSP-Phot E(BP-RP) {red:.2f}, biased for cool dwarfs (Andrae et al. 2023), is not adopted" if (
        red is not None) else None


def _trio(block: dict, planet_rp) -> str:
    """"Teff T K, radius R Rsun and planet radius P Earth radii" from Value dicts, the parts there are."""
    parts = [f"Teff {value_text(block.get('teff'))} K" if value_text(block.get("teff")) else None,
             f"radius {value_text(block.get('radius'))} Rsun" if value_text(block.get("radius")) else None,
             f"planet radius {value_text(planet_rp)} Earth radii" if value_text(planet_rp) else None]
    parts = [p for p in parts if p]
    return ", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + parts[-1] if parts else ""


def _column_trio(col: dict, star: dict, planet: dict) -> str:
    """``_trio`` of the full-column end at the adopted values' decimals (``_at_adopted``), then ``_mann_flag``."""
    vals = [("Teff", _at_adopted(col.get("teff"), star.get("teff")), " K"),
            ("radius", _at_adopted(col.get("radius"), star.get("radius")), " Rsun"),
            ("planet radius", _at_adopted(col.get("rp_rearth"), planet.get("rp_rearth")), " Earth radii")]
    parts = [f"{name} {v}{unit}" for name, v, unit in vals if v]
    text = ", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + parts[-1] if parts else ""
    return text + _mann_flag(col)


def reddening_plain(data: dict) -> str | None:
    """The reddening paragraph of the plain texts (README, ExoFOP note, archive email, Zenodo, the page), by
    ``reddening_state``; None without the block or a characterised star.

    Adopted (Task 17, spec 12.5): "Reddening: E(B-V) E (16th to 84th percentile L to H) from Bayestar19 (Green et
    al. 2019) at D pc[ (E(B-V) a to b across the distance's 16th to 84th percentiles, d1 to d2 pc)], a sightline
    average[, nearer than the map's reliable range (from N pc)], adopted; through the Gaia DR3 law it gives E(BP-RP)
    ..., A_G ... and A_Ks ..., and Teff T K (unreddened U; full column C), radius ... and planet radius ...[, outside
    the Mann relations' range, so indicative]; unreddened (E(B-V) = 0) is the earlier assumption[; the 3D value is
    consistent (inconsistent) with the full column (the full column, F on the Schlafly and Finkbeiner 2011 scale, is
    an upper bound); <the height clause, settled by the 3D value, or saying the map does not constrain the dust this
    near (far)>][; GSP-Phot ...]." (Task 17 fix round 1, rulings 1, 3 and 4.) Bracket: not measured at the
    star's distance, the parameters unreddened (E(B-V) = 0), adopted, the full column as the upper bound with what the
    law makes of it (``_column_trio``: the adopted values' decimals and the Mann-range clause), GSP-Phot not adopted.
    Not available: the unreddened parameters adopted, neither value there."""
    state = reddening_state(data)
    if state is None:
        return None
    star, planet = data.get("star") or {}, data.get("planet") or {}
    e, unred = star["extinction"], star.get("unreddened") or {}
    gsp = _gsp_clause(star)
    col = _column_derived(data)
    if state == RED_ADOPTED:
        how = (f", stored by hand ({e.get('source')})," if e.get("state") == stars_module.EXT_MANUAL else "")
        side = stars_module.range_flag(e)       # fix round 1 (C1, M1): the side of the map's reliable range
        flag = f", {side}" if side else ""
        law = [f"E(BP-RP) {_red(e.get('ebprp'))}", f"A_G {_red(e.get('a_g'))}", f"A_Ks {_red(e.get('a_ks'))}"]
        # Task 17 (spec 12.5): each parameter beside its unreddened and full-column values, the unreddened star the
        # earlier assumption, the full column a check the 3D value sits inside or not, in one clause
        text = (f"Reddening: E(B-V) {e['ebv']:.3f} (16th to 84th percentile {e['ebv_p16']:.3f} to "
                f"{e['ebv_p84']:.3f}) from Bayestar19 (Green et al. 2019){how} at {e['distance_pc']:.0f} pc"
                f"{_distance_check(e)}, a sightline average{flag}, adopted; through the Gaia DR3 law it gives "
                f"{law[0]}, {law[1]} and {law[2]}, and {_adopted_trio(star, planet, unred, col)}{_adopted_mann(e)}; "
                f"{EARLIER_ASSUMPTION}")
        fit = column_fit(data)
        if fit is not None:
            # fix round 1, ruling 3: the column's scale named where the comparison is printed
            text += (f"; the 3D value is {fit} with the full column (the full column, {column_ebv_text(e['column'])} "
                     f"{COLUMN_SCALE}, is an upper bound){_height(data)}")
        return text + (f"; {gsp}." if gsp else ".")
    if state == RED_BRACKET:
        text = (f"Reddening: not measured at the star's distance ({_not_there(data)}), so the "
                f"parameters are {stars_module.UNREDDENED}, adopted ({_trio(star, planet.get('rp_rearth'))}).")
        if col is not None:
            law = [f"E(BP-RP) {_red(col.get('ebprp'))}", f"A_G {_red(col.get('a_g'))}",
                   f"A_Ks {_red(col.get('a_ks'))}"]
            text += (f" The full-column E(B-V) of {column_ebv_text(col)} (Schlafly and Finkbeiner 2011) is an upper "
                     "bound, since it counts the dust beyond the star too: through the Gaia DR3 law it gives "
                     f"{law[0]}, {law[1]} and {law[2]}, and {_column_trio(col, star, planet)}{_height(data)}.")
        return text + (f" {_capital(gsp)}." if gsp else "")
    text = (f"Reddening: not measured ({_not_there(data)}, and the IRSA full-column value could "
            f"not be fetched), so the parameters are {stars_module.UNREDDENED}, adopted.")
    return text + (f" {_capital(gsp)}." if gsp else "")


def reddening_rows(data: dict) -> list[dict]:
    """The README's table under the star paragraph, one row per estimate (``REDDENING_COLUMNS``): the 3D value
    (adopted), the unreddened star (E(B-V) = 0: adopted without a 3D value, else not adopted), the full column (the
    upper bound, its values at the adopted values' decimals and its status with the Mann-range clause) and GSP-Phot
    (not adopted: E(BP-RP) and Teff only, "not given" elsewhere), each where the dossier has it; "0" for the
    unreddened extinctions. The 3D value's status carries the reliable-range flag without the range's end (Task 17
    fix round 1, I1). Empty without the block."""
    state = reddening_state(data)
    if state is None:
        return []
    star, planet = data.get("star") or {}, data.get("planet") or {}
    e, unred = star["extinction"], star.get("unreddened") or {}
    na = "not given"

    def physical(block: dict, rp, insolation) -> dict:
        return {"teff": value_text(block.get("teff")) or na, "radius": value_text(block.get("radius")) or na,
                "mass": value_text(block.get("mass")) or na, "insolation": value_text(insolation) or na,
                "rp": value_text(rp) or na}
    rows = []
    if state == RED_ADOPTED:
        rows.append({"estimate": "Bayestar19 (Green et al. 2019)",
                     "ebv": f"{e['ebv']:.3f} ({e['ebv_p16']:.3f} to {e['ebv_p84']:.3f})",
                     **{k: _red(e.get(k)) or na for k in ("ebprp", "a_g", "a_ks")},
                     "bp_rp0": _f(e.get("bp_rp0"), 3) or na,
                     **physical(star, planet.get("rp_rearth"), planet.get("insolation_earth")),
                     "status": f"adopted{_range_short(e)}{_adopted_mann(e)}"})
    rows.append({"estimate": "Unreddened", **{k: "0" for k in ("ebv", "ebprp", "a_g", "a_ks")},
                 "bp_rp0": _f(unred.get("bp_rp0"), 3) or na,
                 **physical(unred, unred.get("rp_rearth"), unred.get("insolation_earth")),
                 "status": "the earlier assumption, not adopted" if state == RED_ADOPTED else "adopted"})
    col = _column_derived(data)
    if col is not None:
        fit = f", {column_fit(data)} with the 3D value" if column_fit(data) else ""     # Task 17: the check
        at = {"teff": _at_adopted(col.get("teff"), star.get("teff")),
              "radius": _at_adopted(col.get("radius"), star.get("radius")),
              "mass": _at_adopted(col.get("mass"), star.get("mass")),
              "insolation": _at_adopted(col.get("insolation_earth"), planet.get("insolation_earth")),
              "rp": _at_adopted(col.get("rp_rearth"), planet.get("rp_rearth"))}
        rows.append({"estimate": FULL_COLUMN, "ebv": column_ebv_text(col),
                     **{k: _red(col.get(k)) or na for k in ("ebprp", "a_g", "a_ks")},
                     "bp_rp0": _f(col.get("bp_rp0"), 3) or na, **{k: v or na for k, v in at.items()},
                     "status": f"upper bound{fit}{_mann_flag(col)}"})
    red = _num(star.get("ebpminrp_gspphot"))
    teff = _num((star.get("gaia_cross_check") or {}).get("teff_gspphot"))
    if _gsp_row(data):
        rows.append({"estimate": "GSP-Phot (Gaia DR3)", "ebv": na, "ebprp": _f(red, 2) or na, "a_g": na, "a_ks": na,
                     "bp_rp0": na, "teff": f"{teff:,.0f}" if teff is not None else na, "radius": na, "mass": na,
                     "insolation": na, "rp": na, "status": "not adopted"})
    return rows


def reddening_key(data: dict) -> str | None:
    """The caveat line under the README's table: each estimate with its one-clause reason, and the law; the
    unreddened assumption stated once, as the round 3 final review's C1(ii) words it (M2). With the map's own value
    (Task 17 fix round 1, ruling 3) the Bayestar19 sentence carries the reliable-range flag with the range's end,
    ``KEY_SCALE`` and the sample count, and the full-column sentence ``KEY_COLUMN_SCALE``. With a current cross-check it ends
    on ``KEY_TIC_STAR`` (fix wave round 4, polish a). None without the
    block."""
    state = reddening_state(data)
    if state is None:
        return None
    parts = []
    if state == RED_ADOPTED:
        # Task 17 (spec 12.5): the unreddened star is the earlier assumption, the full column the 3D value's check
        e = data["star"]["extinction"]
        mann = (" The dereddened parameters are " + stars_module.MANN_OUTSIDE + "." if
                e.get("outside_mann_range") is True else "")
        side = stars_module.range_flag(e)       # fix round 1 (I1): the flag with the range's end
        flag = f", {side}" if side else ""
        if _map_value(e):                       # ruling 3 (I2, M8): the map's unit, its scale and the samples
            n = e.get("n_samples")
            samples = (f"the median of {n} samples" if isinstance(n, int) and not isinstance(n, bool)
                       else "the samples' median")
            parts.append("Bayestar19: a dust-map sightline average at the Gaia distance, "
                         f"adopted{flag}; {KEY_SCALE.format(samples=samples)}.{mann}")
        else:
            parts.append("Bayestar19: a dust-map sightline average at the Gaia distance with its 16th to 84th "
                         f"percentile range, adopted{flag}.{mann}")
        parts.append("Unreddened (E(B-V) = 0): the earlier assumption, not adopted.")
    else:
        why = _not_there(data) + ("" if state == RED_BRACKET else ", and the full column could not be fetched")
        parts.append(f"Unreddened (E(B-V) = 0), adopted while no 3D value at the star's distance is there ({why}).")
    col = _column_derived(data)
    if col is not None:
        fit = f", {column_fit(data)} with the 3D value" if column_fit(data) else ""
        route = (", on the adopted route (the Mann relations with Ks dereddened by A_Ks), "
                 f"{stars_module.MANN_OUTSIDE}" if col.get("outside_mann_range") is True else "")
        scale = f", {KEY_COLUMN_SCALE}" if fit and _map_value(data["star"]["extinction"]) else ""     # ruling 3
        parts.append("Full column: the IRSA DUST value at the star, the whole line of sight with the dust beyond the "
                     f"star, so an upper bound{fit}{route}{scale}.")      # fix round 2, N1: the route first
    if _gsp_row(data):
        parts.append("GSP-Phot: Gaia's own fit, biased for cool dwarfs (Andrae et al. 2023), not adopted.")
    if state == RED_ADOPTED or _column_derived(data) is not None:
        parts.append(GAIA_LAW_WORDS)
    if cc.is_current(data):                     # fix wave round 4, polish (a)
        parts.append(KEY_TIC_STAR)
    return " ".join(parts)


def reddening_text(data: dict, tex: bool = False) -> str | None:
    """The GSP-Phot reddening with its caveat (spec 11.6; Task 13 fix round 1, I-1): "GSP-Phot gives E(BP-RP) E, a
    value biased for cool dwarfs (Andrae et al. 2023); ..." and what the parameters assume. On the Mann route the
    radius comes from the absolute Ks magnitude, which barely depends on reddening, so the clause says the temperature
    assumes no reddening (E(B-V) = 0); on the colour-table route the radius itself assumes it, "and an E(BP-RP) of 0.2
    would raise (lower) it to X Rsun". None without the value or a characterised star. With ``tex`` a ``Raw`` citing
    ``andrae2023``.

    With a 3D extinction adopted (spec 12.3, ``reddening_state`` "adopted") the clause is ``REDDENING_CLAUSE``
    instead, capitalised, citing ``green2019``, with its E(B-V) and 16th to 84th percentile range (``EBV_RANGE``),
    then for a star outside the map's reliable range the side's words (``stars.range_side``), then one parenthesis
    with the range's end and, when the full column was fetched (``column_fit``), ``COLUMN_NOTE`` citing
    ``schlafly2011`` as an upper bound: "at 303 pc, nearer than the map's reliable range (from 406 pc; full column
    0.674 \\citep{schlafly2011}, an upper bound)"; then ``MANN_ADOPTED`` when the dereddened star leaves the Mann
    relations' range (Task 17, spec 12.5; fix round 1, ruling 2). In the bracket state the note's planet sentence carries
    ``rp_bracket_text`` and no GSP-Phot sentence: None (Task 16 Step 13, cut 1; the README's paragraph and table keep
    GSP-Phot, as not adopted). With neither value, or for a dossier before version 6, the GSP-Phot sentence stands."""
    star = data.get("star") or {}
    red = _num(star.get("ebpminrp_gspphot"))
    state = reddening_state(data)
    if state == RED_ADOPTED:
        e = star["extinction"]
        green = Raw(r" \citep{green2019}") if tex else " (Green et al. 2019)"
        # fix round 1 (I3, ruling 2): the range form at three decimals, never a zero error
        ebv = EBV_RANGE.format(ebv=float(e["ebv"]), p16=float(e["ebv_p16"]), p84=float(e["ebv_p84"]))
        head, rest = REDDENING_CLAUSE.split("{green}")
        before, after = head.split("{ebv}")
        parts = [_capital(before), ebv, after, green, rest.format(d=f"{float(e['distance_pc']):.0f}")]
        side = stars_module.range_side(e)
        end = (f"{stars_module.RANGE_END[side[0]]} {side[1]:,} pc" if side is not None and side[1] is not None
               else None)
        if side is not None:                      # C1: the side of the map's reliable range, its end in the parenthesis
            parts.append(f", {stars_module.RANGE_WORDS[side[0]]}")
        column = ([COLUMN_NOTE.format(col=f"{_num(e['column']['ebv']):.3f}"),
                   Raw(r" \citep{schlafly2011}") if tex else " (Schlafly and Finkbeiner 2011)", ", an upper bound"]
                  if column_fit(data) is not None else [])      # Task 17: the full column as the check, with its source
        if end or column:
            parts += [" (", *([end] if end else []), *(["; "] if end and column else []), *column, ")"]
        if e.get("outside_mann_range") is True:
            parts.append(MANN_ADOPTED)
        return _joined(tex, *parts)
    if state == RED_BRACKET or red is None or not star_characterized(data):
        return None
    cite = Raw(r" \citep{andrae2023}") if tex else " (Andrae et al. 2023)"
    alt, radius = (_num((star.get(k) or {}).get("median")) for k in ("alt_radius_e02", "radius"))
    none = "no reddening (E(B-V) = 0)"          # the round 3 final review, C1(ii): the unreddened star's footing
    if star.get("route") == "mann":
        ks = Raw("$K_s$") if tex else "Ks"
        rest = [f"; the temperature assumes {none}, and the ", ks, " radius barely depends on it"]
    elif alt is not None and radius is not None and alt != radius:
        unit = Raw(r" $R_\odot$") if tex else " Rsun"
        rest = [(f"; the radius assumes {none}, and an E(BP-RP) of 0.2 would "
                 f"{'raise' if alt > radius else 'lower'} it to {alt:.3f}"), unit]
    else:
        rest = [f"; the parameters assume {none}"]
    return _joined(tex, f"GSP-Phot gives E(BP-RP) {red:.2f}, {REDDENING_BIAS}", cite, *rest)


def flame_text(data: dict) -> str | None:
    """The README's systematic on the stellar radius (spec 11.6): "The Gaia FLAME radius of F Rsun differs from the
    adopted radius by P percent; read it as a systematic on the stellar radius, and so on the planet radius."; None
    without the FLAME radius or the adopted one."""
    star = data.get("star") or {}
    flame = _num((star.get("gaia_cross_check") or {}).get("radius_flame"))
    radius = _num((star.get("radius") or {}).get("median"))
    if flame is None or not radius:
        return None
    pct = abs(flame - radius) / radius * 100
    return (f"The Gaia FLAME radius of {flame:.3f} Rsun differs from the adopted radius by {pct:.0f} percent; read it "
            "as a systematic on the stellar radius, and so on the planet radius.")


def hz_text(data: dict) -> str | None:
    """The README's habitable-zone lines (spec 11.6; the note leaves them out): where the orbit lies against the
    conservative edges of Kopparapu et al. (2013), and the sub-Neptune caveat at the planet's radius; None without
    the edges."""
    planet = data.get("planet") or {}
    where = _hz_where(planet)
    if where is None or not star_characterized(data):
        return None
    text = (f"Habitable zone: the orbit lies {where} of Kopparapu et al. (2013), whose conservative edges for this "
            f"star sit at {_f(planet.get('hz_inner_au'), 3)} and {_f(planet.get('hz_outer_au'), 3)} AU.")
    rp = _num((planet.get("rp_rearth") or {}).get("median")) if isinstance(planet.get("rp_rearth"), dict) else None
    if rp is not None:
        text += (f" A planet of {rp:.1f} Earth radii is more likely a sub-Neptune with a gaseous envelope than a rocky "
                 "world (Rogers 2015; Fulton et al. 2017).")
    return text


def followup_text(data: dict, after_bjd: float) -> str | None:
    """One clause for observers (spec 11.6): "A transit in <year> is predicted to within W minutes (1 sigma): a D ppm
    transit on a G <g> star[, within reach of a 1 to 2 m telescope at a few hundred ppm per 10 minutes]." (the depth
    is the transit's, not the precision it needs; round-2 final review, minor 3) from the first transit after
    ``after_bjd`` (``pack_rows.next_transits``) and the depth at two significant figures; the telescope class only
    for a depth of at least ``FOLLOWUP_REACH_DEPTH_PPM`` on a star of G at most ``FOLLOWUP_REACH_GMAG``. None
    without a timing fit."""
    nxt = next_transits(data, after_bjd, n=1)
    depth, gmag = _num((data.get("ephemeris") or {}).get("depth_ppm")), _num((data.get("target") or {}).get("gmag"))
    if not nxt or depth is None:
        return None
    rounded = float(f"{depth:.2g}")
    star = f" on a G {gmag:.1f} star" if gmag is not None else ""
    reach = (FOLLOWUP_REACH if depth >= FOLLOWUP_REACH_DEPTH_PPM and gmag is not None and gmag <= FOLLOWUP_REACH_GMAG
             else "")
    return (f"A transit in {nxt[0]['date'][-4:]} is predicted to within {nxt[0]['window_min']:.0f} minutes (1 sigma): "
            f"a {rounded:,.0f} ppm transit{star}{reach}.")


def _hz_where(planet: dict) -> str | None:
    if _num(planet.get("hz_inner_au")) is None:
        return None
    if planet.get("in_habitable_zone"):
        return "inside the conservative habitable zone"
    if planet.get("in_optimistic_habitable_zone"):
        return "inside the optimistic habitable zone but not the conservative one"
    return "outside the habitable zone"


def aperture_sentence(data: dict, tex: bool = False) -> str | None:
    """Rung 3's aperture ratio on the fold against CROWDSAP as a sentence (``pack_text.aperture_clause``, which the
    dossier page's rung 3 line prints too): "The SAP depth is R ± e of the PSF depth against a CROWDSAP (this star's
    light fraction in the aperture) of C, Z sigma short of it."; None without the block's ratio and error."""
    clause = aperture_clause(data.get("aperture"), tex)
    if clause is None:
        return None
    sentence = f"{clause[:1].upper()}{clause[1:]}."            # the clause is already escaped with ``tex``
    return Raw(sentence) if tex else sentence


def leak_sentence(data: dict, tex: bool = False) -> str | None:
    """Rung 5's measured dip in the neighbour's optimal aperture beside the dip this star's own light predicts there
    (the ``predicted_leak`` block, spec 11.5): "The target's light predicts P ppm in KIC N's optimal aperture against
    D ± e ppm measured."; None without a prediction (no kept pixel file, no PSF width), when the texts keep the
    measured dip alone. The share and the method are the README's (``leak_method_text``; addendum cut 5)."""
    block = data.get("predicted_leak")
    if not isinstance(block, dict):
        return None
    pred, share, measured, err = (_num(block.get(k)) for k in ("predicted_ppm", "share", "measured_ppm",
                                                                "measured_err_ppm"))
    if None in (pred, share, measured):
        return None
    kic = block.get("kic")
    whose = f"KIC {kic}'s" if kic else "the neighbour's"
    digits = pm_digits(err) if err is not None and err > 0 else 0
    dip = pm_text(measured, err, err, nd=0, comma=True, tex=tex)
    return _joined(tex, f"The target's light predicts {pred:,.{digits}f} ppm in {whose} optimal aperture against ",
                   dip, " ppm measured.")


LEAK_FLOOR = "the Gaussian has no PRF wings, so the prediction is a floor"


def leak_method_text(data: dict) -> str | None:
    """The README's method of the predicted leak (Task 12 review M-1; the note's until addendum cut 5): "Predicted
    leak: this star's light is S percent of the light in KIC N's optimal aperture, from <dossier.LEAK_MODEL>, the
    mean over the pixel files the pixel check used; the Gaussian has no PRF wings, so the prediction is a floor.";
    None without a prediction."""
    block = data.get("predicted_leak")
    share = _num(block.get("share")) if isinstance(block, dict) else None
    if share is None or _num(block.get("predicted_ppm")) is None:
        return None
    pct = share * 100
    kic = block.get("kic")
    return (f"Predicted leak: this star's light is {_pct_text(pct)} percent of the light in "
            f"{f'KIC {kic}' if kic else 'the neighbour'}'s optimal aperture, from "
            f"{block.get('model') or ds.LEAK_MODEL}, the mean over the pixel files the pixel check used; {LEAK_FLOOR}.")


def _pct_text(pct: float) -> str:
    return f"{pct:.1f}" if pct >= 0.1 else f"{pct:.2g}"


def dilution_sentence(data: dict, tex: bool = False) -> str | None:
    """The note's dilution sentences (spec 11.5): for a KBonus target the photometry (``dossier.PSF_PHOTOMETRY``), so
    never None for one; the aperture ratio against CROWDSAP (``aperture_sentence``); CROWDSAP (when the ratio
    sentence has not given it) and PSFFRAC; and the
    neighbour's dip beside the dip this star predicts there (``leak_sentence``), or without a prediction the measured
    dip with its error alone. None without any of them."""
    rows = {r["key"]: r for r in dilution_rows(data)}
    words = {"crowdsap": "a target light fraction in the aperture (CROWDSAP)",
             "psffrac": "a PSF flux fraction inside the aperture (PSFFRAC)"}
    sentences: list[list] = []
    ratio = aperture_clause(data.get("aperture"), tex, short=tex)
    kbonus = (data.get("target") or {}).get("source") in (None, "kbonus_bkg")
    # the note names PSFFRAC in the ratio clause of the KBonus sentence (Task 13), only there (review M-8)
    psf = rows.get("psffrac") if tex and ratio and kbonus else None
    if kbonus:
        # the ratio is one clause of the photometry sentence in the note (addendum cut 7)
        sentences.append([f"The depth is {ds.PSF_PHOTOMETRY}", *(["; ", ratio] if ratio else []),
                          # the note drops the keyword (Task 15 fix round 1, cut B7); the README defines it
                          f", with a PSF flux fraction inside the aperture of {psf['text']}" if psf else "", "."])
    elif ratio:
        sentences.append([aperture_sentence(data, tex)])
    # CROWDSAP is printed once (Task 12 review M-5): in the ratio clause when that carries it
    told = ratio is not None and "CROWDSAP" in ratio
    named = [f"{words[k]} of {rows[k]['text']}" for k in ("crowdsap", "psffrac") if k in rows
             and not (k == "crowdsap" and told) and not (k == "psffrac" and psf)]
    parts = [f"The KBonus light curve gives this star {' and '.join(named)}"] if named else []
    predicted = leak_sentence(data, tex)
    leak = rows.get("leak")
    if leak and predicted is None:
        whose = f" (KIC {leak['kic']})" if leak["kic"] else ""
        parts += [", and in" if parts else "In",
                  f" the optimal-aperture flux of the neighbour{whose} the dip at this ephemeris is ",
                  pm_text(leak["value"], leak["minus"], leak["plus"], nd=0, comma=True, tex=tex),
                  " ppm"]
    if parts:
        sentences.append([*parts, "."])
    if predicted:
        sentences.append([predicted])
    if not sentences:
        return None
    flat: list = []
    for i, sentence in enumerate(sentences):
        flat += ([" "] if i else []) + sentence
    return _joined(tex, *flat)


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


def software_versions() -> dict[str, str | None]:
    """{package: installed version, or None}, for ``SOFTWARE_PACKAGES``: the versions the software line prints and
    ``provenance/versions.json`` records, so the two print one set."""
    from importlib import metadata as md
    out: dict[str, str | None] = {}
    for name in SOFTWARE_PACKAGES:
        try:
            out[name] = md.version(name)
        except md.PackageNotFoundError:
            out[name] = None
    return out


def software_line(data: dict, versions: dict | None = None) -> str:
    """The note's software statement: "SkySift <v> (search version <s>, commit <c>), numpy <v>, scipy <v>, astropy
    <v>, matplotlib <v>[, triceratops]", SkySift's version from the package metadata, the search version and the
    commit from the dossier, each package's from ``versions`` (default ``software_versions()``; a package without a
    version is named alone); triceratops only when a cross-check is stored."""
    versions = software_versions() if versions is None else versions
    tools = ", ".join(f"{p} {versions[p]}" if versions.get(p) else p for p in SOFTWARE_PACKAGES)
    if isinstance(data.get("cross_check"), dict):      # with the stored tool version (Task 13 review M-9)
        version = data["cross_check"].get("tool_version")
        tools += f", {CROSS_CHECK_SOFTWARE}" + (f" {version}" if version else "")
    return (f"SkySift {skysift_version()} (search version {search_version(data) or 'unknown'}, commit "
            f"{software_commit(data)}), {tools}")


def code_doi(citation: dict | None) -> str:
    """The code's DOI as the note's data sentence gives it (Task 13 fix round 1, cut 14): "doi:<doi>" from
    ``CITATION.cff``, else ``SOFTWARE_DOI_PLACEHOLDER`` with "(to be minted)"."""
    doi = str((citation or {}).get("doi") or "").strip()
    return f"doi:{doi}" if doi else f"doi:{SOFTWARE_DOI_PLACEHOLDER}{CODE_DOI_PENDING}"


def code_sentence(citation: dict | None) -> str:
    """"the SkySift code at the stated commit is archived at doi:<doi>" with the DOI of ``CITATION.cff``
    (``load_citation``), or with ``SOFTWARE_DOI_PLACEHOLDER`` and "(to be minted)" without one."""
    doi = str((citation or {}).get("doi") or "").strip()
    return CODE_SENTENCE.format(doi=doi) if doi else CODE_SENTENCE.format(doi=SOFTWARE_DOI_PLACEHOLDER) + (
        CODE_DOI_PENDING)


def fit_radius_ratio(data: dict) -> float | None:
    """The radius ratio the planet radius was derived from: in version 5 the quoted centre
    (``dossier.quoted_radius_ratio``, the one k every text prints), before that rung 2's uniform-disc k when its fit
    is ok."""
    shape = data.get("shape")
    if isinstance(shape, dict) and shape.get("fits"):
        return ds.quoted_radius_ratio(shape)
    sh = _metrics(data, "rung2_shape")
    return _num(sh.get("fit_k")) if sh.get("fit_ok") is True else None


def numbers(data: dict, tex: bool = False, after_bjd: float | None = None) -> dict:
    """Everything the four texts print, as text (``tex``: LaTeX-ready, values with errors as ``Raw`` math). The
    ephemeris keys are ``quantities``; ``next_transits`` are the three after ``after_bjd`` (default now)."""
    target, sig = data.get("target") or {}, data.get("signal") or {}
    star, planet, prov = data.get("star") or {}, data.get("planet") or {}, data.get("provenance") or {}
    eph, plots = data.get("ephemeris") or {}, data.get("plots") or {}
    timing = data.get("timing") if isinstance(data.get("timing"), dict) else {}
    timed = timing.get("ok") is True
    window = ((sig.get("extra") or {}).get("search_window") or {})
    lo, hi = _num(window.get("min_period_days")), _num(window.get("max_period_days"))
    fap_text, fpp_text = probability_sentences(data, short=tex)      # the note's short form (addendum cut 2)
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
        "timed": timed, "methods_text": errors_pointer(data), "errors_methods": errors_methods_text(data),
        "rms_min": _f(timing.get("rms_min"), 1) if timed else None,
        "n_used": timing.get("n_used") if timed else None, "n_measured": timing.get("n_measured") if timed else None,
        "next_transits": next_transits(data, julian_date(datetime.now(UTC)) if after_bjd is None else after_bjd),
        "sde": _f(eph.get("sde"), 1), "snr": _f(eph.get("snr"), 1), "n_events": eph.get("n_events"),
        "window": f"{lo:g} to {hi:g} days" if lo is not None and hi is not None else None,
        "vetting": _vetting_sentences(data, tex), "pixel_text": pixel_sentence(data, tex),
        "offset_text": offset_sentence(data),
        # the study's own path here; the README names the pack's copy when the pack holds it (render_readme)
        "hand_built_text": hand_built_sentence(data), "leak_method": leak_method_text(data),
        "beb_radius_text": BEB_RADIUS if _num(((data.get("fpp") or {}).get("inputs") or {}).get(
            "exclusion_radius_arcsec")) is not None else None,
        "pixel_plain": pixel_sentence(data),
        "odd_text": pair[0] if pair else None, "even_text": pair[1] if pair else None,
        "parity_sigma_text": _f((data.get("parity") or {}).get("difference_sigma"), 1) if pair else None,
        "stacked_parity_text": stacked_parity_text(data),
        "secondary_text": secondary_sentence(data, tex), "albedo_text": albedo_text(data),
        "secondary_plain": secondary_sentence(data), "trend_plain": trend_sentence(data),
        "shape_fit": shape_fit_text(data), "fap_method": fap_method_text(data),
        "secondary_pm": pm_text(sec.get("depth_ppm"), sec.get("err_ppm"), sec.get("err_ppm"), nd=0, comma=True),
        "secondary_limit": _f(sec.get("limit_3sigma_ppm"), 0, True),
        "shape_text": shape_sentence(data, tex),
        "crowdsap": (dilution.get("crowdsap") or {}).get("text"),
        "psffrac": (dilution.get("psffrac") or {}).get("text"),
        "leak_text": leak["text"] if leak else None, "dilution_text": dilution_sentence(data, tex),
        "dilution_rows": list(dilution.values()),
        "software_line": software_line(data), "facilities": FACILITIES,
        "gaia_flags_text": gaia_flags_text(data), "flame_text": flame_text(data), "hz_text": hz_text(data),
        "reddening_text": reddening_text(data, tex), "reddening_sentence": reddening_text(data),
        "reddening_plain": reddening_plain(data), "reddening_rows": reddening_rows(data),
        "reddening_columns": REDDENING_COLUMNS, "reddening_key": reddening_key(data),
        "rp_bracket": rp_bracket_text(data, tex),
        "star_method_text": star_method_text(data), "note_forms": note_forms(data),
        "followup_text": followup_text(data, julian_date(datetime.now(UTC)) if after_bjd is None else after_bjd),
        "references_plain": readme_references(data), "in_house_method": in_house_method_text(data),
        "pixel_panels": bool(data.get("pixels")),
        "commit": software_commit(data),
        "star_ok": star_ok, "spectral_type": star.get("spectral_type"),
        "teff": value_text(star.get("teff"), tex), "radius": value_text(star.get("radius"), tex),
        "mass": value_text(star.get("mass"), tex), "distance": value_text(star.get("distance_pc"), tex),
        "fit_k": _f(fit_radius_ratio(data), 4),
        "rp": value_text(rp, tex), "a": value_text(a, tex), "teq": value_text(teq, tex),
        "insolation": value_text(planet.get("insolation_earth"), tex),
        "rp_text": value_text(rp), "a_text": value_text(a), "teq_text": value_text(teq), "host_text": host or "no "
        "stellar parameters",
        "fap_text": fap_text, "fpp_text": fpp_text,
        "caveats": [{**c, "title": _caveat_title(c, star), "text": _caveat_text(c, data)} for c in caveats],
        "in_house_text": in_house_text(data), "in_house_capital": _capital(in_house_text(data)),
        "both_tools": both_tools(data, tex),
        "cross_check_sentence": cross_check_sentence(data, tex),
        # the plain texts print the headline in fpp_text first, so their cross-check sentence refers back to it
        "cross_check_after": cc.comparison_sentence(data, after_headline=True),
        "leakage_basis": cc.leakage_basis(data), "crowdsap_text": crowdsap_text(data),
        "cross_check_block": cc.verdict(data.get("cross_check"))[1],
        "cross_check_flag": cc.owner_flag(data),
        "tess_text": tess_text(data, tex), "tess_row": tess_module.readme_row(data.get("tess")),
        "tess_exofop": tess_module.exofop_line(data.get("tess")), "tess_methods": tess_methods_text(data),
        "tess_pointer": bool(tess_module.note_cites(data.get("tess"))),   # the README labels its paragraph by it (M-2)
        "abstract": abstract_text(data),
        "prior_text": prior_claim_sentence(data, tex), "prior_plain": prior_claim_sentence(data),
        "comparison_text": comparison_text(data, tex), "comparison_plain": comparison_text(data),
        "priority_text": priority_sentence(data),
        "caveat_text": " ".join(f"{_caveat_title(c, star, flag=False).rstrip('.')}." for c in caveats
                                if c.get("key") not in NOTE_STATED_CAVEATS
                                and c.get("title") != ds.CANDIDATE_CAVEAT) or "No caveat is recorded.",
        "window_h": window_hours(data), "bin_minutes": bin_minutes(data),
        "model_words": "transit fit" if plots.get("model_kind") == "fit" else "detection's box model",
        "fold_words": fold_words(data),
        # the signal section and the README's depth row say which ephemeris folded (spec 11.3); a dossier before
        # version 5 does not say, and neither do they
        "fold_clause": (f"; the depth and shape below use the {fold_words(data)}"
                        if eph.get("fold_ephemeris") else ""),
        "depth_source_text": (f" ({eph['depth_source']}, on the {fold_words(data)})"
                              if eph.get("fold_ephemeris") and eph.get("depth_source") else ""),
        "param_rows": param_rows(data), "commands": prov.get("commands") or [],
    }


# caveats the note's body already states, so its "What is not yet known" leaves their titles out (Task 13)
NOTE_STATED_CAVEATS = ("partial_box",)


def _caveat_title(caveat: dict, star: dict, flag: bool = True) -> str:
    """A caveat's title as the texts print it: the stellar-radius caveat follows the star block it sits beside
    (``dossier.no_spectrum_title``: the reddening named while Gaia gives no E(BP-RP), and, unless ``flag`` is False
    as in the note, the reddening the map does not constrain outside its reliable range), every other its stored
    one."""
    if caveat.get("key") == "no_spectrum" or str(caveat.get("title") or "").startswith(ds.NO_SPECTRUM):
        return ds.no_spectrum_title(star, flag)
    return caveat["title"]


def _caveat_text(caveat: dict, data: dict) -> str:
    """A caveat's text as the README prints it: the stored one, and for the stellar-radius caveat of a star whose
    adopted value lies outside the map's reliable range the range its two ends span (``bracket_span``; fix wave
    round 4, I2)."""
    text = caveat.get("text") or ""
    span = bracket_span(data) if caveat.get("key") == "no_spectrum" else None
    return f"{text} {span}" if span else text


def bracket_span(data: dict) -> str | None:
    """"On the adopted route the reddening bracket spans Teff 3,653 to 4,657 K, radius 0.499 to 0.540 Rsun and planet
    radius 2.5 to 2.7 Earth radii." (fix wave round 4, I2): from the unreddened end to the full-column end the derived star
    stores, each at the adopted value's decimals (``_mid_at``), the smaller first; one value where the two print
    alike; ", the full-column end beyond the Mann relations' range" when the full-column star's stored
    ``outside_mann_range`` is true (the scoped re-review, N3). None outside the adopted state, where the map
    constrains the dust at the star's distance (``stars.range_side`` None), or without the full column's derived
    values."""
    if reddening_state(data) != RED_ADOPTED:
        return None
    star, planet = data.get("star") or {}, data.get("planet") or {}
    e, unred, col = star["extinction"], star.get("unreddened") or {}, _column_derived(data)
    if stars_module.range_side(e) is None or col is None:
        return None
    parts = []
    for name, key, unit, holder in (("Teff", "teff", " K", star), ("radius", "radius", " Rsun", star),
                                    ("planet radius", "rp_rearth", " Earth radii", planet)):
        ends = [_mid_at(unred.get(key), holder.get(key)), _mid_at(col.get(key), holder.get(key))]
        if None in ends:
            continue
        a, b = sorted(ends, key=lambda s: float(s.replace(",", "")))
        parts.append(f"{name} {a}{unit}" if a == b else f"{name} {a} to {b}{unit}")
    if not parts:
        return None
    joined = ", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + parts[-1]
    # the scoped re-review (N3): the stored flag of the full-column star, as the key's "so indicative" clause reads it
    mann = ", the full-column end beyond the Mann relations' range" if col.get("outside_mann_range") is True else ""
    return f"On the adopted route the reddening bracket spans {joined}{mann}."


def window_hours(data: dict) -> str | None:
    """The fold window's half-width in hours as the caption prints it: the stored ``plots.window_h``, else, for a
    dossier without its plots (the golden block keeps none), the same rule from the signal: the smaller of
    ``dossier.FOLD_WINDOW_DURATIONS`` search durations and half the period."""
    stored = _num((data.get("plots") or {}).get("window_h"))
    if stored is None:
        sig = data.get("signal") or {}
        dur, period = _num(sig.get("duration")), _num(sig.get("period"))
        stored = min(ds.FOLD_WINDOW_DURATIONS * dur, 0.5 * period) * 24.0 if dur and period else None
    return _f(stored, 1)


def bin_minutes(data: dict) -> str | None:
    """The fold's bin width in minutes as the caption prints it: the stored ``plots.bin_minutes`` (an eighth of the
    search duration), else, for a dossier made before it, the same eighth of the signal's search duration."""
    stored = _num((data.get("plots") or {}).get("bin_minutes"))
    if stored is None:
        dur = _num((data.get("signal") or {}).get("duration"))
        stored = dur * 1440.0 / 8 if dur else None
    return None if stored is None else f"{stored:.1f}".removesuffix(".0")


def abstract_text(data: dict, others: list[dict] | None = None) -> str:
    """The note's abstract (RNAAS requires one, at most 150 words): the signal, where the pixels put it, the planet
    when the star is characterised, both probabilities and the candidate status, each from the stored numbers. With
    ``others`` (the target's further candidates) it is the two-candidate abstract of ``pack_two.abstract``, at most
    130 words."""
    if others:
        from skysift import pack_two
        return pack_two.abstract([data, *others])
    target, eph = data.get("target") or {}, data.get("ephemeris") or {}
    name = target.get("name") or target.get("target_id")
    claims = prior_claims(data)
    # the claim's date and DOI are printed once, in the body beside its citation (Task 8 fix round 3)
    opening = (f"This note vets the transit candidate {claims[0]['author']} reported first" if claims
               else "This note reports a transit candidate")
    # round 2 (spec 11.2) puts both false-positive estimates in the abstract, so the other sentences are kept short
    # to stay inside the RNAAS 150 words; the body carries the radius source, the orbit and the light curve's name
    parts = [f"{opening} on {name}, a star Kepler recorded only in other targets' pixels.",
             (f"Its KBonus light curve shows a {_f(eph.get('duration_h'), 2)} hour, "
              f"{_f(eph.get('depth_ppm'), 0, True)} ppm dip every {quantities(data)['period']} days over "
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
    rp = (data.get("planet") or {}).get("rp_rearth")
    if isinstance(rp, dict) and _num(rp.get("median")) is not None:
        parts.append(f"The planet would be about {_num(rp['median']):.1f} Earth radii.")
    # the false-alarm rate per star with what it means across the sweep (round-2 final review, I-1), never the
    # per-star number alone; the SDE and S/N stay in the body
    # spec 11.2: the TRICERATOPS headline first when a current cross-check exists, the in-house estimate after it
    head = cc.headline(data, short=True)          # the note's short class clause (addendum cut 8)
    fpp_part = f"{head}; {in_house_text(data)}" if head else in_house_text(data)
    parts.append(f"{_capital(fap_rate_clause(data))}; {fpp_part}.")
    parts.append("It remains a candidate pending follow-up.")
    return " ".join(parts)


def _archive_two(datas: list[dict], doi: str, tic: str | None) -> str:
    """The archive paragraph for two candidates: both ephemerides, each candidate's false-alarm and false-positive
    sentences, the prior claims (each told with its candidate) and the one sentence about what is frozen where."""
    ns = [numbers(d) for d in datas]
    n = ns[0]
    names = [_candidate_name(d) for d in datas]
    where = f" (Gaia DR3 {n['gaia_id']}, RA {n['ra']} deg, Dec {n['dec']} deg)" if n["gaia_id"] else ""
    exofop = f"under TIC {tic}" if tic else f"on the {n['name']} page"
    versions = list(dict.fromkeys(str(x["search_version"]) for x in ns))
    search = f"search version{'s' if len(versions) > 1 else ''} {' and '.join(versions)}, commit {n['commit']}"
    claimed = [(nm, c[0], x) for nm, d, x in zip(names, datas, ns, strict=True) if (c := prior_claims(d))]
    if claimed:
        reported = list(dict.fromkeys((c["author"], c["date"], c["doi"]) for _nm, c, _x in claimed))
        if len(reported) == 1 and len(claimed) == len(datas):
            author, day, doi_claim = reported[0]
            told = f"Both were first reported by {author} on {date_words(day)} (doi:{doi_claim})."
        else:
            told = " ".join(f"{_capital(nm)} was first reported by {c['author']} on {date_words(c['date'])} "
                            f"(doi:{c['doi']})." for nm, c, _x in claimed)
        notes = list(dict.fromkeys(c["development_note"] for _nm, c, _x in claimed if c.get("development_note")))
        note = f"; {'; '.join(notes)}" if notes else ""
        compared = "".join(f" For {nm}: {x['comparison_plain'][:1].lower()}{x['comparison_plain'][1:]}"
                           for nm, _c, x in claimed if x["comparison_plain"])
        opening = (f"I write about two transit candidates on {n['name']}{where}. {told} SkySift's vetting of them is "
                   f"now in an accepted refereed paper (doi:{PAPER_DOI_PLACEHOLDER}), since the archive takes "
                   "parameters from such a paper and not from the Research Note of the AAS that reported the vetting "
                   f"(doi:{RNAAS_DOI_PLACEHOLDER}). SkySift ({search}) searched the KBonus Kepler Background light "
                   f"curves{note}.{compared} ")
    else:
        opening = (f"I write about two transit candidates on {n['name']}{where}, now in an accepted refereed paper "
                   f"(doi:{PAPER_DOI_PLACEHOLDER}), since the archive takes parameters from such a paper and not "
                   f"from the Research Note of the AAS that first reported them (doi:{RNAAS_DOI_PLACEHOLDER}). "
                   f"SkySift ({search}) found them in the KBonus Kepler Background light curves. ")
    ephemerides = "; ".join(
        f"for {nm}, {x['ephemeris_text']}" + (f", which gives a planet radius of {x['rp_text']} Earth radii"
                                              if x["rp_text"] else "") for nm, x in zip(names, ns, strict=True))
    red = f" {n['reddening_plain']}" if n["reddening_plain"] else ""
    odds = " ".join(f"{_capital(nm)}: {x['fap_text']} {x['fpp_text']}"
                    + (f" {x['cross_check_after']}" if x["cross_check_after"] else "")
                    for nm, x in zip(names, ns, strict=True))
    return opening + (
            f"The ephemerides are {ephemerides}.{red} {odds} The light curves, ephemerides, transit times, vetting "
            f"metrics and figures of both are frozen on Zenodo (doi:{doi}) and uploaded to ExoFOP-TESS {exofop}. I "
            "would be glad to see them considered for the archive's tables, and I can send anything else you need.")


def archive_paragraph(data: dict, doi: str, tic: str | None, others: list[dict] | None = None) -> str:
    """The one paragraph for the NASA Exoplanet Archive help desk (step 4 and ``archive_email.txt``); with ``others``
    (the further candidates' JSONs) it names both candidates with both ephemerides."""
    if others:
        return _archive_two([data, *others], doi, tic)
    n = numbers(data)
    where = f" (Gaia DR3 {n['gaia_id']}, RA {n['ra']} deg, Dec {n['dec']} deg)" if n["gaia_id"] else ""
    rp = f", which gives a planet radius of {n['rp_text']} Earth radii" if n["rp_text"] else ""
    exofop = f"under TIC {tic}" if tic else f"on the {n['name']} page"
    cross = f" {n['cross_check_after']}" if n["cross_check_after"] else ""
    red = f" {n['reddening_plain']}" if n["reddening_plain"] else ""
    claims = prior_claims(data)
    if claims:
        c = claims[0]
        verb = RELATIONSHIP_VERBS.get(c.get("relationship") or "", "found it in its sweep of")
        note = f"; {c['development_note']}" if c.get("development_note") else ""
        opening = (f"I write about a transit candidate on {n['name']}{where}, first reported by {c['author']} on "
                   f"{date_words(c['date'])} (doi:{c['doi']}). SkySift's vetting of it is now in an accepted refereed "
                   f"paper (doi:{PAPER_DOI_PLACEHOLDER}), since the archive takes parameters from such a paper and "
                   "not from the Research Note of the AAS that reported the vetting "
                   f"(doi:{RNAAS_DOI_PLACEHOLDER}). SkySift (search version {n['search_version']}, commit "
                   f"{n['commit']}) {verb} the KBonus Kepler Background light curves{note}. "
                   + (f"{n['comparison_plain']} " if n["comparison_plain"] else ""))
    else:
        opening = (f"I write about a transit candidate on {n['name']}{where}, now in an accepted refereed paper "
                   f"(doi:{PAPER_DOI_PLACEHOLDER}), since the archive takes parameters from such a paper and not "
                   f"from the Research Note of the AAS that first reported it (doi:{RNAAS_DOI_PLACEHOLDER}). SkySift "
                   f"(search version {n['search_version']}, commit {n['commit']}) found it in the KBonus Kepler "
                   "Background light curves. ")
    return opening + (
            f"The ephemeris is {n['ephemeris_text']}{rp}.{red} {n['fap_text']} {n['fpp_text']}{cross} The light curve, "
            f"ephemeris, transit times, vetting metrics and figures are frozen on Zenodo (doi:{doi}) and uploaded to "
            f"ExoFOP-TESS {exofop}. I would be glad to see it considered for the archive's tables, and I can send "
            "anything else you need.")


# ---------------------------------------------------------------------------------------------------- the texts

def load_author(path: Path | None = None) -> dict | None:
    """``docs/author.json`` (``AUTHOR_FILE``) as {name, affiliation, email, orcid}, each a stripped string ("" when
    empty or missing); None without the file or when it does not read as a JSON object."""
    path = AUTHOR_FILE if path is None else Path(path)
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    return {k: str(raw.get(k) or "").strip() for k in AUTHOR_FIELDS}


def author_problems(author: dict | None) -> list[str]:
    """What keeps the author block from a final pack (spec 11.6): "no docs/author.json"; "docs/author.json has no
    <field>" for each empty field; "docs/author.json has the ORCID '<value>', not 0000-0000-0000-000X" for one that
    is not an ORCID iD. Empty when the block is complete."""
    if author is None:
        return ["no docs/author.json"]
    out = [f"docs/author.json has no {k}" for k in AUTHOR_FIELDS if not author.get(k)]
    orcid = author.get("orcid")
    if orcid and not _ORCID.fullmatch(orcid):
        out.append(f"docs/author.json has the ORCID {orcid!r}, not 0000-0000-0000-000X")
    return out


def final_problems(out_dir: Path) -> list[str]:
    """``f"{name} still says {placeholder!r}"`` for every placeholder of ``FINAL_PLACEHOLDERS`` that a public text
    of the pack (``FINAL_TEXTS``) still carries; empty when a ``--final`` pack may be uploaded."""
    out = []
    for name in FINAL_TEXTS:
        path = Path(out_dir) / name
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        out += [f"{name} still says {p!r}" for p in FINAL_PLACEHOLDERS if p in text]
    return out


def with_author(citation: dict | None) -> dict | None:
    """The citation with ``docs/author.json`` as its one author (Task 13 review M-5), so the note's authors and the
    Zenodo creators come from the same dict: the file's name, affiliation and ORCID, a field the file leaves empty
    taken from the citation's first author of the same name, and that author's family and given names kept for
    Zenodo's "Family, Given" form. The citation unchanged without the file."""
    author = load_author()
    if author is None:
        return citation
    first = next((a for a in (citation or {}).get("authors") or [] if isinstance(a, dict)), {})
    named = " ".join(str(first.get(k) or "") for k in ("given-names", "family-names")).strip() or first.get("name")
    same = bool(first) and (not author["name"] or named == author["name"])
    entry = {"name": author["name"] or (named if same else "") or "AUTHOR NAME TO FILL IN"}
    if same:
        entry.update({k: first[k] for k in ("given-names", "family-names") if first.get(k)})
    for key in ("orcid", "affiliation", "email"):
        value = author[key] or (str(first.get(key) or "") if same else "")
        if value:
            entry[key] = value
    return {**(citation or {}), "authors": [entry]}


def _authors(citation: dict | None) -> list[dict]:
    """The note's authors: the one of ``docs/author.json`` when the file exists (``with_author``, which fills an
    empty field from the citation's same author), else the citation's, each {name, orcid, affiliation, email} (an
    empty field stays empty, and the template prints its placeholder)."""
    if load_author() is not None:
        a = with_author(citation)["authors"][0]
        orcid = _ORCID.search(str(a.get("orcid") or ""))
        return [{"name": a["name"], "orcid": orcid.group(0) if orcid else None,
                 "affiliation": a.get("affiliation", ""), "email": a.get("email", "")}]
    out = []
    for a in (citation or {}).get("authors") or []:
        if not isinstance(a, dict):
            continue
        given, family = a.get("given-names"), a.get("family-names")
        name = " ".join(x for x in (given, family) if x) or a.get("name")
        orcid = re.search(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]", str(a.get("orcid") or ""))
        if name:
            out.append({"name": str(name), "orcid": orcid.group(0) if orcid else None, "affiliation": "",
                        "email": ""})
    return out or [{"name": "AUTHOR NAME TO FILL IN", "orcid": None, "affiliation": "", "email": ""}]


def doi_shown(doi: str | None) -> str:
    """The DOI as the plain texts print it: the placeholder without one, and a sandbox DOI (``10.5072/``, which never
    resolves) only with the words that it must be replaced."""
    if not doi:
        return DOI_PLACEHOLDER
    return f"{doi}, a {SANDBOX_DOI_NOTE}" if doi.startswith(SANDBOX_DOI_PREFIX) else doi


def render_note(data: dict, doi_placeholder: str = DOI_PLACEHOLDER, *, citation: dict | None = None,
                panels: bool = True, others: list[dict] | None = None,
                span: tuple[float, float] | None = None) -> str:
    """The Research Note draft: the AASTeX v7 frame in ``data/rnaas/aastex_template.tex`` and the body from
    ``note.tex.j2`` (title, abstract, sections, one figure, references), citing exactly the works the dossier used.
    A sandbox DOI is printed with the words that it must be replaced before submission. ``panels`` is whether the
    figure is matplotlib's panelled ``figure1`` (the caption names the panels) or the fold alone of ``figure1.svg``;
    the code DOI comes from ``citation`` (``code_doi``). With ``others`` (the target's further candidates, each
    one's stored JSON) the body is ``note_two.tex.j2``: both candidates, the bibliography the union of the works
    either one cites, and ``span`` (the light curve's first and last BKJD) is what the coincidence of their transits
    is counted over; a note without ``others`` is the single candidate's, as before."""
    if others:
        return _render_note_two([data, *others], doi_placeholder, citation, panels, span)
    refs = references(data)
    bib = sorted([(key, Raw(label), Raw(text)) for key, label, text in BIBLIOGRAPHY if refs.get(key)]
                 + prior_bib(data), key=lambda item: item[0])
    doi = doi_placeholder or DOI_PLACEHOLDER
    note = f", a {SANDBOX_DOI_NOTE}" if doi.startswith(SANDBOX_DOI_PREFIX) else ""
    body = _env(tex=True).get_template("note.tex.j2").render(
        n=numbers(data, tex=True), refs=refs, bib=bib, doi=doi, doi_note=note, authors=_authors(citation),
        code_doi=code_doi(citation), panels=panels)
    return AASTEX_FRAME.read_text(encoding="utf-8") + "\n" + body


def _render_note_two(datas: list[dict], doi_placeholder: str, citation: dict | None, panels: bool,
                     span: tuple[float, float] | None) -> str:
    """``render_note`` for a target with a second candidate: the frame, then ``note_two.tex.j2`` on the numbers of
    each candidate and the sentences of ``pack_two.shared``. The references are the union of the works each cites; a
    prior claim's record is listed once."""
    from skysift import pack_two
    each = [references(d) for d in datas]
    refs = {key: any(r[key] for r in each) for key in each[0]}
    items = [(key, Raw(label), Raw(text)) for key, label, text in BIBLIOGRAPHY if refs.get(key)]
    for d in datas:
        items += [item for item in prior_bib(d) if item[0] not in {key for key, _label, _text in items}]
    doi = doi_placeholder or DOI_PLACEHOLDER
    note = f", a {SANDBOX_DOI_NOTE}" if doi.startswith(SANDBOX_DOI_PREFIX) else ""
    cands = [numbers(d, tex=True) for d in datas]
    body = _env(tex=True).get_template("note_two.tex.j2").render(
        n=cands[0], cands=cands, s=pack_two.shared(datas, True, span, panels), refs=refs, bib=sorted(items),
        doi=doi, doi_note=note, authors=_authors(citation), code_doi=code_doi(citation))
    return AASTEX_FRAME.read_text(encoding="utf-8") + "\n" + body


EXOFOP_CONTENTS = (("lightcurve", "data/lightcurve.csv",
                    "the detrended light curve (time BKJD, relative flux and error in ppm, quarter)"),
                   ("ephemeris", "data/ephemeris.csv", "the ephemeris and parameters with their errors"),
                   ("dossier", "dossier.html", "the standalone dossier with every vetting number"),
                   ("fold", "figures/fold.{ext}", "the folded transit with the model"),
                   ("oddeven", "figures/oddeven.{ext}", "the odd and even transits"),
                   ("periodogram", "figures/periodogram.{ext}", "the periodogram"),
                   ("centroids", "figures/centroids.{ext}", "the per-season difference images and centroids"))

BEB_RADIUS = ("The background-binary term uses the pixel exclusion radius, max(3 sigma, 0.5 arcsec), as its "
              "confusion radius; it was 2.0 arcsec in model 2.")
FILE_NOTES = {
    "README.md": "these instructions",
    "note/note.tex": "the Research Note draft (AASTeX v7)",
    "note/figure1.svg": "the fold panel of the note's figure alone (the folded transit); the PDF and PNG carry "
                        "all panels",
    "note/figure1.pdf": "the note's figure as PDF, for pdflatex: (a) the fold and (b) odd and (c) even transits, "
                        "with (d) the per-season difference images when the pixel check stored them",
    "note/figure1.png": "the note's figure as PNG, the same panels",
    "data/lightcurve.csv": "the whitened light curve the search ran on: time BKJD, relative flux in ppm, error in "
                           "ppm, quarter",
    "data/ephemeris.csv": "period, mid-transit times, duration, depth, the star and planet parameters and the "
                          "dilution numbers, each with its errors and source",
    "data/ephemeris.json": "the same with the dossier's ephemeris, star and planet blocks",
    "data/shape.csv": "radius ratio, impact parameter, a/R*, T14 and the depths: the resample median with its "
                      "bootstrap errors, the full-data fit beside it, and why an error is missing",
    "data/stellar_provenance.csv": "each stellar parameter with its errors, method, inputs and reference",
    "note/figure1_data.csv": "the data behind the note's figure: phase in hours, binned flux, error and count, and "
                             "the model, in ppm",
    f"exofop/{EXOFOP_STELLAR_TEMPLATE}": "the stellar parameters in ExoFOP's bulk template (upload now)",
    "data/periodogram.json": "the periodogram envelope, its peaks and the alias and systematic periods",
    "data/rung_metrics.json": "every vetting rung's status, reason and metrics",
    HAND_BUILT_FILE: "the hand-built localisation study of the same pixel files that the README compares with "
                     "(season by season, its centroid's distance from the star)",
    "data/cross_check.json": "the TRICERATOPS cross-check: every run's false-positive probability with all "
                             "neighbours and with the neighbours the difference images exclude removed, with "
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


# the files a pack holds once for each candidate; the second candidate's carry ``_02`` before the extension
PER_CANDIDATE = ("data/lightcurve.csv", "data/ephemeris.csv", "data/ephemeris.json", "data/shape.csv",
                 "data/stellar_provenance.csv", "note/figure1_data.csv", "data/periodogram.json",
                 "data/rung_metrics.json", "data/cross_check.json", "data/events.csv", "data/transit_times.csv",
                 "dossier.html", "provenance/calibration.json",
                 *(f"figures/{stem}.{ext}" for stem in ("fold", "oddeven", "periodogram", "centroids")
                   for ext in ("svg", "png")))


def suffixed(name: str, ordinal: int) -> str:
    """The pack's name for a file of the ``ordinal``-th candidate: ``name`` itself for the first, and for the second
    ``_02`` before the extension (``data/ephemeris.csv`` gives ``data/ephemeris_02.csv``)."""
    if ordinal <= 1:
        return name
    path = PurePosixPath(name)
    return str(path.with_name(f"{path.stem}_{ordinal:02d}{path.suffix}"))


def file_notes(data: dict, others: list[dict] | None = None) -> dict[str, str]:
    """``FILE_NOTES`` with the two entries that count the dossier's transits (spec 11.6, the row counts the README
    gives): ``data/events.csv`` (``event_rows``) and ``data/transit_times.csv`` (``timing_rows``), which list the same
    epochs. With ``others`` (the further candidates' JSONs) every per-candidate file (``PER_CANDIDATE``) has a note for
    each candidate, the suffixed names (``suffixed``) for the further ones, each ending in " (the <P> d candidate)",
    and the note's figure files say which fold each holds (spec 6.1)."""
    notes = _file_notes(data)
    if not others:
        return notes
    datas = [data, *others]
    for ordinal, d in enumerate(datas, start=1):
        own, label = _file_notes(d), f" ({_candidate_name(d)})"
        for name in PER_CANDIDATE:
            what = own.get(name) or _figure_note(name)
            notes[suffixed(name, ordinal)] = what + label
        panel = "ab"[ordinal - 1] if ordinal <= 2 else str(ordinal)
        notes[suffixed("note/figure1.svg", ordinal)] = (
            f"the fold of {_candidate_name(d)}, panel ({panel}) of the note's figure, alone")
    first, second = _candidate_name(data), _candidate_name(others[0])
    notes["note/figure1.pdf"] = ("the note's figure as PDF, for pdflatex: (a) the fold of " + first + " and (b) the "
                                 "fold of " + second)
    notes["note/figure1.png"] = "the note's figure as PNG, the same panels"
    return notes


def _file_notes(data: dict) -> dict[str, str]:
    n, used = len(event_rows(data)), sum(1 for r in timing_rows(data) if r.get("used") is True)
    figure = ("the note's figure as PDF, for pdflatex: (a) the fold, (b) odd and (c) even transits"
              + (", (d) the per-season difference images" if data.get("pixels") else ""))
    return {**FILE_NOTES, "note/figure1.pdf": figure,
            "data/events.csv": f"the {n} transits with data: epoch, mid-time, quarter, depth, error, coverage, "
                               "single-event statistic and whether the transit was timed",
            "data/transit_times.csv": f"the same {len(timing_rows(data))} epochs as `data/events.csv`, {used} of "
                                      "them used: mid-time in BKJD and BJD_TDB, error, reduced chi-square and why a "
                                      "transit was not used"}


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


def _exofop_list(target_id, date, png: bool, pixels_figure: bool,
                 ordinal: int = 1) -> list[tuple[str, str, str]]:
    """(file name, pack source, what it is) for the files step 2 attaches: each figure once, as PNG when the PNGs
    were drawn, else as SVG. The second candidate's copies (``ordinal`` 2) come from its ``_02`` files and end their
    content in ``-02`` (an upload name takes no underscore, ``_CONTENT``)."""
    out = []
    for content, source, what in EXOFOP_CONTENTS:
        if content == "centroids" and not pixels_figure:
            continue
        ext = ("png" if png else "svg") if "{ext}" in source else source.rsplit(".", 1)[1]
        shown = content if ordinal <= 1 else f"{content}-{ordinal:02d}"
        out.append((exofop_name(target_id, date, shown, ext), suffixed(source.format(ext=ext), ordinal), what))
    return out


def _candidate_name(data: dict) -> str:
    """"the 47.690 d candidate", a candidate named by its period (the project's word is candidate, never b and c)."""
    period = _num((data.get("ephemeris") or {}).get("period_days")) or _num((data.get("signal") or {}).get("period"))
    return f"the {period:.3f} d candidate" if period else "the candidate"


def _named(data: dict, others, problems: list[str]) -> list[str]:
    """``problems`` of one candidate's JSON, each prefixed with the candidate's name when the pack holds more than
    one (``others``)."""
    return [f"{_candidate_name(data)}: {p}" for p in problems] if others else problems


def _ephemeris_problems(raw: dict) -> list[str]:
    """The per-candidate checks of ``gate_problems`` that come before the note's: the ephemeris and shape errors, the
    cross-check verdict, the stored commit, the pixel rule and the pixel verdict."""
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
    return out


def _prior_problems(raw: dict) -> list[str]:
    """The per-candidate checks of ``gate_problems`` on its prior claims: not checked, no relationship, and numbers
    that sit far from the candidate's."""
    out = []
    if ds.prior_claims_unchecked(raw):
        out.append("prior claims on the signal were not checked (no docs/candidates folder)")
    # spec 11.1: every prior claim states how SkySift's result stands to it, and no text calls it blind
    for claim in prior_claims(raw):
        if not claim.get("relationship"):
            out.append(f"prior claim doi:{claim['doi']} has no relationship (re-detection, blind-recovery or "
                       "vetting-only)")
    # a claim whose numbers sit far from this signal was filed on another star or misread (final review, minor 10)
    cmp = prior_comparison(raw) or {}
    if any(isinstance(cmp.get(k), dict) and (cmp[k].get("sigma") or 0) > PRIOR_MISMATCH_SIGMA for k in COMPARED):
        out.append(PRIOR_MISMATCH)
    # polish round 2: a quantity neither side gives an error for has no sigma, so the rule is its plain difference
    if any(_unerred_mismatch(k, cmp.get(k)) for k in ("period", "epoch")):
        out.append(PRIOR_MISMATCH_NO_ERROR)
    return out


def gate_problems(raw: dict, others: list[dict] | None = None,
                  span: tuple[float, float] | None = None) -> list[str]:
    """Why a live Zenodo draft is refused on the stored dossier JSON (with its stored cross-check merged in as
    ``cross_check``), one sentence per problem: the ephemeris carries no error (no timing block, or its fit is not
    ok), the transit shape carries no error (no shape block, or it is not ok), and ``crosscheck.verdict``'s reason
    (no cross-check, one stored by an earlier version of the tool or one made for an earlier dossier; its numbers
    never block, ruling t), and a stored
    commit that is dirty or unknown (the software statement could not name archived code; ruling g), a pixel verdict
    made under an older pixel rule (``dossier.pixel_rule_stale``), a pixel rerun that disagrees with the reviewer
    (``dossier.pixel_verdict_disagrees``), prior claims that were not checked (``dossier.prior_claims_unchecked``)
    and a rendered Research Note over the RNAAS limit of 1,500 words (``note_words``; the TESS statement itself never
    blocks), or over another RNAAS limit (``note_limit_problems``); and, after spec 11.1 and 11.2, a prior claim
    without a relationship, a prior claim whose period, epoch or depth sits more than ``PRIOR_MISMATCH_SIGMA`` from
    ours (``PRIOR_MISMATCH``) or, with no error on either side, a period over 1 percent or an epoch over 0.1 d from
    ours (``PRIOR_MISMATCH_NO_ERROR``), a ``FORBIDDEN_WORDS`` match in the Research Note, the abstract, the archive
    email, the ExoFOP notes field or the Zenodo title and description (the README and the upload note print the same
    sentence functions), and an abstract or a Zenodo description without the TRICERATOPS probability ahead of the in-house
    one when a current cross-check is stored (a stale one is refused once, by ``crosscheck.verdict``). Empty when the
    dossier may go to zenodo.org.

    ``others`` are the stored JSONs of the target's further candidates (``load_candidate``): the per-candidate checks
    (the ephemeris and shape errors, the cross-check, the commit, the pixel rule and verdict, the prior claims) run on
    each of them too, every problem naming its candidate by period ("the 47.690 d candidate: ..."); the note-level
    checks run once, on the two-candidate note and abstract (``span`` is the light curve's first and last BKJD, which
    the note's coincidence sentence needs)."""
    out: list[str] = []
    datas = [raw, *(others or [])]
    for data in datas:
        out.extend(_named(data, others, _ephemeris_problems(data)))
    note = render_note(raw, others=others, span=span) if others else render_note(raw)
    counts = note_words(note)
    if counts["total"] > NOTE_LIMITS["total"]:
        out.append(f"the Research Note has {counts['total']:,} words, over the RNAAS limit of "
                   f"{NOTE_LIMITS['total']:,}; shorten it first")
    # every other RNAAS limit (the abstract's 150 words, one figure or table), picked by limit, not by its words
    out.extend(f"the Research Note breaks an RNAAS limit: {p}"
               for limit, p in note_limit_problems(counts, note) if limit != "total")
    for data in datas:
        out.extend(_named(data, others, _prior_problems(data)))
    abstract = abstract_text(raw, others=others) if others else abstract_text(raw)
    zenodo = (metadata_from(raw, None, prior=zenodo_prior(raw), others=others) if others
              else metadata_from(raw, None, prior=zenodo_prior(raw)))
    description = re.sub(r"<[^>]+>", " ", zenodo["description"])
    texts = (("Research Note", note), ("abstract", abstract),
             ("archive email", archive_paragraph(raw, DOI_PLACEHOLDER, None, others or None)),
             ("ExoFOP notes field", exofop_prior_fields(raw).get("notes", "")),
             ("Zenodo title", zenodo["title"]), ("Zenodo description", description))
    for name, text in texts:
        for pattern in FORBIDDEN_WORDS:
            found = re.search(pattern, text, re.IGNORECASE)
            if found:
                out.append(f"the {name} says '{found.group(0)}', which spec 11.1 rules out")
    # spec 11.2: with a current cross-check (a stale one is refused above, once: final review, minor 1), the abstract
    # and the Zenodo description lead with its probability, never the in-house one alone or first
    block = raw.get("cross_check")
    if cc.complete(block) and block.get("current") is True:
        for name, text in (("abstract", abstract), ("Zenodo description", description)):
            if not _triceratops_first(text, block.get("tool_version")):
                out.append(f"the {name} carries the in-house false-positive probability without the TRICERATOPS "
                           "one")
    return out


PRIOR_MISMATCH_SIGMA = 5.0      # a compared quantity further than this from the prior claim's is a mismatch
PRIOR_MISMATCH = "the prior claim does not match this signal; check the claim's star and numbers"
PRIOR_MISMATCH_PERIOD = 0.01    # with no error on either side, a period further than this fraction from ours
PRIOR_MISMATCH_EPOCH_D = 0.1    # or a mid-transit time further than this many days from ours (same epoch)
PRIOR_MISMATCH_NO_ERROR = (PRIOR_MISMATCH + " (the claim and the dossier give no error for the quantity, and the "
                           "period differs by more than 1 percent or the mid-transit time by more than 0.1 d)")


def _unerred_mismatch(name: str, row) -> bool:
    """Whether a compared period or epoch with no error on either side (no sigma) is further apart than
    ``PRIOR_MISMATCH_PERIOD`` of ours (period) or ``PRIOR_MISMATCH_EPOCH_D`` days (epoch)."""
    if name not in ("period", "epoch") or not isinstance(row, dict) or row.get("sigma") is not None:
        return False
    ours, theirs = _num(row.get("ours")), _num(row.get("theirs"))
    if ours is None or theirs is None:
        return False
    if name == "period":
        return ours != 0 and abs(ours - theirs) / abs(ours) > PRIOR_MISMATCH_PERIOD
    return abs(ours - theirs) > PRIOR_MISMATCH_EPOCH_D


def _triceratops_first(text: str, version) -> bool:
    """Whether ``text`` carries our own TRICERATOPS headline sentence ("TRICERATOPS <version> gives", the opening
    ``crosscheck.headline`` prints), and before the in-house four-scenario estimate when it names that too (spec
    11.2's order). The word TRICERATOPS elsewhere does not count: a prior claim that names the tool ("its
    false-positive probability of 0.07 (TRICERATOPS)") is not our probability; nor does the same claim's naming of
    "SkySift's four-scenario estimate" in its comparison count as the in-house sentence."""
    start = text.find(f"TRICERATOPS {version} gives")
    if start < 0:
        return False
    own = re.search(r"(?<!SkySift's )four-scenario estimate", text)
    return own is None or start < own.start()


def zenodo_prior(data: dict) -> str | None:
    """The Zenodo description's prior-claim paragraph (spec 11.1): the relationship sentence and the comparison
    sentence, as the README prints them; None without a claim."""
    return " ".join(x for x in (prior_claim_sentence(data), comparison_text(data)) if x) or None


def independent_periods(period: float, others, tolerance: float = 0.02) -> list[float]:
    """The periods among ``others`` that are independent of ``period``: not within ``tolerance`` (relative) of it,
    nor of 2 or 3 times it, nor of a half or a third of it. Sorted, each once."""
    out: list[float] = []
    for p in others:
        p = _num(p)
        if p is None or p <= 0 or p in out:
            continue
        if ds.alias_of(period, p, tolerance) is None:
            out.append(p)
    return sorted(out)


def other_signals(store, target_id: str, reported_ids: set[int]) -> list[dict]:
    """``{"id", "period"}`` of the target's stage 2 signals (store status ``candidate_stage2``) that the pack does not
    report: not one of ``reported_ids`` and independent (``independent_periods``) of each period reported and of each
    one listed before it, by period; [] without a store or when the signal table cannot be read."""
    if store is None:
        return []
    try:
        with store.session() as s:
            rows = [(int(i), float(p), status) for i, p, status in s.execute(
                select(SignalRow.id, SignalRow.period, SignalRow.final_status).where(
                    SignalRow.target_id == target_id).order_by(SignalRow.period))]
    except SQLAlchemyError:
        return []
    reported = [p for i, p, _ in rows if i in reported_ids]
    out: list[dict] = []
    for i, p, status in rows:
        if i in reported_ids or status != "candidate_stage2":
            continue
        if all(independent_periods(q, [p]) for q in [*reported, *(o["period"] for o in out)]):
            out.append({"id": i, "period": p})
    return out


def other_signals_sentence(signals: list[dict], target_id: str, n_reported: int = 1) -> str:
    """The README's sentence for a star with another independent stage 2 signal the pack does not report (spec 6.3);
    "" without one. It names the signal and the command that gives it a dossier of its own, after which the next
    pack reports both candidates. With a second candidate already reported (``n_reported`` 2) there is no such
    command: the note holds two candidates."""
    if not signals:
        return ""
    listed = [f"{s['period']:.3f} days (signal {s['id']})" for s in signals]
    what = (f"another stage 2 signal at {listed[0]}" if len(listed) == 1
            else f"other stage 2 signals at {', '.join(listed[:-1])} and {listed[-1]}")
    if n_reported >= 2:
        return (f"This star also has {what}, at a period unrelated to these, which this pack does not report; the "
                "note holds two candidates, so it stays out.")
    which = signals[0]["id"] if len(signals) == 1 else "<id>"
    lead = "run" if len(signals) == 1 else "choose one, run"
    return (f"This star also has {what}, at a period unrelated to this one, which this pack does not report; "
            f"{lead} `skysift dossier --target {target_id} --signal {which} --further`, then build the pack again, "
            "and the note reports both candidates.")


CANDIDATE_MAX = 2          # the note shape holds two candidates (spec section 2)


def _out_reason(row: dict) -> str | None:
    """Why a further dossier row cannot be a candidate whatever else is listed: its signal's status is now not a
    candidate's, or its data file is gone; None when it can."""
    rejected = row.get("decision") == "reject" or row.get("candidate_status") == "rejected"
    alive = (row.get("final_status") == "candidate_stage2" or row.get("candidate_status") in ("approved", "published")
             or row.get("decision") == "approve")
    if rejected or not alive:
        return f"its status is now {'rejected' if rejected else row.get('final_status')}"
    path = row.get("data_path")
    if not path or not Path(path).is_file():
        shown = "/".join(Path(path).parts[-4:]) if path else "(none stored)"
        return (f"its data file {shown} is missing; run `skysift dossier --target {row['target_id']} --signal "
                f"{row['signal_id']} --further`")
    return None


def _stored_period(row: dict, fallback: float) -> float:
    """The period the dossier's JSON gives (``ephemeris.period_days``, the one ``_candidate_name`` and the note print),
    or ``fallback``, the signal row's, when the file is missing, unreadable or without one: the two can differ at the
    fourth decimal, and a candidate must carry one name."""
    try:
        data = json.loads(Path(row["data_path"]).read_text(encoding="utf-8"))
        period = _num((data.get("ephemeris") or {}).get("period_days"))
    except (KeyError, TypeError, OSError, ValueError, AttributeError):
        return fallback
    return fallback if period is None or period <= 0 else float(period)


def _scan(store, target_id: str) -> tuple[list[dict], list[dict], set[int]]:
    """(the candidates, the texts for the further dossiers left out, the signal ids of every further dossier) in one
    pass, so the list and the texts agree even when a file disappears while a pack is built."""
    own = store.dossier(target_id)
    if own is None:
        return [], [], set()
    first = ds.pick_signal(store, target_id, int(own["signal_id"]))
    cands = [{"ordinal": 1, "signal_id": int(own["signal_id"]),
              "period_days": _stored_period(own, float(first["period"])), "row": own, "further": False}]
    out: list[dict] = []
    rows = store.further_dossiers(target_id)
    for row in rows:
        sid = int(row["signal_id"])
        why = _out_reason(row)
        blocks = False
        period = _stored_period(row, float(row["period"]))
        if why is None:
            for c in cands:
                ratio = ds.alias_of(c["period_days"], period)
                if ratio is not None:
                    said = {1.0: "1", 2.0: "2", 3.0: "3", 0.5: "1/2"}.get(ratio, "1/3")
                    why = f"its period is within 2 percent of {said} times the {c['period_days']:.3f} d candidate's"
                    break
        if why is None and len(cands) >= CANDIDATE_MAX:
            why = "the note shape holds two candidates, and a third needs its own shape (spec section 2)"
            blocks = True
        if why is None:
            cands.append({"ordinal": len(cands) + 1, "signal_id": sid, "period_days": period, "row": row,
                          "further": True})
        else:
            out.append({"text": f"signal {sid}'s further dossier is left out: {why}", "blocks_live": blocks})
    return cands, out, {int(r["signal_id"]) for r in rows}


def candidates(store, target_id: str) -> list[dict]:
    """The candidates the pack reports, at most two (spec 4.1): the target's own dossier first, then the target's
    further dossiers in period order whose signal is still a candidate (stage 2, or approved, and not rejected), whose
    file is on disk and whose period is independent of every candidate already listed. Each is ``{"ordinal",
    "signal_id", "period_days", "row", "further"}``; empty without a dossier of the target's own."""
    return _scan(store, target_id)[0]


def left_out(store, target_id: str) -> list[dict]:
    """One ``{"text", "blocks_live"}`` for each further dossier of the target that ``candidates`` leaves out, with its
    reason. A third candidate has ``blocks_live`` True: the plain pack is still a correct two-candidate pack, and no
    live draft is made of it."""
    return _scan(store, target_id)[1]


def load_candidate(cand: dict) -> dict:
    """The stored dossier JSON of a candidate, with its row's cross-check merged in as ``cross_check``."""
    row = cand["row"]
    raw = json.loads(Path(row["data_path"]).read_text(encoding="utf-8"))
    raw["cross_check"] = cc.with_current(row.get("cross_check"), raw)
    return raw


def candidate_approval(store: Store, target_id: str, cand: dict, data_dir) -> tuple[bool, str | None]:
    """(approved, stale reason) of one candidate: for the target's own, ``_approval``; for a further one the same
    fingerprint check against its further row, the reason naming the candidate and its signal."""
    if not cand["further"]:
        return _approval(store, target_id, cand["signal_id"], data_dir)
    sig = ds.pick_signal(store, target_id, cand["signal_id"])
    approved = sig.get("decision") == "approve" or sig.get("candidate_status") in ("approved", "published")
    source = ds._target_dict(store, target_id).get("source")
    fp = ds.fingerprint(store.latest_tests(cand["signal_id"]), sig["final_status"], sig.get("decision"),
                        ds.calibration_rows(data_dir, source), ds.sweep_size_of(store, sig, source),
                        **ds.extinction_keys(target_id))
    if cand["row"].get("tests_fingerprint") != fp:
        return approved, (f"the {cand['period_days']:.3f} d candidate's dossier (signal {cand['signal_id']}) is "
                          "stale: the signal's tests, status or reviewer decision, the null calibration or the "
                          "stored extinction changed since it was generated")
    return approved, None


def coincident_transits(eph_short: dict, eph_long: dict, t_start: float, t_end: float) -> tuple[int, int]:
    """``(count, n_long)``: the predicted mid-times of the longer-period ephemeris within ``[t_start, t_end]`` (BKJD,
    from ``t0_bkjd`` and ``period_days``), and how many of them fall within half the sum of the two durations
    (``duration_h``) of a predicted mid-time of the shorter-period one. A coincidence can bias a depth (spec 4.2)."""
    ps, ts = float(eph_short["period_days"]), float(eph_short["t0_bkjd"])
    pl, tl = float(eph_long["period_days"]), float(eph_long["t0_bkjd"])
    if ps <= 0 or pl <= 0 or t_end < t_start:
        return 0, 0
    tolerance = (float(eph_short["duration_h"]) + float(eph_long["duration_h"])) / 48.0
    first, last = math.ceil((t_start - tl) / pl), math.floor((t_end - tl) / pl)
    times = [tl + k * pl for k in range(first, last + 1)]
    count = sum(1 for t in times if abs(t - (ts + round((t - ts) / ps) * ps)) <= tolerance + 1e-9)
    return count, len(times)


def zip_name(target_id: str, date: str) -> str:
    """The pack's zip, ``<target>_<yyyymmdd>_skysift_pack.zip``: the name Zenodo and a download show."""
    return f"{_safe_target(target_id)}_{date}{ZIP_SUFFIX}"


def _candidate_list(datas: list[dict]) -> list[dict]:
    """``f.candidates``: one ``{ordinal, signal_id, period_days, generated_words}`` for each candidate of a pack of
    more than one; empty for a pack of one, so no template line changes its output."""
    if len(datas) < 2:
        return []
    return [{"ordinal": i, "signal_id": (d.get("signal") or {}).get("id"),
             "period_days": _num((d.get("ephemeris") or {}).get("period_days"))
             or _num((d.get("signal") or {}).get("period")),
             "generated_words": date_words(d.get("generated") or "")} for i, d in enumerate(datas, start=1)]


def _fields(data: dict, f: dict, others: list[dict] | None = None) -> dict:
    f = dict(f)
    datas = [data, *(others or [])]
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
    f.setdefault("candidates", _candidate_list(datas))
    f.setdefault("not_approved", [])
    f.setdefault("span", None)
    if "note_words" not in f:
        tex = render_note(data, f["doi"], others=others or None, span=f["span"])
        f["note_words"], f["note_problems"] = note_words(tex), note_problems(note_words(tex), tex)
    f.setdefault("note_problems", note_problems(f["note_words"]))
    f.setdefault("limits", NOTE_LIMITS)
    f.setdefault("tag_seq", 1)
    f.setdefault("data_tag", data_tag(f["date"], f["tag"], _safe_target(target_id), f["tag_seq"]))
    day = datetime.strptime(f["date"], "%Y%m%d").replace(tzinfo=UTC)
    f.setdefault("after_bjd", julian_date(day))
    f.setdefault("after_words", date_words(day))
    f.setdefault("other_signals", "")
    f.setdefault("left_out", [])
    if not f.get("stale_commands"):
        f["stale_commands"] = [f"skysift dossier --target {target_id} --force"]
    stellar = EXOFOP_STELLAR_TEMPLATE.format(date=f["date"]) if star_characterized(data) else None
    f.setdefault("exofop_stellar", stellar)
    f.setdefault("exofop_planet", planet_file_name(f["date"]))
    f.setdefault("exofop_planets", [planet_file_name(f["date"], i) for i in range(1, len(datas) + 1)])
    listed = []
    for i, d in enumerate(datas, start=1):
        shown = f["pixels_figure"] if i == 1 else bool(d.get("pixels"))
        tail = f" ({_candidate_name(d)})" if others else ""
        listed += [(name, what + tail) for name, _src, what in _exofop_list(target_id, f["date"], f["png"], shown, i)]
    f.setdefault("exofop", listed + ([(stellar, FILE_NOTES[f"exofop/{EXOFOP_STELLAR_TEMPLATE}"])] if stellar else []))
    notes = file_notes(data, others)
    f.setdefault("file_notes", [(name, notes.get(name) or _figure_note(name)) for name in sorted(f["files"])
                                if not name.startswith("exofop/") or name == "exofop/note.txt"])
    f.setdefault("exofop_url", EXOFOP_URL)
    f.setdefault("exofop_help_url", EXOFOP_HELP_URL)
    f.setdefault("helpdesk_url", HELPDESK_URL)
    f.setdefault("code_sentence", code_sentence(None))
    return f


def _figure_note(name: str) -> str:
    stem = re.sub(r"_\d\d$", "", Path(name).stem)
    what = {"fold": "the folded transit with the model", "oddeven": "the odd and even transits side by side",
            "periodogram": "the periodogram with the signal and its aliases marked",
            "centroids": "one difference image per season with the star, the neighbour and the centroid"}
    return what.get(stem, "a pack file")


# the README's table rows that belong to the star: printed once, in "The star" table, when there are two candidates
STAR_ROWS = ("spectral type", "stellar effective temperature", "stellar radius", "stellar mass", "stellar density",
             "distance", "habitable zone inner edge (runaway greenhouse)",
             "habitable zone outer edge (maximum greenhouse)")
STAR_DILUTION = ("crowdsap", "psffrac")
NOT_AVAILABLE = "not available"
ALBEDO_ASSUMPTION = " (at a Bond albedo of 0.3)"      # cut C26: the note leaves it out, the README's table says it


def _catalogue_cell(data: dict) -> str:
    """A candidate's catalogue result for the README table (cut C23): the hits, "none" when the four catalogues were
    searched and held no match, or "not searched"."""
    from skysift import pack_two
    checked, hits = pack_two._catalogue(data)
    if hits:
        return ", ".join(hits)
    return "none in the Kepler KOI, DR25 TCE, TESS TOI or Kepler eclipsing binary entries" if checked else \
        "not searched"


def _readme_rows(cands: list[dict]) -> list[tuple[str, list[str]]]:
    """The README's table for two candidates, one ``(quantity, [one cell for each candidate])`` for each row any of
    them has, in the single table's order; a candidate without the row reads ``NOT_AVAILABLE``."""
    def row(label, make, when=lambda c: True):
        if any(when(c) for c in cands):
            rows.append((label, [make(c) if when(c) else NOT_AVAILABLE for c in cands]))
    rows: list[tuple[str, list[str]]] = []
    row("period", lambda c: f"{c['period_pm']} d{c['period_note']}")
    row("first mid-transit (epoch 0)", lambda c: f"BJD_TDB {c['t0_bjd_pm']} (BKJD {c['t0_bkjd']})")
    row("reference mid-transit (the centre of the series)",
        lambda c: f"epoch {c['epoch_ref']}: BJD_TDB {c['t_ref_bjd_pm']}", lambda c: c["timed"])
    row("transit times", lambda c: f"{c['n_used']} of {c['n_measured']} timed transits fit the linear ephemeris, "
                                   f"rms {c['rms_min']} min", lambda c: c["timed"])
    row("duration", lambda c: f"{c['duration_pm']} h{c['duration_note']}")
    row("depth", lambda c: f"{c['depth_pm']} ppm{c['depth_note']}{c['depth_source_text']}")
    row("transits", lambda c: str(c["n_events"]))
    row("signal detection efficiency (SDE)", lambda c: str(c["sde"]))
    row("signal-to-noise ratio", lambda c: str(c["snr"]))
    row("odd and even depths", lambda c: f"{c['odd_text']} and {c['even_text']}" + (
        f" ({c['parity_sigma_text']} sigma apart)" if c["parity_sigma_text"] else ""), lambda c: bool(c["odd_text"]))
    row("odd and even depths, stacked folds", lambda c: c["stacked_parity_text"],
        lambda c: bool(c["stacked_parity_text"]))
    row("secondary eclipse (phase 0.5)", lambda c: f"{c['secondary_pm']} ppm" + (
        f", 3-sigma upper limit {c['secondary_limit']} ppm" if c["secondary_limit"] else ""),
        lambda c: bool(c["secondary_pm"]))
    names = list(dict.fromkeys(r["parameter"] for c in cands for r in c["param_rows"]
                               if r["parameter"] not in STAR_ROWS))
    for name in names:
        def text(c, name=name):
            found = next(r["text"] for r in c["param_rows"] if r["parameter"] == name)
            return found + (ALBEDO_ASSUMPTION if name == "equilibrium temperature" else "")
        row(name, text, lambda c, name=name: any(r["parameter"] == name for r in c["param_rows"]))
    row("dip in the neighbour's aperture (leak test)",
        lambda c: next(r["text"] for r in c["dilution_rows"] if r["key"] == "leak"),
        lambda c: any(r["key"] == "leak" for r in c["dilution_rows"]))
    row("catalogue matches", lambda c: c["catalogue"])
    row("TESS", lambda c: c["tess_row"])
    return rows


def _readme_caveats(cands: list[dict]) -> list[dict]:
    """The caveats of every candidate, each once (``title`` and ``text`` alike), in the first's order and then the
    further ones' new ones; one only some candidates have says whose it is in ``who``."""
    rows: dict[tuple, dict] = {}
    for c in cands:
        for cv in c["caveats"]:
            row = rows.setdefault((cv["title"], cv["text"]), {**cv, "owners": []})
            row["owners"].append(c["name_label"])
    return [{**{k: v for k, v in r.items() if k != "owners"},
             "who": "" if len(r["owners"]) == len(cands) else f" ({' and '.join(r['owners'])} only)"}
            for r in rows.values()]


def _groups(cands: list[dict]):
    """``groups(*keys)`` for the README's methods paragraphs: ``[("", numbers)]`` when the candidates' values for
    ``keys`` are the same (one paragraph, the first candidate's), else ``[(label, numbers), ...]`` with the label of
    each candidate that has a value; empty when none has."""
    def groups(*keys):
        if all(c[k] == cands[0][k] for c in cands for k in keys):
            return [("", cands[0])] if any(cands[0][k] for k in keys) else []
        return [(c["label"], c) for c in cands if any(c[k] for k in keys)]
    return groups


def _readme_prior(cands: list[dict]) -> list[dict]:
    """The README's prior-claim paragraphs for two candidates: each candidate's claim (told in full once when both
    carry the same one) and its comparison sentence, labelled."""
    out, told = [], set()
    for c in cands:
        if not c["prior_plain"]:
            continue
        again = c["prior_plain"] in told
        told.add(c["prior_plain"])
        out.append({"label": c["name_label"], "claim": "the same claim as above." if again else c["prior_plain"],
                    "comparison": c["comparison_plain"] or ""})
    return out


def _readme_context(data: dict, others: list[dict], f: dict, n: dict) -> dict:
    """What ``README.md.j2`` reads besides ``n`` and ``f``: ``two`` (the second candidate's ``numbers``, None for a
    pack of one), ``cands`` (each candidate's ``numbers``, in a pack of two with a ``label``, ``name_label``,
    ``period_label``, ``ordinal`` and ``signal_arg`` added), ``s`` (``pack_two.shared`` in plain form, with the README's
    ``summary`` and ``coincidence``), ``groups``, the two-candidate table and star rows, the caveats and the
    references."""
    if not others:
        return {"two": None, "cands": [n], "s": None, "groups": _groups([n]),
                "caveats": [{**c, "who": ""} for c in n["caveats"]], "references": n["references_plain"],
                "sx": suffixed}
    from skysift import pack_two
    datas = [data, *others]
    cands = [n, *(numbers(d, after_bjd=f["after_bjd"]) for d in others)]
    for i, (c, d) in enumerate(zip(cands, datas, strict=True), start=1):
        name = _candidate_name(d)
        c.update(ordinal=i, name_label=name, label=f"**{_capital(name)}.** ", label_colon=f"**{_capital(name)}:** ",
                 period_label=f"{c['period_title']} d", signal_arg="" if i == 1 else f" --signal {c['signal_id']}",
                 catalogue=_catalogue_cell(d), note_forms=note_forms(d, nearest_in_note=False))
    for c in cands[1:]:
        c["hand_built_text"] = None            # a further dossier stores no hand-built localisation (spec 4.3)
    span = f.get("span")
    s = pack_two.shared(datas, False, span, bool(f["png"]))
    s["summary"] = pack_two.summary(datas)
    s["coincidence"] = pack_two._coincidence(datas, span, note=False)
    star = [r for r in n["param_rows"] if r["parameter"] in STAR_ROWS]
    star += [r for r in n["dilution_rows"] if r["key"] in STAR_DILUTION]
    references = list(dict.fromkeys(r for c in cands for r in c["references_plain"]))
    return {"two": cands[1], "cands": cands, "s": s, "groups": _groups(cands), "rows": _readme_rows(cands),
            "star_rows": star, "caveats": _readme_caveats(cands), "references": references,
            "priors": _readme_prior(cands), "sx": suffixed, "vetted": pack_two.VETTED_SINGLY}


def render_readme(data: dict, report_fields: dict, others: list[dict] | None = None) -> str:
    """The README: the five steps in order (``STEPS``), each with its account, files, text and what the engine did
    or cannot do. ``report_fields`` carries the build's own values (approved, tic, date, tag, files, png, zenodo,
    stale_reason, zip_name, pixels_figure, note_words, creators_source, after_bjd for step 5's transits,
    other_signals, not_approved, span); missing ones take defaults. ``others`` are the further candidates' JSONs: the
    table gets a column for each candidate, the methods are printed once or per candidate, and step 2, step 5, the
    caveats and the file list name both (spec 6.2). Without ``others`` the README is the single candidate's."""
    f = _fields(data, report_fields, others)
    n = numbers(data, after_bjd=f["after_bjd"])
    if HAND_BUILT_FILE in f["files"]:       # Task 12 review M-6: the pack's copy, named only when the pack holds it
        n["hand_built_text"] = hand_built_sentence(data, source=HAND_BUILT_FILE)
    context = _readme_context(data, others or [], f, n)
    return _env(tex=False).get_template("README.md.j2").render(
        n=n, f=f, titles=dict(STEPS), sandbox_note=SANDBOX_DOI_NOTE,
        crowdsap_definition=CROWDSAP_DEFINITION, psffrac_definition=PSFFRAC_DEFINITION,
        archive_paragraph=archive_paragraph(data, f["doi_shown"], f["tic"], others or None), **context)


def _render_plain(name: str, data: dict, f: dict, others: list[dict] | None = None) -> str:
    f = _fields(data, f, others)
    n = numbers(data)
    cands = [n]
    if others:
        cands += [numbers(d) for d in others]
        for c, d in zip(cands, [data, *others], strict=True):
            c.update(name_label=_candidate_name(d), period_label=f"{c['period_title']} d")
    return _env(tex=False).get_template(name).render(
        n=n, f=f, cands=cands, two=cands[1] if others else None,
        any_prior=any(c["prior_plain"] for c in cands),
        archive_paragraph=archive_paragraph(data, f["doi_shown"], f["tic"], others or None))


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


def _fold_axes(ax, plots: dict) -> tuple[float, float]:
    """One fold panel on ``ax``: the points, the binned flux with its errors and the model, within the panel's own
    window and scaled to the bins and the model (the scatter is clipped). Returns the y limits it set."""
    w = _num(plots.get("window_h")) or 12.0
    mx, my = _arr(plots.get("model"), (0, 1))
    fx, fy = _arr(plots.get("fold_points"), (0, 1))
    bx, by, be = _arr(plots.get("fold_bins"), (0, 1, 2))
    ax.plot(fx, fy, ".", color=DOTS, ms=1.5, zorder=1)
    ax.errorbar(bx, by, be, fmt="o", color=POINT, ms=3, lw=1, zorder=2, label="binned flux")
    ax.plot(mx, my, color=MODEL, lw=1.6, zorder=3,
            label="transit fit" if plots.get("model_kind") == "fit" else "box model")
    vals = np.concatenate([by - be, by + be, my]) if by.size or my.size else np.array([-1.0, 1.0])
    pad = 0.15 * (np.ptp(vals) or 1.0)
    low, high = float(vals.min() - pad), float(vals.max() + pad)
    ax.set_ylim(low, high)
    ax.set_xlim(-w, w)
    ax.set_xlabel("hours from mid-transit")
    ax.set_ylabel("relative flux (ppm)")
    ax.legend(loc="lower right", fontsize=8, frameon=False)
    return low, high


def _two_panel_figure(Figure, datas: list[dict], note: Path) -> None:
    """The two-candidate note's one figure (spec 5.1, cut C10): (a) the first candidate's fold and (b) the second's in
    one row, each on its own ephemeris, window and bins, with the same y scale when the depths are within a factor of
    two; ``figure1.png`` and ``figure1.pdf``."""
    depths = [_num((d.get("ephemeris") or {}).get("depth_ppm")) for d in datas]
    shared = None not in depths and min(depths) > 0 and max(depths) / min(depths) <= 2
    fig = Figure(figsize=(7, 3.6), dpi=150, layout="constrained")
    axes = fig.subplots(1, len(datas), sharey=shared)
    limits = []
    for ax, d, label in zip(np.atleast_1d(axes), datas, "abcdefgh", strict=False):
        limits.append(_fold_axes(ax, d.get("plots") or {}))
        ax.set_title(f"({label})", loc="left", fontsize=10)
    if shared:
        np.atleast_1d(axes)[0].set_ylim(min(lo for lo, _hi in limits), max(hi for _lo, hi in limits))
    fig.savefig(note / "figure1.png")
    fig.savefig(note / "figure1.pdf")


def _png_figures(Figure, data: dict, figures: Path, note: Path, others: list[dict] | None = None) -> None:
    """The PNG figures: the note's ``figure1`` (the panelled single figure, or with ``others`` the two fold panels of
    ``_two_panel_figure``) and, per candidate, the fold, odd and even, periodogram and centroid pictures in
    ``figures``; a further candidate's names end in ``_02`` (``fold_02.png``, then ``_03``)."""
    if others:
        _two_panel_figure(Figure, [data, *others], note)
    _candidate_pngs(Figure, data, figures, note, "", not others)
    for i, other in enumerate(others or [], start=2):
        _candidate_pngs(Figure, other, figures, note, f"_{i:02d}", False)


def _candidate_pngs(Figure, data: dict, figures: Path, note: Path, suffix: str, note_figure: bool) -> None:
    plots, eph, sh = data.get("plots") or {}, data.get("ephemeris") or {}, _metrics(data, "rung2_shape")
    w = _num(plots.get("window_h")) or 12.0
    mx, my = _arr(plots.get("model"), (0, 1))

    def fold(ax):
        _fold_axes(ax, plots)

    def parity(ax, kind, colour):
        bx, by, be = _arr(plots.get(f"{kind}_bins"), (0, 1, 2))
        ax.errorbar(bx, by, be, fmt="o", color=colour, ms=3, lw=1)
        ax.plot(mx, my, color=MODEL, lw=1.4)
        depth = _f(sh.get(f"{kind}_ppm"), 0, True)
        ax.set_title(f"{kind.capitalize()} transits" + (f": {depth} ppm" if depth else ""), fontsize=10)
        ax.set_xlim(-w, w)
        ax.set_xlabel("hours from mid-transit")

    pixels = data.get("pixels") or []
    vmax = max([1.0] + [v for s in pixels for row in s.get("snr") or [] for v in row if _num(v) is not None])

    def season(ax, s):
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

    fig = Figure(figsize=(7, 3.6), dpi=150, layout="constrained")
    fold(fig.add_subplot())
    fig.savefig(figures / f"fold{suffix}.png")

    if note_figure:
        # the note's one figure (spec 11.6): (a) the fold across the top, (b) odd and (c) even below it on one scale,
        # (d) one difference image per season in the third row; each panel drawn by the code of its own figure
        cols = max(2, len(pixels))
        fig = Figure(figsize=(7, 8.4 if pixels else 6.4), dpi=150, layout="constrained")
        grid = fig.add_gridspec(3 if pixels else 2, cols)
        top = fig.add_subplot(grid[0, :])
        fold(top)
        top.set_title("(a)", loc="left", fontsize=10)
        half = cols // 2
        odd = fig.add_subplot(grid[1, :half])
        even = fig.add_subplot(grid[1, half:], sharey=odd)
        for ax, kind, colour, label in ((odd, "odd", POINT, "(b)"), (even, "even", EVEN, "(c)")):
            parity(ax, kind, colour)
            ax.set_title(label, loc="left", fontsize=10)
        odd.set_ylabel("relative flux (ppm)")
        for i, s in enumerate(pixels):
            ax = fig.add_subplot(grid[2, i])
            season(ax, s)
            if i == 0:
                ax.set_title("(d)", loc="left", fontsize=10)
        fig.savefig(note / "figure1.png")
        fig.savefig(note / "figure1.pdf")

    fig = Figure(figsize=(8, 3.4), dpi=150, layout="constrained")
    axes = fig.subplots(1, 2, sharey=True)
    for ax, kind, colour in ((axes[0], "odd", POINT), (axes[1], "even", EVEN)):
        parity(ax, kind, colour)
    axes[0].set_ylabel("relative flux (ppm)")
    fig.savefig(figures / f"oddeven{suffix}.png")

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
    fig.savefig(figures / f"periodogram{suffix}.png")

    if pixels:
        fig = Figure(figsize=(2.6 * len(pixels), 3.0), dpi=150, layout="constrained")
        axes = np.atleast_1d(fig.subplots(1, len(pixels)))
        for ax, s in zip(axes, pixels, strict=True):
            season(ax, s)
        fig.savefig(figures / f"centroids{suffix}.png")


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
    ``CITATION.cff`` when present (a source checkout), with its ``doi`` (None without one; the note's code sentence,
    ``code_sentence``), else the installed package's author metadata (from ``pyproject.toml``), else (None, a
    sentence saying the creators are placeholders)."""
    import yaml
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        data = None
    if isinstance(data, dict) and data.get("authors"):
        return {**data, "doi": str(data.get("doi") or "").strip() or None}, "CITATION.cff in the source checkout"
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
                        ds.calibration_rows(data_dir, source), ds.sweep_size_of(store, sig, source),
                        **ds.extinction_keys(target_id))
    if ds.pick_signal(store, target_id)["id"] != signal_id:
        return approved, "another signal is now the target's best"
    if row.get("tests_fingerprint") != fp:
        return approved, ("the signal's tests, status or reviewer decision, the null calibration or the stored "
                          "extinction changed since it was generated")
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


def _write_texts(out_dir: Path, zip_path: Path, data: dict, fields: dict, citation: dict | None,
                 others: list[dict] | None = None) -> dict:
    """The four texts, then the hashes and the zip, for the DOI in ``fields`` (written again once a Zenodo draft
    reserved one). ``others`` are the further candidates' JSONs, which the note and the texts report beside
    ``data`` (``fields["span"]`` is what the note's coincidence count runs over). Returns the note's word counts."""
    tex = render_note(data, fields["doi"], citation=citation, panels=bool(fields.get("png")), others=others or None,
                      span=fields.get("span"))
    _write(out_dir / "note" / "note.tex", tex)
    words = note_words(tex)
    fields = {**fields, "note_words": words, "note_problems": note_problems(words, tex),
              "code_sentence": code_sentence(citation)}
    _write(out_dir / "exofop" / "note.txt", _render_plain("exofop_note.txt.j2", data, fields, others))
    _write(out_dir / "archive_email.txt", _render_plain("archive_email.txt.j2", data, fields, others))
    files = sorted({*_rel(out_dir), "README.md", "provenance/hashes.json"})
    _write(out_dir / "README.md", render_readme(data, {**fields, "files": files}, others))
    hashes = {name: _sha(out_dir / name) for name in _rel(out_dir) if name != "provenance/hashes.json"}
    _write(out_dir / "provenance" / "hashes.json", jsonutil.dumps(hashes, indent=1))
    _zip(out_dir, zip_path, zip_path.stem)
    return words


def build_pack(store, blob, settings, target_id: str, *, out_dir: Path | None = None, tic: str | None = None,
               observer_tag: str = "skysift", tag_seq: int = 1, zenodo: ZenodoClient | None = None,
               final: bool = False, deposition_id: int | None = None,
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
    made for an earlier dossier (``gate_problems``), so a provisional number never reaches a live record. The
    candidates are the target's own and, from its further dossiers, a second (``candidates``); each is checked for a
    reviewer's approval and for a stale dossier, and the README names the further dossiers left out
    (``left_out``) and the target's other stage 2 signals whose periods are independent of these
    (``other_signals``). A third further dossier blocks a live draft. Records the folder on the dossiers row and
    on the candidate row of every candidate.

    Raises NoDossier, ``dossier.LightCurveReleased`` (nothing is downloaded), PackError (bad TIC id, tag or folder; a
    live draft before approval, before the FPP sign-off, for a stale dossier, or for one ``gate_problems`` refuses,
    naming every problem) before anything is written, and ``zenodo.ZenodoError`` naming the built pack and any
    unfinished draft when Zenodo fails.

    ``final`` (spec 11.6) is the pack to submit: it needs a live client (``FINAL_NEEDS_LIVE``), refuses an author
    block with an empty or malformed field (``author_problems``) before anything is written, and after the draft
    reserves its DOI and the texts carry it, refuses any placeholder left in a public text (``final_problems``) with
    the unfinished-draft error, before the draft is filled or the zip uploaded.

    ``deposition_id`` names an unpublished draft of an earlier run to refill instead of making a new one
    (``ZenodoClient.reuse_deposition``): its files are removed, its reserved DOI is the one the texts carry, and its
    metadata is the pack's with what the draft already had (``zenodo.merge_metadata``)."""
    say = log or (lambda _text: None)
    if final and (zenodo is None or zenodo.sandbox):
        raise PackError(FINAL_NEEDS_LIVE)
    if final and author_problems(load_author()):
        raise PackError(f"a final pack needs the author block: {'; '.join(author_problems(load_author()))}")
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
    cands, out_texts, further_ids = _scan(store, target_id)
    others = [load_candidate(c) for c in cands[1:]]
    checks = [(c, *candidate_approval(store, target_id, c, settings.data_dir)) for c in cands]
    approved = all(ok for _c, ok, _why in checks)
    not_approved = [f"signal {c['signal_id']} ({c['period_days']:.3f} d)" for c, ok, _why in checks if not ok]
    stale = "; ".join(why for _c, _ok, why in checks if why) or None
    remedies = [f"skysift dossier --target {target_id} --signal {c['signal_id']} --further" if c["further"]
                else f"skysift dossier --target {target_id} --force" for c, _ok, why in checks if why]
    if zenodo is not None and not zenodo.sandbox and not approved:
        raise PackError(f"{target_id} is not yet approved by a reviewer, so no draft is made on zenodo.org; use "
                        "--zenodo for a sandbox rehearsal")
    if zenodo is not None and not zenodo.sandbox and not fpp_model.SIGNED_OFF:
        raise PackError("the false-positive estimate is provisional until spec 4.2's factor tables are signed off, "
                        "so no draft is made on zenodo.org; use --zenodo for a sandbox rehearsal")
    if zenodo is not None and not zenodo.sandbox and not (raw.get("fpp") or {}).get("signed_off"):
        raise PackError(f"the stored dossier of {target_id} was written before the false-positive sign-off, so no "
                        f"draft is made on zenodo.org; run skysift dossier --target {target_id} --force first")
    if zenodo is not None and not zenodo.sandbox:
        for cand, data in zip(cands[1:], others, strict=True):         # each further dossier needs the sign-off too
            if not (data.get("fpp") or {}).get("signed_off"):
                raise PackError(f"{_candidate_name(data)}: the stored dossier (signal {cand['signal_id']}) was "
                                "written before the false-positive sign-off, so no draft is made on zenodo.org; run "
                                f"skysift dossier --target {target_id} --signal {cand['signal_id']} --further first")
    if zenodo is not None and not zenodo.sandbox and stale:
        remedy = " and ".join(remedies) if others else f"skysift dossier --target {target_id}"
        raise PackError(f"the stored dossier of {target_id} is stale ({stale}), so no draft is made on zenodo.org; "
                        f"run {remedy} first")
    def load_light_curve():
        say("loading the light curve")
        target = ds._target_dict(store, target_id)
        return LightCurveLoader(store, blob, source_factory=ds._offline_factory(target_id, sid)).load(target, pin=True)
    # the two-candidate note counts the transits the candidates share over the light curve's span, so the gate that
    # renders it needs the light curve first
    lc = load_light_curve() if others else None
    span = (float(np.nanmin(lc.time)), float(np.nanmax(lc.time))) if lc is not None else None
    problems = [*gate_problems(raw, others, span), *(o["text"] for o in out_texts if o["blocks_live"])]
    if zenodo is not None and not zenodo.sandbox and problems:
        raise PackError(f"the stored dossier of {target_id} is not ready for zenodo.org: {'; '.join(problems)}. No "
                        "draft is made there; use --zenodo for a sandbox rehearsal")
    # a signal with a further dossier of its own is named under "left out" or stale, not offered a second time
    named = {c["signal_id"] for c in cands} | further_ids
    signals = other_signals(store, target_id, named)
    lc = lc if lc is not None else load_light_curve()
    fnorm = ds.whitened(lc, raw["signal"])
    citation, creators_source = load_citation()
    if load_author() is not None:          # Task 13 review M-5: the note's authors and the Zenodo creators agree
        citation, creators_source = with_author(citation), f"docs/author.json (with {creators_source})"
    elif citation is None:
        say("no CITATION.cff or package author found; the creators are placeholders")
    data = ds.public_claims(public_paths(raw, settings.data_dir))     # the pack embeds and uploads this JSON
    shown = [ds.public_claims(public_paths(o, settings.data_dir)) for o in others]
    place = out_dir                    # the pack's folder (``final`` is the --final flag)
    out_dir = _building(place)
    try:
        zip_file, metadata, fields, words = _write_pack(
            out_dir, data, raw, row, lc, fnorm, target_id, date, now, settings, citation, creators_source,
            {"approved": approved, "stale_reason": stale, "tic": tic, "tag": observer_tag, "tag_seq": tag_seq,
             "stale_commands": remedies or None, "left_out": [o["text"] for o in out_texts],
             "other_signals": other_signals_sentence(signals, target_id, len(cands)),
             "not_approved": not_approved, "span": span}, say, others=shown, others_raw=others, cands=cands)
    except BaseException:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise
    _move_into_place(out_dir, place, say)
    out_dir, zip_path = place, place / zip_file
    store.set_pack(target_id, str(out_dir), now.replace(tzinfo=None))
    for c in cands:
        store.set_candidate_pack(c["signal_id"], str(out_dir))
    say(f"wrote {zip_path}")
    zinfo = None
    if zenodo is not None:
        deposition = None
        try:
            if deposition_id is not None:
                say(f"reusing the Zenodo draft {deposition_id}: removing its files")
                deposition = zenodo.reuse_deposition(deposition_id)
                metadata = merge_metadata(deposition.get("metadata") or {}, metadata)
            else:
                say("creating the Zenodo draft")
                deposition = zenodo.create_deposition()
            zinfo = {"deposition_id": deposition.get("id"), "draft_url": zenodo.draft_url(deposition),
                     "sandbox": zenodo.sandbox, "doi": zenodo.reserved_doi(deposition)}
            doi = zinfo["doi"] or DOI_PLACEHOLDER
            words = _write_texts(out_dir, zip_path, data, {**fields, "zenodo": zinfo, "doi": doi,
                                                           "doi_shown": doi_shown(zinfo["doi"])}, citation, shown)
            left = final_problems(out_dir) if final else []
            if left:
                raise ZenodoError(f"a final pack carries no placeholder, but {'; '.join(left)}")
            say("filling the Zenodo draft and uploading the zip")
            zenodo.set_metadata(deposition, metadata)
            zenodo.upload(deposition, zip_path)
        except Exception as e:  # any failure after the files exist is reported with the draft
            if deposition is None:
                raise ZenodoError(f"{e}; the pack at {out_dir} was built without a Zenodo draft") from e
            again = (f"run the command again with --deposition {deposition.get('id')}" if deposition_id is not None
                     else "discard it on Zenodo, then run the command again")
            raise ZenodoError(f"{e}; the pack at {out_dir} was built, but the unpublished Zenodo draft "
                              f"{deposition.get('id')} ({zenodo.draft_url(deposition)}) is unfinished: {again}") from e
    return PackReport(target_id, out_dir, zip_path, _rel(out_dir), approved, zinfo, stale, words)


def _write_candidate(out_dir: Path, ordinal: int, data: dict, raw: dict, lc, fnorm, settings) -> None:
    """The files one candidate has of its own (``PER_CANDIDATE``, the second's named with ``suffixed``) except the SVG
    figures and the ExoFOP copies, which ``_write_pack`` makes after the folders are there: the whitened light curve,
    the ephemeris and parameters, the transit times, shape, events, stellar provenance, periodogram, rung metrics, the
    cross-check, the figure data, the dossier page and the calibration counts."""
    def name(path: str) -> Path:
        return out_dir / suffixed(path, ordinal)
    _lightcurve_csv(name("data/lightcurve.csv"), lc, fnorm)
    # both a/R* here, named apart (spec 11.3); the texts quote one
    eph_rows = ephemeris_rows(data) + param_rows(data, both_a_over_r=True) + dilution_rows(data)
    cols = ["parameter", "value", "minus", "plus", "unit", "source"]
    _csv(name("data/ephemeris.csv"), cols, [[r[c] for c in cols] for r in eph_rows])
    for file, header, table in (
            ("transit_times.csv", ["epoch", "t_bkjd", "t_bjd", "err_days", "chi2_red", "used", "reason"],
             timing_rows(data)),
            ("shape.csv", ["parameter", "value", "minus", "plus", "unit", "point", "note"], shape_csv_rows(data)),
            ("events.csv", ["epoch", "t_mid_bkjd", "quarter", "depth_ppm", "err_ppm", "coverage", "ses", "timed",
                            "timing_reason"], event_rows(data)),
            ("stellar_provenance.csv", ["parameter", "value", "minus", "plus", "unit", "method", "inputs",
                                        "reference"], provenance_rows(data))):
        _csv(name(f"data/{file}"), header, [[r[c] for c in header] for r in table])
    _csv(name("note/figure1_data.csv"), ["phase_h", "flux_ppm", "err_ppm", "n", "model_ppm"], figure_data_rows(data))
    eph = data.get("ephemeris") or {}
    eph = {**eph, **{f"{k}_bjd": _bjd(eph, k) for k in ("t0", "t_ref") if f"{k}_bjd" in eph}}
    _write(name("data/ephemeris.json"), jsonutil.dumps({"ephemeris": eph, "star": data.get("star"),
                                                        "planet": data.get("planet"),
                                                        "rows": [{c: r[c] for c in cols} for r in eph_rows]},
                                                       indent=1))
    plots = data.get("plots") or {}
    _write(name("data/periodogram.json"), jsonutil.dumps({"sde": (data.get("ephemeris") or {}).get("sde"),
                                                          "period_days": (data.get("ephemeris") or {}).get(
                                                              "period_days"),
                                                          "spectrum": plots.get("spectrum"),
                                                          "lines": plots.get("spectrum_lines"),
                                                          "columns": ["period_days", "sde"]}, indent=1))
    _write(name("data/rung_metrics.json"), jsonutil.dumps(data.get("tests") or [], indent=1))
    if data.get("cross_check"):
        _write(name("data/cross_check.json"), jsonutil.dumps(data["cross_check"], indent=1))
    _write(name("dossier.html"), ds.export_html(data))
    _write(name("provenance/calibration.json"),
           jsonutil.dumps(_calibration(raw, data, settings.data_dir), indent=1))


def _write_pack(out_dir: Path, data: dict, raw: dict, row: dict, lc, fnorm, target_id: str, date: str,
                now: datetime, settings, citation: dict | None, creators_source: str, review: dict, say,
                others: list[dict] | None = None, others_raw: list[dict] | None = None,
                cands: list[dict] | None = None):
    """Every file of the pack into ``out_dir`` (the building folder) and its zip there, with the DOI placeholder.
    ``others`` are the further candidates' JSONs as the pack embeds them and ``others_raw`` as stored (the whitened
    flux and the calibration counts come from the stored signal); ``cands`` is ``candidates()`` (versions.json lists
    them). Each further candidate's own files carry ``_02`` (``suffixed``), its ExoFOP copies ``-02`` and its planet
    file ``_002``. Returns (zip file name, Zenodo metadata, the text fields, the note's word counts)."""
    others, others_raw = others or [], others_raw or []
    zip_path = out_dir / zip_name(target_id, date)
    say("writing the pack")
    figures, note, d = out_dir / "figures", out_dir / "note", out_dir / "data"
    for folder in (figures, note, d, out_dir / "provenance", out_dir / "exofop"):
        folder.mkdir(parents=True, exist_ok=True)
    datas, raws = [data, *others], [raw, *others_raw]
    fnorms = [fnorm, *(ds.whitened(lc, r["signal"]) for r in others_raw)]
    # data and the dossier page, for each candidate
    for ordinal, (one, one_raw, flux) in enumerate(zip(datas, raws, fnorms, strict=True), start=1):
        _write_candidate(out_dir, ordinal, one, one_raw, lc, flux, settings)
    study = Path(ds.CANDIDATES_DIR) / target_id / "localisation" / "results.json"
    if study.is_file():             # the file the README's hand-built sentence compares with (spec 11.5)
        shutil.copyfile(study, out_dir / HAND_BUILT_FILE)
    # figures: the SVGs of each candidate, and the note's figure1.svg (its fold) beside figure1_02.svg
    shown = []
    for ordinal, one in enumerate(datas, start=1):
        _write(out_dir / suffixed("figures/fold.svg", ordinal), fold_svg(one))
        _write(out_dir / suffixed("figures/oddeven.svg", ordinal), oddeven_svg(one))
        _write(out_dir / suffixed("figures/periodogram.svg", ordinal), periodogram_svg(one))
        shown.append(bool(one.get("pixels")))
        if shown[-1]:
            _write(out_dir / suffixed("figures/centroids.svg", ordinal), centroids_svg(one["pixels"]))
        shutil.copyfile(out_dir / suffixed("figures/fold.svg", ordinal),
                        out_dir / suffixed("note/figure1.svg", ordinal))
    pixels_figure = shown[0]
    Figure = _matplotlib()
    png = Figure is not None
    if png:
        say("drawing the PNG figures")
        _png_figures(Figure, data, figures, note, others or None)
    # provenance, the ExoFOP copies
    prov = data.get("provenance") or {}
    _write(out_dir / "provenance" / "queries.json", jsonutil.dumps(prov.get("queries") or [], indent=1))
    listing = [{"ordinal": c["ordinal"], "signal_id": c["signal_id"], "period_days": c["period_days"],
                "dossier_generated": ds._iso(c["row"].get("generated")),
                "dossier_path": public_paths(str(c["row"].get("data_path")), settings.data_dir)}
               for c in cands or []] if others else []
    _write(out_dir / MARKER, jsonutil.dumps(_versions(data, row, now, Figure, zip_path.name, creators_source,
                                                      listing), indent=1))
    # spec 11.1: the relationship sentence and the comparison sentence, as the README prints them
    prior = zenodo_prior(data)
    metadata = (metadata_from(data, citation, prior=prior, others=others) if others
                else metadata_from(data, citation, prior=prior))
    _write(out_dir / "zenodo_metadata.json", jsonutil.dumps(metadata, indent=1))
    tag = data_tag(date, review["tag"], _safe_target(target_id), review.get("tag_seq", 1))
    for ordinal, (one, has_pixels) in enumerate(zip(datas, shown, strict=True), start=1):
        for name, source, _what in _exofop_list(target_id, date, png, has_pixels, ordinal):
            shutil.copyfile(out_dir / source, out_dir / "exofop" / name)
        _write(out_dir / "exofop" / planet_file_name(date, ordinal),
               render_exofop_planet(one, date, review["tic"], tag, ordinal))
    stellar = render_exofop_stellar(data, date, review["tic"], tag)
    if stellar is not None:
        _write(out_dir / "exofop" / EXOFOP_STELLAR_TEMPLATE.format(date=date), stellar)
    fields = {**review, "date": date, "png": png, "zenodo": None, "pixels_figure": pixels_figure,
              "after_bjd": julian_date(now), "after_words": date_words(now),
              "zip_name": zip_path.name, "built_words": date_words(now), "doi": DOI_PLACEHOLDER,
              "doi_shown": DOI_PLACEHOLDER, "creators_source": creators_source}
    words = _write_texts(out_dir, zip_path, data, fields, citation, others)
    return zip_path.name, metadata, fields, words


def _versions(data: dict, row: dict, now: datetime, Figure, zip_file: str, creators_source: str,
              cands: list[dict] | None = None) -> dict:
    version = skysift_version()
    mpl = None
    if Figure is not None:
        import matplotlib
        mpl = matplotlib.__version__
    prov, sig = data.get("provenance") or {}, data.get("signal") or {}
    out = {"pack_version": PACK_VERSION, "dossier_version": data.get("dossier_version"),
           "target_id": (data.get("target") or {}).get("target_id"), "signal_id": sig.get("id"),
           "search_version": sig.get("detector_version"), "ladder_version": prov.get("ladder_version"),
           "git_commit": prov.get("git_commit"), "pack_git_commit": ds.git_commit(), "skysift_version": version,
           "python": platform.python_version(), "numpy": np.__version__, "matplotlib": mpl,
           "platform": sys.platform, "built_utc": now.isoformat(), "dossier_generated": data.get("generated"),
           "dossier_row_generated": ds._iso(row.get("generated")), "kbonus_doi": KBONUS_DOI,
           "zip_name": zip_file, "creators_source": creators_source, "software": software_versions()}
    if cands and len(cands) > 1:
        out["candidates"] = cands            # [{ordinal, signal_id, period_days, dossier_generated, dossier_path}]
    return out
