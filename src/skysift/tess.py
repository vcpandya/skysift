"""The TESS non-detection statement (referee pack spec 10): should TESS have seen this Kepler transit, and does its
silence say anything. Pure: every helper takes plain numbers (``statement`` takes the stored inputs and the dossier's
ephemeris block as dicts), nothing here reads the store or the network; ``tools/tess_inputs.py`` fetches the inputs
once into ``targets.meta["tess"]`` and the dossier reads them from there.

``SECTORS`` holds the 14 sectors that observed Kepler-field stars as of 22 October 2026 (14, 15, 26 in Cycle 2 at
30-minute full-frame cadence; 40, 41, 53, 54, 55 in Cycle 4 at 10 minutes; 56 in Cycle 5 and 74, 75, 80, 81, 82 in
Cycle 6 at 200 seconds), each with its observing segments in BTJD (BJD - 2457000), one per row of MIT's FFI
observation-times file (https://tess.mit.edu/public/files/TESS_FFI_observation_times.csv, read 22 October 2026), the
Sector 26 placeholder end (1971-12-31) repaired from the orbit-times file
(https://tess.mit.edu/public/files/TESS_orbit_times.csv), as tabulated in
docs/superpowers/notes/2026-10-22-tess-non-detection-research.md section 1.2. Sector 56 is not in that table: the
research took Cycle 5 to miss the field, which holds for its four test positions but not at the field's edge, where
TESScut lists Sector 56 for KIC 9243078 and KIC 10352243. Its four segments come from the same FFI file (orbits 119
and 120; UTC 2022-09-02 18:02:05 to 09-09 05:22:05, 09-09 10:25:25 to 09-16 00:18:45, 09-16 05:22:05 to 09-23
09:02:04 and 09-23 14:02:04 to 09-30 15:18:44), converted to BTJD as the research note converts the others (the
Julian date of the UTC time minus 2457000, which reproduces its Sector 55 and 82 rows to the third decimal). Sector
109 was in progress on that date and Sectors 119, 120, 125, 126, 127 and 132 are provisional future coverage: none is
counted. A sector TESScut lists that is not here is recorded, named in every text as not assessed and not counted.
The segment times carry no barycentric correction (up to about 0.006 d), which is fine for deciding whether a transit
falls in a window and not for timing.

The rest follows spec sections 10.4 to 10.8: transit count and timing error (``count_transits``), Gaussian aperture
dilution (``aperture_fraction``, ``dilution``), nominal and conservative noise (``sigma_1h_nominal``,
``sigma_1h_conservative``, ``scale_to_duration``), significance (``significance``) and the verdict (``verdict``),
assembled by ``statement``; ``note_sentence``, ``methods_sentence``, ``readme_row`` and ``exofop_line`` are the
texts."""
from __future__ import annotations

import itertools
import math

from skysift.pack_text import Raw, tex_escape

BTJD_OFFSET = 2457000.0         # BTJD = BJD_TDB - 2457000 (= BKJD - 2167)
LAST_COMPLETED_SECTOR = 108     # 22 October 2026: Sector 109 in progress
SECTORS: dict[int, dict] = {
    14: {"cycle": 2, "cadence_min": 30, "segments": [(1683.354, 1696.375), (1697.354, 1710.187)]},
    15: {"cycle": 2, "cadence_min": 30, "segments": [(1711.375, 1723.854), (1724.937, 1737.395)]},
    26: {"cycle": 2, "cadence_min": 30, "segments": [(2010.270, 2022.187), (2023.124, 2035.142)]},
    40: {"cycle": 4, "cadence_min": 10, "segments": [(2390.652, 2404.395), (2405.326, 2418.853)]},
    41: {"cycle": 4, "cadence_min": 10, "segments": [(2419.985, 2432.597), (2433.715, 2446.576)]},
    53: {"cycle": 4, "cadence_min": 10, "segments": [(2743.992, 2755.992), (2756.909, 2768.978)]},
    54: {"cycle": 4, "cadence_min": 10, "segments": [(2769.895, 2782.332), (2783.527, 2796.124)]},
    55: {"cycle": 4, "cadence_min": 10, "segments": [(2797.096, 2809.957), (2810.867, 2824.263)]},
    56: {"cycle": 5, "cadence_min": 200 / 60, "segments": [(2825.251, 2831.724), (2831.934, 2838.513),
                                                           (2838.724, 2845.876), (2846.085, 2853.138)]},
    74: {"cycle": 6, "cadence_min": 200 / 60, "segments": [(3312.858, 3319.256), (3319.467, 3325.846),
                                                           (3326.057, 3332.946), (3333.115, 3339.568)]},
    75: {"cycle": 6, "cadence_min": 200 / 60, "segments": [(3339.779, 3346.175), (3346.385, 3353.554),
                                                           (3353.765, 3360.119), (3360.330, 3367.480)]},
    80: {"cycle": 6, "cadence_min": 200 / 60, "segments": [(3479.882, 3486.453), (3486.664, 3493.342),
                                                           (3493.553, 3499.995), (3500.201, 3506.335)]},
    81: {"cycle": 6, "cadence_min": 200 / 60, "segments": [(3506.546, 3513.384), (3513.595, 3519.213),
                                                           (3519.782, 3526.106), (3526.317, 3533.176)]},
    82: {"cycle": 6, "cadence_min": 200 / 60, "segments": [(3533.386, 3539.259), (3539.470, 3545.460),
                                                           (3545.671, 3552.162), (3552.372, 3559.245)]},
}

