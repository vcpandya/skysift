"""Stored rows in, plain words out: the verdict, one sentence per check with its deciding numbers, what an
undetermined check was missing and what happens next. The single source for the popup, the cards, the signal page and
the About page (spec sections 4.1, 4.2 and 5.4). Pure: no store, no network, no templates.

Thresholds are not stored on the test rows and a sweep may override them, so a sentence that has no limit in its reason
string quotes the configured thresholds (``configure``, called by ``create_app`` with ``settings.thresholds``; the
defaults until then) as "the usual" limit, never as the one the run used."""
from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime

from skysift.config import Thresholds
from skysift.store.db import ENGINE_STATUSES, REVIEWER_DECIDED_REASON

LADDER_ORDER = ["rung1_detection", "rung1_events", "rung2_shape", "rung3_stability", "rung4_novelty", "rung5_pixels"]
RUNG_NAMES = {"rung1_detection": "Detected above the noise", "rung1_events": "Seen in every transit with data",
              "rung2_shape": "Transit shape", "rung3_stability": "Stable across quarter groups and apertures",
              "rung4_novelty": "Not already known", "rung5_pixels": "On this star, not a neighbour"}

STATUS_GLOSSARY = [
    ("Candidate, stage 1", "Every light-curve check reached a verdict and all of them passed. The engine decides."),
    ("Candidate, stage 2", ("Stage 1, and the pixel checks show the dip on this star and not on a neighbour. "
                            "The engine decides.")),
    ("Undetermined", ("No check failed, but at least one could not reach a verdict. The popup names it and says what "
                      "happens next. The engine decides, then the recheck.")),
    ("Rejected", "A check failed. The popup names the check and the number that failed it. The engine decides."),
    ("Approved / Rejected by reviewer / Needs data", ("A person's decision, recorded with a note, on top of the "
                                                      "engine's status. A person decides.")),
]
GLOSSARY = {
    "SDE": "Signal detection efficiency: how far the strongest period in the search stands above the others. "
           "Higher is stronger.",
    "SNR": "Signal-to-noise ratio: the depth of the dip divided by the scatter of the light curve around it.",
    "odd/even": "The odd-numbered and the even-numbered transits, measured apart. A planet makes them equally deep; "
                "two stars eclipsing each other often do not.",
    "secondary eclipse": "A second, shallower dip half an orbit after the first. It is the mark of a binary star, or "
                         "rarely of a hot planet passing behind its star.",
    "pre-whitening": "Removing the star's own regular brightness waves before the search, so they cannot pose as "
                     "transits.",
    "season": "Kepler rolled a quarter turn every three months, so a star fell on one of four detectors. A season is "
              "the set of quarters on the same detector.",
    "aperture": "The set of pixels whose light is added up to make the light curve. Two apertures should give the "
                "same depth for a dip on this star.",
    "MS2": "Model-shift secondary significance (Kepler DV): how close the strongest other dip in the fold comes to the "
           "transit's own significance.",
    "MS3": "Model-shift positive-event significance (Kepler DV): how close the strongest brightening in the fold comes "
           "to the transit's own significance.",
    "Chases": "The local uniqueness of each event: how far its dip stands above other dips close to it in time.",
    "BKJD": "Barycentric Kepler Julian Date: the Kepler mission's clock, the Julian date minus 2,454,833.",
    "ppm": "Parts per million of the star's light. A dip of 1,000 ppm is 0.1 percent.",
    "impact parameter": "How far from the centre of the star the planet crosses, from 0 (dead centre) to about 1 "
                        "(the edge). Above 1 the crossing is grazing.",
    "False alarm": "How often noise alone, on light curves like this one, gives a peak at least as strong as the "
                   "candidate's, written as 1 in N. It comes from the calibration searches on flipped and shuffled "
                   "light curves, and it says nothing about a real dip on another star.",
    "False positive": "Given that the dip is real, how likely it is to be something other than a planet on this star, "
                      "such as an eclipsing binary on the star or behind it. An approximate count of the common "
                      "impostors with simple priors, shown apart from the false alarm and never combined with it.",
}

REVET = "re-vet under the current ladder"
HALVE = "re-vet at the suggested period"
PIXELS = "pixel stage"
RETRY = "retry the catalogue"
PERSON = "needs a person"
PERSON_DECIDES = "a person decides"
REVET_CRASH = "re-vet under the current ladder; stays flagged for a person if it crashes again"
FAILED = "no recheck: a failed check is final"
# the pixel stage runs once per signal (an archive outage aside), so what its own "no verdict" leaves is for a person
PIXELS_SAID = "a person reads the pixel figures"
PIXELS_NONE = "a person decides without the pixels"
PIXELS_RETRY = "ask the pixel archive again"

