"""The submission pack's row builders (moved out of ``skysift.pack``): the ephemeris, parameter, shape, timing,
stellar-provenance and dilution rows of ``data/``, the data behind the note's figure and the next transits,
each read from the dossier JSON."""
from __future__ import annotations

import math
import re
from datetime import UTC, datetime

import numpy as np

from skysift import ephemeris as ephem
from skysift.ephemeris import BKJD_OFFSET
from skysift.pack_text import _bjd, _errors, _num, date_words, pm_digits, pm_text, value_text

# the KBonus header keywords of the dilution rows, defined once for the README and the CSV
CROWDSAP_DEFINITION = ("the fraction of the light in the aperture that belongs to the target (Kepler's CROWDSAP "
                       "keyword, 1 means no contamination)")
PSFFRAC_DEFINITION = "the fraction of the target's PSF flux inside the KBonus aperture (its PSFFRAC keyword)"
SOLAR_LOGG = 4.438          # log10 of the nominal solar surface gravity in cgs (IAU 2015 B3)


def _metrics(data: dict, rung: str) -> dict:
    return next((t.get("metrics") or {} for t in data.get("tests") or [] if t.get("rung") == rung), {})


def _test(data: dict, rung: str) -> dict | None:
    return next((t for t in data.get("tests") or [] if t.get("rung") == rung), None)


def star_characterized(data: dict) -> bool:
    """Whether the star block has parameters to quote: a route other than "none" and a radius."""
    star = data.get("star") or {}
    return star.get("route") not in (None, "none") and value_text(star.get("radius")) is not None


A_OVER_R_STELLAR = "a/R* from the stellar parameters (Kepler's third law)"
A_OVER_R_FITTED = "a/R* fitted"
A_OVER_R_FREE = "a/R* fitted (free fit)"
FIT_SOURCE = "quadratic limb-darkened, exposure-integrated transit fit"


def _comparison(data: dict) -> dict:
    shape = data.get("shape")
    return (shape.get("comparison") or {}) if isinstance(shape, dict) else {}


# the README table's b row when the density prior sets b (round-2 final review, I-4)
B_PRIOR_LABEL = "with the density prior"


def a_over_r_fitted(data: dict) -> bool:
    """Whether the texts quote the fitted a/R* (``shape.comparison.a_over_r_label`` "fitted": the shape constrains it,
    or there is no density prior) rather than the one from the stellar parameters. A dossier before version 5 quotes
    the stellar one."""
    return _comparison(data).get("a_over_r_label") == "fitted"


def _with_point(text: str, r: dict) -> str:
    """``text`` with the row's full-data value beside the resample median (spec 11.3), at the errors' decimals; the
    text alone for a row without errors or a full-data value."""
    point = _num(r.get("point"))
    if point is None or r.get("minus") is None or r.get("plus") is None:
        return text
    return f"{text} (full-data fit {point:.{pm_digits(r['minus'], r['plus'])}f})"