# ---- dilution (spec 10.5)
PIXEL_ARCSEC = 21.0             # Ricker et al. 2015, Table 1 (0.35 arcmin)
PSF_SIGMA_PIX = 0.75            # Giacalone et al. 2021, section 2.1: a 2D Gaussian PSF for every star
# the centre pixel and its four edge-sharing neighbours, the target at the centre pixel's centre: between the six
# pixels SPOC assigns at Tmag 13 (Guerrero et al. 2021) and the four the research takes as the faint floor
APERTURE_PIXELS = ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1))
# Ricker et al. 2015's two-pixel rule with unit weight: the two-pixel-radius sum, printed for comparison only. It is
# no bound: a bright star just outside 42 arcsec counts in the Gaussian sum and not in this one
HARD_CUT_ARCSEC = 42.0
CONE_DEG = 0.03                 # the TIC cone the tool asks for (108 arcsec, past five pixels)
TMAG_TIC = "TIC"
TMAG_EQ1 = "Gaia (Stassun 2019 eq. 1)"
TMAG_EQ2 = "Gaia (Stassun 2019 eq. 2)"

# ---- noise (spec 10.6)
# ln sigma_1h(ppm) = sum c_k T^k: the TIC polynomial (Stassun et al. 2018, arXiv v1 section 3.3; the ticgen source)
NOMINAL_COEFFS = (3.29685004771, 0.850021465753, -0.285041632435, 0.0395908321369, -0.0022308015894,
                  4.73508403525e-5)
# the SPOC robust-RMS 1-hour CDPP medians over the 13 Kepler-field sectors (Twicken et al. 2025; research note
# section 2.3), Tmag against ppm, interpolated linearly in Tmag; beyond the table the TIC polynomial times the
# table's excess over it at the nearer end (1.730 at T = 16, 1.328 at T = 12), and never below the polynomial
CONSERVATIVE_1H = ((12.0, 790.0), (13.0, 1625.0), (14.0, 3810.0), (15.0, 8804.0), (15.5, 14449.0), (16.0, 24151.0))
# the empirical CDPP(tau) / CDPP(1 h) medians for Tmag 15 to 16 (research note section 2.4), interpolated in log tau;
# below 1 h the white-noise rule, beyond 6 h held at the 6 h ratio (the conservative side)
CONSERVATIVE_RATIOS = ((1.0, 1.0), (2.0, 0.752), (3.0, 0.647), (6.0, 0.514))

# ---- verdict (spec 10.7)
SHOULD_DETECT_S = 7.1
SHOULD_DETECT_DOC = ("A conservative folded significance at or above 7.1: the Kepler threshold on which Christ, "
                     "Montet and Fabrycky 2019 (AJ 157, 235) centre their TESS recovery logistic; Sullivan et al. "
                     "2015 (ApJ 809, 77) use 7.3.")
NOT_EXPECTED_S = 3.0
NOT_EXPECTED_DOC = ("A nominal folded significance below 3: the floor Christ, Montet and Fabrycky 2019 (AJ 157, "
                    "235) report as adequate for characterising known planets.")
