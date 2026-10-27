"""The two-candidate Research Note's own sentences (spec 2026-10-27, sections 4.2 and 5.1). The period ratio with its
alias statement and the count of coincident transits are plain text, with no markup, so the note, the README and the
abstract print them as they are. ``pack.coincident_transits`` makes the count they report.

The rest are the note's sentences about one candidate (``signal_sentence``, ``vetting_sentence``,
``centroid_sentence``, ``planet_sentence``) or about both (``fap_pair``, ``triceratops_pair``, ``in_house_pair``,
``tess_pair``, ``caveat_union``, ``abstract``, ``caption``, ``summary``). Each is built from the same functions and
numbers the single-candidate note prints (``pack.numbers``, ``pack.shape_rows`` and the rest), never from a value
reformatted here; a sentence with markup returns a ``Raw`` when ``tex``. ``shared`` collects them for
``templates/pack/note_two.tex.j2``. Candidates are named by their periods at two decimals ("the 15.11 d candidate"),
never b and c."""
from __future__ import annotations

import math

import numpy as np

from skysift import crosscheck as cc
from skysift import dossier as ds
from skysift import pack
from skysift import tess as tess_module
from skysift.pack_rows import _metrics, _test, dilution_rows, shape_rows, star_characterized
from skysift.pack_text import Raw, _f, _joined, _num, pm_digits, pm_text, quantities, value_text
from skysift.zenodo import PROVISIONAL, fap_sentence

ALIAS_RATIOS = ((2.0, "2"), (3.0, "3"), (0.5, "1/2"), (1 / 3, "1/3"))      # the ratios the search treats as aliases
ALIAS_PERCENT = 2                                                          # and the tolerance it allows them

NUMBER_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")

# cut C23 (amending spec 5.4): the sentence that neither candidate matches a catalogued object is the README's; a
# candidate that does match one still says so in its own vetting sentence
CATALOGUE_IN_NOTE = False
# cut C26 (spec 5.2's second fallback): "at a Bond albedo of 0.3" after the second planet is the README's, which gives
# the equilibrium temperature with its assumption
ALBEDO_IN_NOTE = False
SOLE_CANDIDATE_TEXT = "Neither matches a Kepler KOI, DR25 TCE, TESS TOI or Kepler eclipsing binary entry."
NO_MATCH_TEXT = "It matches no Kepler KOI, DR25 TCE, TESS TOI or Kepler eclipsing binary entry."
VETTED_SINGLY = "The two candidates were vetted singly; no joint fit was made."
CATALOGUE_NAMES = {"cumulative": "the Kepler cumulative KOI table", "q1_q17_dr25_tce": "the Q1 to Q17 DR25 TCE list",
                   "toi": "the TESS TOI list", "kepler_eb": "the Kepler eclipsing binary catalogue"}


def _distance_text(percent: float) -> str:
    """The distance at one significant figure under 10 percent, else as a whole number."""
    if percent <= 0:
        return "0"
    one = float(f"{percent:.1g}")
    if one >= 10:
        return f"{round(percent)}"
    return f"{one:.{max(0, -math.floor(math.log10(one)))}f}"


def ratio_sentence(p_short: float, p_long: float) -> str:
    """The period ratio of two candidates, the longer over the shorter at three decimals, its distance from the
    nearest of the alias ratios in percent of that ratio, and the tolerance the search used (spec 4.2)."""
    low, high = sorted((float(p_short), float(p_long)))
    ratio = high / low
    nearest, word = min(ALIAS_RATIOS, key=lambda r: abs(ratio - r[0]) / r[0])
    distance = abs(ratio - nearest) / nearest * 100
    return (f"The period ratio is {ratio:.3f}, {_distance_text(distance)} percent from {word}, outside the "
            f"{ALIAS_PERCENT} percent alias tolerance of the search (ratios of 2, 3, 1/2 and 1/3), so neither is an "
            "alias of the other.")


def _count_word(n: int) -> str:
    return NUMBER_WORDS[n] if 0 <= n < len(NUMBER_WORDS) else str(n)


def coincidence_sentence(count: int, n_long: int, p_short: str, p_long: str, note: bool) -> str | None:
    """How many of the longer candidate's predicted transits fall within a duration of a transit of the shorter one.
    In the note (``note``) the count is in words up to nine and a count of none prints nothing, since the ratio
    sentence stands alone then; the README always states it, in figures. ``p_short`` and ``p_long`` are the periods as
    printed ("15.11")."""
    if not note:
        verb = "coincides" if count == 1 else "coincide"
        return (f"{count} of {n_long} predicted {p_long} d transits {verb} with a {p_short} d transit within a "
                "duration.")
    if count <= 0:
        return None
    first = _count_word(count)
    first = first[0].upper() + first[1:]
    if count == 1:
        return (f"{first} of the {n_long} predicted {p_long} d transits falls within a duration of a {p_short} d "
                "transit.")
    return (f"{first} of the {n_long} predicted {p_long} d transits fall within a duration of a {p_short} d "
            "transit.")


# ---------------------------------------------------------------------------------------------------- helpers


def _period(data: dict) -> float | None:
    return _num((data.get("ephemeris") or {}).get("period_days")) or _num((data.get("signal") or {}).get("period"))


def period_word(data: dict) -> str:
    """The candidate's period at two decimals, as the prose names it ("15.11"; the "d" is the sentence's)."""
    period = _period(data)
    return f"{period:.2f}" if period else "unknown"


def _name(data: dict) -> str:
    return f"the {period_word(data)} d candidate"


def _target_name(datas: list[dict]) -> str:
    target = datas[0].get("target") or {}
    return str(target.get("name") or target.get("target_id"))


def _lower(text):
    """``text`` with its first letter in lower case, a ``Raw`` staying one."""
    out = str(text)[:1].lower() + str(text)[1:]
    return Raw(out) if isinstance(text, Raw) else out


