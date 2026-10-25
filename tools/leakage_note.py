"""Write the comparison note of spec 12.1: each neighbour's leakage share before and after the Kepler-pixel rule, with
the NFPP each set of shares drove.

It reads a round-2 result of ``tools/triceratops_check.py`` (its ``stars`` rows: separation, G, Kepler-band flux
relative to the target, the share of the round-2 rule and the Gaia DR3 id, and ``settings.flux_shares.budget``) and the
star's ``dossier_data.json`` (rung 5's seasons, for the PSF width through ``skysift.crosscheck.rung5_psf_sigma``),
recomputes each share under spec 12.1 with the tool's own ``overlap`` (overlap times Kepler-band flux, scaled down to
the budget only when the sum passes it) and writes a Markdown note. A neighbour's overlap is taken at the Gaia DR3
separation the dossier stores for it (``fpp.inputs.neighbours``, the separation the export carries and the tool
uses since the round 3 final review, I1), the round-2 row's TIC separation only for a star no Gaia neighbour matched. With ``--after`` (the round-3 result) it prints
that result's NFPP beside the round-2 one. Both files are only read. It runs under the engine's interpreter from the
repository root:

    PYTHONUTF8=1 PYTHONPATH=src .venv/Scripts/python.exe -m tools.leakage_note --result <results-r2 json>
        --dossier <dossier_data.json> --out docs/superpowers/notes/2026-10-23-leakage-shares.md [--after <result>]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from skysift import crosscheck
from tools import triceratops_check as tc

NOTE_WITHIN_ARCSEC = 21.0           # the note lists the stars within this of the target (seven for KIC 6146676)
BIG_SHARE = 0.01                    # a largest share above this gets its own paragraph


def gaia_separations(dossier: dict) -> dict[str, float]:
    """{Gaia DR3 source id: separation in arcsec} of the dossier's stored neighbours (``fpp.inputs.neighbours``)."""
    out = {}
    for n in ((dossier.get("fpp") or {}).get("inputs") or {}).get("neighbours") or []:
        sep = _num(n.get("sep_arcsec"))
        if sep is not None and n.get("source_id") is not None:
            out[str(n["source_id"])] = sep
    return out


def _sep(star: dict, gaia_seps: dict | None = None) -> float | None:
    """The separation a row's overlap is taken at: the dossier's Gaia DR3 one for the row's ``gaia_source_id``
    (``gaia_separations``; the round 3 final review, I1), else ``overlap_sep_arcsec`` (the tool's, for an added star
    whose ``sep_arcsec`` is None; round 3 review M1), else ``sep_arcsec`` (the TIC's)."""
    gid = star.get("gaia_source_id")
    if gaia_seps and gid is not None and str(gid) in gaia_seps:
        return gaia_seps[str(gid)]
    sep = _num(star.get("overlap_sep_arcsec"))
    return sep if sep is not None else _num(star.get("sep_arcsec"))


def _num(x) -> float | None:
    if isinstance(x, bool) or x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _weights(result: dict, sigma_pix: float, gaia_seps: dict | None = None) -> tuple[float, float | None, float]:
    """(the sum of overlap times Kepler-band flux over every non-target row with a flux, each at ``_sep``, the budget,
    the scale min(1, budget / that sum), 1 without a budget or a sum)."""
    total = sum(tc.overlap(_sep(s, gaia_seps), sigma_pix) * float(s["kepler_flux"])
                for s in result["stars"][1:] if _num(s.get("kepler_flux")) is not None)
    budget = _num(((result.get("settings") or {}).get("flux_shares") or {}).get("budget"))
    scale = min(1.0, budget / total) if budget is not None and total > 0 else 1.0
    return total, budget, scale