def param_rows(data: dict, *, both_a_over_r: bool = False) -> list[dict]:
    """The fit, star and planet parameters as rows {parameter, value, minus, plus, unit, source, text}. The radius
    ratio, the impact parameter and a fitted a/R* print the full-data value beside the resample median in ``text``.
    One a/R* row, ``A_OVER_R_STELLAR`` (planet.a_over_r) or ``A_OVER_R_FITTED`` (shape.a_over_r), as
    ``shape.comparison.a_over_r_label`` says (``a_over_r_fitted``); with ``both_a_over_r`` (``data/ephemeris.csv``
    and so its ExoFOP copy) both, the stellar one and ``A_OVER_R_FREE``, read from ``shape.fits.free`` (with the
    bootstrap errors when the free fit is the quoted one)."""
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
    shape = {r["key"]: r for r in shape_rows(data)}
    if _num(sh.get("depth_ppm")) is not None and "depth_source" not in (data.get("ephemeris") or {}):
        e = _num(sh.get("depth_err_ppm"))           # version 3 quotes rung 2's depth in the ephemeris rows
        add("fitted depth", {"median": sh["depth_ppm"], "minus": e, "plus": e}, "ppm", "rung 2 box depth")
    v5 = bool(_comparison(data))
    point_source = FIT_SOURCE if v5 else "rung 2 transit fit (uniform disc)"
    if sh.get("fit_ok") is True and shape:
        ok = (data.get("shape") or {}).get("ok") is True
        cmp = _comparison(data)
        # round-2 final review, I-4: a b the density prior sets says so in its own row
        b_name = (f"impact parameter ({B_PRIOR_LABEL})"
                  if cmp.get("quoted") == "prior" and cmp.get("constrained") is False else "impact parameter")
        for key, name in (("k", "radius ratio"), ("b", b_name)):
            r = shape.get(key)
            if r is None:
                continue
            text = _with_point(pm_text(r["value"], r["minus"], r["plus"], nd=r["nd"]), r)
            if ok and r["note"]:            # a degenerate b; without a bootstrap the duration names the reason
                text += f" ({r['note']})"
            rows.append({"parameter": name, "value": r["value"], "minus": r["minus"], "plus": r["plus"], "unit": "",
                         "source": (data.get("shape") or {}).get("method") if r["minus"] is not None
                         else point_source, "text": text})
    elif sh.get("fit_ok") is True:
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
    fitted = shape.get("a_over_r")
    if both_a_over_r or not a_over_r_fitted(data):
        add(A_OVER_R_STELLAR, planet.get("a_over_r"), "")
    if a_over_r_fitted(data) and not both_a_over_r and fitted is not None:
        text = _with_point(pm_text(fitted["value"], fitted["minus"], fitted["plus"], nd=fitted["nd"]), fitted)
        rows.append({"parameter": A_OVER_R_FITTED, "value": fitted["value"], "minus": fitted["minus"],
                     "plus": fitted["plus"], "unit": "", "source": (data.get("shape") or {}).get("method")
                     if fitted["minus"] is not None else FIT_SOURCE, "text": text})
    if both_a_over_r:
        free = ((data.get("shape") or {}).get("fits") or {}).get("free") or {}
        if free.get("ok") is True and _num(free.get("a_over_r")) is not None:
            quoted_free = _comparison(data).get("quoted") == "free" and fitted is not None
            lo, hi = (fitted["minus"], fitted["plus"]) if quoted_free else (None, None)
            value = fitted["value"] if quoted_free else _num(free["a_over_r"])
            rows.append({"parameter": A_OVER_R_FREE, "value": value, "minus": lo, "plus": hi, "unit": "",
                         "source": (data.get("shape") or {}).get("method") if lo is not None else
                         f"{FIT_SOURCE}, free of the density prior",
                         "text": pm_text(value, lo, hi, nd=fitted["nd"] if fitted else 1)})
    add("equilibrium temperature", planet.get("teq_k"), "K")
    add("insolation", planet.get("insolation_earth"), "Earth = 1")
    add("transit probability", planet.get("transit_probability"), "")
    for key, name in (("hz_inner_au", "habitable zone inner edge (runaway greenhouse)"),
                      ("hz_outer_au", "habitable zone outer edge (maximum greenhouse)")):
        add(name, planet.get(key), "AU", "Kopparapu et al. 2013, erratum coefficients")
    return rows


def ephemeris_rows(data: dict) -> list[dict]:
    """The ephemeris as rows {parameter, value, minus, plus, unit, source, text}: each value with its own error from
    the dossier's version 3 keys (none for a search value), the source naming the method."""
    eph = data.get("ephemeris") or {}
    e = _errors(eph)
    pe, t0e, tre, de = e["period"], e["t0"], e["t_ref"], e["depth"]
    timing = ("; ".join(x for x in (eph.get("period_source"), eph.get("ephemeris_method")) if x) if pe is not None
              else "box search (no formal error)")
    ref = f"{timing}; epoch {eph.get('epoch_ref')}, the centre of the series"
    bkjd = "BKJD (BJD - 2454833)"
    rows = (("period", eph.get("period_days"), pe, pe, "d", timing, 5),
            ("mid-transit time", eph.get("t0_bkjd"), t0e, t0e, bkjd, f"{timing}; epoch 0", 4),
            ("mid-transit time", _bjd(eph, "t0"), t0e, t0e, "BJD_TDB", f"{timing}; epoch 0", 4),
            ("reference mid-transit time", eph.get("t_ref_bkjd"), tre, tre, bkjd, ref, 4),
            ("reference mid-transit time", _bjd(eph, "t_ref"), tre, tre, "BJD_TDB", ref, 4),
            ("duration", eph.get("duration_h"), e["duration_minus"], e["duration_plus"], "h",
             eph.get("duration_source") or "box search", 2),
            ("depth", eph.get("depth_ppm"), de, de, "ppm", eph.get("depth_source") or "box search", 0),
            ("signal detection efficiency", eph.get("sde"), None, None, "", "box search", 1),
            ("signal-to-noise ratio", eph.get("snr"), None, None, "", "box search", 1),
            ("transits", eph.get("n_events"), None, None, "", "box search", 0))
    return [{"parameter": p, "value": v, "minus": _num(lo), "plus": _num(hi), "unit": u, "source": s,
             "text": f"{pm_text(v, lo, hi, nd=nd)} {u}".strip()} for p, v, lo, hi, u, s, nd in rows
            if _num(v) is not None]


