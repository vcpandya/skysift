"""The submission pack's numbers as text (moved out of ``skysift.pack``): every value with an error is printed
beside its own error at the error's decimals (``pm_text``, ``quantities``), a missing error as "(no error;
<reason>)" (``missing_error_reasons``), and LaTeX escaping for the Research Note. The README, the note,
the ExoFOP note, the archive email and the Zenodo description share these, so they all round alike."""
from __future__ import annotations

import math
import re
from datetime import datetime
from decimal import Decimal

from skysift.ephemeris import BKJD_OFFSET


class Raw(str):
    """Text that is already LaTeX: the note's template prints it as it is."""


def _num(x) -> float | None:
    if isinstance(x, bool) or x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def err_digits(err: float | None, default: int = 2) -> int:
    """The decimals that print ``err`` to two significant figures (0.016, 0.083, 73), ``default`` for a missing or
    non-positive error: the one precision rule of every text and file (referee pack ruling j). A value printed at the
    same decimals ends where its error does. The page's Jinja helper is this function and ``dossier.js`` ``errDigits``
    is its copy."""
    if err is None or err <= 0:
        return default
    return max(0, 1 - math.floor(math.log10(err)))


def _f(x, nd: int = 2, comma: bool = False) -> str | None:
    x = _num(x)
    if x is None:
        return None
    return f"{x:,.{nd}f}" if comma else f"{x:.{nd}f}"


def _places(x: float) -> int:
    """The decimals a stored float carries (2.61 has 2, 9.1e-05 has 6, 269.0 has none)."""
    return max(0, -int(Decimal(repr(float(x))).normalize().as_tuple().exponent))


def pm_digits(*errs) -> int:
    """The decimals ``_pm_parts`` prints a value and its errors at: two significant figures of the smaller error
    (``err_digits``), never more decimals than the stored errors carry (``_places``), so 0.1 prints as 0.1 and 5.0
    as 5. The page's Jinja ``err_digits`` and ``dossier.js`` ``errDigits`` take this rule for one error. 2 without a
    positive error."""
    good = [float(e) for e in errs if e is not None and not isinstance(e, bool) and math.isfinite(e) and e > 0]
    if not good:
        return 2
    return min(err_digits(min(good)), max(_places(e) for e in good))


def _pm_parts(value, minus=None, plus=None, nd: int = 2,
              comma: bool = False) -> tuple[str, str | None, str | None] | None:
    """(value, minus, plus) as text, all at the decimals of the smaller error (``err_digits``: two significant
    figures), never more than the stored errors carry (an error stored as 0.08 prints as 0.08, not 0.080), the value
    at ``nd`` decimals and both errors None without an error; None without a value. Every number with an error in the
    pack's texts and files goes through here, so they all round alike."""
    m = _num(value)
    if m is None:
        return None
    lo, hi = _num(minus), _num(plus)
    errs = [e for e in (lo, hi) if e is not None and e > 0]

    def fmt(x: float, d: int) -> str:
        return f"{x:,.{d}f}" if comma else f"{x:.{d}f}"
    if not errs:
        return fmt(m, nd), None, None
    d = pm_digits(*errs)
    return fmt(m, d), fmt(lo or 0.0, d), fmt(hi or 0.0, d)


def pm_text(value, minus=None, plus=None, *, nd: int = 2, comma: bool = False, tex: bool = False) -> str | None:
    """A value with its 1-sigma error: "40.28986 ± 0.00009", "4.16 +0.06/-0.05" when asymmetric, LaTeX
    "4.16$^{+0.06}_{-0.05}$" (a ``Raw``) with ``tex``; the value alone without an error; None without a value."""
    parts = _pm_parts(value, minus, plus, nd, comma)
    if parts is None:
        return None
    mid, lo, hi = parts
    if lo is None:
        text = mid
    elif lo == hi:
        text = f"{mid} $\\pm$ {hi}" if tex else f"{mid} ± {hi}"
    else:
        text = f"{mid}$^{{+{hi}}}_{{-{lo}}}$" if tex else f"{mid} +{hi}/-{lo}"
    return Raw(text) if tex else text


def err_text(minus, plus, *, comma: bool = False) -> str | None:
    """The error alone, as ``pm_text`` prints it: "0.00009", "+0.06/-0.05"; None without one."""
    parts = _pm_parts(0.0, minus, plus, comma=comma)
    if parts is None or parts[1] is None:
        return None
    return parts[2] if parts[1] == parts[2] else f"+{parts[2]}/-{parts[1]}"