def _unstop(text):
    """``text`` without its closing full stop, a ``Raw`` staying one."""
    out = str(text).removesuffix(".")
    return Raw(out) if isinstance(text, Raw) else out


def _show(value):
    """``value`` as a part of a sentence: "" for None, a ``Raw`` kept as it is."""
    return "" if value is None else value if isinstance(value, Raw) else str(value)


def _and_list(items: list[str]) -> str:
    if not items:
        return ""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + f" and {items[-1]}"


def _pair(values: list[str]) -> str:
    """"a and b", or "a" when they print alike."""
    return values[0] if len(set(values)) == 1 else " and ".join(values)


def title(datas: list[dict]) -> str:
    """"Two transit candidates on <name>", and with a prior claim on either "SkySift vetting of two transit
    candidates on <name>" (cut C18: the periods are the abstract's first numbers)."""
    claimed = any(pack.prior_claims(d) for d in datas)
    return f"{'SkySift vetting of two' if claimed else 'Two'} transit candidates on {_target_name(datas)}"


# ---------------------------------------------------------------------------------------------------- the signals


def signal_sentence(data: dict, tex: bool, *, after: dict | None = None) -> str:
    """Spec 5.1's signal sentence: "A dip of 1,160 ± 50 ppm lasting 4.02 (+0.13, -0.11) hours repeats every 15.113696
    ± 0.000054 days (SDE 66.0, S/N 32.9, 81 transits), with the reference mid-transit at BJD_TDB 2455662.9571 ± 0.0015
    at epoch 46, the centre of the series; 57 of 60 timed transits fit this linear ephemeris with an rms of 18.0
    minutes.", the values from ``quantities``. Without a timing fit it gives the first mid-transit instead ("with the
    first mid-transit at BJD_TDB ... (BKJD ...)"), and a value without an error says why, as the single note does.
    ``after`` is the candidate whose sentence came first: this one then opens "A second dip" and does not explain the
    centre of the series again (when that one did)."""
    q = quantities(data, tex)
    eph, timing = data.get("ephemeris") or {}, data.get("timing") if isinstance(data.get("timing"), dict) else {}
    stats = f"SDE {_f(eph.get('sde'), 1)}, S/N {_f(eph.get('snr'), 1)}, {eph.get('n_events')} transits"
    parts: list = ["A second dip" if after is not None else "A dip", " of ", _show(q["depth_pm"]), " ppm",
                   q["depth_note"], " lasting ", _show(q["duration_pm"]), " hours", q["duration_note"],
                   " repeats every ", _show(q["period_pm"]), " days"]
    note = q["period_note"]
    parts.append(f"{note[:-1]}; {stats})" if note else f" ({stats})")
    bjd = Raw(r"BJD$_{\rm TDB}$") if tex else "BJD_TDB"
    if pack._timed(data):
        parts += [", with the reference mid-transit at ", bjd, " ", _show(q["t_ref_bjd_pm"]),
                  f" at epoch {q['epoch_ref']}"]
        if after is None or not pack._timed(after):
            parts.append(", the centre of the series")
        parts.append(f"; {timing.get('n_used')} of {timing.get('n_measured')} timed transits fit this linear "
                     f"ephemeris with an rms of {_f(timing.get('rms_min'), 1)} minutes")
    else:
        parts += [", with the first mid-transit at ", bjd, " ", _show(q["t0_bjd_pm"]), f" (BKJD {q['t0_bkjd']})"]
    return _joined(tex, *parts, ".")


def fold_clause(datas: list[dict]) -> str:
    """The sentence saying which ephemeris the depth and shape below use, once for both candidates when they fold
    alike ("For both, the depth and shape below use the linear-fit ephemeris."), else for each; "" for dossiers that
    do not say (before version 5, as the single note leaves it out)."""
    told = [(d, pack.fold_words(d)) for d in datas if (d.get("ephemeris") or {}).get("fold_ephemeris")]
    if not told:
        return ""
    words = {w for _d, w in told}
    if len(told) == len(datas) and len(words) == 1:
        return f"For both, the depth and shape below use the {told[0][1]}."
    return ("The depth and shape below use "
            + _and_list([f"the {w} for {_name(d)}" for d, w in told]) + ".")


# ---------------------------------------------------------------------------------------------------- vetting


def _comma(parts: list) -> list:
    """``parts`` of a clause that opens with a semicolon, opening with a comma instead (the vetting sentence is
    already joined by semicolons)."""
    if parts and isinstance(parts[0], str) and parts[0].startswith("; "):
        return [f", {parts[0][2:]}", *parts[1:]]
    return parts


def _fit_clause(data: dict, tex: bool) -> list | None:
    """"the fit gives a radius ratio of k (+e, -e)" with b and a/R* worded by ``pack._geometry_words`` (a degenerate b
    says so and gives a/R* from the stellar parameters); None when the shape has no error (its block is not ok) or
    no fit."""
    shape = data.get("shape")
    fitted = _metrics(data, "rung2_shape").get("fit_ok") is True
    if not (isinstance(shape, dict) and shape.get("ok") is True and fitted):
        return None
    rows = {r["key"]: r for r in shape_rows(data)}
    if "k" not in rows:
        return None
    cmp = shape.get("comparison") or {}
    offset = any(isinstance(c, dict) and c.get("key") == "shape_offset" for c in data.get("caveats") or [])

    def val(key: str):
        r = rows[key]
        text = pm_text(r["value"], r["minus"], r["plus"], nd=r["nd"], tex=tex)
        point = _num(r.get("point"))
        if offset and key == "k" and point is not None and r["minus"] is not None:
            return _joined(tex, text, f" (full-data fit {point:.{pm_digits(r['minus'], r['plus'])}f})")
        return text
    parts: list = ["the fit gives a radius ratio of ", val("k")]
    if "b" in rows and "a_over_r" in rows:
        parts += _comma(pack._geometry_words(data, rows, val, cmp, tex))
    return parts