VERDICTS = ("should have detected", "marginal", "not expected", "not observed in transit")
VERDICT_PHRASES = {
    "should have detected": "TESS should have detected the transit and a TESS search is the natural follow-up",
    "marginal": "a TESS detection would be marginal",
    "not expected": "TESS is not expected to have detected the transit",
    "not observed in transit": "no transit fell in a TESS window",
}
NOT_FETCHED = "TESS inputs not fetched"
NOT_ASSESSED = "TESS coverage was not assessed."
PREDICTION_CAVEAT = ("This is a prediction of detectability: no TESS light curve was examined. A fold of the TESScut "
                     "full-frame pixels needs photometry of its own and is the follow-up for a candidate TESS should "
                     "have detected.")


def _num(x) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


# ---------------------------------------------------------------------------------------------------- magnitudes

def tmag_from_gaia(g, bp_rp=None) -> float | None:
    """The TESS magnitude from Gaia: Stassun et al. 2019 equation 1, T = G - 0.00522555 x^3 + 0.0891337 x^2
    - 0.633923 x + 0.0324473 with x = BP - RP, or equation 2, T = G - 0.430, without a colour; None without G. The
    relation is fitted for -0.2 < x < 3.5 and extrapolated by the paper to -1 to 6; x is not clipped, since the Gaia
    colours of field stars fall inside that range."""
    g = _num(g)
    if g is None:
        return None
    x = _num(bp_rp)
    if x is None:
        return g - 0.430
    return g - 0.00522555 * x ** 3 + 0.0891337 * x ** 2 - 0.633923 * x + 0.0324473


# ---------------------------------------------------------------------------------------------------- dilution

def _interval(lo: float, hi: float, sigma: float) -> float:
    s = sigma * math.sqrt(2.0)
    return 0.5 * (math.erf(hi / s) - math.erf(lo / s))


def aperture_fraction(dx_pix: float, dy_pix: float, sigma: float = PSF_SIGMA_PIX,
                      pixels=APERTURE_PIXELS) -> float:
    """The fraction of a star's light, a 2D Gaussian of ``sigma`` pixels centred ``(dx_pix, dy_pix)`` from the
    target, that falls in the aperture pixels (unit squares centred on the integer offsets in ``pixels``)."""
    return sum(_interval(px - 0.5 - dx_pix, px + 0.5 - dx_pix, sigma)
               * _interval(py - 0.5 - dy_pix, py + 0.5 - dy_pix, sigma) for px, py in pixels)


def dilution(target_tmag: float, neighbours) -> dict:
    """The contamination of the target's aperture: ``neighbours`` are ``(tmag, dra_pix, ddec_pix, dst_arcsec)``
    (``dst_arcsec`` None to take it from the offsets). ``c`` = sum of 10^(-0.4 (T_i - T)) f_i / f_target with the
    Gaussian aperture fractions, ``c_hard42`` the two-pixel-radius sum with unit weight inside 42 arcsec and none
    outside, for comparison (no bound), ``t_total`` = T - 2.5 log10(1 + c), the brightness that sets the noise. A
    neighbour without a magnitude is left out and counted in ``n_without_tmag``; one without both offsets is left
    out and counted in ``n_without_offsets``, never placed on the target."""
    f_target = aperture_fraction(0.0, 0.0)
    c = hard = 0.0
    used = missing = no_offsets = 0
    for tmag, dx, dy, dst in neighbours:
        t, dx, dy = _num(tmag), _num(dx), _num(dy)
        if t is None:
            missing += 1
            continue
        if dx is None or dy is None:
            no_offsets += 1
            continue
        used += 1
        ratio = 10 ** (-0.4 * (t - target_tmag))
        c += ratio * aperture_fraction(dx, dy) / f_target
        dist = _num(dst)
        if dist is None:
            dist = math.hypot(dx, dy) * PIXEL_ARCSEC
        if dist <= HARD_CUT_ARCSEC:
            hard += ratio
    return {"c": c, "c_hard42": hard, "t_total": target_tmag - 2.5 * math.log10(1 + c),
            "aperture_pixels": len(APERTURE_PIXELS), "target_fraction": f_target, "n_neighbours": used,
            "n_without_tmag": missing, "n_without_offsets": no_offsets}


