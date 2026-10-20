"""Stellar and planet parameters from Gaia (spec 5.3): the route of the hand-built KIC 6146676 dossier
(``docs/candidates/kic-006146676/context/d_stellar_planet.py``), made general and seeded.

Four ADQL queries to the Gaia archive, each through the caller's ``fetch`` and so logged like every other request:
the ``gaia_source`` row, the Bailer-Jones et al. 2021 distance, the 2MASS ``Ks`` cross-match and a 20 arcsec cone for
the neighbours that could make the dip. ``derive`` is pure: the parallax corrected by the Lindegren et al. 2021 zero
point (from the installed ``gaiadr3-zeropoint`` package, the optional ``dossier`` extra; LGPL, so imported and never
vendored; without it the global offset of -0.017 mas with a caveat), the
Mamajek dwarf table at the observed BP-RP for Teff and spectral type, and below 4,000 K the Mann et al. 2015 radius
and Mann et al. 2019 mass from the absolute Ks; errors from a ``N_DRAWS`` Monte Carlo reported as the 16th, 50th and
84th percentiles. Gaia's own GSP-Phot and FLAME values are kept as a cross-check, never as the headline."""
from __future__ import annotations

import functools
import importlib
import math
import re
import warnings
from dataclasses import dataclass
from importlib import resources

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
KOPPARAPU_TEFF = (2600.0, 7200.0)
# Kopparapu et al. 2013 (erratum) effective-flux coefficients, as in the hand-built template; t = Teff - 5780
KOPPARAPU = {
    "recent_venus": (1.7763, 1.4335e-4, 3.3954e-9, -7.6364e-12, -1.1950e-15),
    "runaway_greenhouse": (1.0512, 1.3242e-4, 1.5418e-8, -7.9895e-12, -1.8328e-15),
    "maximum_greenhouse": (0.3438, 5.8942e-5, 1.6558e-9, -3.0045e-12, -5.2983e-16),
    "early_mars": (0.3179, 5.4513e-5, 1.5313e-9, -2.7786e-12, -4.8997e-16),
}
G_SI, MSUN, RSUN, AU, REARTH = 6.674e-11, 1.98892e30, 6.957e8, 1.495978707e11, 6.3781e6   # as d_stellar_planet.py

NO_STAR = "no Gaia parallax or colour: the star cannot be characterised from photometry"
KS_MISSING = "Ks missing: Mamajek radius with doubled scatter"
ZERO_POINT_FALLBACK = ("Gaia parallax zero point: the global offset of -0.017 mas was used because the "
                       "gaiadr3-zeropoint package is not installed")

Q_SOURCE = ("SELECT source_id, ra, dec, parallax, parallax_error, phot_g_mean_mag, bp_rp, ruwe, "
            "nu_eff_used_in_astrometry, pseudocolour, ecl_lat, astrometric_params_solved, teff_gspphot, radius_flame, "
            "mass_flame FROM gaiadr3.gaia_source WHERE source_id = {id}")
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


def _adql(query: str, *, fetch, store, target: Target) -> list[dict]:
    params = {"request": "doQuery", "lang": "ADQL", "format": "csv", "query": query}
    data = fetch(GAIA_TAP, params=params, service="gaia", store=store, target_id=target.target_id)
    err = looks_like_error_document(data)
    if err:  # HTTP 200 carrying an error document is an outage, never an empty result
        raise ServiceUnavailable(f"gaia: {err}")
    return _csv_rows(data)[1]


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
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
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
    src = _adql(Q_SOURCE.format(id=gid), fetch=fetch, store=store, target=target)
    if not src:
        return None
    s = src[0]
    dist = _adql(Q_DISTANCE.format(id=gid), fetch=fetch, store=store, target=target)
    tmass = _adql(Q_TMASS.format(id=gid), fetch=fetch, store=store, target=target)
    d, t = (dist[0] if dist else {}), (tmass[0] if tmass else {})
    gmag, nu_eff = _num(s.get("phot_g_mean_mag")), _num(s.get("nu_eff_used_in_astrometry"))
    pc = _num(s.get("pseudocolour"))
    ecl_lat, aps = _num(s.get("ecl_lat")), _int(s.get("astrometric_params_solved"))
    try:
        zp = zero_point(gmag, nu_eff, pc, ecl_lat, aps)
    except ValueError:
        zp = None
    return GaiaRow(
        source_id=str(s.get("source_id") or gid), ra=_num(s.get("ra")) or target.ra,
        dec=_num(s.get("dec")) or target.dec,
        parallax=_num(s.get("parallax")), parallax_error=_num(s.get("parallax_error")), zero_point=zp,
        gmag=gmag, bp_rp=_num(s.get("bp_rp")), ruwe=_num(s.get("ruwe")), nu_eff=nu_eff, pseudocolour=pc,
        ecl_lat=ecl_lat, params_solved=aps, teff_gspphot=_num(s.get("teff_gspphot")),
        radius_flame=_num(s.get("radius_flame")), mass_flame=_num(s.get("mass_flame")),
        r_med_geo=_num(d.get("r_med_geo")), r_lo_geo=_num(d.get("r_lo_geo")), r_hi_geo=_num(d.get("r_hi_geo")),
        ks=_num(t.get("ks_m")), ks_err=_num(t.get("ks_msigcom")))


