"""The one-time numeric comparison of a generated dossier with the hand-built KIC 6146676 study (dossiers spec 5.7):
period, depth, duration, SDE, the stellar and planet parameters, the per-season difference-image centroid and the
two-source depths, each with the difference in units of the quoted error and a yes/no; and the FPP factor tables of
spec 4.2 for the sign-off.

    python -m tools.dossier_compare --data <generated dossier_data.json> \\
        --reference docs/candidates/kic-006146676/dossier_data.json \\
        [--fpp <another generated dossier_data.json> ...] [--out <markdown file>] [--write-golden <json file>]

``--data`` is a generated ``dossier_data.json`` or the slim block ``--write-golden`` extracts from one (the keys the
comparison and the golden test read: target, signal, ephemeris, fap, fpp, star, planet, look and the rung 2, 3, 4 and
5 rows). The tables are printed and written to ``--out`` (default: beside ``--data``, ``<stem>.comparison.md``).

The two pages name the stars differently: the engine's "target" is the candidate star KIC 6146676 (the hand-built
"candidate") and its "neighbour" is the Kepler target KIC 6146666 whose pixels hold it (the hand-built "target").

Agreement: within one quoted error of the hand-built value (the side of an asymmetric error towards the generated
value); the per-season centroid distances within 0.1 pixel; a quantity the study quotes without an error against the
tolerance written in its row."""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

CENTROID_TOL_PIX = 0.1          # the brief's tolerance for the per-season centroid offsets
FIT_DURATION_ERROR = 0.1        # skysift.fpp's fractional fit-duration error, for the T14 row (no error quoted)
PERIOD_ROUNDING_D = 0.5e-5      # the study prints the period to five decimals
GOLDEN_RUNGS = ("rung2_shape", "rung3_stability", "rung4_novelty", "rung5_pixels")
GOLDEN_KEYS = ("dossier_version", "generated", "target", "ephemeris", "fap", "fpp", "star", "planet", "look")


@dataclass
class Row:
    quantity: str
    unit: str
    generated: float | None
    reference: float | None
    error: float | None             # the error the difference is measured in
    error_note: str                 # where that error comes from
    tolerance_sigma: float = 1.0    # agree when |difference| <= tolerance_sigma x error

    @property
    def difference(self) -> float | None:
        if self.generated is None or self.reference is None:
            return None
        return self.generated - self.reference

    @property
    def sigma(self) -> float | None:
        d = self.difference
        if d is None or not self.error:
            return None
        return d / self.error

    @property
    def agree(self) -> bool | None:
        s = self.sigma
        return None if s is None else abs(s) <= self.tolerance_sigma


# ---------------------------------------------------------------------------------------------------- reading

def _get(d, *path, default=None):
    for p in path:
        if isinstance(d, dict):
            d = d.get(p)
        elif isinstance(d, list) and isinstance(p, int) and -len(d) <= p < len(d):
            d = d[p]
        else:
            return default
        if d is None:
            return default
    return d