# (matcher, in words, next step), first match wins, in the order of the spec 4.2 table. A named group ``detail`` is the
# part of the reason that carries its numbers; ``why`` adds it to the sentence.
REASONS: list[tuple[re.Pattern, str, str]] = [(re.compile(p), w, n) for p, w, n in [
    (r"^too few (odd/even )?events", "Each transit has too few points for this test", REVET),
    (r"^too few seasons", "Fewer than three seasons have data", REVET),
    (r"^equal secondary at phase 0\.5(?:.*\((?P<detail>[^)]*)\))?",
     "The dip at phase 0.5 matches the main one, so the real period is probably half of this", HALVE),
    (r"^(season|aperture)-dependent depth(?: \((?P<detail>[^)]*)\))?",
     "The depth changes with the detector or the aperture, which a real transit does not do", PIXELS),
    (r"^systematic_alias", "The period sits on a known spacecraft rhythm", PIXELS),
    (r"^outage:\s*(?P<detail>mast\b.*)", "The pixel archive did not answer when we asked", PIXELS_RETRY),
    (r"^outage:\s*(?P<detail>.*)", "A catalogue did not answer when we asked", RETRY),
    (r"^schema:(?P<detail>.*)", "A catalogue answered in a form we could not read", RETRY),
    (r"^period matches (?P<detail>.+?) but epoch differs",
     "A catalogue has this period on a neighbour, but its transits fall at a different time", PIXELS),
    (r"^exception:\s*(?P<detail>.*)", "The check crashed", REVET_CRASH),
    (r"^free_secondary_undetermined", "The search for a second dip away from phase 0.5 had too few points", PERSON),
    (r"^pixels: (?P<detail>\d+ usable transits? < \d+)", "The pixels could not show which star the dip is on",
     PIXELS_SAID),
    (r"^pixels: no season holds both stars on the frame",
     "No season's pixel stamp holds both this star and the neighbour, so the dip cannot be placed between them",
     PIXELS_SAID),
    (r"^pixels: no season locali[sz]es the dip",
     "No season's difference image places the dip well enough to vote on which star dims", PIXELS_SAID),
    (r"^pixels: (?P<detail>season \d+ ambiguous.*)",
     "In one season the centroid of the lost light sits too close to the line between the two stars to call",
     PIXELS_SAID),
    (r"^pixels: only one season could vote \((?P<detail>.*)\)$",
     "Only one observing season could vote on which star dims, and one is too few", PERSON_DECIDES),
    (r"^pixels: (?P<detail>season \d+ disagrees \(neighbour depth.*)",
     "The neighbour's own depth vetoes one season's vote", PIXELS_SAID),
    (r"^pixels: (?P<detail>season \d+ disagrees.*)",
     "One season does not agree with the others on which star dims", PIXELS_SAID),
    (r"^pixels: no pixel files for this star",
     "The archive has no pixel files for the Kepler star whose pixels hold this one", PIXELS_NONE),
    (r"^pixels: (?P<detail>no readable pixel files.*)",
     "None of the archive's pixel files for this star could be read", PIXELS_NONE),
    (r"^pixels: no neighbour to test against",
     "This star is itself the Kepler target, so there is no neighbour in its pixels to test the dip against",
     PIXELS_NONE),
    (r"^pixels:\s*(?P<detail>.*)", "The pixels could not show which star the dip is on", PIXELS_SAID),
    (r"^the dip is on a neighbouring star(?P<detail>.*)", "The pixels put the dip on a neighbouring star", FAILED),
]]
_TH = Thresholds()


def configure(thresholds: Thresholds) -> None:
    """Quote ``thresholds`` (the dashboard's settings, the ones the About page shows) as the usual limits."""
    global _TH
    _TH = thresholds


def _f(x: float) -> str:
    return f"{x:g}"


# (matcher, sentence from the match) for the reasons a check fails with; the numbers come from the reason itself
FAILS: list[tuple[re.Pattern, Callable[[re.Match], str]]] = [(re.compile(p), w) for p, w in [
    (r"^sde ([-\d.]+) < ([-\d.]+)", lambda g: f"SDE {g[1]} is below the threshold of {_f(float(g[2]))}"),
    (r"^snr ([-\d.]+) < ([-\d.]+)", lambda g: f"SNR {g[1]} is below the threshold of {_f(float(g[2]))}"),
    (r"^n_events (\d+) < (\d+)", lambda g: f"Only {g[1]} transits, fewer than the {g[2]} needed"),
    (r"^duration ratio ([-\d.]+)", lambda g: f"The dip lasts {g[1]} times the expected duration, outside the usual "
                                             f"range of {_f(_TH.duration_ratio_min)} to {_f(_TH.duration_ratio_max)}"),
    (r"^gapped events \((\d+) of (\d+)", lambda g: f"Only {g[1]} of {g[2]} predicted transits have data"),
    (r"^n_good (\d+) < min_events (\d+)", lambda g: f"Only {g[1]} transits are clean, fewer than the {g[2]} needed"),
    (r"^single event dominates \(([-\d.]+)", lambda g: f"One transit carries most of the signal ({g[1]} of it)"),
    (r"^chases median ([-\d.]+) < ([-\d.]+)",
     lambda g: f"The transits sit among dips as strong as themselves (median {g[1]}, limit {g[2]})"),
    (r"^sinusoidal", lambda g: "A sine wave fits the fold as well as a transit does"),
    (r"^ellipsoidal", lambda g: "The star brightens and fades with the period, as a close binary does"),
    (r"^not unique \(MS2 ([-\d.]+) > ([-\d.]+)",
     lambda g: f"Another dip in the fold is about as strong as the transit (MS2 {g[1]}, limit {g[2]})"),
    (r"^positive event .*\(MS3 ([-\d.]+) > ([-\d.]+)",
     lambda g: f"A brightening in the fold is as strong as the transit (MS3 {g[1]}, limit {g[2]})"),
    (r"^odd_even ([-\d.]+) sigma", lambda g: f"Odd and even transits differ by {g[1]} sigma, above the usual limit "
                                             f"of {_f(_TH.parity_sigma)}"),
    (r"^secondary_at_0\.5 ([-\d.]+) sigma",
     lambda g: f"A second dip at phase 0.5 stands out at {g[1]} sigma, the mark of an eclipsing binary"),
    (r"^eccentric_secondary", lambda g: "A second dip away from phase 0.5 stands out, the mark of an eclipsing binary"),
    (r"^V-shaped or deep \(k \+ b = ([-\d.]+) > ([-\d.]+)",
     lambda g: f"The dip is V-shaped, as a grazing eclipse is (k + b = {g[1]}, limit {g[2]})"),
    (r"^known:([^:]+):(.+)", lambda g: f"Already known: {g[2]} in {g[1]}"),
    (r"^the dip is on a neighbouring star \(([-\d.]+) sigma, (\d+)/(\d+) seasons\)",
     lambda g: f"The pixels put the dip on a neighbouring star in {g[2]} of {g[3]} seasons ({g[1]} sigma)"),
    (r"^the dip is on a neighbouring star(.*)", lambda g: f"The pixels put the dip on a neighbouring star{g[1]}"),
]]
# (matcher, sentence) for the reasons a check passes with (only the pixel rung gives one)
PASSES: list[tuple[re.Pattern, Callable[[re.Match], str]]] = [(re.compile(p), w) for p, w in [
    (r"^(?:pixels: )?the dip is on the target in (\d+)/(\d+) seasons \(centroid >= ([-\d.]+) sigma nearer\)",
     lambda g: f"The dip is on this star in {g[1]} of {g[2]} seasons, the centroid at least {g[3]} sigma nearer it "
               "than the neighbour"),
]]