def value_text(v: dict | None, tex: bool = False) -> str | None:
    """A stored ``{"median", "minus", "plus"}`` as "0.509 +0.017/-0.016" (or "2.61 ± 0.08" when symmetric), or as
    LaTeX "0.509$^{+0.017}_{-0.016}$" with ``tex``, thousands with commas (3,653 K, as the dossier prints them);
    None without a median."""
    if not isinstance(v, dict) or _num(v.get("median")) is None:
        return None
    return pm_text(v.get("median"), v.get("minus"), v.get("plus"), comma=True, tex=tex)


_TEX = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{",
        "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}", "±": r"$\pm$", "<": r"$<$", ">": r"$>$",
        "≤": r"$\leq$", "≥": r"$\geq$", "×": r"$\times$", "°": r"$^\circ$", "−": "-", "–": "--", "—": ", "}
_TEX_RE = re.compile("|".join(re.escape(k) for k in _TEX))


def tex_escape(text: str) -> str:
    return _TEX_RE.sub(lambda m: _TEX[m.group(0)], str(text))


def _joined(tex: bool, *parts) -> str:
    """Parts of one sentence as one string; with ``tex`` a ``Raw`` whose plain parts are escaped and whose ``Raw``
    parts (values with errors) are kept, so the note prints the errors as math."""
    if not tex:
        return "".join(str(p) for p in parts)
    return Raw("".join(p if isinstance(p, Raw) else tex_escape(p) for p in parts))


def date_words(when) -> str:
    if isinstance(when, str):
        try:
            when = datetime.fromisoformat(when)
        except ValueError:
            return when
    return f"{when.day} {when:%B %Y}" if isinstance(when, datetime) else "an unknown date"


def _bjd(eph: dict, key: str) -> float | None:
    """The BJD_TDB of ``key`` ("t0" or "t_ref"): the stored ``<key>_bjd``, which the dossier keeps at six decimals
    (``dossier.BJD_DECIMALS``; a BKJD above 1000 keeps only four at the dossier's eight significant figures), else
    ``<key>_bkjd`` plus the Kepler offset. A dossier stored before the BJD decimals were kept holds the BJD at eight
    significant figures, tenths of a day, so the BKJD wins when it carries more decimals; None without either."""
    bjd, bkjd = _num(eph.get(f"{key}_bjd")), _num(eph.get(f"{key}_bkjd"))
    if bjd is not None and (bkjd is None or _places(bjd) >= _places(bkjd)):
        return bjd
    return round(bkjd + BKJD_OFFSET, 6) if bkjd is not None else None


def missing_error_reasons(data: dict) -> dict[str, str]:
    """Why the period and epoch (``timing``), the duration and the depth would be printed without an error, from the
    stored blocks; a text names each once, as "(no error; <reason>)"."""
    eph, timing, shape = data.get("ephemeris") or {}, data.get("timing"), data.get("shape")
    timing_reason = ((timing.get("reason") or "no reason stored") if isinstance(timing, dict)
                     else "this dossier has no per-transit timing")
    if isinstance(shape, dict) and not shape.get("ok"):
        duration = shape.get("reason") or "no reason stored"
    elif eph.get("duration_source") == "search box":
        duration = "the search box width"
    else:
        duration = "this dossier has no shape bootstrap"
    depth = "the search box depth" if eph.get("depth_source") == "search box" else "this dossier has no depth error"
    return {"timing": timing_reason, "duration": duration, "depth": depth}


def _errors(eph: dict) -> dict:
    """The version 3 errors of an ephemeris block as numbers: period, t0, t_ref, depth (one value each, None without
    one) and the duration's minus and plus."""
    dur = eph.get("duration_h_err") or {}
    return {"period": _num(eph.get("period_err_days")), "t0": _num(eph.get("t0_bjd_err")),
            "t_ref": _num(eph.get("t_ref_err_days")), "depth": _num(eph.get("depth_ppm_err")),
            "duration_minus": _num(dur.get("minus")), "duration_plus": _num(dur.get("plus"))}