def _odd_even_clause(data: dict, tex: bool) -> list | None:
    pair = pack._odd_even(data, tex)
    if pair is None:
        sh = _metrics(data, "rung2_shape")
        odd, even, par = _f(sh.get("odd_ppm"), 0, True), _f(sh.get("even_ppm"), 0, True), _f(sh.get("parity_sigma"), 1)
        if not (odd and even):
            return None
        return [f"the odd and even depths are {odd} and {even} ppm" + (f" ({par} sigma apart)" if par else "")]
    diff = _num((data.get("parity") or {}).get("difference_sigma"))
    parity = data["parity"]
    odd = pm_text(parity["odd"]["depth_ppm"], parity["odd"]["err_ppm"], parity["odd"]["err_ppm"], nd=0, comma=True,
                  tex=tex)                                  # the unit once, after the even depth
    return ["the odd and even depths are ", odd, " and ", pair[1],
            f" ({diff:.1f} sigma apart)" if diff is not None else ""]


def _secondary_clause(data: dict, tex: bool) -> list | None:
    sec = data.get("secondary")
    if not isinstance(sec, dict) or _num(sec.get("depth_ppm")) is None:
        older = pack.secondary_sentence(data, tex)
        return [_lower(_unstop(older))] if older else None
    limit = _f(sec.get("limit_3sigma_ppm"), 0, True)
    return ["the phase 0.5 depth is ",
            pm_text(sec["depth_ppm"], sec.get("err_ppm"), sec.get("err_ppm"), nd=0, comma=True, tex=tex), " ppm",
            f" (3-sigma limit {limit} ppm)" if limit else ""]


def _failed_checks(data: dict) -> dict[str, str]:
    """The rungs other than the pixel check that did not pass, each in the words the single note uses ("the check
    "<name>" gave fail: <reason>"), by rung."""
    words = {w.get("rung"): w for w in data.get("why") or [] if isinstance(w, dict)}
    out = {}
    for t in data.get("tests") or []:
        if t.get("rung") != "rung5_pixels" and t.get("status") != "pass":
            w = words.get(t.get("rung")) or {}
            out[t.get("rung")] = (f"the check \"{w.get('name') or t.get('rung')}\" gave {t.get('status')}: "
                                  f"{t.get('reason') or w.get('sentence') or 'no reason stored'}")
    return out


def _stability_clause(data: dict, failed: dict[str, str], tex: bool) -> list | None:
    """"the depth agrees across quarter groups (p = 0.22)" (cut C17: no chi-squared); a rung 3 that did not pass says so
    in its own words instead. The per-event depth trend the single note ends the clause with is the README's (C22)."""
    if "rung3_stability" in failed:
        return [failed["rung3_stability"]]
    if pack.stability_sentence(data) is None:
        return None
    stab = data.get("stability")
    st = (stab.get("metrics") or {}) if isinstance(stab, dict) and stab.get("metrics") else _metrics(
        data, "rung3_stability")
    p = _f(st.get("p_value"), 2)
    return ["the depth agrees across quarter groups" + (f" (p = {p})" if p else "")]


def _catalogue(data: dict) -> tuple[bool, list[str]]:
    """(whether rung 4 searched the catalogues, the hits as "N in <catalogue>")."""
    nv = _metrics(data, "rung4_novelty")
    hits = [f"{int(nv[k])} in {v}" for k, v in CATALOGUE_NAMES.items() if _num(nv.get(k))]
    return bool(nv) and any(k in nv for k in CATALOGUE_NAMES), hits


def vetting_sentence(data: dict, tex: bool) -> str:
    """Spec 5.1's compact vetting sentence, its clauses joined by semicolons: "15.11 d: <coverage>; <fit clause>;
    <odd and even>; <phase 0.5 depth with the 3-sigma limit>; the depth agrees across quarter groups (p = 0.22)." The
    fit clause is "the fit gives a radius ratio of k (+e, -e)" with b and a/R* in the words of
    ``pack._geometry_words`` (a degenerate b says so), and there is none when the shape carries no error. A rung that
    did not pass says so in the words of the single note, and a catalogue match is named; the clean case is the
    shared catalogue sentence."""
    ev = _metrics(data, "rung1_events")
    failed = _failed_checks(data)
    clauses: list[list] = []
    if ev.get("n_covered") is not None and ev.get("n_events_with_data"):
        covered = f"{ev['n_covered']} of {ev['n_events_with_data']} predicted transits with data are well covered"
        clauses.append([covered])
    for clause in (_fit_clause(data, tex), _odd_even_clause(data, tex), _secondary_clause(data, tex),
                   _stability_clause(data, failed, tex)):
        if clause:
            clauses.append(clause)
    _checked, hits = _catalogue(data)
    if hits:
        clauses.append([f"catalogue matches: {', '.join(hits)}"])
    clauses += [[text] for rung, text in failed.items() if rung != "rung3_stability"]
    parts: list = [f"{period_word(data)} d: "]
    for i, clause in enumerate(clauses):
        parts += (["; "] if i else []) + clause
    return _joined(tex, *parts, ".")


def ladder_sentence(datas: list[dict], tex: bool) -> str:
    """The ladder and the model clause once (C2): "SkySift vets each signal through a fixed ladder of checks; the shape
    fit is a quadratic limb-darkened model \\citep{claret2011} on a circular orbit, integrated over the 29.4-minute
    exposure." The clause is left out when no candidate has a shape fit."""
    shaped = [d for d in datas if pack.shape_sentence(d) is not None]
    if not shaped:
        return "SkySift vets each signal through a fixed ladder of checks."
    first = shaped[0]
    exposure = _num((first.get("shape") or {}).get("exposure_min")) or pack.KEPLER_EXPOSURE_MIN
    if any(pack.references(d)["claret2011"] for d in shaped):
        model = pack._model_words(tex, exposure)
        model[0] = model[0].removeprefix("Fitting ")
    else:
        model = [pack.LEGACY_MODEL_CLAUSE]
    return _joined(tex, "SkySift vets each signal through a fixed ladder of checks; the shape fit is ", *model, ".")