@dataclass(frozen=True)
class Why:
    rung: str
    name: str
    status: str
    sentence: str
    numbers: str
    missing: str | None
    next_step: str | None
    when: str | None
    recheck_id: int | None = None       # the recheck step that wrote the row, None for a sweep's row


@dataclass(frozen=True)
class Verdict:
    word: str
    stage: str | None
    tone: str
    headline: str
    reviewer: str | None


def _match_reason(reason: str) -> tuple[re.Match, str, str] | None:
    for pattern, words, nxt in REASONS:
        m = pattern.match(reason or "")
        if m:
            return m, words, nxt
    return None


def reason_in_words(reason: str) -> tuple[str, str]:
    """(what was missing, what happens next) for an undetermined reason; the raw reason and "needs a person" when no
    prefix of the spec 4.2 table matches."""
    hit = _match_reason(reason)
    return (hit[1], hit[2]) if hit else (reason, PERSON)


def _num(m: dict, key: str) -> float | None:
    v = m.get(key)
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
        return None
    return float(v)


def _ppm(x: float | None) -> str:
    return "?" if x is None else f"{x:,.0f}"


def _join(parts: list[str | None], sep: str = ", ") -> str:
    return sep.join(p for p in parts if p)


def _detection(m: dict) -> tuple[str, str]:
    sde, snr, n, ratio = (_num(m, k) for k in ("sde", "snr", "n_events", "duration_ratio"))
    if sde is None:
        sentence = "The detection strength was not recorded"
    elif sde < _TH.sde_min:      # passed under an older, lower threshold: the configured one would mislead
        sentence = f"SDE {sde:.1f}, above the threshold that run used"
    else:
        sentence = f"SDE {sde:.1f}, above the usual minimum of {_f(_TH.sde_min)}"
    return sentence, _join([sde is not None and f"SDE {sde:.1f}", snr is not None and f"SNR {snr:.1f}",
                            n is not None and f"{n:.0f} transits",
                            ratio is not None and f"duration {ratio:.2f} of expected"])


def _events(m: dict) -> tuple[str, str]:
    n, covered, good = (_num(m, k) for k in ("n_events_with_data", "n_covered", "n_good"))
    dom, chases, duty = (_num(m, k) for k in ("ses_max_over_mes", "chases_median", "duty_cycle"))
    sentence = (f"{covered:.0f} of {n:.0f} predicted transits have data" if n is not None and covered is not None
                else "Per-transit coverage was not recorded")
    return sentence, _join([covered is not None and n is not None and f"covered {covered:.0f} of {n:.0f}",
                            good is not None and f"clean {good:.0f}", duty is not None and f"duty cycle {duty:.2f}",
                            dom is not None and f"largest event {dom:.2f} of the total",
                            chases is not None and f"Chases median {chases:.2f}"])


def _shape(m: dict) -> tuple[str, str]:
    odd, even, par = _num(m, "odd_ppm"), _num(m, "even_ppm"), _num(m, "parity_sigma")
    sec, sec_s = _num(m, "secondary_ppm"), _num(m, "secondary_sigma")
    free_s, free_p = _num(m, "free_secondary_sigma"), _num(m, "free_secondary_phase")
    box, sine, k, b = (_num(m, key) for key in ("box_chi2", "sine_chi2", "fit_k", "fit_b"))
    parts = [par is not None and f"Odd and even transits agree within {par:.1f} sigma"]
    if m.get("occultation_exception"):
        parts.append("a shallow second dip fits the planet's own occultation")
    elif sec_s is not None:
        parts.append(f"no second dip shows at phase 0.5 ({sec_s:.1f} sigma)")
    sentence = _join(parts, " and ") or "The transit shape was not recorded"
    numbers = _join([par is not None and f"odd {_ppm(odd)} ppm, even {_ppm(even)} ppm, {par:.1f} σ apart",
                     sec_s is not None and f"phase 0.5: {_ppm(sec)} ppm, {sec_s:.1f} σ",
                     free_s is not None and free_p is not None and f"free search: {free_s:.1f} σ at phase {free_p:.2f}",
                     box is not None and sine is not None and f"box χ² {box:,.0f} against sine {sine:,.0f}",
                     k is not None and b is not None and f"fit k {k:.3f}, b {b:.2f}",
                     _num(m, "suggested_period") is not None and f"half period {_num(m, 'suggested_period'):g} d"],
                    "; ")
    return sentence[:1].upper() + sentence[1:], numbers