def query_neighbours(target: Target, depth: float, *, fetch, store, radius_arcsec: float = 20.0) -> list[Neighbour]:
    """Gaia DR3 sources within ``radius_arcsec`` that are bright enough to make the dip even when fully eclipsed
    (spec 4.2: ``G <= G_target + 2.5 log10(1 / (2 depth))``), nearest first; one logged query. Without a target G or a
    positive depth no cut is possible and every source is kept. Without a Gaia id the cone has no ``source_id`` clause
    and the nearest source within ``SELF_MATCH_ARCSEC`` is taken to be the target itself."""
    gid = _numeric_id(target.gaia_id)
    query = Q_CONE.format(ra=target.ra, dec=target.dec, radius_deg=radius_arcsec / 3600.0)
    if gid is not None:
        query += f" AND source_id != {gid}"
    rows = _adql(query, fetch=fetch, store=store, target=target)
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

def _value(draws, nd: int, source: str) -> Value:
    lo, med, hi = np.nanpercentile(draws, [16, 50, 84])
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


def _unknown(gaia: GaiaRow | None, extra: tuple[str, ...] = ()) -> tuple[StarParams, PlanetParams]:
    star = StarParams(route="none", spectral_type=None, teff=None, radius=None, mass=None, density_gcc=None,
                      distance_pc=None, abs_g=None, abs_ks=None,
                      gaia_cross_check=_cross_check(gaia, None, None, None, None), reddening_note="",
                      alt_radius_e02=None, caveats=(NO_STAR, *extra))
    planet = PlanetParams(rp_rearth=None, a_au=None, a_over_r=None, teq_k=None, insolation_earth=None,
                          transit_probability=None, hz_inner_au=None, hz_outer_au=None, in_habitable_zone=None)
    return star, planet


def _hz_distance(teff: float, lum: float, coeffs) -> float:
    t = teff - 5780.0
    s_eff = sum(c * t ** i for i, c in enumerate(coeffs))
    return round(math.sqrt(lum / s_eff), 4)


