"""Stellar and planet parameters from Gaia (spec 5.3): the route of the hand-built KIC 6146676 dossier
(``docs/candidates/kic-006146676/context/d_stellar_planet.py``), made general and seeded.

Four ADQL queries to the Gaia archive, each through the caller's ``fetch`` and so logged like every other request:
the ``gaia_source`` row, the Bailer-Jones et al. 2021 distance, the 2MASS ``Ks`` cross-match and a 20 arcsec cone for
the neighbours that could make the dip. ``derive`` is pure: the parallax corrected by the Lindegren et al. 2021 zero
point (from the installed ``gaiadr3-zeropoint`` package, the optional ``dossier`` extra; LGPL, so imported and never
vendored; without it the global offset of -0.017 mas with a caveat), the
Mamajek dwarf table at the observed BP-RP for Teff and spectral type, and below 4,000 K the Mann et al. 2015 radius
and Mann et al. 2019 mass from the absolute Ks; errors from a ``N_DRAWS`` Monte Carlo reported as the 16th, 50th and
84th percentiles. Gaia's own GSP-Phot and FLAME values are kept as a cross-check, never as the headline.

The extinction (spec 12.3 as amended on 23 October 2026) is read from ``docs/candidates/<target>/extinction.json``,
written once by ``tools/extinction.py``. A 3D value at the Gaia distance (Bayestar19, Green et al. 2019), adopted or
stored by hand, is turned into E(BP-RP), A_G and A_Ks by the Gaia (E)DR3 law (A_Ks over A_0 from Cardelli et al.
1989) and taken out of the draws; without one the unreddened star stays adopted, and the Schlafly and Finkbeiner 2011
full-column E(B-V) runs through the same law as the upper end of a bracket. GSP-Phot's E(BP-RP) is never adopted:
Andrae et al. 2023 document its bias for cool dwarfs."""
from __future__ import annotations

import copy
import dataclasses
import functools
import importlib
import json
import math
import re
import warnings
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import numpy as np

from skysift.fpp import Neighbour
from skysift.models import Target
from skysift.net import ServiceUnavailable, looks_like_error_document
from skysift.priors import CITATIONS, M_TEFF_MAX
from skysift.vetting.rung4_novelty import GAIA_TAP, _csv_rows

N_DRAWS = 20_000
# BP-RP span of the table rows used. The hand-built route kept 1.3 to 3.0 (M dwarfs only); the engine keeps the whole
# dwarf sequence that has a mass (B9V to M9.5V, BP-RP -0.12 to 4.86) so FGK stars take the same route. Inside 1.3 to
# 3.0 the interpolation is the same, since np.interp only reads the two rows either side of the colour.
MAMAJEK_COLOUR_RANGE = (-0.2, 5.0)

COLOUR_SIGMA = 0.10              # mag: the BP-RP draw width, E(BP-RP) at or below 0.1 assumed
ZERO_POINT_SYSTEMATIC = 0.015    # mas, added in quadrature to the parallax error
ZERO_POINT_GLOBAL = -0.017       # mas, the Lindegren et al. 2021 global median offset (quasars)
KS_ERR_DEFAULT = 0.03            # mag, when 2MASS gives no error
A_KS_MAX = 0.07                  # mag: Ks extinction drawn uniformly in 0 to this
MANN_R_SCATTER = 0.0289          # Mann et al. 2015, fractional
MANN_M_SCATTER = 0.021           # Mann et al. 2019, fractional
MANN_MKS_RANGE = (4.6, 9.8)      # M_Ks span of the Mann et al. 2015 calibration
REDDENING_ALT = 0.2              # E(BP-RP) of the alternative radius
BOND_ALBEDO = 0.3
TSUN = 5772.0
RUWE_MAX = 1.4
DWARF_MG_TOLERANCE = 1.0         # mag between the absolute G and the dwarf sequence before a caveat
SELF_MATCH_ARCSEC = 1.0          # without a Gaia id, the nearest cone row within this is the target itself
# the extinction (spec 12.3 as amended by the controller's addenda of 23 October): E(B-V) through the Gaia (E)DR3 law
R_V = 3.1
GAIA_COLOUR_RANGE = (-0.06, 2.5)      # the fit's validity in (BP-RP)_0; outside it the colour is held at the edge
GAIA_A0_RANGE = (0.01, 20.0)          # the fit's validity in A_0 (documented, not enforced: below 0.01 is no dust)
GAIA_LAW = ("Gaia (E)DR3 extinction law, main-sequence fit (log g 4.5) in (BP-RP)_0 and A_0 on the Fitzpatrick "
            "et al. 2019 law (Riello et al. 2021 passbands): www.cosmos.esa.int/web/gaia/edr3-extinction-law, "
            "Fitz19_EDR3_MainSequence.csv")
# k = a1 + a2 X + a3 X^2 + a4 X^3 + a5 A0 + a6 A0^2 + a7 A0^3 + a8 A0 X + a9 A0 X^2 + a10 X A0^2, X = (BP-RP)_0
GAIA_K_G = (0.995969721536602, -0.159726460302015, 0.0122380738156057, 0.00090726555099859, -0.0377160263914123,
            0.00151347495244888, -2.52364537395142e-05, 0.0114522658102451, -0.000936914989014318,
            -0.000260296774134201)
GAIA_K_BP = (1.15363197483424, -0.0814012991657388, -0.036013023976704, 0.0192143585568966, -0.022397548243016,
             0.000840562680547171, -1.31018008013549e-05, 0.00660124080271006, -0.000882247501989453,
             -0.000111215755291684)
GAIA_K_RP = (0.66320787941067, -0.0179847164933981, 0.000493769449961458, -0.00267994405695751,
             -0.00651422146709376, 3.30179903473159e-05, 1.57894227641527e-06, -7.9800898337247e-05,
             0.000255679812110045, 1.10476584967393e-05)
GAIA_COLOUR_ITERATIONS = 10
A_KS_OVER_A0 = 0.117                  # Cardelli, Clayton and Mathis 1989 at R_V 3.1 and the 2MASS Ks 2.159 um
EXTINCTION_SEED = 20261023            # the E(B-V) draws, a generator of their own, so the other draws do not move
EXTINCTION_FILE = "extinction.json"
EXTINCTION_FIELDS = ("ebv", "ebv_p16", "ebv_p84", "source", "map", "queried_utc", "distance_pc")
# the 3D value's three states: "adopted" (a stored Bayestar19 value; a block without ``status`` is one), "manual"
# (stored by hand with ``tools/extinction.py --store``), "not available" (with its reason)
EXT_ADOPTED, EXT_MANUAL, EXT_NOT_AVAILABLE = "adopted", "manual", "not available"
EXTINCTION_STATES = (EXT_ADOPTED, EXT_MANUAL, EXT_NOT_AVAILABLE)
# the full-column E(B-V) of Schlafly and Finkbeiner 2011 (IRSA DUST, the pixel at the star), stored under "column":
# the whole line of sight, dust beyond the star included, so an upper bound on the reddening at the star's distance
COLUMN_OK = "ok"
COLUMN_FIELDS = ("ebv", "source", "queried_utc")
BAYESTAR_LABEL = "Bayestar19"
# Task 16 fix round 1, ruling (c): the star's height above the plane, z = d sin|b|, against the dust layer
HEIGHT_DUST_LAYER_PC = 150.0
GALACTIC_POLE = (192.85948, 27.12825)  # the north galactic pole in ICRS, degrees (Hipparcos and Gaia convention)
HEIGHT_ABOVE = ("the star sits about {z} pc {side} the plane, so the full-column value is probably close to its true "
                "reddening")