def _stability(m: dict) -> tuple[str, str]:
    depths, errs = m.get("season_depths") or {}, m.get("season_errs") or {}
    p, chi2, dof = _num(m, "p_value"), _num(m, "chi2"), _num(m, "dof")
    ratio = _num(m, "sap_psf_ratio") if _num(m, "aperture_ratio") is None else _num(m, "aperture_ratio")
    az = _num(m, "aperture_z")
    parts = [f"Depth agrees across {len(depths)} quarter groups (p = {p:.2g})" if depths and p is not None
             else "Depths per quarter group were not recorded"]
    if ratio is not None:           # spec 11.5: the ratio itself, not a claim that the apertures agree
        parts.append(f"the SAP depth is {ratio:.2f} of the PSF depth" + (f", {abs(az):.1f} sigma apart"
                                                                         if az is not None else ""))
    # rung 3 groups the quarters by quarter mod 4, which is not the archive SEASON rung 5 labels its seasons by, so
    # its groups are "quarter group N" wherever rung 5's are "archive season N"
    seasons = [f"quarter group {s}: {_ppm(_num(depths, s))}" + (f" ± {_ppm(_num(errs, s))}" if s in errs else "")
               + " ppm" for s in sorted(depths, key=str)]
    fit = chi2 is not None and dof is not None and p is not None and f"χ² {chi2:.1f} on {dof:.0f} dof, p = {p:.2g}"
    aperture = ratio is not None and f"aperture ratio {ratio:.2f}" + (f", {az:.1f} σ" if az is not None else "")
    return _join(parts, " and "), _join([*seasons, fit, aperture], "; ")


CATALOGUES = ("cumulative", "q1_q17_dr25_tce", "toi", "kepler_eb", "gaia_nss_two_body_orbit",
              "gaia_vari_eclipsing_binary")


def _novelty(m: dict) -> tuple[str, str]:
    asked = [c for c in CATALOGUES if _num(m, c) is not None]
    others = m.get("other_period_objects") or []
    sentence = f"No match in {len(asked)} catalogue{'s' if len(asked) != 1 else ''}"
    if others:
        sentence += f"; {len(others)} nearby object{'s' if len(others) != 1 else ''} at other periods"
    hits = []
    for h in m.get("period_matched") or []:
        period, sep, off = _num(h, "period"), _num(h, "sep_arcsec"), _num(h, "epoch_offset_h")
        hits.append(_join([f"{h.get('id')} in {h.get('catalog')}", period is not None and f"{period:.4g} d",
                           sep is not None and f'{sep:.1f}"', off is not None and f"{off:.1f} h off"]))
    return sentence, _join([*hits, _join([f"{c} {_num(m, c):.0f}" for c in asked])], "; ")


def season_votes(s: dict) -> bool:
    """Whether a stored season voted in rung 5's verdict: its ``voting`` flag, or for a row stored before the flag,
    a nearer star named."""
    return bool(s["voting"]) if "voting" in s else s.get("nearer") in ("target", "neighbour")


def season_name(s: dict) -> str:
    """"archive season 2", or "archive season (unlabelled)" for a season stored without its label."""
    label = _num(s, "season")
    return f"archive season {s['season']}" if label is not None else "archive season (unlabelled)"


def pixel_seasons(m: dict) -> list[dict]:
    """Rung 5's stored seasons in archive SEASON order (a season without a label last)."""
    seasons = [s for s in m.get("seasons") or [] if isinstance(s, dict)]
    return sorted(seasons, key=lambda s: (_num(s, "season") is None, _num(s, "season") or 0.0))


NEARER = {"target": "the star", "neighbour": "the neighbour"}


def partial_box_clause(m: dict) -> str:
    """" (1 of 3 with a box cut by the stamp edge)" when a voting season's box was cut by the edge of its stamp, else
    "" (also for a row stored before the box keys, which holds no such season)."""
    voting = [s for s in pixel_seasons(m) if season_votes(s)]
    cut = sum(1 for s in voting if s.get("box_partial") is True)
    return f" ({cut} of {len(voting)} with a box cut by the stamp edge)" if cut else ""


def _box_cut(s: dict) -> list[str | bool]:
    """A voting season's box-cut words: the stars whose box lost pixels, then the allowance it paid in the error."""
    if s.get("box_partial") is not True:
        return []
    cut = [f"{who}'s box cut, {n:.0f} of 9 pixels"
           for who, key in (("this star", "box_pixels_target"), ("the neighbour", "box_pixels_neighbour"))
           if (n := _num(s, key)) is not None and n < 9]
    bias = _num(s, "edge_bias_pix")
    return [*cut, bias is not None and f"{bias:.2f} px allowance in the error"]