def catalogue_sentence(datas: list[dict]) -> str | None:
    """"Neither matches a Kepler KOI, DR25 TCE, TESS TOI or Kepler eclipsing binary entry." when both were searched and
    neither matched; None otherwise (a candidate that matches says so in its own sentence)."""
    checked = [_catalogue(d) for d in datas]
    if all(c for c, _hits in checked) and not any(hits for _c, hits in checked):
        return SOLE_CANDIDATE_TEXT
    return None


# ---------------------------------------------------------------------------------------------------- the pixels


def centroid_sentence(data: dict, tex: bool, *, after: dict | None = None) -> str:
    """Spec 5.1's per-candidate centroid sentence: "For the 15.11 d candidate, the difference-image centroid sits a
    median 0.16 pixels from this star and 2.47 from its brightest neighbour, nearer this star in 4 of 4 observing
    seasons (<the partial-box sentence, short>); against the star's out-of-transit image the offset is 0.54 ± 0.17
    arcsec (3.2 sigma), an offset by the rule of \\citet{bryson2013} (over 3 sigma and 0.2 arcsec); a two-source fit
    gives a median depth of 1,213 ppm on this star and 23 on the neighbour." Without a pixel verdict it gives the
    single note's no-verdict or not-run form. ``after`` is the candidate whose sentence came first: this one then
    says "that rule" and "the same boxes" when they are the first's."""
    row = _test(data, "rung5_pixels")
    voting, silent = pack._pixel_votes(row)
    name = f"For {_name(data)}, "
    if row is None or row.get("status") == "undetermined" or not voting:
        return _joined(tex, name, _lower(pack.pixel_sentence(data, tex)))
    near = sum(1 for s in voting if s.get("nearer") == "target")
    kind = "seasons that voted" if silent else "observing seasons"
    dt = [d for s in voting if (d := _num(s.get("dist_target_pix"))) is not None]
    dn = [d for s in voting if (d := _num(s.get("dist_neighbour_pix"))) is not None]
    first_row = _test(after, "rung5_pixels") if after is not None else None
    if dt and dn:
        centroid = "the centroid" if after is not None else "the difference-image centroid"
        parts: list = [name, (f"{centroid} sits a median {np.median(dt):.2f} pixels from this star and "
                              f"{np.median(dn):.2f} from its brightest neighbour, nearer this star in {near} of "
                              f"{len(voting)} {kind}")]
    else:
        parts = [name, (f"the difference-image centroid is nearer this star than its brightest neighbour in {near} "
                        f"of {len(voting)} {kind}")]
    cut = ds.partial_box_sentence(voting, short=True)
    if cut:
        same = after is not None and ds.partial_box_sentence(pack._pixel_votes(first_row)[0], short=True) == cut
        parts.append(" (the same boxes are cut)" if same else f" ({_unstop(_lower(cut))})")
    block = pack.offset_block(data)
    off, err, sigma = (_num((block or {}).get(k)) for k in ("self_referenced_arcsec", "self_referenced_err_arcsec",
                                                              "sigma"))
    if block and None not in (off, err, sigma):
        if pack.self_referenced(block):
            told = after is not None and pack.self_referenced(pack.offset_block(after))
            verdict = "an offset" if block.get("counts") else "not an offset"
            level, k = _num(block.get("level_arcsec")) or 0.2, _num(block.get("sigma_level")) or 3.0
            cite = Raw(r"\citet{bryson2013}") if tex else "Bryson et al. (2013)"
            rule = ["that rule"] if told else ["the rule of ", cite, f" (over {k:g} sigma and {level:g} arcsec)"]
            parts += ["; the offset is " if told else "; against the star's out-of-transit image the offset is ",
                      pm_text(off, err, err, tex=tex), f" arcsec ({sigma:.1f} sigma), {verdict} by ", *rule]
        else:
            reference = block.get("reference") or "the fitted position of the star"
            parts += [f"; against {reference} the offset is ", pm_text(off, err, err, tex=tex),
                      f" arcsec ({sigma:.1f} sigma), not assessed from the star's own image"]
    pairs = [(t, n) for s in voting
             if (t := pack._first(s.get("depth_target_ppm"))) is not None
             and (n := pack._first(s.get("depth_neighbour_ppm"))) is not None]
    if pairs:
        t, n = (float(np.median([p[i] for p in pairs])) for i in (0, 1))
        parts.append(f"; the two-source fit gives {t:,.0f} ppm on this star and {n:,.0f} on the neighbour"
                     if after is not None else
                     f"; a two-source fit gives a median depth of {t:,.0f} ppm on this star and {n:,.0f} on the "
                     "neighbour")
    parts.append(".")
    if silent:
        parts.append(f" {pack._silent_sentence(silent)}")
    if row.get("status") == "fail":
        parts.append(" The pixel check failed: the dip may sit on the neighbour.")
    return _joined(tex, *parts)


def photometry_sentence(datas: list[dict]) -> str:
    """The per-target sentences of the pixel section, once: the pointing-jitter floor of the centroid errors and the
    photometry ("The depths are KBonus PSF photometry, decontaminated to first order, with no crowding correction
    applied, with a CROWDSAP of 0.88 and a PSF flux fraction inside the aperture of 0.73."), the clause of
    ``pack.dilution_sentence`` without the SAP-depth ratio and the predicted leak (cuts C5)."""
    floors = [f for d in datas if (f := ds.floor_pix(pack._pixel_votes(_test(d, "rung5_pixels"))[0])) is not None]
    out = [f"The centroid errors carry a {pack.JITTER_FLOOR} of {max(floors):.3f} px."] if floors else []
    rows = {r["key"]: r for r in dilution_rows(datas[0])}
    tail = []
    if "crowdsap" in rows:
        tail.append(f"a CROWDSAP of {rows['crowdsap']['text']}")
    if "psffrac" in rows:
        tail.append(f"a PSF flux fraction inside the aperture of {rows['psffrac']['text']}")
    out.append(f"The depths are {ds.PSF_PHOTOMETRY}" + (f", with {' and '.join(tail)}" if tail else "") + ".")
    return " ".join(out)