HEIGHT_INSIDE = ("the star sits about {z} pc {side} the plane, inside the dust layer, so its true reddening lies well "
                 "inside the bracket")
COLUMN_LABEL = "the full column (Schlafly and Finkbeiner 2011)"
KOPPARAPU_TEFF = (2600.0, 7200.0)
# Kopparapu et al. 2013, erratum 2013 ApJ 770 82: effective-flux coefficients, t = Teff - 5780. The hand-built template
# carried the pre-erratum recent-Venus set; the other three are unchanged by the erratum.
KOPPARAPU = {
    "recent_venus": (1.7753, 1.4316e-4, 2.9875e-9, -7.5702e-12, -1.1635e-15),
    "runaway_greenhouse": (1.0512, 1.3242e-4, 1.5418e-8, -7.9895e-12, -1.8328e-15),
    "maximum_greenhouse": (0.3438, 5.8942e-5, 1.6558e-9, -3.0045e-12, -5.2983e-16),
    "early_mars": (0.3179, 5.4513e-5, 1.5313e-9, -2.7786e-12, -4.8997e-16),
}
G_SI, MSUN, RSUN, AU, REARTH = 6.674e-11, 1.98892e30, 6.957e8, 1.495978707e11, 6.3781e6   # as d_stellar_planet.py

NO_STAR = "no Gaia parallax or colour: the star cannot be characterised from photometry"
KS_MISSING = "Ks missing: Mamajek radius with doubled scatter"
ZERO_POINT_FALLBACK = ("Gaia parallax zero point: the global offset of -0.017 mas was used because the "
                       "gaiadr3-zeropoint package is not installed")
ZERO_POINT_MISSING = ("Gaia parallax zero point: the global offset of -0.017 mas was used because the inputs of the "
                      "Lindegren et al. 2021 correction (G, colour, ecliptic latitude, a 5p or 6p solution) are "
                      "missing")
ZP_PACKAGE, ZP_GLOBAL, ZP_MISSING = "gaiadr3-zeropoint", "global offset -0.017 mas", "none: missing inputs"
PARALLAX_DROP_MAX = 0.01         # fraction of non-positive parallax draws before the bias caveat

SOURCE_COLUMNS = ("source_id", "ra", "dec", "parallax", "parallax_error", "phot_g_mean_mag", "bp_rp")
CONE_COLUMNS = ("phot_g_mean_mag", "sep_arcsec", "source_id")
# the FLAME radius and mass live in astrophysical_parameters, not gaia_source (asking gaia_source for them is an
# HTTP 400 from the archive); the outer join keeps the gaia_source row of a star FLAME did not fit
Q_SOURCE = ("SELECT g.source_id, g.ra, g.dec, g.parallax, g.parallax_error, g.phot_g_mean_mag, g.bp_rp, g.ruwe, "
            "g.nu_eff_used_in_astrometry, g.pseudocolour, g.ecl_lat, g.astrometric_params_solved, g.teff_gspphot, "
            "g.non_single_star, g.ebpminrp_gspphot, ap.radius_flame, ap.mass_flame FROM gaiadr3.gaia_source AS g "
            "LEFT OUTER JOIN gaiadr3.astrophysical_parameters AS ap ON ap.source_id = g.source_id "
            "WHERE g.source_id = {id}")
Q_DISTANCE = "SELECT r_med_geo, r_lo_geo, r_hi_geo FROM external.gaiaedr3_distance WHERE source_id = {id}"
Q_TMASS = ("SELECT t.ks_m, t.ks_msigcom FROM gaiadr3.tmass_psc_xsc_best_neighbour AS x "
           "JOIN gaiadr1.tmass_original_valid AS t ON x.original_ext_source_id = t.designation "
           "WHERE x.source_id = {id}")
Q_CONE = ("SELECT source_id, ra, dec, phot_g_mean_mag, bp_rp, "
          "DISTANCE(POINT('ICRS', ra, dec), POINT('ICRS', {ra}, {dec})) * 3600 AS sep_arcsec FROM gaiadr3.gaia_source "
          "WHERE 1 = CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra}, {dec}, {radius_deg}))")


@dataclass(frozen=True)
class GaiaRow:                               # None where Gaia has no value
    source_id: str
    ra: float
    dec: float
    parallax: float | None
    parallax_error: float | None
    zero_point: float | None                 # mas, Lindegren et al. 2021; subtract from the parallax
    zero_point_source: str                   # ZP_PACKAGE | ZP_GLOBAL | ZP_MISSING
    gmag: float | None
    bp_rp: float | None
    ruwe: float | None
    nu_eff: float | None
    pseudocolour: float | None
    ecl_lat: float | None
    params_solved: int | None
    teff_gspphot: float | None
    radius_flame: float | None
    mass_flame: float | None
    r_med_geo: float | None                  # Bailer-Jones et al. 2021
    r_lo_geo: float | None
    r_hi_geo: float | None
    ks: float | None                         # 2MASS
    ks_err: float | None
    non_single_star: int | None = None       # gaia_source's NSS flag: 0 without a non-single-star solution
    ebpminrp_gspphot: float | None = None    # GSP-Phot E(BP-RP), when Gaia fitted one


@dataclass(frozen=True)
class Value:
    """Median and the 16th to 84th percentile half-widths, with its source."""
    median: float
    minus: float
    plus: float
    source: str

    def as_dict(self) -> dict:
        return {"median": self.median, "minus": self.minus, "plus": self.plus, "source": self.source}


@dataclass(frozen=True)
class StarParams:
    route: str                   # "mann" | "mamajek" | "none"
    spectral_type: str | None    # Mamajek row label at the colour, e.g. "M1V"
    teff: Value | None
    radius: Value | None
    mass: Value | None
    density_gcc: Value | None
    distance_pc: Value | None
    abs_g: float | None
    abs_ks: float | None
    gaia_cross_check: dict       # teff_gspphot, radius_flame, mass_flame and a sentence on the difference
    reddening_note: str          # the E(BP-RP) assumption and the alternative radius at E = 0.2
    alt_radius_e02: Value | None
    caveats: tuple[str, ...]
    ruwe: float | None = None                # Gaia DR3, copied for the star sentence and the provenance (spec 11.6)
    non_single_star: int | None = None
    ebpminrp_gspphot: float | None = None
    # spec 12.3: the stored extinction block with what the law makes of it (``derive``), and the unreddened star and
    # planet beside it; None without a block or for a star that cannot be characterised
    extinction: dict | None = None
    unreddened: dict | None = None