# (parameter, shape key, unit, decimals without an error, rung 2's point value)
SHAPE_ROWS = (("radius ratio k", "k", "", 4, "fit_k"), ("impact parameter b", "b", "", 2, "fit_b"),
              ("a/R*", "a_over_r", "", 1, "fit_a_over_r"), ("T14", "t14_h", "h", 2, "fit_t14_h"),
              ("model depth", "model_depth_ppm", "ppm", 0, "fit_depth_ppm"),
              ("box depth", "box_depth_ppm", "ppm", 0, None), ("box duration", "box_duration_h", "h", 2, None))


# the shape block's a/R* names the fit it comes from (fix round 1 of Task 10, I-1 and M-8): under the density prior
# it follows the host's density, so it is not a second, independent a/R*
SHAPE_A_OVER_R_NAMES = {"prior": "a/R* (density-prior fit)", "free": "a/R* (free fit)"}


def _quoted_values(shape: dict) -> dict:
    """The quoted shape fit's full-data values by shape key (version 5: ``shape.fits`` and ``shape.comparison``; all
    five from the quoted fit, spec 11.3 as amended in fix round 1 of Task 10), {} before version 5 or without an ok
    quoted fit."""
    fits, cmp = shape.get("fits") or {}, shape.get("comparison") or {}
    fit = fits.get(cmp.get("quoted") or "free")
    if not (isinstance(fit, dict) and fit.get("ok") is True):
        return {}
    t14 = _num(fit.get("t14_days"))
    return {"k": fit.get("k"), "b": fit.get("b"), "a_over_r": fit.get("a_over_r"),
            "t14_h": None if t14 is None else t14 * 24.0, "model_depth_ppm": fit.get("depth_ppm")}


def shape_rows(data: dict) -> list[dict]:
    """The shape block as rows {key, parameter, value, minus, plus, unit, point, note, nd}: k, b, a/R*, T14, the
    model depth, the box depth and the box duration, each the resample median with its bootstrap errors and the
    full-data value (``point``) beside it. A parameter the block has no value for takes the quoted fit's full-data
    value (version 5), else rung 2's point value when the fit is ok; in version 5 the a/R* row is named by its fit
    (``SHAPE_A_OVER_R_NAMES``); a row without errors says why in ``note`` ("no
    error; <the degeneracy reason>" for b and a/R* of a degenerate fit, else the block's reason). [] without the
    block."""
    shape = data.get("shape")
    if not isinstance(shape, dict):
        return []
    sh = _metrics(data, "rung2_shape")
    fit = sh.get("fit_ok") is True
    quoted = _quoted_values(shape)
    variant = (shape.get("comparison") or {}).get("quoted")
    rows = []
    for name, key, unit, nd, point in SHAPE_ROWS:
        if key == "a_over_r" and variant in SHAPE_A_OVER_R_NAMES:
            name = SHAPE_A_OVER_R_NAMES[variant]
        block = shape.get(key)
        if isinstance(block, dict) and _num(block.get("median")) is not None:
            value, lo, hi, boot = (_num(block.get(x)) for x in ("median", "minus", "plus", "point"))
        elif fit and _num(quoted.get(key)) is not None:
            value, lo, hi, boot = _num(quoted[key]), None, None, _num(quoted[key])
        elif fit and point and _num(sh.get(point)) is not None:
            value, lo, hi, boot = _num(sh.get(point)), None, None, None
        else:
            continue
        note = ""
        if lo is None or hi is None:
            degenerate = shape.get("ok") and shape.get("degenerate") and key in ("b", "a_over_r")
            why = (shape.get("degeneracy_reason") if degenerate else shape.get("reason")) or "no reason stored"
            note = f"no error; {why.removesuffix(', errors not quoted')}"       # an older reason said it again
        rows.append({"key": key, "parameter": name, "value": value, "minus": lo, "plus": hi, "unit": unit,
                     "point": boot, "note": note, "nd": nd})
    return rows