def note_rows(result: dict, sigma_pix: float, gaia_seps: dict | None = None) -> list[dict]:
    """The stars of ``result`` other than the target with a finite separation (``_sep``) at or below
    ``NOTE_WITHIN_ARCSEC``, sorted by separation: {"id", "gaia_source_id", "sep_arcsec" (the one the overlap is taken
    at), "tic_sep_arcsec" (the round-2 row's), "sep_pix", "gmag", "kepler_flux", "overlap", "share_before" (the
    stored share), "share"}, the share overlap x kepler_flux x scale (``_weights``), None for a row without a
    Kepler-band flux."""
    _total, _budget, scale = _weights(result, sigma_pix, gaia_seps)
    out = []
    for s in result["stars"][1:]:
        sep = _sep(s, gaia_seps)
        if sep is None or sep > NOTE_WITHIN_ARCSEC:
            continue
        flux, ov = _num(s.get("kepler_flux")), tc.overlap(sep, sigma_pix)
        out.append({"id": str(s.get("id")), "gaia_source_id": s.get("gaia_source_id"), "sep_arcsec": sep,
                    "tic_sep_arcsec": _num(s.get("sep_arcsec")),
                    "sep_pix": sep / tc.KEPLER_PIX_ARCSEC, "gmag": _num(s.get("gmag")), "kepler_flux": flux,
                    "overlap": ov, "share_before": s.get("share"),
                    "share": None if flux is None else ov * flux * scale})
    return sorted(out, key=lambda r: r["sep_arcsec"])


def _share(x) -> str:
    if x is None:
        return "none"
    return "0" if x < 1e-12 else f"{x:.4g}"


def _fixed(x, spec: str = ".2f") -> str:
    return "unknown" if x is None else format(x, spec)


def _nfpp(label: str, result: dict) -> str:
    p = crosscheck.probability_text
    return (f"NFPP {label}: {p(result['nfpp'])} (16th to 84th percentile {p(result['nfpp_p16'])} to "
            f"{p(result['nfpp_p84'])}) over {result['n_runs']} runs")