@dataclass(frozen=True)
class PlanetParams:
    rp_rearth: Value | None
    a_au: Value | None
    a_over_r: Value | None
    teq_k: Value | None
    insolation_earth: Value | None
    transit_probability: Value | None
    hz_inner_au: float | None    # Kopparapu et al. 2013 runaway greenhouse (conservative inner edge)
    hz_outer_au: float | None    # Kopparapu et al. 2013 maximum greenhouse (conservative outer edge)
    in_habitable_zone: bool | None   # inside the conservative zone, runaway to maximum greenhouse
    hz_optimistic_inner_au: float | None = None    # recent Venus
    hz_optimistic_outer_au: float | None = None    # early Mars
    in_optimistic_habitable_zone: bool | None = None   # inside the optimistic zone, recent Venus to early Mars


# ---------------------------------------------------------------------------------------------------- parsing helpers

def _num(value) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _int(value) -> int | None:
    v = _num(value)
    return int(v) if v is not None else None


def _numeric_id(gaia_id) -> str | None:
    gid = str(gaia_id or "").strip()
    return gid if re.fullmatch(r"[0-9]+", gid) else None


def _adql(query: str, label: str, required: tuple[str, ...], *, fetch, store, target: Target) -> list[dict]:
    """The rows of one logged ADQL query. As rung 4 does, an error document, an empty body (no header) or a header
    without a ``required`` column is an outage or a schema change, never "no data": ``ServiceUnavailable``. A header
    with no rows is a genuine "no row"."""
    params = {"request": "doQuery", "lang": "ADQL", "format": "csv", "query": query}
    data = fetch(GAIA_TAP, params=params, service="gaia", store=store, target_id=target.target_id)
    err = looks_like_error_document(data)
    if err:  # HTTP 200 carrying an error document is an outage, never an empty result
        raise ServiceUnavailable(f"gaia {label}: {err}")
    header, rows = _csv_rows(data)
    if header is None:
        raise ServiceUnavailable(f"gaia {label}: empty response")
    missing = [c for c in required if c not in header]
    if missing:
        raise ServiceUnavailable(f"gaia {label}: missing column {', '.join(missing)}")
    return rows


# ---------------------------------------------------------------------------------------------------- Gaia zero point

@functools.lru_cache(maxsize=1)
def _zpt_module():
    """``zero_point.zpt`` from the installed gaiadr3-zeropoint package with its tables loaded (once), or None."""
    try:
        zpt = importlib.import_module("zero_point.zpt")
    except ImportError:
        return None
    zpt.load_tables()
    return zpt


def zero_point_available() -> bool:
    """Whether the gaiadr3-zeropoint package is installed (else ``zero_point`` gives the global offset)."""
    return _zpt_module() is not None


def zero_point(gmag, nu_eff, pseudocolour, ecl_lat, params_solved) -> float:
    """The Lindegren et al. 2021 parallax zero point in mas, ``parallax_corrected = parallax - zero_point``, from the
    installed package's ``get_zpt``. Five-parameter solutions (31) use ``nu_eff``, six-parameter ones (95)
    ``pseudocolour``; outside G 6 to 21 and the colour ranges the package clamps (its warning is silenced). Without the
    package: ``ZERO_POINT_GLOBAL``. Raises ``ValueError`` for any other solution type or a missing input the solution
    needs."""
    if params_solved == 31:
        colour = nu_eff
    elif params_solved == 95:
        colour = pseudocolour
    else:
        raise ValueError(f"astrometric_params_solved {params_solved}: the zero point needs a 5p (31) or 6p (95) "
                         "solution")
    if colour is None or gmag is None or ecl_lat is None:
        raise ValueError("the zero point needs G, ecl_lat and the colour of the astrometric solution")
    zpt = _zpt_module()
    if zpt is None:
        return ZERO_POINT_GLOBAL
    nan = float("nan")
    with warnings.catch_warnings():  # the package's out-of-range notices (it clamps); nothing else is silenced
        warnings.filterwarnings("ignore", category=UserWarning, module=r"zero_point\.zpt")
        # arrays, not scalars: the package's scalar path calls np.can_cast on Python floats, which numpy 2 rejects
        value = zpt.get_zpt(np.array([float(gmag)]), np.array([nan if nu_eff is None else float(nu_eff)]),
                            np.array([nan if pseudocolour is None else float(pseudocolour)]),
                            np.array([float(ecl_lat)]), np.array([int(params_solved)]))[0]
    return float(value)


# ---------------------------------------------------------------------------------------------------- Mamajek table

@functools.lru_cache(maxsize=1)
def _mamajek() -> tuple[np.ndarray, tuple[str, ...]]:
    text = resources.files("skysift.data").joinpath("mamajek_dwarf_table.txt").read_text(encoding="utf-8")
    rows = []
    for line in text.splitlines():
        p = line.split()
        if len(p) > 30 and re.match(r"^[OBAFGKML]\d", p[0]):
            try:  # BP-RP, Teff, R, M, M_G, M_Ks; a row missing any but the mass is skipped, as the hand-built route did
                rows.append(((float(p[11]), float(p[1]), float(p[6]), float(p[30]) if p[30] != "..." else math.nan,
                              float(p[13]), float(p[22])), p[0]))
            except ValueError:
                continue
    lo, hi = MAMAJEK_COLOUR_RANGE
    keep = sorted(((r, s) for r, s in rows if lo < r[0] < hi and math.isfinite(r[3])), key=lambda t: t[0][0])
    table = np.array([r for r, _ in keep])
    table.setflags(write=False)
    return table, tuple(s for _, s in keep)


def mamajek_table() -> np.ndarray:
    """Columns BP-RP, Teff, R, M, M_G, M_Ks of the dwarf rows used, sorted by colour (read-only)."""
    return _mamajek()[0]


def mamajek_labels() -> tuple[str, ...]:
    """The spectral-type labels parallel to ``mamajek_table()``."""
    return _mamajek()[1]


def _interp(colour, col: int):
    tab = mamajek_table()
    return np.interp(colour, tab[:, 0], tab[:, col])


# ---------------------------------------------------------------------------------------------------- queries