def _pixels(m: dict) -> tuple[str, str]:
    seasons = pixel_seasons(m)
    voting = [s for s in seasons if season_votes(s)]
    on_target = sum(1 for s in voting if s.get("nearer") == "target")
    if voting:
        sentence = (f"The dip sits on this star in {on_target} of {len(voting)} seasons that could vote"
                    + partial_box_clause(m))
    else:
        sentence = "No season could vote on which star dims" if seasons else "Pixel offsets were not recorded"
    if voting and len(voting) < len(seasons):
        sentence += f"; {len(seasons) - len(voting)} could not"
    rows = []
    for s in seasons:
        if not season_votes(s):
            rows.append(f"{season_name(s)}: not voting "
                        f"({s.get('non_voting_reason') or 'no reason stored'})")
            continue
        d_star, d_nbr, err = (_num(s, k) for k in ("dist_target_pix", "dist_neighbour_pix", "centroid_sigma_pix"))
        sep = _num(s, "separation_sigma")
        rows.append(f"{season_name(s)}: " + _join([
            d_star is not None and f"{d_star:.2f}" + (f" ± {err:.2f}" if err is not None else "") + " px from the star",
            d_nbr is not None and f"{d_nbr:.2f} from the neighbour",
            sep is not None and s.get("nearer") in NEARER and f"{abs(sep):.1f} σ nearer {NEARER[s['nearer']]}",
            *_box_cut(s)]))
    n_ev, n_q = _num(m, "n_events_used"), _num(m, "n_quarters_usable")
    leak = m.get("leak_depth_ppm")
    leak = [_num({"x": x}, "x") for x in leak] if isinstance(leak, list) and len(leak) == 2 else [None, None]
    used = n_ev is not None and n_q is not None and f"{n_ev:.0f} transits in {n_q:.0f} quarters"
    return sentence, _join([*rows, used,
                            leak[0] is not None and leak[1] is not None
                            and f"the neighbour's own light at the transits: {leak[0]:,.0f} ± {leak[1]:,.0f} ppm"],
                 "; ")


BUILDERS: dict[str, Callable[[dict], tuple[str, str]]] = {
    "rung1_detection": _detection, "rung1_events": _events, "rung2_shape": _shape, "rung3_stability": _stability,
    "rung4_novelty": _novelty, "rung5_pixels": _pixels}


def _when(row: dict) -> str | None:
    created = row.get("created")
    if isinstance(created, datetime | date):
        return created.isoformat()[:10]
    return str(created)[:10] if created else None


def _failed_in_words(reason: str) -> str:
    for pattern, words in FAILS:
        g = pattern.match(reason or "")
        if g:
            return words(g)
    return f"Failed: {reason}" if reason else "Failed"


def _passed_in_words(reason: str) -> str | None:
    for pattern, words in PASSES:
        g = pattern.match(reason or "")
        if g:
            return words(g)
    return None


_ARCHIVE_SEASON = re.compile(r"\bseason (?=\d)")


def _row(rung: str, row: dict) -> Why:
    status, reason, m = row.get("status") or "", row.get("reason") or "", row.get("metrics") or {}
    m = m if isinstance(m, dict) else {}         # a row whose metrics are not a mapping reads as one with none
    passed, numbers = BUILDERS.get(rung, lambda _m: ("Passed", ""))(m)
    missing = nxt = None
    if status == "fail":
        sentence = _failed_in_words(reason)
    elif status == "undetermined":
        missing, nxt = reason_in_words(reason)
        hit = _match_reason(reason)
        detail = hit[0].groupdict().get("detail") if hit else None
        if detail and rung == "rung5_pixels":         # rung 5 names the Kepler archive SEASON, not quarter mod 4
            detail = _ARCHIVE_SEASON.sub("archive season ", detail)
        sentence = f"No verdict: {missing[:1].lower()}{missing[1:]}" + (f" ({detail.strip()})" if detail else "")
    else:
        said = _passed_in_words(reason)       # the fallback headline of _pixels carries the clause already
        sentence = said + partial_box_clause(m) if said and rung == "rung5_pixels" else said or passed
    return Why(rung, RUNG_NAMES.get(rung, rung), status, sentence, numbers, missing, nxt, _when(row),
               row.get("recheck_id"))


def _latest(tests: list[dict]) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for row in tests:            # ordered by id, so the last row of a rung is its latest
        latest[row.get("rung")] = row
    return latest


def why(tests: list[dict]) -> list[Why]:
    """One line per rung in ladder order (then any rung the ladder does not know), from the latest row of each. A rung
    without a row is skipped: after a failed check because the ladder stopped, otherwise because it was not run."""
    latest = _latest(tests)
    out, failed = [], False
    for rung in [*LADDER_ORDER, *(r for r in latest if r not in LADDER_ORDER)]:
        row = latest.get(rung)
        if row is None:
            sentence = ("Skipped: an earlier check failed" if failed else
                        "Pixels not yet examined" if rung == "rung5_pixels" else "Not run under this ladder")
            out.append(Why(rung, RUNG_NAMES.get(rung, rung), "skipped", sentence, "", None, None, None))
            continue
        out.append(_row(rung, row))
        failed = failed or out[-1].status == "fail"
    return out