def event_rows(data: dict) -> list[dict]:
    """One row per epoch of ``timing.times`` (of ``plots.events`` for a dossier without a timing block), {epoch,
    t_mid_bkjd, quarter, depth_ppm, err_ppm, coverage, ses, timed, timing_reason}: the predicted mid-time on the
    ephemeris the dossier folded on, the quarter, depth, error, coverage and single-event statistic from
    ``plots.events`` by epoch (blank when that event has no depth; the single-event statistic is the timing row's
    own ``ses`` when it carries one, the value the SES cut read on the search ephemeris, fix round 1 of Task 10,
    M-4), and whether the transit was timed (the timing row's ``used``) with the reason it was not, so ``events.csv``
    and ``transit_times.csv`` list the same epochs."""
    events = {int(e["epoch"]): e for e in (data.get("plots") or {}).get("events") or []
              if isinstance(e, dict) and e.get("epoch") is not None}
    timing = data.get("timing")
    eph = data.get("ephemeris") or {}
    period, t0 = _num(eph.get("period_days")), _num(eph.get("t0_bkjd"))

    def row(epoch: int, timed, reason, cut: dict | None = None) -> dict:
        ev = events.get(epoch) or {}
        t_mid = ev.get("t_mid")
        if t_mid is None and period is not None and t0 is not None:
            t_mid = round(t0 + epoch * period, 3)
        ses = cut["ses"] if cut is not None and "ses" in cut else ev.get("ses")
        return {"epoch": epoch, "t_mid_bkjd": t_mid, "quarter": ev.get("quarter"), "depth_ppm": ev.get("depth_ppm"),
                "err_ppm": ev.get("err_ppm"), "coverage": ev.get("coverage"), "ses": ses, "timed": timed,
                "timing_reason": reason}
    if isinstance(timing, dict) and isinstance(timing.get("times"), list):
        return [row(int(t["epoch"]), t.get("used"), t.get("reason"), t) for t in timing["times"]
                if isinstance(t, dict) and t.get("epoch") is not None]
    return [row(e, None, None) for e in events]


def timing_rows(data: dict) -> list[dict]:
    """One row per measured transit {epoch, t_bkjd, t_bjd, err_days, chi2_red, used, reason}, BJD_TDB from BKJD;
    [] without a timing block."""
    timing = data.get("timing")
    if not isinstance(timing, dict):
        return []
    out = []
    for t in timing.get("times") or []:
        bkjd = _num(t.get("t_bkjd"))
        out.append({"epoch": t.get("epoch"), "t_bkjd": bkjd,
                    "t_bjd": None if bkjd is None else round(bkjd + BKJD_OFFSET, 6), "err_days": t.get("err_days"),
                    "chi2_red": t.get("chi2_red"), "used": t.get("used"), "reason": t.get("reason")})
    return out


_REFERENCE = re.compile(r"\(([^()]*\b(?:19|20)\d{2}\b[^()]*)\)")
STAR_ROWS = (("effective temperature", "teff", "K"), ("radius", "radius", "solar radii"),
             ("mass", "mass", "solar masses"), ("density", "density_gcc", "g/cm3"), ("distance", "distance_pc", "pc"))