def query_gaia(target: Target, *, fetch, store) -> GaiaRow | None:
    """The target's ``gaia_source`` row, Bailer-Jones distance and 2MASS Ks: three logged queries. None when the target
    has no numeric Gaia DR3 id (nothing is asked) or Gaia has no row for it (one query)."""
    gid = _numeric_id(target.gaia_id)
    if gid is None:
        return None
    src = _adql(Q_SOURCE.format(id=gid), "gaia_source", SOURCE_COLUMNS, fetch=fetch, store=store, target=target)
    if not src:
        return None
    s = src[0]
    dist = _adql(Q_DISTANCE.format(id=gid), "gaiaedr3_distance", ("r_med_geo",), fetch=fetch, store=store,
                 target=target)
    tmass = _adql(Q_TMASS.format(id=gid), "2MASS cross-match", ("ks_m",), fetch=fetch, store=store, target=target)
    d, t = (dist[0] if dist else {}), (tmass[0] if tmass else {})
    gmag, nu_eff = _num(s.get("phot_g_mean_mag")), _num(s.get("nu_eff_used_in_astrometry"))
    pc = _num(s.get("pseudocolour"))
    ecl_lat, aps = _num(s.get("ecl_lat")), _int(s.get("astrometric_params_solved"))
    try:
        zp = zero_point(gmag, nu_eff, pc, ecl_lat, aps)
        zp_source = ZP_PACKAGE if zero_point_available() else ZP_GLOBAL
    except ValueError:
        zp, zp_source = ZERO_POINT_GLOBAL, ZP_MISSING
    ra, dec = _num(s.get("ra")), _num(s.get("dec"))
    return GaiaRow(
        source_id=str(s.get("source_id") or gid), ra=target.ra if ra is None else ra,
        dec=target.dec if dec is None else dec,
        parallax=_num(s.get("parallax")), parallax_error=_num(s.get("parallax_error")), zero_point=zp,
        zero_point_source=zp_source,
        gmag=gmag, bp_rp=_num(s.get("bp_rp")), ruwe=_num(s.get("ruwe")), nu_eff=nu_eff, pseudocolour=pc,
        ecl_lat=ecl_lat, params_solved=aps, teff_gspphot=_num(s.get("teff_gspphot")),
        radius_flame=_num(s.get("radius_flame")), mass_flame=_num(s.get("mass_flame")),
        r_med_geo=_num(d.get("r_med_geo")), r_lo_geo=_num(d.get("r_lo_geo")), r_hi_geo=_num(d.get("r_hi_geo")),
        ks=_num(t.get("ks_m")), ks_err=_num(t.get("ks_msigcom")), non_single_star=_int(s.get("non_single_star")),
        ebpminrp_gspphot=_num(s.get("ebpminrp_gspphot")))


def query_neighbours(target: Target, depth: float, *, fetch, store, radius_arcsec: float = 20.0) -> list[Neighbour]:
    """Gaia DR3 sources within ``radius_arcsec`` that are bright enough to make the dip even when fully eclipsed
    (spec 4.2: ``G <= G_target + 2.5 log10(1 / (2 depth))``), nearest first; one logged query. Without a target G or a
    positive depth no cut is possible and every source is kept. Without a Gaia id the cone has no ``source_id`` clause
    and the nearest source within ``SELF_MATCH_ARCSEC`` is taken to be the target itself."""
    gid = _numeric_id(target.gaia_id)
    query = Q_CONE.format(ra=target.ra, dec=target.dec, radius_deg=radius_arcsec / 3600.0)
    if gid is not None:
        query += f" AND source_id != {gid}"
    rows = _adql(query, "neighbour cone", CONE_COLUMNS, fetch=fetch, store=store, target=target)
    found = []
    for r in rows:
        g, sep = _num(r.get("phot_g_mean_mag")), _num(r.get("sep_arcsec"))
        if g is None or sep is None:
            continue
        found.append(Neighbour(source_id=str(r.get("source_id")), sep_arcsec=sep, gmag=g, bp_rp=_num(r.get("bp_rp"))))
    found.sort(key=lambda n: n.sep_arcsec)
    if gid is None and found and found[0].sep_arcsec <= SELF_MATCH_ARCSEC:
        found = found[1:]
    if target.gmag is None or depth <= 0:
        return found
    limit = target.gmag + 2.5 * math.log10(1.0 / (2.0 * depth))
    return [n for n in found if n.gmag <= limit]


# ---------------------------------------------------------------------------------------------------- derivation

def _value(draws, nd: int, source: str) -> Value | None:
    """16th, 50th and 84th percentiles over the finite draws; None when no draw is finite (never inf or NaN)."""
    draws = np.asarray(draws, dtype=float)
    draws = draws[np.isfinite(draws)]
    if draws.size == 0:
        return None
    lo, med, hi = np.percentile(draws, [16, 50, 84])
    return Value(median=round(float(med), nd), minus=round(float(med - lo), nd), plus=round(float(hi - med), nd),
                 source=source)


def _pct(new: float, ref: float, what: str) -> str:
    diff = 100.0 * (new - ref) / ref
    if abs(diff) < 1:
        return f"within 1 per cent of {what}"
    return f"{abs(diff):.0f} per cent {'above' if diff >= 0 else 'below'} {what}"


def _cross_check(gaia: GaiaRow | None, teff: Value | None, radius: Value | None, mass: Value | None,
                 distance: Value | None) -> dict:
    if gaia is None:
        return {}
    parts = []
    if gaia.teff_gspphot is not None and teff is not None:
        dt = gaia.teff_gspphot - teff.median
        parts.append(f"GSP-Phot Teff {gaia.teff_gspphot:.0f} K is {abs(dt):.0f} K {'hotter' if dt >= 0 else 'cooler'} "
                     "than the colour route")
    flame = []
    if gaia.radius_flame is not None and radius is not None:
        flame.append(f"radius {gaia.radius_flame:.3f} Rsun ({_pct(gaia.radius_flame, radius.median, 'the route')})")
    if gaia.mass_flame is not None and mass is not None:
        flame.append(f"mass {gaia.mass_flame:.3f} Msun ({_pct(gaia.mass_flame, mass.median, 'the route')})")
    if flame:
        parts.append("FLAME gives " + " and ".join(flame))
    if gaia.r_med_geo is not None and distance is not None:
        parts.append(f"the Bailer-Jones geometric distance {gaia.r_med_geo:.1f} pc is "
                     f"{_pct(gaia.r_med_geo, distance.median, 'the inverted parallax')}")
    if parts:
        note = ("Gaia DR3: " + "; ".join(parts) + ". Gaia's values fold in its own extinction and model grids, so they "
                "are shown as a cross-check, not as the headline.")
    else:
        note = "Gaia DR3 gives no GSP-Phot, FLAME or Bailer-Jones values to compare for this star."
    return {"teff_gspphot": gaia.teff_gspphot, "radius_flame": gaia.radius_flame, "mass_flame": gaia.mass_flame,
            "distance_bailer_jones_pc": gaia.r_med_geo, "note": note}


def _gaia_flags(gaia: GaiaRow | None) -> dict:
    """RUWE, the non-single-star flag and GSP-Phot E(BP-RP) of the Gaia row, as ``StarParams`` carries them."""
    if gaia is None:
        return {}
    return {"ruwe": gaia.ruwe, "non_single_star": gaia.non_single_star, "ebpminrp_gspphot": gaia.ebpminrp_gspphot}