# ---------------------------------------------------------------------------------------------------- noise

def sigma_1h_nominal(tmag: float) -> float:
    """The 1-hour noise in ppm from the TIC polynomial (Stassun et al. 2018, arXiv v1 section 3.3; ticgen)."""
    return math.exp(sum(c * tmag ** k for k, c in enumerate(NOMINAL_COEFFS)))


def sigma_1h_conservative(tmag: float) -> float:
    """The 1-hour noise in ppm from the SPOC CDPP medians of the Kepler-field sectors (Twicken et al. 2025), linear
    in Tmag between the table's rows; beyond the table the TIC polynomial times the table's excess over it at the
    nearer end (24151 / 13957 = 1.730 above T = 16, 790 / 595 = 1.328 below T = 12), so the faint end keeps rising
    with the polynomial; never below the nominal noise."""
    table = CONSERVATIVE_1H
    nominal = sigma_1h_nominal(tmag)
    if tmag <= table[0][0] or tmag >= table[-1][0]:
        end_t, end_s = table[0] if tmag <= table[0][0] else table[-1]
        return max(nominal, nominal * end_s / sigma_1h_nominal(end_t))
    for (t0, s0), (t1, s1) in itertools.pairwise(table):
        if t0 <= tmag <= t1:
            return max(nominal, s0 + (s1 - s0) * (tmag - t0) / (t1 - t0))
    raise AssertionError("unreachable")


def _empirical_ratio(t_h: float) -> float:
    table = CONSERVATIVE_RATIOS
    if t_h <= table[0][0]:
        return 1.0 / math.sqrt(t_h)
    if t_h >= table[-1][0]:
        return table[-1][1]
    for (a, ra), (b, rb) in itertools.pairwise(table):
        if a <= t_h <= b:
            return ra + (rb - ra) * math.log(t_h / a) / math.log(b / a)
    raise AssertionError("unreachable")


def scale_to_duration(sigma_1h: float, t14_h: float, empirical: bool = False) -> float:
    """The noise over a transit of ``t14_h`` hours: white noise (1 / sqrt(T14 / 1 h), the ticgen rule) for the
    nominal model, the SPOC's empirical ratios (``CONSERVATIVE_RATIOS``) with ``empirical``."""
    return sigma_1h * (_empirical_ratio(t14_h) if empirical else 1.0 / math.sqrt(t14_h))


# ---------------------------------------------------------------------------------------------------- transits

def timing_error(n: int, sigma_t0, sigma_p) -> float | None:
    """sqrt(sigma_T0^2 + (n sigma_P)^2) in days at epoch ``n`` from T0; None without both errors."""
    st0, sp = _num(sigma_t0), _num(sigma_p)
    if st0 is None or sp is None:
        return None
    return math.hypot(st0, n * sp)


def count_transits(t0_btjd: float, period: float, t14_h: float, segments, sigma_t0=None, sigma_p=None) -> dict:
    """The epochs n of T0 + nP whose mid-time falls inside a segment with at least half of T14 covered, the timing
    error at the latest epoch the segments reach (``timing_error``), and ``uncertain`` when that error exceeds T14 / 2
    or is not known."""
    half = t14_h / 48.0
    epochs = []
    for a, b in segments:
        for n in range(math.ceil((a - t0_btjd) / period), math.floor((b - t0_btjd) / period) + 1):
            mid = t0_btjd + n * period
            covered = min(b, mid + half) - max(a, mid - half)
            if a <= mid <= b and covered >= half - 1e-12:
                epochs.append(n)
    end = max((b for _a, b in segments), default=t0_btjd)
    err = timing_error(math.floor((end - t0_btjd) / period), sigma_t0, sigma_p)
    return {"n_transits": len(epochs), "epochs": sorted(epochs), "timing_error_days": err,
            "uncertain": err is None or err > half}


def expected_transits(segments, period: float) -> float:
    """The number of transits expected from phase coverage: the segments' summed length over the period."""
    return sum(b - a for a, b in segments) / period