def _split_source(source: str | None) -> tuple[str, str, str]:
    """(method, inputs, reference) of a stored source string such as "Mann et al. 2015 R(M_Ks) (Mann, A. W. et al.
    2015, ApJ 804, 64), 2MASS Ks 13.030, A_Ks 0 to 0.07, Gaia parallax": the reference is the first parenthesis that
    names a year, the method what comes before it, the inputs what follows. A source without one is all method.

    The parse is left on the string on purpose: a structured source would need method, inputs and reference fields
    on the Value records of skysift.stars, which would change the stored star block of every version-3 dossier."""
    source = source or ""
    m = _REFERENCE.search(source)
    if not m:
        return source.strip(), "", ""
    return source[:m.start()].strip(" ,;"), source[m.end():].strip(" ,;"), m.group(1).strip()


def logg_value(star: dict) -> dict | None:
    """log g (cgs) from the star block's mass and radius, {"median", "minus", "plus"}: ``SOLAR_LOGG`` + log10 M -
    2 log10 R. log g is low when the mass is low and the radius high, so the lower side is the quadrature of the
    mass's minus and twice the radius's plus (relative), the upper side the reverse. The correlation of mass and
    radius in the stellar draws is not stored, so it is ignored. None without both."""
    m, r = star.get("mass"), star.get("radius")
    if not (isinstance(m, dict) and isinstance(r, dict)):
        return None
    mv, rv = _num(m.get("median")), _num(r.get("median"))
    if not mv or not rv or mv <= 0 or rv <= 0:
        return None

    def side(mass_side: str, radius_side: str) -> float | None:
        em, er = _num(m.get(mass_side)), _num(r.get(radius_side))
        return None if em is None or er is None else round(math.hypot(em / mv, 2 * er / rv) / math.log(10), 3)
    return {"median": round(SOLAR_LOGG + math.log10(mv) - 2 * math.log10(rv), 3), "minus": side("minus", "plus"),
            "plus": side("plus", "minus")}


GAIA_FLAG_ROWS = (("RUWE", "ruwe", ""), ("Gaia non-single-star flag", "non_single_star", ""),
                  ("GSP-Phot E(BP-RP)", "ebpminrp_gspphot", "mag"))


def provenance_rows(data: dict) -> list[dict]:
    """The star block as rows {parameter, value, minus, plus, unit, method, inputs, reference}: each parameter with
    the method, inputs and reference its stored source names (the Gaia DR3 id first among the inputs), the spectral
    type with the temperature's, log g from the mass and radius (``logg_value``), and RUWE, the Gaia non-single-star
    flag and GSP-Phot E(BP-RP) as Gaia DR3 gives them (``GAIA_FLAG_ROWS``, spec 11.6), each when stored."""
    star, gaia = data.get("star") or {}, (data.get("target") or {}).get("gaia_id")
    rows = []

    def inputs(rest: str) -> str:
        return "; ".join(x for x in (f"Gaia DR3 {gaia}" if gaia else None, rest) if x)
    for name, key, unit in STAR_ROWS:
        v = star.get(key)
        if not isinstance(v, dict) or _num(v.get("median")) is None:
            continue
        method, rest, ref = _split_source(v.get("source"))
        rows.append({"parameter": name, "value": _num(v["median"]), "minus": _num(v.get("minus")),
                     "plus": _num(v.get("plus")), "unit": unit, "method": method, "inputs": inputs(rest),
                     "reference": ref})
    if star.get("spectral_type"):
        method, rest, ref = _split_source((star.get("teff") or {}).get("source"))
        rows.append({"parameter": "spectral type", "value": star["spectral_type"], "minus": None, "plus": None,
                     "unit": "", "method": method, "inputs": inputs(rest), "reference": ref})
    logg = logg_value(star)
    if logg is not None:
        rest = _split_source((star.get("radius") or {}).get("source"))[1]
        rows.append({"parameter": "surface gravity log g", **{k: logg[k] for k in ("minus", "plus")},
                     "value": logg["median"], "unit": "cgs",
                     "method": f"log10(G M / R^2) from the mass and radius above (solar {SOLAR_LOGG}), errors in "
                               "quadrature with their correlation ignored", "inputs": inputs(rest), "reference": ""})
    for name, key, unit in GAIA_FLAG_ROWS:
        v = star.get(key)
        if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        rows.append({"parameter": name, "value": v, "minus": None, "plus": None, "unit": unit,
                     "method": "Gaia DR3 gaia_source", "inputs": inputs(""), "reference": "Gaia Collaboration 2023"})
    return rows