# the engine's statuses that carry a verdict (the store's list, the one source, less "pending")
VETTED_STATUSES = tuple(s for s in ENGINE_STATUSES if s != "pending")
REVIEWER = {"approved": "Approved by reviewer", "published": "Approved by reviewer", "rejected": "Rejected by reviewer"}


def _reviewer(decision: dict | None, decisions: list[dict] | None) -> str | None:
    status = (decision or {}).get("status")
    if status == "review":       # the default state, or a "needs data" request: only the latter is a decision
        return "Needs data" if decisions and decisions[0].get("decision") == "needs_data" else None
    return REVIEWER.get(status)


def _derived(rows: list[Why]) -> str:
    statuses = {w.rung: w.status for w in rows}
    if all(s == "skipped" for s in statuses.values()):
        return "unvetted"
    if "fail" in statuses.values():
        return "rejected"
    if "undetermined" in statuses.values():
        return "undetermined"
    return "candidate_stage2" if statuses.get("rung5_pixels") == "pass" else "candidate_stage1"


def verdict(signal: dict, tests: list[dict], decision: dict | None, decisions: list[dict] | None = None) -> Verdict:
    """The engine's word for the signal with the reviewer's decision beside it. ``decisions`` (newest first, as
    ``Store.decisions`` returns them) tells a "needs data" request from a candidate row nobody has decided."""
    reviewer = _reviewer(decision, decisions)
    rows = why(tests)
    status = signal.get("final_status")
    if status not in VETTED_STATUSES:
        status = _derived(rows)
    if status == "candidate_stage1":
        return Verdict("Candidate", "stage 1", "pass", "Candidate, stage 1: every light-curve check passed", reviewer)
    if status == "candidate_stage2":
        return Verdict("Candidate", "stage 2", "pass", "Candidate, stage 2: the pixels show the dip on this star",
                       reviewer)
    if status == "undetermined":
        n = sum(1 for w in rows if w.status == "undetermined")
        what = "one check" if n == 1 else f"{n} checks" if n else "a check"   # 0: stored status and rows disagree
        return Verdict("Undetermined", None, "undetermined", f"Undetermined: {what} could not decide", reviewer)
    if status == "rejected":
        failed = next((w for w in rows if w.status == "fail"), None)
        headline = (f'Rejected: failed the check "{failed.name}"' if failed else
                    "Rejected by reviewer" if reviewer == "Rejected by reviewer" else "Rejected")
        return Verdict("Rejected", None, "fail", headline, reviewer)
    if status == "superseded":
        return Verdict("Superseded", None, "muted", "Superseded: a recheck replaced this signal", reviewer)
    return Verdict("Not vetted yet", None, "muted", "Not vetted yet: no check has run", reviewer)


# ---- the stage-2 recheck in words: the history lines and the links between a signal and its P/2 signal ----------

# a rechecks row's step (recheck.STEPS and the pixel stage) and outcome in words
STEP_WORDS = {"ladder": "Re-vetted under ladder {ladder}", "period": "Re-vetted at half the period",
              "catalogue": "Catalogue asked again", "pixels": "Pixels checked"}
OUTCOME_WORDS = {"pass": "passed", "fail": "failed", "undetermined": "no verdict", "skipped": "skipped",
                 "superseded": "superseded"}
# a final status as the history and the links say it: "that signal was rejected", "(now a stage 1 candidate)"
STATUS_NOW = {"candidate_stage1": "a stage 1 candidate", "candidate_stage2": "a stage 2 candidate",
              "undetermined": "undetermined", "rejected": "rejected", "superseded": "superseded",
              "pending": "not vetted yet"}
STATUS_PAST = {"candidate_stage1": "became a stage 1 candidate", "candidate_stage2": "became a stage 2 candidate",
               "undetermined": "stayed undetermined", "rejected": "was rejected", "superseded": "was superseded",
               "pending": "was not vetted"}
# (matcher, words) for why a step was skipped, from the reasons recheck.py writes
SKIPPED: list[tuple[re.Pattern, Callable[[re.Match], str]]] = [(re.compile(p), w) for p, w in [
    (rf"^{re.escape(REVIEWER_DECIDED_REASON)}$", lambda g: "a reviewer decided on the signal while the step ran"),
    (r"^search version differs: signal of search version (\S+), not the current (\S+)",
     lambda g: f"the signal comes from search version {g[1]}, not the current {g[2]}"),
    (r"^light curve unavailable: (.+)", lambda g: f"the light curve could not be loaded ({g[1]})"),
    (r"^exception: (.+)", lambda g: f"the step crashed ({g[1]})"),
    (r"^half period not measurable", lambda g: "the half period could not be measured"),
]]
_RUNG_PREFIX = re.compile(r"^rung\w+: ")
_REMEASURED = re.compile(r"^re-measured at [-\d.e+]+ d: (\w+)")


def _lower_first(text: str) -> str:
    """The sentence as a clause: its first letter lowered, unless it starts an abbreviation (SDE, SNR)."""
    first = text.split(" ", 1)[0]
    return text if not text or (len(first) > 1 and first[:2].isupper()) else text[:1].lower() + text[1:]


def _date(utc) -> str | None:
    when = _as_utc(utc)
    return f"{when.day} {when:%b %Y}" if when is not None else None