# ---------------------------------------------------------------------------------------------------- the planets


def planet_sentence(data: dict, tex: bool, albedo: bool, *, bracket: bool = False) -> str | None:
    """"The 15.11 d candidate's radius is 2.85 (+0.19, -0.15) R_earth (radius ratio 0.0357), on an orbit of 0.1087 ±
    0.0018 AU at an equilibrium temperature of 536 K and 19.7 times Earth's insolation", with " at a Bond albedo of
    0.3" when ``albedo`` (said once, on the last); None when the star could not be characterised (the single note's
    "could not be characterised" sentence prints once instead) or the planet has no radius. ``bracket`` adds the
    radius bracket of the reddening (the star's, so the first candidate's alone)."""
    planet = data.get("planet") or {}
    rp = value_text(planet.get("rp_rearth"), tex)
    if not star_characterized(data) or rp is None:
        return None
    a, teq = value_text(planet.get("a_au"), tex), value_text(planet.get("teq_k"), tex)
    insolation = value_text(planet.get("insolation_earth"), tex)
    fit_k = _f(pack.fit_radius_ratio(data), 4)
    unit = Raw(r"$R_\oplus$") if tex else "Earth radii"
    parts: list = [f"The {period_word(data)} d candidate's radius is ", rp, " ", unit]
    if bracket and pack.rp_bracket_text(data, tex):
        parts.append(pack.rp_bracket_text(data, tex))
    if fit_k:
        parts.append(f" (ratio {fit_k})")
    if a:
        parts += [", on an orbit of ", a, " AU"]
    if teq and insolation:
        parts += [" at an equilibrium temperature of ", teq, " K and ", insolation, " times Earth's insolation"]
    elif teq:
        parts += [" at an equilibrium temperature of ", teq, " K"]
    elif insolation:
        parts += [" with ", insolation, " times Earth's insolation"]
    if albedo and teq:
        parts.append(" at a Bond albedo of 0.3")
    return _joined(tex, *parts, ".")


# ---------------------------------------------------------------------------------------------------- the probabilities


def _fap_calibrated(data: dict) -> bool:
    fap = data.get("fap") or {}
    return bool(fap.get("calibrated") and fap.get("n") is not None)


def _peaks(x: float) -> str:
    n = math.floor(x + 0.5)
    return "fewer than one" if n < 1 else "one" if n == 1 else f"{n:,}"


def fap_pair(datas: list[dict], tex: bool) -> str:
    """Spec 5.1's false-alarm sentence with both counts and both rates: "6 of 1,998 null searches of other KBonus stars
    reached the 15.11 d candidate's SDE and 16 of 1,998 the 47.69 d candidate's (1 in 290 and 1 in 120); across the
    1,579-star sweep about 6 and 13 such peaks are expected by chance, and the vetting ladder, not the SDE, separates
    the candidates from them." A candidate without a calibrated rate gets the single note's sentence on its own."""
    if not all(_fap_calibrated(d) for d in datas):
        return " ".join(f"For {_name(d)}: {fap_sentence(d, short=True)}" for d in datas)
    first, rest = datas[0], datas[1:]

    def count(d: dict) -> str:
        fap = d.get("fap") or {}
        return f"{fap.get('k')} of {fap['n']:,}"
    text = (f"{count(first)} null searches of other KBonus stars reached {_name(first)}'s SDE and "
            + " and ".join(f"{count(d)} {_name(d)}'s" for d in rest)
            + f" ({_and_list([(d.get('fap') or {}).get('words') for d in datas])})")
    sizes = {(d.get("calibration") or {}).get("sweep_size") for d in datas}
    size = sizes.pop() if len(sizes) == 1 else None
    values = [(d.get("fap") or {}).get("fap") for d in datas]
    if isinstance(size, int) and size > 0 and all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
        peaks = [_peaks(size * v) for v in values]
        about = "about " if all(p not in ("fewer than one",) for p in peaks) else ""
        text += (f"; across the {size:,}-star sweep {about}{' and '.join(peaks)} such peaks are expected by chance, "
                 "and the vetting ladder, not the SDE, separates the candidates from them")
    else:
        text += "; the vetting ladder, not the SDE, separates the candidates from such peaks"
    return text + "."


def _variant_ok(block: dict) -> bool:
    v = block.get("variant_excluded")
    return isinstance(v, dict) and not v.get("same_as_all")


def _no_cross_check(data: dict) -> str:
    stale = data.get("cross_check") and cc._current(data) is None
    return (f"the cross-check stored for {_name(data)} is not current" if stale
            else f"no cross-check is stored for {_name(data)}")