def dilution_rows(data: dict) -> list[dict]:
    """The dilution numbers as rows {key, parameter, value, minus, plus, unit, source, text}: the KBonus header
    keywords CROWDSAP and PSFFRAC for this star (the target meta; the source column defines each) and rung 5's leak
    test, the depth with its error at the ephemeris in the neighbour's own aperture flux. Each is left out when it
    was not stored."""
    meta = (data.get("target") or {}).get("meta") or {}
    rows = []
    for key, name, what in (("crowdsap", "target light fraction (CROWDSAP)", CROWDSAP_DEFINITION),
                            ("psffrac", "PSF flux fraction (PSFFRAC)", PSFFRAC_DEFINITION)):
        v = _num(meta.get(key))
        if v is not None:
            rows.append({"key": key, "parameter": name, "value": v, "minus": None, "plus": None, "unit": "",
                         "source": f"KBonus header keyword {key.upper()}: {what}", "text": f"{v:.2f}"})
    px = _metrics(data, "rung5_pixels")
    leak = px.get("leak_depth_ppm")
    if isinstance(leak, list) and leak and _num(leak[0]) is not None:
        e = _num(leak[1]) if len(leak) > 1 else None
        kic = meta.get("tpf_org") or px.get("neighbour_kic")
        whose = f"KIC {kic}" if kic else "the neighbour"
        rows.append({"key": "leak", "parameter": "dip in the neighbour's aperture (leak test)", "value": _num(leak[0]),
                     "minus": e, "plus": e, "unit": "ppm",
                     "source": f"rung 5 leak test: the box depth at the ephemeris in the optimal-aperture flux of "
                               f"{whose}", "text": f"{pm_text(leak[0], e, e, nd=0, comma=True)} ppm",
                     "kic": kic})
    return rows


def figure_data_rows(data: dict) -> list[list]:
    """The data behind the note's figure, one row per fold bin: [phase_h, flux_ppm, err_ppm, n, model_ppm], the model
    interpolated at the bin's phase; [] without the plots block."""
    plots = data.get("plots") or {}
    model = [(float(p[0]), float(p[1])) for p in plots.get("model") or []
             if _num(p[0]) is not None and _num(p[1]) is not None]
    xs, ys = np.array([p[0] for p in model]), np.array([p[1] for p in model])
    out = []
    for b in plots.get("fold_bins") or []:
        if not isinstance(b, list) or len(b) < 4 or _num(b[0]) is None:
            continue
        m = round(float(np.interp(b[0], xs, ys)), 2) if model else None
        out.append([b[0], b[1], b[2], b[3], m])
    return out


def julian_date(when: datetime) -> float:
    """The Julian date of a UTC time (2440587.5 at the Unix epoch); within a few minutes of BJD_TDB, which is all
    "the next transits after a date" needs."""
    return 2440587.5 + when.timestamp() / 86400.0


def _utc_of(jd: float) -> datetime:
    return datetime.fromtimestamp((jd - 2440587.5) * 86400.0, UTC)


def next_transits(data: dict, after_bjd: float, n: int = 3) -> list[dict]:
    """The first ``n`` transits predicted after ``after_bjd`` by the linear ephemeris (``ephem.predict_from``),
    each {epoch, t_bkjd, t_bjd, window_min, date, text}: the window is the 1-sigma prediction error in minutes. []
    without a timing block or when its fit is not ok."""
    timing = data.get("timing")
    if not isinstance(timing, dict) or timing.get("ok") is not True:
        return []
    after, period = float(after_bjd) - BKJD_OFFSET, float(timing["period"])
    epoch = int(timing["epoch_ref"]) + math.floor((after - float(timing["t_ref"])) / period)
    while ephem.predict_from(timing, epoch)[0] <= after:
        epoch += 1
    out = []
    for e in range(epoch, epoch + n):
        t, err = ephem.predict_from(timing, e)
        bjd, window = t + BKJD_OFFSET, (err or 0.0) * 1440.0
        day = date_words(_utc_of(bjd))
        out.append({"epoch": e, "t_bkjd": t, "t_bjd": bjd, "window_min": window, "date": day,
                    "text": f"epoch {e}: BJD_TDB {bjd:.4f} ± {window:.0f} min ({day})"})
    return out