def render(result: dict, sigma: dict, rows: list[dict], after: dict | None = None, *, star: str = "KIC 6146676",
           files: tuple[str, str] | None = None, gaia_seps: dict | None = None) -> str:
    """The note: the title, the paragraph on the sources, the budget, rung 5's width and the rule, the basis, the
    paragraph on the separations and the cap (``separation_paragraph``), the table of ``rows``, the NFPP before and
    after, the largest share after the rule when it passes ``BIG_SHARE``, the bound the shares are for PSF photometry
    and ``tc.PRF_CAVEAT`` last. ``files``: the result and dossier paths as printed; ``gaia_seps``: as ``_sep``."""
    s = sigma["sigma_pix"]
    total, budget, scale = _weights(result, s, gaia_seps)
    crowdsap = _num(((result.get("settings") or {}).get("flux_shares") or {}).get("crowdsap"))
    widths = ", ".join(f"{w:.4f} (season {k})" for k, w in sigma["per_season"].items())
    seasons = len(sigma["seasons"])
    result_file, dossier_file = files or ("the round-2 result", "the star's dossier")
    n_flux = sum(1 for x in result["stars"][1:] if _num(x.get("kepler_flux")) is not None)
    if budget is None:
        cap = "The round-2 result stores no budget, so the shares after the rule are not capped."
    elif total > budget:
        cap = (f"Here, over the {n_flux} neighbours with a Kepler-band flux, "
               f"{crosscheck.cap_bound_clause(total, budget)}, a factor of {scale:.4f}.")
    else:
        cap = (f"Here the products sum to {total:.4f} over the {n_flux} neighbours with a Kepler-band flux, below the "
               "budget, so the cap never binds for this star and the shares after the rule are the products "
               "themselves.")
    crowd = f"CROWDSAP {crowdsap:.8g}, so " if crowdsap is not None else ""
    plural = "s" if seasons != 1 else ""
    opening = (f"The shares before are the stored `stars` rows of {result_file} (the round-2 rule: the contamination "
               f"budget shared by the package's box fractions in the Kepler band); rung 5's seasons come from "
               f"{dossier_file}. The budget is {crowd}(1 - CROWDSAP) / CROWDSAP = {_fixed(budget, '.5f')}. Rung 5's "
               f"fitted PSF width is {s:.7f} Kepler pixels, the mean over its {seasons} voting season{plural} with a "
               f"width ({widths}). The rule of spec 12.1 gives each neighbour the fraction of a Gaussian of that "
               "width at its separation that falls inside the target's 3 x 3 box, over that of a star at the centre, "
               "times its Kepler-band flux relative to the target's (Gaia G as the proxy), and scales the products "
               f"down to the budget only when their sum passes it. {cap}")
    header = ("| TIC | Gaia DR3 | sep (arcsec) | TIC sep (arcsec) | sep (Kepler px) | G | Kepler flux / target | "
              "overlap | share before | share after |")
    lines = [f"# Leakage shares of {star} before and after spec 12.1 (23 October 2026)", "", opening, ""]
    basis = _basis(crowdsap, budget, total, s)
    lines += [basis, ""] if basis else []
    separations = separation_paragraph(result, s, rows, gaia_seps)
    lines += [separations, ""] if separations else []
    lines += [header, "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['id']} | {r['gaia_source_id'] or 'not matched'} | {r['sep_arcsec']:.2f} | "
                     f"{_fixed(r.get('tic_sep_arcsec'))} | {r['sep_pix']:.2f} | {_fixed(r['gmag'])} | "
                     f"{_fixed(r['kepler_flux'])} | {r['overlap']:.4g} | {_share(r['share_before'])} | "
                     f"{_share(r['share'])} |")
    lines += ["", f"{_nfpp('before', result)}.", "",
              (f"{_nfpp('after', after)}." if after else "NFPP after: not yet measured (the round-3 run).")]
    rated = [r for r in rows if r["share"] is not None]
    top = max(rated, key=lambda r: r["share"]) if rated else None
    if top is not None and top["share"] > BIG_SHARE:
        scaled = f", scaled by {scale:.4f} to the budget" if scale < 1 else ""
        largest = (f"The largest share after the rule is {top['share']:.3g} (TIC {top['id']}, "
                   f"{top['sep_arcsec']:.2f} arcsec, {top['sep_pix']:.2f} Kepler pixels): its overlap "
                   f"{top['overlap']:.2g} times its Kepler-band flux of {top['kepler_flux']:.3g} times the "
                   f"target's{scaled}.")
        before = _num(top.get("share_before"))
        if top["sep_pix"] > 2 and before is not None and top["share"] >= 0.5 * before:     # round 3 review M5
            largest += (" Spec 12.1 expected a star beyond two Kepler pixels to take effectively nothing; this one "
                        "keeps most of its round-2 share, so the NFPP it drives is for the controller to read before "
                        "the live runs.")
        lines += ["", largest]
    bound = ("The shares after the rule are a floor on the light each neighbour puts in the target's 3 x 3 box, and "
             f"that box light, with the NFPP it drives, is {tc.PSF_BOUND}. The third variant of spec 12.2, with the "
             "neighbours rung 5's difference images exclude removed, is where those neighbours are taken out.")
    lines += ["", bound, "", tc.PRF_CAVEAT, ""]
    return "\n".join(lines)


WIDTH_STEP_PIX = 0.05               # the width change the separation paragraph quotes the largest share's swing for