def triceratops_pair(datas: list[dict], tex: bool) -> str:
    """Spec 5.1's paired TRICERATOPS sentence: the method clause once, the numbers paired in a fixed order (median FPP;
    without STP; pixel-excluded, and without STP; NFPP; runs), closing with the clause that the pixel-excluded values
    rest on our own difference-image centroids and the all-neighbours values on a crowding bound (C13). When only one
    candidate has a current cross-check the sentence names it and says "no cross-check is stored for the 47.69 d
    candidate"; with none it is empty. When a candidate's pixels exclude no neighbour the sentence is the single
    note's for each candidate."""
    current = [(d, cc._current(d)) for d in datas]
    have = [(d, b) for d, b in current if b is not None]
    if not have:
        return ""
    missing = _and_list([_no_cross_check(d) for d, b in current if b is None])
    blocks = [b for _d, b in have]
    if not all(_variant_ok(b) for b in blocks):
        joined: list = []
        for d, _b in have:
            joined += [" " if joined else "", f"For {_name(d)}: ", pack.cross_check_sentence(d, tex)]
        return _joined(tex, *joined, f" {missing[:1].upper()}{missing[1:]}." if missing else "")
    one = len(blocks) == 1
    variants = [b["variant_excluded"] for b in blocks]
    removed = [len(v.get("removed") or []) for v in variants]
    phrase = ("the one neighbour the difference images exclude" if removed == [1] * len(removed)
              else f"the {_pair([str(n) for n in removed])} neighbours the difference images exclude")

    def values(key: str, source: list[dict]) -> str:
        return " and ".join(cc._p(x[key]) for x in source)
    versions = [str(b["tool_version"]) for b in blocks]
    head = "TRICERATOPS " + (f"{' and '.join(versions)} " if len(set(versions)) > 1 else "")
    who = f" {_name(have[0][0])} a" if one else ""
    kind, nouns = ("probability", "value") if one else ("probabilities", "values")
    rest = (f", run on the KBonus light curve with Kepler limb darkening and Gaia DR3 field stars, gives{who} median "
            f"false-positive {kind} of {values('fpp', blocks)} ({values('fpp_no_stp', blocks)} without a planet "
            f"transiting an unresolved bound companion, STP, absent from SkySift's model; {values('fpp', variants)}, "
            f"and {values('fpp_no_stp', variants)} without STP, with {phrase} removed) and {'a ' if one else ''}nearby "
            f"false-positive {kind} of {values('nfpp', blocks)} over {_pair([str(b['n_runs']) for b in blocks])} runs "
            f"({cc.NO_CLASS_SHORT}); the pixel-excluded {nouns} rest{'s' if one else ''} on our own difference-image "
            f"centroids, the all-neighbours {nouns} on a crowding bound" + (f"; {missing}" if missing else "") + ".")
    return _joined(tex, head, Raw(r"\citep{giacalone2021}") if tex else cc.CITATION, rest)


def _in_house_value(data: dict) -> str | None:
    fpp = data.get("fpp") or {}
    value = fpp.get("value")
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    return None if fpp.get("neighbours_known") is False or not ok else cc.probability_text(value)


def _provisional(datas: list[dict]) -> str:
    return "" if all((d.get("fpp") or {}).get("signed_off") for d in datas) else PROVISIONAL


def in_house_pair(datas: list[dict]) -> str:
    """The in-house estimate once with both values: "A four-scenario estimate without a bound-companion or
    diluted-planet scenario gives 0.0078 and 0.11, lower bounds." (provisional until the factor tables are signed
    off); a candidate whose estimate is not computed gets the single note's wording on its own."""
    values = [_in_house_value(d) for d in datas]
    if None in values:
        return " ".join(f"For {_name(d)}, {pack.in_house_text(d)}." for d in datas)
    return ("A four-scenario estimate without a bound-companion or diluted-planet scenario gives "
            f"{' and '.join(values)}, lower bounds{_provisional(datas)}.")


def tess_pair(datas: list[dict], tex: bool) -> str:
    """Spec 5.1's TESS sentence once, with the sector count and both significance ranges, citing Christ et al. 2019
    once: "TESS observed the star in 10 sectors; the predicted folded significances of 0.46 to 0.93 and 0.25 to 0.54
    sigma mean no detection is expected at the nominal noise; no TESS data were examined." When the candidates'
    sector lists or verdicts differ, or no prediction was made, it is the single note's sentence for each."""
    blocks = [d.get("tess") for d in datas]
    if all(tess_module.note_cites(b) and tess_module._short_sentence(b) is not None for b in blocks):
        sectors = {(len(b["sectors"]), tuple(b.get("sectors_skipped") or []), b["verdict"]) for b in blocks}
        if len(sectors) == 1:
            k, skipped = len(blocks[0]["sectors"]), blocks[0].get("sectors_skipped") or []
            gone = (f" (Sector{'s' if len(skipped) > 1 else ''} {tess_module._and(skipped)} not assessed)"
                    if skipped else "")
            ranges = " and ".join(f"{tess_module._s(b['significance']['folded_conservative'])} to "
                                  f"{tess_module._s(b['significance']['folded_nominal'])}" for b in blocks)
            verdict = tess_module.NOTE_VERDICTS.get(blocks[0]["verdict"],
                                                    tess_module.VERDICT_PHRASES[blocks[0]["verdict"]])
            text = (f"TESS observed the star in {k} sector{'s' if k != 1 else ''}{gone}; the predicted folded "
                    f"significances of {ranges} sigma mean {verdict}{tess_module.NO_DATA_EXAMINED}")
            return _joined(tex, text, Raw(r" \citep{christ2019}.") if tex else " (Christ et al. 2019).")
    texts = [pack.tess_text(d, tex) for d in datas]
    if len({str(t) for t in texts}) == 1:
        return texts[0]
    joined: list = []
    for d, t in zip(datas, texts, strict=True):
        joined += [" " if joined else "", f"For {_name(d)}: ", t]
    return _joined(tex, *joined)


def caveat_union(datas: list[dict]) -> str:
    """The union of both candidates' caveat titles, each once, in the first's order and then the second's new ones,
    under the single note's rules (a caveat the body states, and the candidate caveat, are left out), then "The two
    candidates were vetted singly; no joint fit was made."."""
    titles: list[str] = []
    for d in datas:
        star = d.get("star") or {}
        for c in d.get("caveats") or []:
            if not (isinstance(c, dict) and c.get("title")) or c.get("key") in pack.NOTE_STATED_CAVEATS \
                    or c.get("title") == ds.CANDIDATE_CAVEAT:
                continue
            text = f"{pack._caveat_title(c, star, flag=False).rstrip('.')}."
            if text not in titles:
                titles.append(text)
    return " ".join([*titles, VETTED_SINGLY])


# ---------------------------------------------------------------------------------------------------- abstract, caption