def _unknown(gaia: GaiaRow | None, extra: tuple[str, ...] = (), lead: str | None = NO_STAR,
             ) -> tuple[StarParams, PlanetParams]:
    star = StarParams(route="none", spectral_type=None, teff=None, radius=None, mass=None, density_gcc=None,
                      distance_pc=None, abs_g=None, abs_ks=None,
                      gaia_cross_check=_cross_check(gaia, None, None, None, None), reddening_note="",
                      alt_radius_e02=None, caveats=(lead, *extra) if lead else tuple(extra), **_gaia_flags(gaia))
    planet = PlanetParams(rp_rearth=None, a_au=None, a_over_r=None, teq_k=None, insolation_earth=None,
                          transit_probability=None, hz_inner_au=None, hz_outer_au=None, in_habitable_zone=None)
    return star, planet


def _hz_distance(teff: float, lum: float, coeffs) -> float:
    t = teff - 5780.0
    s_eff = sum(c * t ** i for i, c in enumerate(coeffs))
    return round(math.sqrt(lum / s_eff), 4)


# ---------------------------------------------------------------------------------------------------- extinction

class ExtinctionError(ValueError):
    """A stored ``extinction.json`` that is not a valid block; the message names the first problem."""


class NoExtinction(LookupError):
    """A star Gaia can characterise, without a stored ``extinction.json`` (spec 12.3: never a silent fallback)."""


def gaia_k(coeffs, x, a0):
    """The Gaia (E)DR3 extinction coefficient ``A_band / A_0`` of the ten-term polynomial in X = (BP-RP)_0 and A_0
    (``GAIA_K_G``, ``GAIA_K_BP``, ``GAIA_K_RP``); scalars or numpy arrays."""
    x, a0 = np.asarray(x, dtype=float), np.asarray(a0, dtype=float)
    c = coeffs
    return (c[0] + c[1] * x + c[2] * x ** 2 + c[3] * x ** 3 + c[4] * a0 + c[5] * a0 ** 2 + c[6] * a0 ** 3
            + c[7] * a0 * x + c[8] * a0 * x ** 2 + c[9] * x * a0 ** 2)


def gaia_extinction(ebv, bp_rp_observed: float) -> dict:
    """E(B-V) through the Gaia (E)DR3 law: A_0 = R_V E(B-V); the intrinsic colour X_0 starts at the observed one and,
    ``GAIA_COLOUR_ITERATIONS`` times, becomes observed - A_0 (k_BP - k_RP) at X_0 held inside ``GAIA_COLOUR_RANGE``.
    {"ebv", "a0", "bp_rp0", "colour_held" (X_0 outside the range), "k_g", "k_bp", "k_rp", "ebprp", "a_g", "a_ks"},
    every k at the held X_0, A_Ks = ``A_KS_OVER_A0`` A_0. Python floats (and a bool) for a scalar ``ebv``; for an
    array (the draws) each value is an array and ``colour_held`` a bool array."""
    scalar = np.ndim(ebv) == 0
    ebv_a = np.asarray(ebv, dtype=float)
    a0 = R_V * ebv_a
    x0 = np.full_like(a0, float(bp_rp_observed))
    for _ in range(GAIA_COLOUR_ITERATIONS):
        held = np.clip(x0, *GAIA_COLOUR_RANGE)
        x0 = bp_rp_observed - a0 * (gaia_k(GAIA_K_BP, held, a0) - gaia_k(GAIA_K_RP, held, a0))
    held = np.clip(x0, *GAIA_COLOUR_RANGE)
    k_g, k_bp, k_rp = (gaia_k(c, held, a0) for c in (GAIA_K_G, GAIA_K_BP, GAIA_K_RP))
    out = {"ebv": ebv_a, "a0": a0, "bp_rp0": x0, "colour_held": (x0 < GAIA_COLOUR_RANGE[0]) | (x0 > GAIA_COLOUR_RANGE[1]),
           "k_g": k_g, "k_bp": k_bp, "k_rp": k_rp, "ebprp": a0 * (k_bp - k_rp), "a_g": a0 * k_g,
           "a_ks": A_KS_OVER_A0 * a0}
    if scalar:
        return {k: (bool(v) if k == "colour_held" else float(v)) for k, v in out.items()}
    return out


def galactic_latitude(ra_deg: float, dec_deg: float) -> float:
    """The galactic latitude b in degrees of an ICRS position: sin b = cos d cos d_P cos(a - a_P) + sin d sin d_P with
    the north galactic pole at ``GALACTIC_POLE`` (good to about 0.001 deg, ample for a height above the plane)."""
    a, d = math.radians(ra_deg), math.radians(dec_deg)
    ap, dp = (math.radians(x) for x in GALACTIC_POLE)
    return math.degrees(math.asin(math.cos(d) * math.cos(dp) * math.cos(a - ap) + math.sin(d) * math.sin(dp)))


def height_fields(ra_deg: float, dec_deg: float, distance_pc: float) -> dict:
    """{"b_deg", "height_pc", "height_clause"}: z = d sin|b| rounded to the parsec, and the clause of ruling (c),
    ``HEIGHT_ABOVE`` over ``HEIGHT_DUST_LAYER_PC``, ``HEIGHT_INSIDE`` at or below it."""
    b = galactic_latitude(ra_deg, dec_deg)
    z = round(distance_pc * math.sin(math.radians(abs(b))))      # the printed parsec decides the branch too
    side = "above" if b >= 0 else "below"
    words = HEIGHT_ABOVE if z > HEIGHT_DUST_LAYER_PC else HEIGHT_INSIDE
    return {"b_deg": round(b, 4), "height_pc": z, "height_clause": words.format(z=z, side=side)}


def extinction_state(block: dict | None) -> str | None:
    """The 3D value's state of a stored block: ``EXT_ADOPTED`` (a block without ``status`` is a stored Bayestar19
    value), ``EXT_MANUAL`` or ``EXT_NOT_AVAILABLE``; None without a block."""
    if not isinstance(block, dict):
        return None
    return block.get("status") or EXT_ADOPTED


def column_ebv(block: dict | None) -> float | None:
    """The stored full-column E(B-V) (the upper bound) when the block's ``column`` was fetched, else None."""
    col = (block or {}).get("column") if isinstance(block, dict) else None
    if not isinstance(col, dict) or col.get("status") != COLUMN_OK:
        return None
    return float(col["ebv"])


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _block_problem(block) -> str | None:
    """The first thing that makes ``block`` not a valid stored extinction, or None."""
    if not isinstance(block, dict):
        return "not a JSON object"
    state = extinction_state(block)
    if state not in EXTINCTION_STATES:
        return f"status {state!r} is not one of {', '.join(EXTINCTION_STATES)}"
    if state == EXT_NOT_AVAILABLE:
        if not str(block.get("reason") or "").strip():
            return "reason is empty"
        if not str(block.get("queried_utc") or "").strip():
            return "queried_utc is missing"
    else:
        for field in EXTINCTION_FIELDS:
            if field not in block:
                return f"{field} is missing"
        for field in ("ebv", "ebv_p16", "ebv_p84", "distance_pc"):
            if not _finite(block[field]):
                return f"{field} is not a finite number"
        if not 0 <= block["ebv_p16"] <= block["ebv"] <= block["ebv_p84"]:
            return "not 0 <= ebv_p16 <= ebv <= ebv_p84"
        if not str(block["source"]).strip():
            return "source is empty"
    col = block.get("column")
    if col is not None:
        if not isinstance(col, dict):
            return "column is not a JSON object"
        if col.get("status") == COLUMN_OK:
            for field in COLUMN_FIELDS:
                if field not in col:
                    return f"column {field} is missing"
            if not _finite(col["ebv"]) or col["ebv"] < 0:
                return "column ebv is not a finite number at or above 0"
            if not str(col["source"]).strip():
                return "column source is empty"
            if "ebv_std" in col and (not _finite(col["ebv_std"]) or col["ebv_std"] < 0):
                return "column ebv_std is not a finite number at or above 0"
        elif col.get("status") == EXT_NOT_AVAILABLE:
            if not str(col.get("reason") or "").strip():
                return "column reason is empty"
        else:
            return f"column status {col.get('status')!r} is not {COLUMN_OK!r} or {EXT_NOT_AVAILABLE!r}"
    return None