def significance(depth_ppm: float, sigma_t14_ppm: float, n: int) -> tuple[float, float]:
    """(per-transit S1 = depth / sigma(T14), folded S = sqrt(N) S1): Twicken et al. 2025 equation 3 in one band."""
    s1 = depth_ppm / sigma_t14_ppm
    return s1, math.sqrt(max(n, 0)) * s1


def verdict(n_transits: int, folded_nominal: float, folded_conservative: float) -> str:
    """One of ``VERDICTS``: "not observed in transit" when no transit fell in a window; "should have detected" at a
    conservative folded S of ``SHOULD_DETECT_S`` or more; "not expected" below ``NOT_EXPECTED_S`` nominal; else
    "marginal". The thresholds are cited (``SHOULD_DETECT_DOC``, ``NOT_EXPECTED_DOC``), the words are ours."""
    if n_transits <= 0:
        return "not observed in transit"
    if folded_conservative >= SHOULD_DETECT_S:
        return "should have detected"
    if folded_nominal < NOT_EXPECTED_S:
        return "not expected"
    return "marginal"


# ---------------------------------------------------------------------------------------------------- the block

def _unavailable(why: str) -> dict:
    return {"available": False, "caveats": [why]}


def statement(meta: dict | None, ephemeris: dict | None) -> dict:
    """The dossier's ``tess`` block from the stored inputs (``targets.meta["tess"]``, written by
    ``tools/tess_inputs.py``) and the dossier's ``ephemeris`` block (the fitted period, T0 in BJD, T14 in hours, the
    depth in ppm, the period and T0 errors). ``{"available": False, "caveats": [...]}`` without the inputs, a TESS
    magnitude or one of the four ephemeris values."""
    if not isinstance(meta, dict):
        return _unavailable(NOT_FETCHED)
    eph = ephemeris or {}
    tmag = _num(meta.get("tmag"))
    if tmag is None:
        return _unavailable("no TESS magnitude for the star")
    values = {"period": _num(eph.get("period_days")), "mid-transit time": _num(eph.get("t0_bjd")),
              "duration": _num(eph.get("duration_h")), "depth": _num(eph.get("depth_ppm"))}
    missing = [k for k, v in values.items() if v is None or (k != "mid-transit time" and v <= 0)]
    if missing:
        return _unavailable(f"the ephemeris has no {' or '.join(missing)}")
    period, t0_bjd, t14, depth = values.values()
    t0 = t0_bjd - BTJD_OFFSET
    sig_t0, sig_p = eph.get("t0_bjd_err"), eph.get("period_err_days")
    caveats = [PREDICTION_CAVEAT]
    rows, skipped, segments = [], [], []
    for n in sorted({int(s) for s in meta.get("sectors") or []}):
        if n not in SECTORS:
            skipped.append(n)
            continue
        info = SECTORS[n]
        got = count_transits(t0, period, t14, info["segments"], sig_t0, sig_p)
        segments += info["segments"]
        rows.append({"sector": n, "cycle": info["cycle"], "cadence_min": info["cadence_min"], **got})
    n_transits = sum(r["n_transits"] for r in rows)
    n_expected = expected_transits(segments, period)
    errors = [r["timing_error_days"] for r in rows]
    terr = max(errors) if errors and None not in errors else None
    uncertain = any(r["uncertain"] for r in rows)
    neighbours = [(nb.get("tmag"), nb.get("dra_pix"), nb.get("ddec_pix"), nb.get("dst_arcsec"))
                  for nb in meta.get("neighbours") or [] if isinstance(nb, dict)]
    dil = dilution(tmag, neighbours)
    depth_tess = depth / (1 + dil["c"])
    nom_1h, con_1h = sigma_1h_nominal(dil["t_total"]), sigma_1h_conservative(dil["t_total"])
    nom_t = scale_to_duration(nom_1h, t14)
    con_t = max(nom_t, scale_to_duration(con_1h, t14, empirical=True))     # the conservative model never the lower
    s1_nom, s_nom = significance(depth_tess, nom_t, n_transits)
    s1_con, s_con = significance(depth_tess, con_t, n_transits)
    word = verdict(n_transits, s_nom, s_con)
    if meta.get("tmag_source") and meta.get("tmag_source") != TMAG_TIC:
        caveats.append(f"The star's TESS magnitude comes from {meta['tmag_source']}: the TIC gives none.")
    if dil["n_without_tmag"]:
        k = dil["n_without_tmag"]
        caveats.append(f"{k} neighbour{'s' if k != 1 else ''} without a TESS magnitude or Gaia G "
                       f"{'are' if k != 1 else 'is'} left out of the dilution.")
    if dil["n_without_offsets"]:
        k = dil["n_without_offsets"]
        caveats.append(f"{k} neighbour{'s' if k != 1 else ''} without a stored offset from the star "
                       f"{'are' if k != 1 else 'is'} left out of the dilution rather than placed on the star.")
    for n in skipped:
        caveats.append(f"TESScut lists Sector {n} for this star, so TESS observed it, but the sector's observing "
                       f"windows are not in the table here (to Sector {LAST_COMPLETED_SECTOR}, 22 October 2026): its "
                       "transits are not counted and every text names it as not assessed.")
    if rows and terr is None:
        caveats.append("The ephemeris carries no error, so which transits fall in the TESS windows is uncertain; "
                       f"about {n_expected:.1f} are expected from phase coverage.")
    elif uncertain:
        caveats.append(f"The timing error at the TESS epoch ({terr * 24:.1f} h) exceeds half the duration, so which "
                       f"transits fall in the windows is uncertain; about {n_expected:.1f} are expected from phase "
                       "coverage (Battley et al. 2021's \"outside\" class).")
    block = {
        "available": True, "queried": meta.get("queried"), "tic": meta.get("tic"), "tmag": tmag,
        "tmag_source": meta.get("tmag_source"),
        "ephemeris_used": {"period_days": period, "t0_btjd": t0, "duration_h": t14, "depth_ppm": depth,
                           "period_err_days": _num(sig_p), "t0_err_days": _num(sig_t0)},
        "sectors": rows, "sectors_skipped": skipped, "n_transits": n_transits, "n_expected": n_expected,
        "uncertain": uncertain, "timing_error_hours_at_tess": None if terr is None else terr * 24,
        "dilution": {"c": dil["c"], "c_hard42": dil["c_hard42"], "depth_tess_ppm": depth_tess,
                     "depth_tess_hard42_ppm": depth / (1 + dil["c_hard42"]), "t_total": dil["t_total"],
                     "aperture_pixels": dil["aperture_pixels"], "psf_sigma_pix": PSF_SIGMA_PIX,
                     "target_fraction": dil["target_fraction"], "n_neighbours": dil["n_neighbours"],
                     "n_without_tmag": dil["n_without_tmag"], "n_without_offsets": dil["n_without_offsets"]},
        "noise": {"nominal_1h_ppm": nom_1h, "conservative_1h_ppm": con_1h, "nominal_t14_ppm": nom_t,
                  "conservative_t14_ppm": con_t},
        "significance": {"per_transit_nominal": s1_nom, "per_transit_conservative": s1_con,
                         "folded_nominal": s_nom, "folded_conservative": s_con},
        "verdict": word, "caveats": caveats,
    }
    block["sentence"] = note_sentence(block)
    return block