def _step_detail(outcome: str, reason: str) -> str:
    """The reason of a rechecks row as a clause after its outcome."""
    reason = reason or ""
    if outcome == "skipped":
        for pattern, words in SKIPPED:
            g = pattern.match(reason)
            if g:
                return words(g)
        return reason
    if outcome == "superseded":
        g = _REMEASURED.match(reason)
        return f"that signal {STATUS_PAST.get(g[1], 'became ' + g[1])}" if g else reason
    reason = _RUNG_PREFIX.sub("", reason)
    if not reason:
        return ""
    if outcome == "fail":
        return _lower_first(_failed_in_words(reason))
    if outcome == "undetermined":
        hit = _match_reason(reason)
        return _lower_first(hit[1]) if hit else reason
    passed = _passed_in_words(reason)
    return _lower_first(passed) if passed else reason


def recheck_history(rechecks: list[dict], children: list[dict] | None = None) -> list[dict]:
    """The popup's and the signal page's recheck lines, oldest first, from ``Store.rechecks``: the date, the step in
    words ("Re-vetted under ladder 4", "Re-vetted at half the period", "Catalogue asked again", "Pixels checked"),
    the outcome in words, the reason as a clause (``detail``, a skipped step's reason too) and, for a period step,
    the P/2 signal it made (``link_id``, with ``link_period`` from ``children``)."""
    periods = {c.get("id"): c.get("period") for c in children or []}
    out = []
    for r in rechecks:
        step = STEP_WORDS.get(r.get("step"), str(r.get("step"))).format(ladder=r.get("ladder_version") or "?")
        outcome = r.get("outcome") or ""
        out.append({"date": _date(r.get("utc")), "step": step, "outcome": OUTCOME_WORDS.get(outcome, outcome),
                    "detail": _step_detail(outcome, r.get("reason") or ""), "link_id": r.get("new_signal_id"),
                    "link_period": periods.get(r.get("new_signal_id"))})
    return out


# the rechecks step that takes each next step: once it has run, "pixel stage" or "retry the catalogue" is done
STEP_OF_NEXT = {REVET: "ladder", REVET_CRASH: "ladder", HALVE: "period", PIXELS: "pixels", PIXELS_RETRY: "pixels",
                RETRY: "catalogue"}
PERIOD_BRANCH = "recheck:P/2"           # recheck.PERIOD_BRANCH: the branch of a signal the period step made
# the next steps that ask the same source again; a retry that met the outage again writes the row being explained
RETRIES = (RETRY, PIXELS_RETRY)
NOT_EXAMINED_SUPERSEDED = "Not examined: superseded"


def _current_ladder() -> str:
    # late: the pipeline is heavy and imports the store
    from skysift.pipeline import DEFAULT_LADDER_VERSION
    return DEFAULT_LADDER_VERSION


def _no_recheck_coming(w: Why, signal: dict | None, rechecks: list[dict], history: list[dict]) -> str | None:
    """The "a person decides" next step when the recheck will not take the row's own (recheck.wants_ladder and
    wants_period): the ladder re-vet runs once per signal, only for a signal vetted under an older ladder, and a
    P/2 signal is never halved again. None when the recheck may still take it."""
    if signal is None:
        return None
    if w.next_step in (REVET, REVET_CRASH) and w.rung != "rung5_pixels":      # a rung 5 crash: the pixel step retries
        current = _current_ladder()
        ran = [h for r, h in zip(rechecks, history, strict=True) if r.get("step") == "ladder"
               and r.get("ladder_version") == current and r.get("outcome") != "skipped"]
        if ran:
            return f"re-vetted on {ran[-1]['date']}; {PERSON_DECIDES}"
        if signal.get("ladder_version") == current:
            return PERSON
    if w.next_step == HALVE and (signal.get("branch") == PERIOD_BRANCH or (signal.get("extra") or {}).get("halving")):
        return PERSON
    return None


def with_recheck_state(rows: list[Why], rechecks: list[dict], children: list[dict] | None = None,
                       final_status: str | None = None, signal: dict | None = None) -> list[Why]:
    """The Why rows with each undetermined row's next step given its state once the recheck has taken it ("rechecked
    on 21 Oct 2026: superseded by the signal at 40.2899 d, now a stage 1 candidate"), and, on a superseded signal, the
    unexamined pixel row saying the recheck never will examine it; other rows as they are.

    The step that wrote a row (``Why.recheck_id``) never counts as having taken that row's next step: a catalogue or
    pixel retry that met the outage again leaves an outage row, and the recheck will retry it, so the row keeps its
    retry wording with the attempt in front ("retried on 21 Oct 2026, still no answer; retry the catalogue").

    With ``signal`` (its row: ladder_version, branch, extra), a next step the recheck will not take reads "a person
    decides" instead (``_no_recheck_coming``)."""
    status = {c.get("id"): c.get("final_status") for c in children or []}
    history = recheck_history(rechecks, children)
    out = []
    for w in rows:
        if final_status == "superseded" and w.rung == "rung5_pixels" and w.status == "skipped":
            out.append(replace(w, sentence=NOT_EXAMINED_SUPERSEDED))
            continue
        step = STEP_OF_NEXT.get(w.next_step or "")
        done = [h for r, h in zip(rechecks, history, strict=True) if step and r.get("step") == step
                and r.get("outcome") != "skipped" and (w.recheck_id is None or r.get("id") != w.recheck_id)]
        wrote = [h for r, h in zip(rechecks, history, strict=True)
                 if w.recheck_id is not None and r.get("id") == w.recheck_id]
        if w.status == "undetermined" and w.next_step in RETRIES and wrote:
            out.append(replace(w, next_step=f"retried on {wrote[0]['date']}, still no answer; {w.next_step}"))
            continue
        person = _no_recheck_coming(w, signal, rechecks, history) if w.status == "undetermined" else None
        if person:
            out.append(replace(w, next_step=person))
            continue
        if w.status != "undetermined" or not done:
            out.append(w)
            continue
        h = done[-1]
        text = f"rechecked on {h['date']}: {h['outcome']}"
        if h["link_id"] is not None:
            period = f"{h['link_period']:g} d" if isinstance(h["link_period"], int | float) else "half the period"
            now = STATUS_NOW.get(status.get(h["link_id"]))
            text += (" by" if h["outcome"] == "superseded" else ",") + f" the signal at {period}"
            text += f", now {now}" if now else ""
        out.append(replace(w, next_step=text))
    return out