def _num(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def metrics(data: dict, rung: str) -> dict:
    """The metrics of the dossier's latest row of ``rung`` ({} when there is none)."""
    return next((t.get("metrics") or {} for t in data.get("tests") or [] if t.get("rung") == rung), {})


def latest_row(data: dict, rung: str) -> dict | None:
    return next((t for t in data.get("tests") or [] if t.get("rung") == rung), None)


def golden_block(data: dict) -> dict:
    """The slim block the golden test reads: the dossier's identity, numbers and probabilities, and the rung 2 to 5
    rows (rung 1's per-event arrays and the plot and pixel arrays are left out)."""
    out = {k: data.get(k) for k in GOLDEN_KEYS}
    out["signal"] = {k: (data.get("signal") or {}).get(k) for k in
                     ("id", "target_id", "detector", "detector_version", "period", "t0", "duration", "depth_ppm", "snr",
                      "sde", "n_events", "final_status")}
    out["tests"] = [t for t in data.get("tests") or [] if t.get("rung") in GOLDEN_RUNGS]
    out["provenance"] = {k: _get(data, "provenance", k) for k in ("search_version", "git_commit")}
    return out


# ---------------------------------------------------------------------------------------------------- rows

def _value_row(name: str, unit: str, gen: dict | None, ref: dict | None) -> Row:
    """A row of two ``{median, minus, plus}`` values, measured in the hand-built error on the generated side."""
    g, r = _num(_get(gen, "median")), _num(_get(ref, "median"))
    err = None
    if g is not None and r is not None:
        err = _num(_get(ref, "plus" if g >= r else "minus"))
    return Row(name, unit, g, r, err, "hand-built 16th to 84th percentile")


def rows(gen: dict, ref: dict) -> list[Row]:
    """The comparison rows of spec 5.7 for KIC 6146676, in the order of the dossier's sections."""
    eph, shape = gen.get("ephemeris") or {}, metrics(gen, "rung2_shape")
    vet_signal = _get(ref, "vet", "signals", 0, "signal") or {}
    out = [
        Row("period", "d", _num(eph.get("period_days")), _num(_get(ref, "ephemeris", "period_days")),
            PERIOD_ROUNDING_D, "rounding of the study's five decimals"),
        Row("SDE of the search peak", "", _num(eph.get("sde")), _num(vet_signal.get("sde")), 0.01,
            "same search and version; 0.01 tolerance"),
        Row("depth, box (search)", "ppm", _num(eph.get("depth_ppm")), _num(vet_signal.get("depth_ppm")),
            _num(_get(ref, "depth", "event_err_ppm")), "hand-built event-mean error"),
        Row("depth, mean of the events (rung 2)", "ppm", _num(shape.get("depth_ppm")),
            _num(_get(ref, "depth", "event_mean_ppm")), _num(_get(ref, "depth", "event_err_ppm")),
            "hand-built event-mean error"),
        Row("duration T14 from the transit fit", "h", _num(shape.get("fit_t14_h")), _num(_get(ref, "fit", "t14_h")),
            (FIT_DURATION_ERROR * _num(_get(ref, "fit", "t14_h"))) if _num(_get(ref, "fit", "t14_h")) else None,
            "none quoted; 10 percent fit-duration error of skysift.fpp"),
        Row("odd-transit depth", "ppm", _num(shape.get("odd_ppm")), _num(_get(ref, "odd_even", "odd_ppm")),
            _num(_get(ref, "odd_even", "odd_err")), "hand-built odd error"),
        Row("even-transit depth", "ppm", _num(shape.get("even_ppm")), _num(_get(ref, "odd_even", "even_ppm")),
            _num(_get(ref, "odd_even", "even_err")), "hand-built even error"),
        Row("secondary depth at phase 0.5", "ppm", _num(shape.get("secondary_ppm")),
            _num(_get(ref, "secondary", "at_half", "depth_ppm")), _num(_get(ref, "secondary", "at_half", "err_ppm")),
            "hand-built secondary error"),
    ]
    star, planet = gen.get("star") or {}, gen.get("planet") or {}
    sb = _get(ref, "context", "summary", "stellar_baseline") or {}
    pb = _get(ref, "context", "summary", "planet_baseline") or {}
    out += [
        _value_row("stellar Teff", "K", star.get("teff"), sb.get("Teff_K")),
        _value_row("stellar radius", "Rsun", star.get("radius"), sb.get("R_star_Rsun")),
        _value_row("stellar mass", "Msun", star.get("mass"), sb.get("M_star_Msun")),
        _value_row("distance", "pc", star.get("distance_pc"), _get(ref, "context", "summary", "distance_pc")),
        _value_row("planet radius Rp (from k)", "Rearth", planet.get("rp_rearth"), pb.get("Rp_from_k_Rearth")),
        _value_row("semi-major axis a", "AU", planet.get("a_au"), pb.get("a_AU")),
        _value_row("a / R*", "", planet.get("a_over_r"), pb.get("a_over_Rstar")),
        _value_row("equilibrium temperature Teq (A 0.3)", "K", planet.get("teq_k"),
                   pb.get("Teq_K_A0.3_full_redistribution")),
        _value_row("transit probability R*/a", "", planet.get("transit_probability"),
                   pb.get("transit_probability_Rstar_over_a")),
    ]
    out += season_depth_rows(gen, ref) + pixel_rows(gen, ref)
    return out


def season_depth_rows(gen: dict, ref: dict) -> list[Row]:
    """Rung 3's per-season depths (seasons as quarter mod 4, as the study's lc_analysis.py)."""
    stab = metrics(gen, "rung3_stability")
    depths, errs = stab.get("season_depths") or {}, stab.get("season_errs") or {}
    out = []
    for s, r in sorted((ref.get("season_depths") or {}).items(), key=lambda kv: int(kv[0])):
        g = _num(depths.get(str(s)))
        if g is None:
            continue
        err = math.hypot(_num(r.get("err_ppm")) or 0.0, _num(errs.get(str(s))) or 0.0) or None
        out.append(Row(f"rung 3 depth, season {s} (quarter mod 4)", "ppm", g, _num(r.get("depth_ppm")), err,
                       "both errors in quadrature"))
    return out


def _ref_season(ref: dict, season: int) -> dict:
    return next((s for s in _get(ref, "pixels", "seasons", default=[]) if s.get("season") == season), {})


def pixel_rows(gen: dict, ref: dict) -> list[Row]:
    """Per archive season: the centroid's distance from each star (0.1 pixel tolerance) and the two-source depths
    (within the hand-built error); the leak test in KIC 6146666's own flux."""
    m = metrics(gen, "rung5_pixels")
    if not m:
        return []
    summ = _get(ref, "pixels", "summary") or {}
    to_cand = summ.get("seasons_snrbox_centroid_dist_to_candidate_pix") or {}
    to_nbr = summ.get("seasons_snrbox_centroid_dist_to_target_pix") or {}
    out = []
    for s in m.get("seasons") or []:
        season = int(s["season"])
        out.append(Row(f"centroid to KIC 6146676, season {season}", "pix", _num(s.get("dist_target_pix")),
                       _num(to_cand.get(str(season))), CENTROID_TOL_PIX, "0.1 pixel tolerance"))
        out.append(Row(f"centroid to KIC 6146666, season {season}", "pix", _num(s.get("dist_neighbour_pix")),
                       _num(to_nbr.get(str(season))), CENTROID_TOL_PIX, "0.1 pixel tolerance"))
    for s in m.get("seasons") or []:
        season = int(s["season"])
        two = _ref_season(ref, season).get("two_source") or {}
        for star, gkey, rkey in (("KIC 6146676", "depth_target_ppm", "candidate"),
                                 ("KIC 6146666", "depth_neighbour_ppm", "target")):
            g = s.get(gkey) or [None, None]
            out.append(Row(f"two-source depth on {star}, season {season}", "ppm", _num(g[0]),
                           _num(two.get(f"implied_depth_ppm_{rkey}")), _num(two.get(f"implied_depth_ppm_{rkey}_err")),
                           "hand-built fit error"))
    leak = m.get("leak_depth_ppm") or [None, None]
    sap = _get(ref, "context", "summary", "neighbour_KIC6146666_sap_depth_ppm") or [None, None]
    out.append(Row("leak test: dip in KIC 6146666's own flux", "ppm", _num(leak[0]), _num(sap[0]), _num(sap[1]),
                   "hand-built SAP leak-test error"))
    return out


# ---------------------------------------------------------------------------------------------------- markdown

def _fmt(x, sig: int = 4) -> str:
    """``sig`` significant figures (``g`` format), yes/no for a flag, n/a for a missing value."""
    if x is None:
        return "n/a"
    if isinstance(x, bool):
        return "yes" if x else "no"
    if isinstance(x, int):
        return f"{x:,}"
    return f"{float(x):.{sig}g}"


def comparison_table(items: list[Row]) -> str:
    lines = ["| Quantity | Unit | Generated | Hand-built | Difference | Error used | Difference / error | Agree |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in items:
        sig = "n/a" if r.sigma is None else f"{r.sigma:+.2f}"
        agree = {True: "yes", False: "no", None: "n/a"}[r.agree]
        lines.append(f"| {r.quantity} | {r.unit} | {_fmt(r.generated, 8)} | {_fmt(r.reference, 8)} | "
                     f"{_fmt(r.difference)} | {_fmt(r.error)} ({r.error_note}) | {sig} | {agree} |")
    return "\n".join(lines)


def seasons_table(gen: dict, ref: dict) -> str:
    """Rung 5 per archive season beside the study: quarters, events, the vote and why a season did not vote."""
    m = metrics(gen, "rung5_pixels")
    if not m:
        return "The pixel step has not run for this signal (no rung 5 row), so there is no per-season verdict."
    lines = [("| Season | Quarters (generated / hand-built) | Events | Nearer | separation_sigma | voting | "
             "non_voting_reason | Centroid sigma (pix) |"),
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for s in m.get("seasons") or []:
        r = _ref_season(ref, int(s["season"]))
        rq = ", ".join(str(q) for q in r.get("quarters") or []) or "n/a"
        gq = ", ".join(str(q) for q in s.get("quarters") or [])
        sep = _num(s.get("separation_sigma"))
        lines.append(f"| {s['season']} | {gq} / {rq} | {s.get('n_events')} / {r.get('n_events', 'n/a')} | "
                     f"{s.get('nearer')} | {'n/a' if sep is None else f'{sep:.1f}'} | "
                     f"{'yes' if s.get('voting') else 'no'} | {s.get('non_voting_reason') or 'none'} | "
                     f"{_fmt(_num(s.get('centroid_sigma_pix')))} |")
    summ = _get(ref, "pixels", "summary") or {}
    lines += ["", (f"Rung 5: {latest_row(gen, 'rung5_pixels').get('status')}, "
              f"\"{latest_row(gen, 'rung5_pixels').get('reason')}\"; n_seasons_voting / n_seasons = "
              f"{m.get('n_seasons_voting')} / {m.get('n_seasons')}; tpf_missing = {m.get('tpf_missing')}; "
              f"{m.get('n_tpf')} pixel files of KIC {m.get('neighbour_kic')}; unusable quarters "
              f"{m.get('unusable_quarters')} (hand-built {summ.get('unusable_quarters')}); events used "
              f"{m.get('n_events_used')} (hand-built {summ.get('n_events_used')}).")]
    return "\n".join(lines)


def factor_table(data: dict) -> str:
    """Spec 4.2's table for one dossier: the three weights, every named factor and the neighbours."""
    f = data.get("fpp") or {}
    name = _get(data, "target", "name") or _get(data, "target", "target_id") or "?"
    scen = f.get("scenarios") or []
    total = sum(_num(s.get("weight")) or 0.0 for s in scen)
    lines = [(f"**{name}**: FPP {_fmt(_num(f.get('value')))} (\"{f.get('words')}\", label \"{f.get('label')}\", "
             f"pixels {f.get('pixels')}, signed off {f.get('signed_off')}), confusion radius "
             f"{_fmt(_num(f.get('confusion_radius_arcsec')))} arcsec."), "",
             "| Scenario | Prior | Geometric | Data | Weight | Share of the total |",
             "| --- | --- | --- | --- | --- | --- |"]
    for s in scen:
        w = _num(s.get("weight")) or 0.0
        lines.append(f"| {s.get('name')} | {_fmt(_num(s.get('prior')))} | {_fmt(_num(s.get('geometric')))} | "
                     f"{_fmt(_num(s.get('data')))} | {_fmt(w)} | {_fmt(w / total if total else None)} |")
    lines += ["", "| Scenario | Factor | Value |", "| --- | --- | --- |"]
    for s in scen:
        for k, v in (s.get("factors") or {}).items():
            lines.append(f"| {s.get('name')} | {k} | {_fmt(v) if not isinstance(v, str) else v} |")
    nbrs = f.get("neighbours") or []
    if nbrs:
        lines += ["", ("| Gaia DR3 neighbour | Separation (arcsec) | G | Flux fraction | Bright enough | "
                  "s from the centroid (arcsec) | Exclusion X_i |"), "| --- | --- | --- | --- | --- | --- | --- |"]
        for n in nbrs:
            lines.append(f"| {n.get('source_id')} | {_fmt(_num(n.get('sep_arcsec')))} | {_fmt(_num(n.get('gmag')))} | "
                         f"{_fmt(_num(n.get('flux_fraction')))} | {_fmt(n.get('bright_enough'))} | "
                         f"{_fmt(_num(n.get('s_arcsec')))} | {_fmt(_num(n.get('exclusion')))} |")
    return "\n".join(lines)


def report(gen: dict, ref: dict, fpp_data: list[dict]) -> str:
    items = rows(gen, ref)
    n_yes = sum(1 for r in items if r.agree)
    n_no = [r.quantity for r in items if r.agree is False]
    n_na = [r.quantity for r in items if r.agree is None]
    fap = gen.get("fap") or {}
    parts = [f"## Comparison: {_get(gen, 'target', 'name')} generated against the hand-built study", "",
             (f"{n_yes} of {len(items)} rows agree; disagreements: {', '.join(n_no) or 'none'}; without a value on "
             f"one side: {', '.join(n_na) or 'none'}."), "",
             comparison_table(items), "", "## Pixel step per season", "", seasons_table(gen, ref), "",
             "## Probabilities", "",
             (f"False alarm: \"{fap.get('words')}\", {fap.get('k')} of {fap.get('n')} null searches at or above SDE "
             f"{_fmt(_num(fap.get('sde')))}, calibration version {fap.get('calibration_version')} (signal search "
             f"{fap.get('signal_search_version')}, version matches {fap.get('version_matches')})."), ""]
    for d in [gen, *fpp_data]:
        parts += [factor_table(d), ""]
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", required=True, type=Path, help="the generated dossier_data.json (or its slim block)")
    ap.add_argument("--reference", required=True, type=Path, help="the hand-built dossier_data.json")
    ap.add_argument("--fpp", action="append", default=[], type=Path,
                    help="another generated dossier whose FPP factor table is printed too (repeatable)")
    ap.add_argument("--out", type=Path, help="the markdown file (default <data stem>.comparison.md beside --data)")
    ap.add_argument("--write-golden", type=Path, help="also write the slim block of --data here")
    args = ap.parse_args(argv)
    gen = json.loads(args.data.read_text(encoding="utf-8"))
    ref = json.loads(args.reference.read_text(encoding="utf-8"))
    others = [json.loads(p.read_text(encoding="utf-8")) for p in args.fpp]
    text = report(gen, ref, others)
    out = args.out or args.data.with_name(f"{args.data.stem}.comparison.md")
    out.write_text(text, encoding="utf-8", newline="\n")
    if args.write_golden:
        args.write_golden.write_text(json.dumps(golden_block(gen), indent=1, sort_keys=False) + "\n",
                                     encoding="utf-8", newline="\n")
    sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