# ---------------------------------------------------------------------------------------------------- the texts

def _and(items) -> str:
    items = [str(x) for x in items]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + f" and {items[-1]}"


def _cadence(minutes: float) -> str:
    if abs(minutes - round(minutes)) < 1e-9:
        return f"{round(minutes)} min"
    return f"{round(minutes * 60)} s"


def _span(rows: list[dict]) -> str:
    """"14 to 82, 30 min to 200 s": the first and last sector and the longest and shortest cadence."""
    first, last = rows[0]["sector"], rows[-1]["sector"]
    slow, fast = max(r["cadence_min"] for r in rows), min(r["cadence_min"] for r in rows)
    sectors = str(first) if first == last else f"{first} to {last}"
    cadence = _cadence(slow) if slow == fast else f"{_cadence(slow)} to {_cadence(fast)}"
    return f"{sectors}, {cadence}"


def _skipped(b: dict) -> str:
    """", and Sector 109, not assessed" for the sectors TESScut lists that the table lacks; "" without any."""
    skipped = b.get("sectors_skipped") or []
    if not skipped:
        return ""
    return f", and Sector{'s' if len(skipped) > 1 else ''} {_and(skipped)}, not assessed"


def _s(x: float) -> str:
    """A significance as the texts print it: whole numbers from 10, one decimal from 1, two below."""
    return f"{x:.0f}" if x >= 10 else f"{x:.1f}" if x >= 1 else f"{x:.2f}"