def _link(words: str, other: dict) -> dict:
    status = other.get("final_status")
    return {"words": words, "id": other.get("id"), "period": other.get("period"), "status": status,
            "status_words": STATUS_NOW.get(status, status)}


def lineage(signal: dict, parent: dict | None, children: list[dict]) -> list[dict]:
    """The links between a signal and the signals the recheck measured from it at half the period: "Superseded by"
    the child of a superseded signal, "Supersedes" the superseded parent of a child, and "Measured again at half the
    period as" / "Measured at half the period of" when the P/2 signal did not settle it (the parent stays open)."""
    out = []
    if parent is not None:
        words = "Supersedes" if parent.get("final_status") == "superseded" else "Measured at half the period of"
        out.append(_link(words, parent))
    for child in children:
        words = ("Superseded by" if signal.get("final_status") == "superseded"
                 else "Measured again at half the period as")
        out.append(_link(words, child))
    return out


def _plural(x: float, unit: str) -> str:
    return f"{x:.3g} {unit}" + ("" if x == 1 else "s")


def _facts(signal: dict, target: dict, depth: float | None, ppm: bool) -> str:
    period, gmag = _num(signal, "period"), _num(target, "gmag")
    dip = "A dip"
    if depth is not None:
        dip += f" of {depth / 1e4:.2g} percent" + (f" ({_ppm(depth)} ppm)" if ppm else "")
    every = f" repeats every {_plural(period, 'day')}" if period is not None else " repeats"
    star = (f" on a star of magnitude {gmag:.1f}" if gmag is not None
            else f" on {target.get('target_id') or signal.get('target_id')}")
    return f"{dip}{every}{star}."


def summary_sentence(signal: dict, target: dict, tests: list[dict]) -> str:
    """One sentence a newcomer can read: depth in percent, period, star. The depth is the detector's, so the sentence
    agrees with ``copy_summary``; rung 2's folded depth stands in only when the signal has none."""
    shape = (_latest(tests).get("rung2_shape") or {}).get("metrics") or {}
    depth = _num(signal, "depth_ppm")
    return _facts(signal, target, depth if depth is not None else _num(shape, "depth_ppm"), ppm=False)


def copy_summary(signal: dict, target: dict, v: Verdict, url: str) -> str:
    """The plain-text paragraph the "Copy summary" button puts on the clipboard."""
    name = target.get("target_id") or signal.get("target_id") or "?"
    if target.get("kic"):
        name += f" (KIC {target['kic']})"
    status = _join([v.reviewer and v.reviewer != v.headline and v.reviewer + ".", v.headline + "."], " ")
    return f"{name}. {_facts(signal, target, _num(signal, 'depth_ppm'), ppm=True)} {status} Details: {url}"


def _count_of(n: int, unit: str) -> str:
    return f"{n} {unit}" + ("" if n == 1 else "s")


def duration_words(seconds: float | None) -> str:
    """A span of time as Live and Tonight say it: "under a minute", "40 minutes", "1 hour 15 minutes", then whole hours
    ("2 hours", up to "47 hours") and whole days ("3 days")."""
    if seconds is None or not math.isfinite(seconds) or seconds < 60:
        return "under a minute"
    minutes = round(seconds / 60)
    if minutes < 60:
        return _count_of(minutes, "minute")
    hours, rest = divmod(minutes, 60)
    if hours < 48:
        return _count_of(hours, "hour") + (f" {_count_of(rest, 'minute')}" if rest and hours == 1 else "")
    return _count_of(round(hours / 24), "day")


def _as_utc(when: datetime | str | None) -> datetime | None:
    if isinstance(when, str):
        try:
            when = datetime.fromisoformat(when)
        except ValueError:
            return None
    if not isinstance(when, datetime):
        return None
    return when.replace(tzinfo=UTC) if when.tzinfo is None else when.astimezone(UTC)


def ago(when: datetime | str | None, now: datetime | None = None) -> str:
    """How long ago, in words: "just now", "5 minutes ago", "2 hours ago". A store time without a zone is UTC; a
    missing or unreadable time is "at an unknown time"."""
    then = _as_utc(when)
    if then is None:
        return "at an unknown time"
    seconds = ((now or datetime.now(UTC)) - then).total_seconds()
    return "just now" if seconds < 60 else f"{duration_words(seconds)} ago"


def time_left(seconds: float) -> str:
    """The time a sweep has left: "under a minute left" or "about 40 minutes left"."""
    return "under a minute left" if not math.isfinite(seconds) or seconds < 60 else f"about {duration_words(seconds)} left"