def _pixel_kind(data: dict) -> tuple:
    """How a candidate's pixel check stands, to tell whether one abstract sentence covers both: ("none",), ("open",),
    ("fail",) or ("pass", near, voting seasons, silent seasons)."""
    row = _test(data, "rung5_pixels")
    voting, silent = pack._pixel_votes(row)
    if row is None:
        return ("none",)
    if row.get("status") == "undetermined" or not voting:
        return ("open",)
    if row.get("status") == "fail":
        return ("fail",)
    return ("pass", sum(1 for s in voting if s.get("nearer") == "target"), len(voting), bool(silent))


def _seasons_kind(silent: bool) -> str:
    return "seasons that voted" if silent else "observing seasons"


def _pixel_abstract(datas: list[dict]) -> str:
    kinds = [_pixel_kind(d) for d in datas]
    if len(set(kinds)) == 1:
        kind = kinds[0]
        if kind[0] == "none":
            return "The pixel check has not run, so a neighbouring star could still cause either dip."
        if kind[0] == "open":
            return "The pixel check gave no verdict on which star dims either dip."
        if kind[0] == "fail":
            return "In difference images both dips are nearer a neighbouring star than this one."
        return ("In difference images both dips are nearer this star than its brightest neighbour in "
                f"{kind[1]} of {kind[2]} {_seasons_kind(kind[3])}.")
    out = []
    for d, kind in zip(datas, kinds, strict=True):
        if kind[0] == "none":
            out.append(f"The pixel check has not run for the {period_word(d)} d dip, so a neighbouring star could "
                       "still cause it.")
        elif kind[0] == "open":
            out.append(f"The pixel check gave no verdict on which star dims the {period_word(d)} d dip.")
        elif kind[0] == "fail":
            out.append(f"In difference images the {period_word(d)} d dip is nearer a neighbouring star than this "
                       "one.")
        else:
            out.append(f"In difference images the {period_word(d)} d dip is nearer this star than its brightest "
                       f"neighbour in {kind[1]} of {kind[2]} {_seasons_kind(kind[3])}.")
    return " ".join(out)


def _radii_abstract(datas: list[dict]) -> str | None:
    radii = []
    for d in datas:
        rp = (d.get("planet") or {}).get("rp_rearth")
        radii.append(f"{_num(rp['median']):.1f}" if isinstance(rp, dict) and _num(rp.get("median")) is not None
                     else None)
    if all(radii):
        return f"The planets would be about {' and '.join(radii)} Earth radii."
    return " ".join(f"The {period_word(d)} d planet would be about {r} Earth radii." for d, r in
                    zip(datas, radii, strict=True) if r) or None


def _fap_abstract(datas: list[dict]) -> str:
    if all(_fap_calibrated(d) for d in datas):
        words = " and ".join((d.get("fap") or {}).get("words") for d in datas)
        return f"Null searches give false-alarm rates of {words} per star"
    return "; ".join(pack._capital(pack.fap_rate_clause(d)) if len(datas) == 1 else
                     f"For the {period_word(d)} d candidate, {pack.fap_rate_clause(d)}" for d in datas)


def _tric_abstract(datas: list[dict]) -> str | None:
    current = [(d, cc._current(d)) for d in datas]
    have = [(d, b) for d, b in current if b is not None]
    if not have:
        return None
    versions = list(dict.fromkeys(str(b["tool_version"]) for _d, b in have))
    tool = f"TRICERATOPS {' and '.join(versions)}"
    if len(have) == 2:
        both = " and ".join(cc._p(b["fpp"]) for _d, b in have)
        return f"{tool} gives false-positive probabilities of {both} ({cc.NO_CLASS_SHORT}; Giacalone et al. 2021)"
    d, b = have[0]
    which, other = ("first", "other") if d is datas[0] else ("second", "other")
    return (f"{tool} gives a false-positive probability of {cc._p(b['fpp'])} for the {which} and none for the "
            f"{other} (no validation class applies; Giacalone et al. 2021)")


def abstract(datas: list[dict]) -> str:
    """Spec 5.1's abstract for two candidates, at most 130 words: one opening sentence naming the star and both periods;
    one pixel sentence for both when their verdicts agree, one each when they differ; the planets' radii in one
    sentence; the false-alarm rates; the TRICERATOPS headline with the two medians (C19), then the in-house values in
    spec 11.2's order; "Both remain candidates pending follow-up." The pixel-excluded values, the sweep-wide
    expectation and the partial-box clause are the body's (C19 to C21)."""
    name = _target_name(datas)
    claims = [c for d in datas if (cs := pack.prior_claims(d)) for c in cs[:1]]
    authors = list(dict.fromkeys(c["author"] for c in claims))
    where = f"{name}, a star Kepler recorded only in other targets' pixels"
    if claims:
        opening = (f"This note vets two transit candidates on {where}, {'' if len(claims) == 2 else 'one '}"
                   f"first reported by {_and_list(authors)}: a ")
    else:
        opening = f"This note reports two transit candidates on {where}: a "

    def dip(d: dict, unit: str = " transits") -> str:
        eph = d.get("ephemeris") or {}
        return (f"{_f(eph.get('duration_h'), 2)} hour, {_f(eph.get('depth_ppm'), 0, True)} ppm dip every "
                f"{quantities(d)['period']} days over {(d.get('ephemeris') or {}).get('n_events')}{unit}")
    parts = [f"{opening}{dip(datas[0])} and a {dip(datas[1], '')}.", _pixel_abstract(datas)]
    radii = _radii_abstract(datas)
    if radii:
        parts.append(radii)
    tric = _tric_abstract(datas)
    values = [_in_house_value(d) for d in datas]
    if None in values:
        house = "; ".join(f"for {_name(d)}, {pack.in_house_text(d)}" for d in datas)
    else:
        house = ("four-scenario estimates without a bound-companion or diluted-planet scenario give "
                 f"{' and '.join(values)}, lower bounds{_provisional(datas)}")
    parts.append(f"{_fap_abstract(datas)}; {tric}; {house}." if tric else f"{_fap_abstract(datas)}; {house}.")
    parts.append("Both remain candidates pending follow-up.")
    return " ".join(parts)