def _ppm(x: float) -> str:
    return f"{x:,.0f}"


def _magnitude(b: dict) -> str:
    """"T = 15.2", or "a blended T = 12.4" when the neighbours' light moves it (c of 0.01 or more)."""
    dil = b["dilution"]
    if dil["c"] >= 0.01:
        return f"a blended T = {dil['t_total']:.1f}"
    return f"T = {b['tmag']:.1f}"


def _uncertain_clause(b: dict) -> str:
    if not b.get("uncertain"):
        return ""
    if b.get("timing_error_hours_at_tess") is None:
        return f" (no ephemeris error; about {b['n_expected']:.1f} expected)"
    return (f" (timing error {b['timing_error_hours_at_tess']:.1f} h, above half the duration; about "
            f"{b['n_expected']:.1f} expected)")


def note_sentence(block: dict | None, tex: bool = False) -> str:
    """The note's sentence (spec 10.8), one short sentence per candidate: the sectors as a span with the cadences,
    any sector TESScut lists that is not assessed, the transits in the windows, the diluted depth, the nominal and
    conservative noise per hour at the (blended) magnitude, the folded significance under both and the verdict
    phrase; "TESS coverage was not assessed." without the inputs. With ``tex`` a LaTeX ``Raw``."""
    text = _plain_sentence(block)
    return Raw(tex_escape(text)) if tex else text


def _plain_sentence(b: dict | None) -> str:
    if not isinstance(b, dict) or not b.get("available"):
        return NOT_ASSESSED
    rows, skipped = b["sectors"], b.get("sectors_skipped") or []
    if not rows and skipped:
        return (f"TESS observed the star only in Sector{'s' if len(skipped) > 1 else ''} {_and(skipped)}, not "
                "assessed here, so no transit is counted in a TESS window.")
    if not rows:
        return (f"No TESS sector up to {LAST_COMPLETED_SECTOR} observed the star, so "
                f"{VERDICT_PHRASES['not observed in transit']}.")
    k = len(rows)
    head = f"TESS observed the star in {k} sector{'s' if k != 1 else ''} ({_span(rows)}){_skipped(b)}"
    n = b["n_transits"]
    if n == 0:
        unc = "; count uncertain" if b.get("uncertain") else ""
        return (f"{head}, but {VERDICT_PHRASES['not observed in transit']} on the Kepler ephemeris (about "
                f"{b['n_expected']:.1f} expected{unc}), so the coverage says nothing about the signal.")
    noise, sig = b["noise"], b["significance"]
    return (f"{head}; {n} ephemeris transit{'s fall' if n != 1 else ' falls'} in its windows{_uncertain_clause(b)} "
            f"at a diluted {_ppm(b['dilution']['depth_tess_ppm'])} ppm against {_ppm(noise['nominal_1h_ppm'])} to "
            f"{_ppm(noise['conservative_1h_ppm'])} ppm per hour at {_magnitude(b)}, a folded "
            f"{_s(sig['folded_conservative'])} to {_s(sig['folded_nominal'])} sigma, so "
            f"{VERDICT_PHRASES[b['verdict']]}.")


# the works the note's methods sentence cites, as (key, plain citation)
METHOD_CITES = (("christ2019", "Christ et al. 2019"), ("stassun2019", "Stassun et al. 2019"),
                ("ricker2015", "Ricker et al. 2015"), ("giacalone2021", "Giacalone et al. 2021"),
                ("guerrero2021", "Guerrero et al. 2021"), ("stassun2018", "Stassun et al. 2018"),
                ("sullivan2015", "Sullivan et al. 2015"), ("twicken2025", "Twicken et al. 2025"),
                ("battley2021", "Battley et al. 2021"))


