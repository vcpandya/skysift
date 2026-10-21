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
from dataclasses import dataclass
from datetime import UTC, date, datetime

from skysift.config import Thresholds

LADDER_ORDER = ["rung1_detection", "rung1_events", "rung2_shape", "rung3_stability", "rung4_novelty", "rung5_pixels"]
RUNG_NAMES = {"rung1_detection": "Detected above the noise", "rung1_events": "Seen in every transit with data",
              "rung2_shape": "Transit shape", "rung3_stability": "Stable across seasons and apertures",
              "rung4_novelty": "Not already known", "rung5_pixels": "Pixels show the dip on this star"}

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
FAILED = "no recheck: a failed check is final"

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
    (r"^outage:\s*(?P<detail>mast\b.*)", "The pixel archive did not answer when we asked", PIXELS),
    (r"^outage:\s*(?P<detail>.*)", "A catalogue did not answer when we asked", RETRY),
    (r"^schema:(?P<detail>.*)", "A catalogue answered in a form we could not read", RETRY),
    (r"^period matches (?P<detail>.+?) but epoch differs",
     "A catalogue has this period on a neighbour, but its transits fall at a different time", PIXELS),
    (r"^exception:\s*(?P<detail>.*)", "The check crashed",
     "re-vet under the current ladder; stays flagged for a person if it crashes again"),
    (r"^free_secondary_undetermined", "The search for a second dip away from phase 0.5 had too few points", PERSON),
    (r"^pixels:\s*(?P<detail>.*)", "The pixels could not show which star the dip is on", PIXELS),
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
    (r"^the dip is on a neighbouring star(.*)", lambda g: f"The pixels put the dip on a neighbouring star{g[1]}"),
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
    parts = [f"Depth agrees across {len(depths)} seasons (p = {p:.2g})" if depths and p is not None
             else "Season depths were not recorded"]
    if ratio is not None:
        parts.append(f"the two apertures agree (ratio {ratio:.2f})")
    seasons = [f"season {s}: {_ppm(_num(depths, s))}" + (f" ± {_ppm(_num(errs, s))}" if s in errs else "") + " ppm"
               for s in sorted(depths, key=str)]
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


def _pixels(m: dict) -> tuple[str, str]:
    seasons = [s for s in m.get("seasons") or [] if isinstance(s, dict)]
    on_target = sum(1 for s in seasons if s.get("nearer") == "target")
    sentence = (f"The dip sits on this star in {on_target} of {len(seasons)} seasons" if seasons
                else "Pixel offsets were not recorded")
    rows = []
    for s in seasons:
        d_star, d_nbr, err = (_num(s, k) for k in ("dist_target_pix", "dist_neighbour_pix", "centroid_sigma_pix"))
        rows.append(f"season {s.get('season')}: " + _join([
            d_star is not None and f"{d_star:.2f}" + (f" ± {err:.2f}" if err is not None else "") + " px from the star",
            d_nbr is not None and f"{d_nbr:.2f} from the neighbour"]))
    n_ev, n_q = _num(m, "n_events_used"), _num(m, "n_quarters_usable")
    return sentence, _join([*rows, n_ev is not None and n_q is not None
                            and f"{n_ev:.0f} transits in {n_q:.0f} quarters"], "; ")


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


def _row(rung: str, row: dict) -> Why:
    status, reason, m = row.get("status") or "", row.get("reason") or "", row.get("metrics") or {}
    passed, numbers = BUILDERS.get(rung, lambda _m: ("Passed", ""))(m)
    missing = nxt = None
    if status == "fail":
        sentence = _failed_in_words(reason)
    elif status == "undetermined":
        missing, nxt = reason_in_words(reason)
        hit = _match_reason(reason)
        detail = hit[0].groupdict().get("detail") if hit else None
        sentence = f"No verdict: {missing[:1].lower()}{missing[1:]}" + (f" ({detail.strip()})" if detail else "")
    else:
        sentence = passed
    return Why(rung, RUNG_NAMES.get(rung, rung), status, sentence, numbers, missing, nxt, _when(row))


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
    if status not in ("candidate_stage1", "candidate_stage2", "undetermined", "rejected", "superseded"):
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