def caption(datas: list[dict], panels: bool) -> str:
    """The figure's caption: "(a) the light curve folded on the 15.113696 day linear-fit ephemeris and (b) on the
    47.69057 day one, within 12.0 and 16.2 hours of mid-transit, in parts per million: bins of 22.5 and 30.4 minutes
    with standard errors (dots) and the transit fits (lines)." Without ``panels`` (the SVG fold of the first
    candidate alone) it is the single note's caption, with a sentence for the second candidate's own file."""
    a, b = datas[0], datas[1]
    pa, pb = quantities(a)["period"], quantities(b)["period"]
    fits = [(d.get("plots") or {}).get("model_kind") == "fit" for d in datas]
    if not panels:
        model = "transit fit" if fits[0] else "detection's box model"
        return (f"The light curve folded on the {pa} day {pack.fold_words(a)}, within {_show(pack.window_hours(a))} "
                f"hours of mid-transit, in parts per million: bins of {_show(pack.bin_minutes(a))} minutes with "
                f"standard errors (dots) and the {model} (line). The {pb} day candidate's fold is in figure1_02.")
    window = _pair([_show(pack.window_hours(d)) for d in datas])
    bins = _pair([_show(pack.bin_minutes(d)) for d in datas])
    model = "transit fits" if all(fits) else "detection's box models" if not any(fits) else "models"
    second = "one" if pack.fold_words(a) == pack.fold_words(b) else pack.fold_words(b)
    return (f"(a) the light curve folded on the {pa} day {pack.fold_words(a)} and (b) on the {pb} day {second}, "
            f"within {window} hours of mid-transit, in parts per million: bins of {bins} minutes with standard "
            f"errors (dots) and the {model} (lines).")


def summary(datas: list[dict]) -> str:
    """One sentence for the README's top: "Two dips, of 0.11 and 0.092 percent, repeat every 15.1 and 47.7 days on a
    star of magnitude 16.2." (the detector's depths and periods, as the dossier's own summary reads them)."""
    sigs = [d.get("signal") or {} for d in datas]
    depths = [_num(s.get("depth_ppm")) for s in sigs]
    periods = [_num(s.get("period")) for s in sigs]
    gmag = _num((datas[0].get("target") or {}).get("gmag"))
    percent = " and ".join(f"{x / 1e4:.2g}" for x in depths) if None not in depths else None
    dips = "Two dips" + (f", of {percent} percent," if percent else "")
    every = f" repeat every {' and '.join(f'{p:.3g}' for p in periods)} days" if None not in periods else " repeat"
    star = (f" on a star of magnitude {gmag:.1f}" if gmag is not None
            else f" on {(datas[0].get('target') or {}).get('target_id')}")
    return f"{dips}{every}{star}."


# ---------------------------------------------------------------------------------------------------- shared


def _coincidence(datas: list[dict], span: tuple[float, float] | None) -> str | None:
    if span is None:
        return None
    short, long_ = sorted(datas[:2], key=lambda d: _period(d) or 0.0)
    try:
        count, n_long = pack.coincident_transits(short["ephemeris"], long_["ephemeris"], span[0], span[1])
    except (KeyError, TypeError, ValueError):
        return None
    return coincidence_sentence(count, n_long, period_word(short), period_word(long_), note=True)


def shared(datas: list[dict], tex: bool, span: tuple[float, float] | None, panels: bool = True) -> dict:
    """What ``note_two.tex.j2`` prints beside the per-candidate numbers: the title, the ratio and coincidence sentences,
    the signal, vetting and centroid sentences per candidate, the planet sentences, the probability sentences, the
    caveats, the caption, the abstract, the ladder sentence, the catalogue sentence (None when a candidate matches
    one), the photometry sentences and the fold clause. ``span`` is the light curve's (min, max) in BKJD, which the
    coincidence count runs on; without it that sentence is left out."""
    periods = [_period(d) for d in datas]
    signals, told = [], set()
    for i, d in enumerate(datas):
        sentence = signal_sentence(d, tex, after=datas[i - 1] if i else None)
        new = [c for c in d.get("prior_claims") or [] if isinstance(c, dict) and c.get("doi") not in told]
        told.update(c.get("doi") for c in new)
        bare = [{**c, "development_note": "", "development_note_short": ""} for c in new]
        prior = pack.prior_claim_sentence({**d, "prior_claims": bare}, tex)
        signals.append(_joined(tex, sentence, " ", prior) if prior else sentence)
    catalogue = catalogue_sentence(datas) if CATALOGUE_IN_NOTE else None
    vettings = [vetting_sentence(d, tex) for d in datas]
    checked = [_catalogue(d) for d in datas]
    if catalogue is None and any(hits for _c, hits in checked):
        vettings = [v if (hits or not c) else _joined(tex, v, " ", NO_MATCH_TEXT)
                    for v, (c, hits) in zip(vettings, checked, strict=True)]
    centroids = [centroid_sentence(d, tex, after=datas[i - 1] if i else None) for i, d in enumerate(datas)]
    with_teq = [i for i, d in enumerate(datas) if value_text((d.get("planet") or {}).get("teq_k"))]
    planets = [p for i, d in enumerate(datas)
               if (p := planet_sentence(d, tex, albedo=ALBEDO_IN_NOTE and bool(with_teq) and i == with_teq[-1],
                                        bracket=i == 0))]
    return {"title": title(datas),
            "ratio": ratio_sentence(min(periods), max(periods)),
            "coincidence": _coincidence(datas, span),
            "signals": signals, "vettings": vettings, "centroids": centroids, "planets": planets,
            "fap": fap_pair(datas, tex), "triceratops": triceratops_pair(datas, tex),
            "in_house": in_house_pair(datas), "tess": tess_pair(datas, tex),
            "caveats": caveat_union(datas), "caption": caption(datas, panels), "abstract": abstract(datas),
            "ladder": ladder_sentence(datas, tex), "catalogue": catalogue,
            "photometry": photometry_sentence(datas), "fold_clause": fold_clause(datas)}