def methods_sentence(block: dict | None, tex: bool = False) -> str | None:
    """How the TESS prediction was made, each choice credited to its source (spec 10.9), for the note after
    ``note_sentence``; None without a verdict. With ``tex`` a LaTeX ``Raw`` with ``\\citep`` and ``\\citet``."""
    if not isinstance(block, dict) or not block.get("verdict"):
        return None
    words = dict(METHOD_CITES)

    def p(key):
        return Raw(rf"\citep{{{key}}}") if tex else f"({words[key]})"

    def t(key):
        return Raw(rf"\citet{{{key}}}") if tex else words[key]
    parts = ["The method follows ", t("christ2019"), ", with TIC neighbours ", p("stassun2019"),
             ", 21 arcsec pixels ", p("ricker2015"), ", a 0.75-pixel Gaussian PSF ", p("giacalone2021"),
             ", five pixels against the SPOC's six at Tmag 13 ", p("guerrero2021"), ", the TIC noise polynomial ",
             p("stassun2018"), " after ", t("sullivan2015"), ", the SPOC CDPP medians ", p("twicken2025"),
             " and the coverage rule of ", t("battley2021"), "; no TESS light curve was examined."]
    if not tex:
        return "".join(parts)
    return Raw("".join(x if isinstance(x, Raw) else tex_escape(x) for x in parts))


def readme_row(block: dict | None) -> str:
    """The README's "TESS" row: the verdict and the numbers of ``note_sentence`` with the full sector list, or "not
    assessed" with why."""
    if not isinstance(block, dict) or not block.get("available"):
        why = "; ".join((block or {}).get("caveats") or [NOT_FETCHED])
        return f"not assessed ({why})"
    rows, n, skipped = block["sectors"], block["n_transits"], block.get("sectors_skipped") or []
    if not rows and skipped:
        return f"not assessed: TESS observed the star only in Sector{'s' if len(skipped) > 1 else ''} {_and(skipped)}"
    if not rows:
        return f"not observed in transit: no sector up to {LAST_COMPLETED_SECTOR} observed the star"
    where = f"sector{'s' if len(rows) != 1 else ''} {_and(r['sector'] for r in rows)}{_skipped(block)}"
    if n == 0:
        unc = "; count uncertain" if block.get("uncertain") else ""
        return (f"not observed in transit: no transit in {where} (about {block['n_expected']:.1f} expected from "
                f"phase coverage{unc})")
    dil, noise, sig = block["dilution"], block["noise"], block["significance"]
    return (f"{block['verdict']}: {n} transit{'s' if n != 1 else ''} in {where}{_uncertain_clause(block)}; diluted "
            f"depth {_ppm(dil['depth_tess_ppm'])} ppm (c = {dil['c']:.2f}; {_ppm(dil['depth_tess_hard42_ppm'])} ppm "
            f"with the two-pixel-radius sum, for comparison); 1-hour noise {_ppm(noise['nominal_1h_ppm'])} to "
            f"{_ppm(noise['conservative_1h_ppm'])} ppm at {_magnitude(block)}; folded S/N "
            f"{_s(sig['folded_conservative'])} to {_s(sig['folded_nominal'])}; a prediction, not a search")


def exofop_line(block: dict | None) -> str | None:
    """The ExoFOP note's line, "TESS: <verdict>; <N> transits in sectors <list>; folded S/N <S_cons>-<S_nom>", only
    when a verdict exists: a sector TESScut lists that is not assessed is named, a zero count keeps its uncertainty
    and the expected number, and the folded S/N is left out when no transit fell in a window."""
    if not isinstance(block, dict) or not block.get("verdict"):
        return None
    rows, n, skipped = block["sectors"], block["n_transits"], block.get("sectors_skipped") or []
    if not rows and skipped:
        return (f"TESS: not assessed; observed only in Sector{'s' if len(skipped) > 1 else ''} {_and(skipped)}, "
                "whose windows are not in the table")
    if not rows:
        return f"TESS: {block['verdict']}; no sector up to {LAST_COMPLETED_SECTOR} observed the star"
    line = (f"TESS: {block['verdict']}; {n} transit{'s' if n != 1 else ''} in sector{'s' if len(rows) != 1 else ''} "
            f"{_and(r['sector'] for r in rows)}{_skipped(block)}")
    if n == 0:
        if block.get("uncertain"):
            line += f" (count uncertain, about {block['n_expected']:.1f} expected)"
        return line
    sig = block["significance"]
    return f"{line}; folded S/N {_s(sig['folded_conservative'])}-{_s(sig['folded_nominal'])}"