def derive(gaia: GaiaRow | None, period: float, fit_k: float | None, depth: float,
           rng: np.random.Generator) -> tuple[StarParams, PlanetParams]:
    """The star and planet of spec 5.3 from one Gaia row, the period (days), the fitted radius ratio (None without a
    fit: the depth is used) and the depth (fraction). Every draw comes from ``rng``."""
    if gaia is None or gaia.parallax is None or gaia.parallax_error is None or gaia.bp_rp is None:
        return _unknown(gaia)
    caveats: list[str] = []
    zp = gaia.zero_point
    if zp is None:
        caveats.append("no Lindegren et al. 2021 zero point for this astrometric solution: the parallax is uncorrected")
        zp = 0.0
    elif not zero_point_available():
        caveats.append(ZERO_POINT_FALLBACK)
    plx, eplx = gaia.parallax - zp, math.hypot(gaia.parallax_error, ZERO_POINT_SYSTEMATIC)
    if plx <= 0:
        return _unknown(gaia, (f"corrected parallax {plx:.3f} mas is not positive",))
    if plx / eplx < 5:
        caveats.append(f"parallax over error {plx / eplx:.1f}: the distance and radius are loose")
    if gaia.ruwe is not None and gaia.ruwe > RUWE_MAX:
        caveats.append(f"RUWE {gaia.ruwe:.2f} above {RUWE_MAX}: the astrometry may be disturbed by a companion")
    bp_rp, tab = gaia.bp_rp, mamajek_table()
    if not tab[0, 0] <= bp_rp <= tab[-1, 0]:
        caveats.append(f"BP-RP {bp_rp:.2f} is outside the dwarf table ({tab[0, 0]:.2f} to {tab[-1, 0]:.2f}): "
                       "values are held at the table edge")

    # the draws, in a fixed order so a seed always gives the same numbers
    colour = rng.normal(bp_rp, COLOUR_SIGMA, N_DRAWS)
    plx_s = rng.normal(plx, eplx, N_DRAWS)
    plx_s[plx_s <= 0] = np.nan
    dist_s = 1000.0 / plx_s
    dm_s = 5 * np.log10(dist_s) - 5
    teff_s = _interp(colour, 1)
    dist0 = 1000.0 / plx
    dm0 = 5 * math.log10(dist0) - 5
    abs_g = round(gaia.gmag - dm0, 3) if gaia.gmag is not None else None
    abs_ks = round(gaia.ks - dm0, 3) if gaia.ks is not None else None
    mg_dwarf = float(_interp(bp_rp, 4))
    if abs_g is not None and abs(abs_g - mg_dwarf) > DWARF_MG_TOLERANCE:
        caveats.append(f"absolute G {abs_g:.2f} is more than {DWARF_MG_TOLERANCE:.0f} mag from the dwarf sequence "
                       f"({mg_dwarf:.2f}) at this colour: the star may be evolved, an unresolved binary or reddened")
    cool = float(_interp(bp_rp, 1)) < M_TEFF_MAX
    table_src = f"Mamajek dwarf table at BP-RP {bp_rp:.3f} +- {COLOUR_SIGMA:.2f} ({CITATIONS['DWARF_SEQUENCE']})"

    if cool and gaia.ks is not None:
        route = "mann"
        ks_s = rng.normal(gaia.ks, gaia.ks_err or KS_ERR_DEFAULT, N_DRAWS)
        mks = ks_s - dm_s - rng.uniform(0.0, A_KS_MAX, N_DRAWS)
        radius_s = (1.9515 - 0.3520 * mks + 0.01680 * mks ** 2) * rng.normal(1, MANN_R_SCATTER, N_DRAWS)
        x = mks - 7.5
        log_m = -0.642 - 0.208 * x - 8.43e-4 * x ** 2 + 7.87e-3 * x ** 3 + 1.42e-4 * x ** 4 - 2.13e-4 * x ** 5
        mass_s = 10 ** log_m * rng.normal(1, MANN_M_SCATTER, N_DRAWS)
        mks_med = float(np.nanmedian(mks))
        if not MANN_MKS_RANGE[0] <= mks_med <= MANN_MKS_RANGE[1]:
            caveats.append(f"M_Ks {mks_med:.2f} is outside the Mann et al. calibration ({MANN_MKS_RANGE[0]} to "
                           f"{MANN_MKS_RANGE[1]}): the star may not be a dwarf")
        ks_src = f"2MASS Ks {gaia.ks:.3f}, A_Ks 0 to {A_KS_MAX}, Gaia parallax"
        r_src = f"Mann et al. 2015 R(M_Ks) ({CITATIONS['M_DWARF_RADIUS']}), {ks_src}"
        m_src = f"Mann et al. 2019 M(M_Ks) ({CITATIONS['M_DWARF_MASS']}), {ks_src}"
    else:
        route = "mamajek"
        rm_colour = colour
        if cool:  # an M dwarf without Ks: the colour radius, with the colour scatter doubled
            rm_colour = bp_rp + 2.0 * (colour - bp_rp)
            caveats.append(KS_MISSING)
        radius_s, mass_s = _interp(rm_colour, 2), _interp(rm_colour, 3)
        width = 2 * COLOUR_SIGMA if cool else COLOUR_SIGMA
        r_src = m_src = f"Mamajek dwarf table at BP-RP {bp_rp:.3f} +- {width:.2f} ({CITATIONS['DWARF_SEQUENCE']})"

    density_s = mass_s * MSUN / (4 / 3 * np.pi * (radius_s * RSUN) ** 3) / 1000.0
    teff = _value(teff_s, 0, table_src + ", E(BP-RP) <= 0.1 assumed")
    radius, mass = _value(radius_s, 3, r_src), _value(mass_s, 3, m_src)
    distance = _value(dist_s, 1, f"1000 / Gaia DR3 parallax, zero point {zp:+.4f} mas "
                                 f"({CITATIONS['GAIA_PARALLAX_ZERO_POINT']}), +- {eplx:.4f} mas")
    alt = _value(_interp(colour - REDDENING_ALT, 2), 3,
                 f"Mamajek dwarf table at BP-RP {bp_rp - REDDENING_ALT:.3f} +- {COLOUR_SIGMA:.2f} (if E(BP-RP) = 0.2)")
    reddening_note = (f"Teff and spectral type assume E(BP-RP) at or below 0.1 at the observed BP-RP {bp_rp:.2f}; "
                      f"if E(BP-RP) were {REDDENING_ALT} the dwarf-table radius would be {alt.median:.3f} Rsun.")
    if route == "mann":
        reddening_note += " The Ks radius barely depends on reddening: A_Ks 0 to 0.07 is in the draws."
    labels = mamajek_labels()
    spectral_type = labels[int(np.argmin(np.abs(tab[:, 0] - bp_rp)))]
    star = StarParams(route=route, spectral_type=spectral_type, teff=teff, radius=radius, mass=mass,
                      density_gcc=_value(density_s, 2, "M* / (4/3 pi R*^3) from the same draws"),
                      distance_pc=distance, abs_g=abs_g, abs_ks=abs_ks,
                      gaia_cross_check=_cross_check(gaia, teff, radius, mass, distance),
                      reddening_note=reddening_note, alt_radius_e02=alt, caveats=tuple(caveats))
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
    if KOPPARAPU_TEFF[0] <= teff_med <= KOPPARAPU_TEFF[1]:
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