def load_extinction(target_id: str, root) -> dict | None:
    """``root / target_id / EXTINCTION_FILE`` as a dict, or None without the file. ``ExtinctionError`` naming the
    first problem: not JSON, not a JSON object, a state outside ``EXTINCTION_STATES``; for a 3D value a field of
    ``EXTINCTION_FIELDS`` missing, a number not finite, not 0 <= ebv_p16 <= ebv <= ebv_p84 or an empty source; for
    "not available" an empty reason; a ``column`` that is neither a fetched value nor a reason."""
    path = Path(root) / target_id / EXTINCTION_FILE
    if not path.is_file():
        return None
    try:
        block = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ExtinctionError(f"{path} does not read as JSON ({e})") from e
    problem = _block_problem(block)
    if problem:
        raise ExtinctionError(f"{path}: {problem}")
    return block


def _split_normal(rng: np.random.Generator, median: float, low: float, high: float, n: int) -> np.ndarray:
    """``n`` draws of a split normal with the stored 16th and 84th percentiles: median + z (median - low) for z < 0,
    median + z (high - median) for z >= 0, z standard normal, held at 0 and above."""
    z = rng.standard_normal(n)
    return np.maximum(median + np.where(z < 0, z * (median - low), z * (high - median)), 0.0)


def _values(draws: dict, keys=("a0", "ebprp", "a_g", "a_ks"), source: str = "") -> dict:
    return {k: _value(draws[k], 3, source).as_dict() for k in keys}


def derive(gaia: GaiaRow | None, period: float, fit_k: float | None, depth: float, rng: np.random.Generator,
           extinction: dict | None = None) -> tuple[StarParams, PlanetParams]:
    """The star and planet of spec 5.3 from one Gaia row, the period (days), the fitted radius ratio (None without a
    fit: the depth is used) and the depth (fraction). Every draw comes from ``rng``.

    The Mann relations are used only for a dwarf inside their calibration: an M-type colour with Ks, the median M_Ks
    inside ``MANN_MKS_RANGE`` and the absolute G within ``DWARF_MG_TOLERANCE`` of the dwarf sequence. Otherwise the
    dwarf-table radius and mass at the colour, with a caveat saying why, or no star when the colour is off the table
    too.

    ``extinction`` (spec 12.3, the stored block of ``load_extinction``): None leaves everything as before. With a
    block, the unreddened pair is taken first from a copy of ``rng`` (the version 5 numbers, ``StarParams.unreddened``).
    A 3D value (adopted or manual) is drawn as a split normal from its percentiles (a generator of its own,
    ``EXTINCTION_SEED``), turned into E(BP-RP), A_G and A_Ks by ``gaia_extinction`` and taken out of the colour, the
    absolute magnitudes and the Ks draws: the adopted star. Without one the unreddened star stays adopted. The
    full-column E(B-V), when stored (a normal with its stored ``ebv_std``, held at 0 and above), runs through the
    same law and draws from another copy of ``rng``: the upper end of the bracket, under ``extinction["column"]``. The
    block also carries the star's height above the plane (``height_fields``: ``b_deg``, ``height_pc``,
    ``height_clause``) from the Gaia distance."""
    if extinction is None:
        return _derive(gaia, period, fit_k, depth, rng)
    plain_rng, column_rng = copy.deepcopy(rng), copy.deepcopy(rng)
    plain, plain_planet = _derive(gaia, period, fit_k, depth, plain_rng)
    if plain.route == "none":
        return _derive(gaia, period, fit_k, depth, rng)
    state = extinction_state(extinction)
    block = {**extinction, "state": state, "r_v": R_V, "law": GAIA_LAW, "a_ks_over_a0": A_KS_OVER_A0}
    if state in (EXT_ADOPTED, EXT_MANUAL):
        ebv = _split_normal(np.random.default_rng(EXTINCTION_SEED), float(extinction["ebv"]),
                            float(extinction["ebv_p16"]), float(extinction["ebv_p84"]), N_DRAWS)
        e = gaia_extinction(ebv, gaia.bp_rp)
        tail = f"(E(B-V) {float(extinction['ebv']):.3f} at {float(extinction['distance_pc']):.0f} pc)"
        star, planet = _derive(gaia, period, fit_k, depth, rng, e, BAYESTAR_LABEL, tail)
        block.update({"bp_rp0": round(float(np.median(e["bp_rp0"])), 4), "colour_held": bool(np.any(e["colour_held"])),
                      **_values(e, source=f"{BAYESTAR_LABEL} E(B-V) through the Gaia (E)DR3 law")})
    else:
        star, planet = _derive(gaia, period, fit_k, depth, rng)
    if plain.distance_pc is not None:
        block.update(height_fields(gaia.ra, gaia.dec, plain.distance_pc.median))
    ebv_col = column_ebv(extinction)
    if ebv_col is not None:
        # minor 8: the stored spread inside the 5 arcmin circle, a normal held at 0 and above on a generator of its own
        std = float(extinction["column"].get("ebv_std") or 0.0)
        ebv = np.maximum(np.random.default_rng(EXTINCTION_SEED + 1).normal(ebv_col, std, N_DRAWS), 0.0)
        e = gaia_extinction(ebv, gaia.bp_rp)
        cs, cp = _derive(gaia, period, fit_k, depth, column_rng, e, COLUMN_LABEL, f"(E(B-V) {ebv_col:.3f})")
        block["column"] = {**extinction["column"], "bp_rp0": round(float(np.median(e["bp_rp0"])), 4),
                           "colour_held": bool(np.any(e["colour_held"])),
                           **_values(e, source="the full-column E(B-V) through the Gaia (E)DR3 law"),
                           **_star_planet(cs, cp)}
    unreddened = {**_star_planet(plain, plain_planet), "bp_rp0": gaia.bp_rp}
    return dataclasses.replace(star, extinction=block, unreddened=unreddened), planet


def _star_planet(star: StarParams, planet: PlanetParams) -> dict:
    """The route, Teff, radius and mass of a star and the planet radius and insolation it gives, as Value dicts."""
    def d(v):
        return v.as_dict() if v is not None else None
    return {"route": star.route, "teff": d(star.teff), "radius": d(star.radius), "mass": d(star.mass),
            "rp_rearth": d(planet.rp_rearth), "insolation_earth": d(planet.insolation_earth)}


