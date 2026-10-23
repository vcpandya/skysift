"""Write the comparison note of spec 12.1: each neighbour's leakage share before and after the Kepler-pixel rule, with
the NFPP each set of shares drove.

It reads a round-2 result of ``tools/triceratops_check.py`` (its ``stars`` rows: separation, G, Kepler-band flux
relative to the target, the share of the round-2 rule and the Gaia DR3 id, and ``settings.flux_shares.budget``) and the
star's ``dossier_data.json`` (rung 5's seasons, for the PSF width through ``skysift.crosscheck.rung5_psf_sigma``),
recomputes each share under spec 12.1 with the tool's own ``overlap`` (overlap times Kepler-band flux, scaled down to
the budget only when the sum passes it) and writes a Markdown note. With ``--after`` (the round-3 result) it prints
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


def _sep(star: dict) -> float | None:
    """The separation a row's overlap is taken at: ``overlap_sep_arcsec`` (the tool's, for an added star whose
    ``sep_arcsec`` is None; round 3 review M1), else ``sep_arcsec``."""
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


def _weights(result: dict, sigma_pix: float) -> tuple[float, float | None, float]:
    """(the sum of overlap times Kepler-band flux over every non-target row with a flux, the budget, the scale
    min(1, budget / that sum), 1 without a budget or a sum)."""
    total = sum(tc.overlap(_sep(s), sigma_pix) * float(s["kepler_flux"])
                for s in result["stars"][1:] if _num(s.get("kepler_flux")) is not None)
    budget = _num(((result.get("settings") or {}).get("flux_shares") or {}).get("budget"))
    scale = min(1.0, budget / total) if budget is not None and total > 0 else 1.0
    return total, budget, scale


def note_rows(result: dict, sigma_pix: float) -> list[dict]:
    """The stars of ``result`` other than the target with a finite separation (``_sep``) at or below
    ``NOTE_WITHIN_ARCSEC``,
    sorted by separation: {"id", "gaia_source_id", "sep_arcsec", "sep_pix", "gmag", "kepler_flux", "overlap",
    "share_before" (the stored share), "share"}, the share overlap x kepler_flux x scale (``_weights``), None for a
    row without a Kepler-band flux."""
    _total, _budget, scale = _weights(result, sigma_pix)
    out = []
    for s in result["stars"][1:]:
        sep = _sep(s)
        if sep is None or sep > NOTE_WITHIN_ARCSEC:
            continue
        flux, ov = _num(s.get("kepler_flux")), tc.overlap(sep, sigma_pix)
        out.append({"id": str(s.get("id")), "gaia_source_id": s.get("gaia_source_id"), "sep_arcsec": sep,
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
           files: tuple[str, str] | None = None) -> str:
    """The note: the title, the paragraph on the sources, the budget, rung 5's width and the rule, the table of
    ``rows``, the NFPP before and after, the largest share after the rule when it passes ``BIG_SHARE``, the bound the
    shares are for PSF photometry and ``tc.PRF_CAVEAT`` last. ``files``: the result and dossier paths as printed."""
    s = sigma["sigma_pix"]
    total, budget, scale = _weights(result, s)
    crowdsap = _num(((result.get("settings") or {}).get("flux_shares") or {}).get("crowdsap"))
    widths = ", ".join(f"{w:.4f} (season {k})" for k, w in sigma["per_season"].items())
    seasons = len(sigma["seasons"])
    result_file, dossier_file = files or ("the round-2 result", "the star's dossier")
    n_flux = sum(1 for x in result["stars"][1:] if _num(x.get("kepler_flux")) is not None)
    if budget is None:
        cap = "The round-2 result stores no budget, so the shares after the rule are not capped."
    elif total > budget:
        cap = (f"Here the products sum to {total:.4f} over the {n_flux} neighbours with a Kepler-band flux, above the "
               f"budget, so they are scaled down to it by {scale:.4f}.")
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
    header = ("| TIC | Gaia DR3 | sep (arcsec) | sep (Kepler px) | G | Kepler flux / target | overlap | share before "
              "| share after |")
    lines = [f"# Leakage shares of {star} before and after spec 12.1 (23 October 2026)", "", opening, ""]
    basis = _basis(crowdsap, budget, total)
    lines += [basis, ""] if basis else []
    lines += [header, "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['id']} | {r['gaia_source_id'] or 'not matched'} | {r['sep_arcsec']:.2f} | "
                     f"{r['sep_pix']:.2f} | {_fixed(r['gmag'])} | {_fixed(r['kepler_flux'])} | {r['overlap']:.4g} | "
                     f"{_share(r['share_before'])} | {_share(r['share'])} |")
    lines += ["", f"{_nfpp('before', result)}.", "",
              (f"{_nfpp('after', after)}." if after else "NFPP after: not yet measured (the round-3 run).")]
    rated = [r for r in rows if r["share"] is not None]
    top = max(rated, key=lambda r: r["share"]) if rated else None
    if top is not None and top["share"] > BIG_SHARE:
        largest = (f"The largest share after the rule is {top['share']:.3g} (TIC {top['id']}, "
                   f"{top['sep_arcsec']:.2f} arcsec, {top['sep_pix']:.2f} Kepler pixels): its overlap "
                   f"{top['overlap']:.2g} times its Kepler-band flux of {top['kepler_flux']:.3g} times the target's.")
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


def _basis(crowdsap: float | None, budget: float | None, total: float) -> str | None:
    """The budget's basis in three parts, in the order every text gives them (Task 15 addendum 2): the catalogue's
    crowding metric, the PRF overlap rule, whether the cap bound. None without a CROWDSAP or a budget."""
    from skysift.sources.kbonus_bkg import CATALOGUE, CROWDSAP_COLUMN
    if crowdsap is None or budget is None:
        return None
    cap = (f"the cap bound: the overlap-weighted fluxes sum to {total:.4f}, above the budget of {budget:.4f}"
           if total > budget else
           f"the cap did not bind: the overlap-weighted fluxes sum to {total:.4f} against the budget of {budget:.4f}")
    return ("The budget's basis has three parts. First, it comes from the catalogue, not from SkySift: (1 - C) / C = "
            f"{budget:.5f} with C = {crowdsap:.8g} the KBonus crowding metric for this extraction, the "
            f"{CROWDSAP_COLUMN} column of the {CATALOGUE} (`src/skysift/sources/kbonus_bkg.py` reads it into the "
            "target's `meta[\"crowdsap\"]`; the light-curve file's per-quarter CROWDSAP keywords, `lc/kbonus.py`, "
            "are the same metric per quarter), the catalogue's own estimate that "
            f"{100 * (1 - crowdsap):.0f} percent of the fitted flux belongs to other sources, PRF wings included. "
            "Second, the split among the neighbours follows the PRF overlap rule above, each share an absolute "
            f"fraction of the target's flux. Third, {cap}.")


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
    text = render(result, sigma, note_rows(result, sigma["sigma_pix"]), after, star=star,
                  files=(_shown(a.result), _shown(a.dossier)))
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8", newline="\n")
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