def separation_paragraph(result: dict, sigma_pix: float, rows: list[dict], gaia_seps: dict | None) -> str | None:
    """The paragraph on the separations and the cap (the round 3 final review, I1): which separation the overlaps are
    taken at and why, the largest-share star's Gaia and TIC separations, the sum of the products and the cap's state
    at each, and how far a width ``WIDTH_STEP_PIX`` either way moves that star's overlap. None without ``gaia_seps``
    or a budget."""
    budget = _num(((result.get("settings") or {}).get("flux_shares") or {}).get("budget"))
    if not gaia_seps or budget is None:
        return None
    gaia_total = _weights(result, sigma_pix, gaia_seps)[0]
    tic_total = _weights(result, sigma_pix)[0]

    def state(total: float) -> str:
        return "the cap binds" if total > budget else "the cap does not bind"
    rated = [r for r in rows if r["share"] is not None]
    top = max(rated, key=lambda r: r["share"]) if rated else None
    text = ("The overlaps are taken at the Gaia DR3 separations the dossier stores for the matched neighbours (epoch "
            "2016), the separations the exclusion test and the stars block use, not at the TIC's epoch-2000 ones the "
            "round-2 rows carry (beside them in the table); a star no Gaia neighbour matched keeps its TIC separation.")
    if top is not None and top.get("tic_sep_arcsec") is not None:
        text += (f" For TIC {top['id']}, which takes the largest share, that is {top['sep_arcsec']:.2f} arcsec "
                 f"against {top['tic_sep_arcsec']:.2f}.")
    text += (f" At the Gaia separations the products sum to {gaia_total:.4f} against the budget of {budget:.4f}, so "
             f"{state(gaia_total)}; at the TIC separations they sum to {tic_total:.4f}, and {state(tic_total)}.")
    if top is not None:
        ov = tc.overlap(top["sep_arcsec"], sigma_pix)
        lo, hi = (tc.overlap(top["sep_arcsec"], sigma_pix + d) / ov - 1 for d in (-WIDTH_STEP_PIX, WIDTH_STEP_PIX))
        text += (f" That star's overlap sits far down the Gaussian's tail, so it moves with the width too: "
                 f"{WIDTH_STEP_PIX:.2f} Kepler pixels less or more than {sigma_pix:.3f} changes it by "
                 f"{100 * lo:+.0f} and {100 * hi:+.0f} percent. The overlap is one-dimensional, along the separation, "
                 "a lower bound on the two-dimensional box fraction, so every share here is a floor.")
    return text


def _basis(crowdsap: float | None, budget: float | None, total: float, sigma: float) -> str | None:
    """The budget's basis in three parts (Task 15 addendum 2), in the engine's one wording
    (``skysift.crosscheck.budget_basis``, fix round 2, M6), then where the code reads C. None without a CROWDSAP or a
    budget."""
    from skysift.crosscheck import budget_basis
    from skysift.sources.kbonus_bkg import CATALOGUE, CROWDSAP_COLUMN
    if crowdsap is None or budget is None:
        return None
    return (f"{budget_basis(crowdsap, budget, total, total > budget, sigma, CROWDSAP_COLUMN, CATALOGUE)} "
            "`src/skysift/sources/kbonus_bkg.py` reads C into the target's `meta[\"crowdsap\"]`; the light-curve "
            "file's per-quarter CROWDSAP keywords (`lc/kbonus.py`) are the same metric per quarter.")


def _shown(path: str) -> str:
    """A path as the note prints it: from its ``.superpowers`` folder on when it has one, else as given."""
    parts = Path(path).parts
    return "/".join(parts[parts.index(".superpowers"):]) if ".superpowers" in parts else Path(path).as_posix()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--result", required=True, help="the round-2 result of tools/triceratops_check.py")
    ap.add_argument("--dossier", required=True, help="the star's dossier_data.json (rung 5's seasons)")
    ap.add_argument("--out", required=True, help="the note to write")
    ap.add_argument("--after", help="the round-3 result, for the NFPP after")
    a = ap.parse_args(argv)
    result = json.loads(Path(a.result).read_text(encoding="utf-8"))
    dossier = json.loads(Path(a.dossier).read_text(encoding="utf-8"))
    after = json.loads(Path(a.after).read_text(encoding="utf-8")) if a.after else None
    sigma = crosscheck.rung5_psf_sigma(dossier)
    if sigma["sigma_pix"] is None:
        print(f"no PSF width for the overlap: {sigma['reason']}", file=sys.stderr)
        return 2
    kic = (dossier.get("target") or {}).get("kic")
    star = f"KIC {int(kic)}" if kic else str((dossier.get("target") or {}).get("target_id") or "the star")
    seps = gaia_separations(dossier)
    text = render(result, sigma, note_rows(result, sigma["sigma_pix"], seps), after, star=star,
                  files=(_shown(a.result), _shown(a.dossier)), gaia_seps=seps)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8", newline="\n")
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