def _derive(gaia: GaiaRow | None, period: float, fit_k: float | None, depth: float, rng: np.random.Generator,
            e: dict | None = None, label: str | None = None,
            tail: str = "") -> tuple[StarParams, PlanetParams]:
    """``derive`` for one reddening: ``e`` None is the unreddened star (the version 5 route); otherwise the
    ``gaia_extinction`` draws, named ``label`` in the sources, ``tail`` after the E(BP-RP) in the reddening note."""
    if gaia is None or gaia.parallax is None or gaia.parallax_error is None or gaia.bp_rp is None:
        return _unknown(gaia)
    caveats: list[str] = []
    zp, zp_source = gaia.zero_point, gaia.zero_point_source
    if zp is None:
        zp, zp_source = ZERO_POINT_GLOBAL, ZP_MISSING
    if zp_source == ZP_GLOBAL:
        caveats.append(ZERO_POINT_FALLBACK)
    elif zp_source == ZP_MISSING:
        caveats.append(ZERO_POINT_MISSING)
    plx, eplx = gaia.parallax - zp, math.hypot(gaia.parallax_error, ZERO_POINT_SYSTEMATIC)
    if plx <= 0:
        return _unknown(gaia, (*caveats, f"corrected parallax {plx:.3f} mas is not positive"))
    if plx / eplx < 5:
        caveats.append(f"parallax over error {plx / eplx:.1f}: the distance and radius are loose")
    if gaia.ruwe is not None and gaia.ruwe > RUWE_MAX:
        caveats.append(f"RUWE {gaia.ruwe:.2f} above {RUWE_MAX}: the astrometry may be disturbed by a companion")
    bp_rp, tab = gaia.bp_rp, mamajek_table()
    # with a reddening the dwarf sequence, the table check and the spectral type are read at the median intrinsic
    # colour, and the absolute magnitudes lose the median extinction (spec 12.3)
    ebprp = 0.0 if e is None else e["ebprp"]
    x0 = bp_rp if e is None else float(np.median(e["bp_rp0"]))
    a_g = 0.0 if e is None else float(np.median(e["a_g"]))
    a_ks = 0.0 if e is None else float(np.median(e["a_ks"]))
    which = "BP-RP" if e is None else "dereddened BP-RP"
    at = "BP-RP" if e is None else "the dereddened BP-RP"
    in_table = bool(tab[0, 0] <= x0 <= tab[-1, 0])
    if not in_table:
        caveats.append(f"{which} {x0:.2f} is outside the dwarf table ({tab[0, 0]:.2f} to {tab[-1, 0]:.2f}): "
                       "values are held at the table edge")

    # the draws, in a fixed order so a seed always gives the same numbers (the reddening has its own generator)
    colour_obs = rng.normal(bp_rp, COLOUR_SIGMA, N_DRAWS)
    colour = colour_obs - ebprp
    plx_s = rng.normal(plx, eplx, N_DRAWS)
    dropped = float(np.mean(plx_s <= 0))
    plx_s[plx_s <= 0] = np.nan
    if dropped > PARALLAX_DROP_MAX:
        caveats.append(f"the distance is biased by dropping {100 * dropped:.0f} percent of non-positive parallax draws")
    dist_s = 1000.0 / plx_s
    dm_s = 5 * np.log10(dist_s) - 5
    teff_s = _interp(colour, 1)
    dist0 = 1000.0 / plx
    dm0 = 5 * math.log10(dist0) - 5
    abs_g = round(gaia.gmag - dm0 - a_g, 3) if gaia.gmag is not None else None
    abs_ks = round(gaia.ks - dm0 - a_ks, 3) if gaia.ks is not None else None
    mg_dwarf = float(_interp(x0, 4))
    off_dwarf = abs_g is not None and abs(abs_g - mg_dwarf) > DWARF_MG_TOLERANCE
    if off_dwarf:
        caveats.append(f"absolute G {abs_g:.2f} is more than {DWARF_MG_TOLERANCE:.0f} mag from the dwarf sequence "
                       f"({mg_dwarf:.2f}) at this colour: the star may be evolved, an unresolved binary or reddened")
    cool = float(_interp(x0, 1)) < M_TEFF_MAX
    emed = float(np.median(ebprp))
    from_label = f", E(BP-RP) {emed:.3f} from {label}" if e is not None else ""
    table_src = (f"Mamajek dwarf table at {at} {x0:.3f} +- {COLOUR_SIGMA:.2f} "
                 f"({CITATIONS['DWARF_SEQUENCE']}){from_label}")

    route = None
    if cool and gaia.ks is not None:
        ks_s = rng.normal(gaia.ks, gaia.ks_err or KS_ERR_DEFAULT, N_DRAWS)
        a_ks_uniform = rng.uniform(0.0, A_KS_MAX, N_DRAWS)     # drawn with a reddening too, so later draws stay
        mks = ks_s - dm_s - (a_ks_uniform if e is None else e["a_ks"])
        mks_med = float(np.nanmedian(mks))
        why = None
        if not MANN_MKS_RANGE[0] <= mks_med <= MANN_MKS_RANGE[1]:
            why = (f"M_Ks {mks_med:.2f} is outside their calibration ({MANN_MKS_RANGE[0]} to {MANN_MKS_RANGE[1]}), "
                   "so the star may not be a dwarf")
        elif off_dwarf:
            why = "the absolute G is off the dwarf sequence"
        else:
            with np.errstate(over="ignore", invalid="ignore"):
                radius_s = (1.9515 - 0.3520 * mks + 0.01680 * mks ** 2) * rng.normal(1, MANN_R_SCATTER, N_DRAWS)
                x = mks - 7.5
                log_m = -0.642 - 0.208 * x - 8.43e-4 * x ** 2 + 7.87e-3 * x ** 3 + 1.42e-4 * x ** 4 - 2.13e-4 * x ** 5
                mass_s = 10 ** log_m * rng.normal(1, MANN_M_SCATTER, N_DRAWS)
                good = np.isfinite(radius_s) & np.isfinite(mass_s) & (radius_s > 0) & (mass_s > 0)
            bad = np.isfinite(mks) & ~good
            if bad.mean() > 0.5:
                why = "most of their draws gave no finite radius or mass"
            else:
                route = "mann"
                if bad.any():
                    radius_s[bad], mass_s[bad] = np.nan, np.nan
                    caveats.append(f"{100 * bad.mean():.1f} percent of the Mann draws gave no finite radius or mass "
                                   "and were dropped")
                ks_src = (f"2MASS Ks {gaia.ks:.3f}, A_Ks 0 to {A_KS_MAX}, Gaia parallax" if e is None else
                          f"2MASS Ks {gaia.ks:.3f}, A_Ks {a_ks:.3f} from {label}, Gaia parallax")
                r_src = f"Mann et al. 2015 R(M_Ks) ({CITATIONS['M_DWARF_RADIUS']}), {ks_src}"
                m_src = f"Mann et al. 2019 M(M_Ks) ({CITATIONS['M_DWARF_MASS']}), {ks_src}"
        if why is not None:
            if not in_table:
                last = (f"Mann relations not used: {why}; the colour is off the dwarf table too, so the star cannot be "
                        "characterised from photometry")
                return _unknown(gaia, (*caveats, last), lead=None)
            caveats.append(f"Mann relations not used: {why}; the dwarf-table radius and mass at the colour are shown")

    if route is None:
        route = "mamajek"
        no_ks = cool and gaia.ks is None
        rm_colour = colour
        if no_ks:  # an M dwarf without Ks: the colour radius, with the colour scatter doubled
            rm_colour = (bp_rp - ebprp) + 2.0 * (colour_obs - bp_rp)
            caveats.append(KS_MISSING)
        radius_s, mass_s = _interp(rm_colour, 2), _interp(rm_colour, 3)
        width = 2 * COLOUR_SIGMA if no_ks else COLOUR_SIGMA
        r_src = m_src = (f"Mamajek dwarf table at {at} {x0:.3f} +- {width:.2f} "
                         f"({CITATIONS['DWARF_SEQUENCE']}){from_label}")

    density_s = mass_s * MSUN / (4 / 3 * np.pi * (radius_s * RSUN) ** 3) / 1000.0
    teff = _value(teff_s, 0, table_src + (", E(BP-RP) <= 0.1 assumed" if e is None else ""))
    radius, mass = _value(radius_s, 3, r_src), _value(mass_s, 3, m_src)
    distance = _value(dist_s, 1, f"1000 / Gaia DR3 parallax, zero point {zp:+.4f} mas ({zp_source}; "
                                 f"{CITATIONS['GAIA_PARALLAX_ZERO_POINT']}), +- {eplx:.4f} mas")
    alt = _value(_interp(colour_obs - REDDENING_ALT, 2), 3,
                 f"Mamajek dwarf table at BP-RP {bp_rp - REDDENING_ALT:.3f} +- {COLOUR_SIGMA:.2f} (if E(BP-RP) = 0.2)")
    if e is None:
        reddening_note = (f"Teff and spectral type assume E(BP-RP) at or below 0.1 at the observed BP-RP "
                          f"{bp_rp:.2f}; if E(BP-RP) were {REDDENING_ALT} the dwarf-table radius would be "
                          f"{alt.median:.3f} Rsun.")
        if route == "mann":
            reddening_note += " The Ks radius barely depends on reddening: A_Ks 0 to 0.07 is in the draws."
    else:
        reddening_note = (f"Teff and spectral type use the BP-RP {bp_rp:.2f} dereddened by the {label} E(BP-RP) of "
                          f"{emed:.3f} {tail}; if E(BP-RP) were {REDDENING_ALT} the dwarf-table radius would be "
                          f"{alt.median:.3f} Rsun.")
    labels = mamajek_labels()
    spectral_type = labels[int(np.argmin(np.abs(tab[:, 0] - x0)))]
    star = StarParams(route=route, spectral_type=spectral_type, teff=teff, radius=radius, mass=mass,
                      density_gcc=_value(density_s, 2, "M* / (4/3 pi R*^3) from the same draws"),
                      distance_pc=distance, abs_g=abs_g, abs_ks=abs_ks,
                      gaia_cross_check=_cross_check(gaia, teff, radius, mass, distance),
                      reddening_note=reddening_note, alt_radius_e02=alt, caveats=tuple(caveats),
                      **_gaia_flags(gaia))
    return star, _planet(teff_s, radius_s, mass_s, period, fit_k, depth)