def quantities(data: dict, tex: bool = False) -> dict:
    """The ephemeris as the texts print it, each value beside its own error (never a search value beside a fitted
    error): ``<name>`` the value at its error's decimals, ``<name>_err`` the error (None without one), ``<name>_pm``
    both (LaTeX with ``tex``), and for the period, duration and depth ``<name>_note``, " (no error; <reason>)"
    when the error is missing, else "". The epochs share the period's note, so a text names a missing timing error
    once. Names: period, t0_bjd, t0_bkjd, t_ref_bjd, duration, depth; ``epoch_ref``."""
    eph = data.get("ephemeris") or {}
    why = missing_error_reasons(data)
    out: dict = {"epoch_ref": eph.get("epoch_ref")}

    def put(name, value, minus, plus, nd, comma=False, note=None):
        parts = _pm_parts(value, minus, plus, nd, comma)
        out[name] = parts[0] if parts else None
        out[f"{name}_err"] = err_text(minus, plus, comma=comma) if parts else None
        out[f"{name}_pm"] = pm_text(value, minus, plus, nd=nd, comma=comma, tex=tex)
        if note is not None:
            out[f"{name}_note"] = "" if parts is None or out[f"{name}_err"] else f" (no error; {note})"
    e = _errors(eph)
    put("period", eph.get("period_days"), e["period"], e["period"], 5, note=why["timing"])
    put("t0_bjd", _bjd(eph, "t0"), e["t0"], e["t0"], 4)
    put("t0_bkjd", eph.get("t0_bkjd"), e["t0"], e["t0"], 4)
    put("t_ref_bjd", _bjd(eph, "t_ref"), e["t_ref"], e["t_ref"], 4)
    put("duration", eph.get("duration_h"), e["duration_minus"], e["duration_plus"], 2, note=why["duration"])
    put("depth", eph.get("depth_ppm"), e["depth"], e["depth"], 0, comma=True, note=why["depth"])
    return out


def ephemeris_sentence(data: dict) -> str:
    """"P = ... d, T0 = BJD_TDB ..., duration ... h, depth ... ppm", each with its error or the reason it has none:
    the ExoFOP note, the archive email and the Zenodo description quote this one sentence."""
    q = quantities(data)
    parts = [f"P = {q['period_pm']} d{q['period_note']}" if q["period_pm"] else None,
             f"T0 = BJD_TDB {q['t0_bjd_pm']}" if q["t0_bjd_pm"] else None,
             f"duration {q['duration_pm']} h{q['duration_note']}" if q["duration_pm"] else None,
             f"depth {q['depth_pm']} ppm{q['depth_note']}" if q["depth_pm"] else None]
    return ", ".join(p for p in parts if p)


# what the public texts may say about a prior claim (dossier.PRIOR_CLAIM_PUBLIC holds the same names)
PRIOR_CLAIM_NUMBERS = ("period_days", "period_err_days", "t0_bjd", "t0_err_days", "depth_ppm", "depth_err_ppm", "fpp")
PRIOR_CLAIM_PUBLIC = ("doi", "date", "author", "title", "relationship", "development_note", *PRIOR_CLAIM_NUMBERS,
                      "fpp_tool")
# spec 11.1: how each text names the relationship of SkySift's result to the claim, as a noun and as a verb
RELATIONSHIP_WORDS = {"re-detection": "a re-detection",
                      "blind-recovery": "a recovery made before SkySift knew of the claim",
                      "vetting-only": "a vetting of the claim's signal, not a detection"}
RELATIONSHIP_VERBS = {"re-detection": "re-detected it in its sweep of",
                      "blind-recovery": "found it in its sweep of", "vetting-only": "vetted it on"}


def prior_claims(data: dict) -> list[dict]:
    """The dossier's ``prior_claims`` (``dossier.prior_claims``: earlier public claims on the same signal, earliest
    first), each with a DOI, cut to ``PRIOR_CLAIM_PUBLIC``: the texts cite the record, never the status of someone
    else's submission, even from a dossier stored before the status was dropped. Text fields are stripped strings,
    the claim's numbers finite floats or None, and ``relationship`` a key of ``RELATIONSHIP_WORDS`` or None. An
    empty list for a dossier without the key (a version-3 reader allows it) or with None (the claims were not
    checked)."""
    claims = data.get("prior_claims")
    if not isinstance(claims, list):
        return []
    out = []
    for c in claims:
        if not isinstance(c, dict) or not str(c.get("doi") or "").strip():
            continue
        row = {k: _num(c.get(k)) if k in PRIOR_CLAIM_NUMBERS else str(c.get(k) or "").strip()
               for k in PRIOR_CLAIM_PUBLIC}
        out.append({**row, "relationship": row["relationship"] or None})
    return out