def _planet(teff_s, radius_s, mass_s, period: float, fit_k: float | None, depth: float) -> PlanetParams:
    a_m = (G_SI * mass_s * MSUN * (period * 86400.0) ** 2 / (4 * np.pi ** 2)) ** (1 / 3)
    a_r = a_m / (radius_s * RSUN)
    a_au = a_m / AU
    if fit_k is not None:
        rp = _value(fit_k * radius_s * RSUN / REARTH, 2, f"k {fit_k:.4f} from the transit fit x R*")
    elif depth > 0:
        rp = _value(math.sqrt(depth) * radius_s * RSUN / REARTH, 2,
                    f"sqrt(depth) x R* from the box depth {depth * 1e6:.0f} ppm "
                    "(no transit fit; limb darkening ignored)")
    else:
        rp = None
    lum_s = radius_s ** 2 * (teff_s / TSUN) ** 4
    a = _value(a_au, 4, f"Kepler's third law, P {period:.5f} d and the stellar mass draws")
    teq = _value(teff_s * np.sqrt(1 / (2 * a_r)) * (1 - BOND_ALBEDO) ** 0.25, 0,
                 f"Teff (R*/2a)^0.5 at Bond albedo {BOND_ALBEDO}, full redistribution")
    teff_med, lum_med = float(np.nanmedian(teff_s)), float(np.nanmedian(lum_s))
    if a is not None and KOPPARAPU_TEFF[0] <= teff_med <= KOPPARAPU_TEFF[1]:
        hz = {name: _hz_distance(teff_med, lum_med, c) for name, c in KOPPARAPU.items()}
        in_hz = hz["runaway_greenhouse"] <= a.median <= hz["maximum_greenhouse"]
        in_opt = hz["recent_venus"] <= a.median <= hz["early_mars"]
    else:
        hz, in_hz, in_opt = {}, None, None
    return PlanetParams(
        rp_rearth=rp, a_au=a, a_over_r=_value(a_r, 1, "a / R* from the same draws"), teq_k=teq,
        insolation_earth=_value(lum_s / a_au ** 2, 2, f"L* / a^2, L* = R*^2 (Teff / {TSUN:.0f} K)^4, Earth = 1"),
        transit_probability=_value(1 / a_r, 4, "R* / a, circular orbit"),
        hz_inner_au=hz.get("runaway_greenhouse"), hz_outer_au=hz.get("maximum_greenhouse"), in_habitable_zone=in_hz,
        hz_optimistic_inner_au=hz.get("recent_venus"), hz_optimistic_outer_au=hz.get("early_mars"),
        in_optimistic_habitable_zone=in_opt)


def lookup(target: Target, period: float, fit_k: float | None, depth: float, *, fetch, store,
           seed: int = 20261021) -> tuple[StarParams, PlanetParams, list[Neighbour], GaiaRow | None]:
    """The four Gaia queries and the derivation, seeded: (star, planet, neighbours, gaia row)."""
    gaia = query_gaia(target, fetch=fetch, store=store)
    cone_target = target
    if target.gmag is None and gaia is not None and gaia.gmag is not None:
        cone_target = target.model_copy(update={"gmag": gaia.gmag})
    neighbours = query_neighbours(cone_target, depth, fetch=fetch, store=store)
    star, planet = derive(gaia, period, fit_k, depth, np.random.default_rng(seed))
    return star, planet, neighbours, gaia
