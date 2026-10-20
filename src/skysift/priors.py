"""The constants of the approximate astrophysical false-positive probability (spec 4.2), each with its citation in
``CITATIONS``: the planet occurrence grids (Fressin et al. 2013 for FGK stars, Dressing and Charbonneau 2015 for M
dwarfs), the eclipsing-binary fraction, the binary fractions and the Raghavan et al. 2010 period distribution, and the
arcsec scales of the confusion radius. Pure numbers and small lookups; ``fpp`` does the scenario arithmetic.

The FPP numbers built from these priors are provisional until the sign-off gate of spec 4.2: Vikram's review of the
factor tables of KIC 6146676 and KIC 5689121, after which they become the golden tests.

The occurrence cells are transcribed from the per-bin tables of the two papers (percent there, planets per star here);
a cell the paper does not estimate is ``nan`` and is never guessed, and a one-sided upper limit is stored as half its
value. ``OccurrenceGrid`` fills each ``nan`` from the nearest finite cell along the period axis when it is built."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

NAN = float("nan")

DRESSING_2015 = "Dressing, C. D. and Charbonneau, D. 2015, ApJ 807, 45"
FRESSIN_2013 = "Fressin, F. et al. 2013, ApJ 766, 81"
KIRK_2016 = "Kirk, B. et al. 2016, AJ 151, 68"
WINTERS_2019 = "Winters, J. G. et al. 2019, AJ 157, 216"
RAGHAVAN_2010 = "Raghavan, D. et al. 2010, ApJS 190, 1"
MORTON_2012 = "Morton, T. D. 2012, ApJ 761, 6"

M_TEFF_MAX = 4000.0                       # below this Teff the M-dwarf grid and binary fraction apply
M_GRID_NOTE = "radius above the M-dwarf grid; Fressin rate used"

F_EB = 0.014                              # Kirk et al. 2016
F_BIN = {"M": 0.26, "FGK": 0.44}          # Winters et al. 2019; Raghavan et al. 2010
RAGHAVAN_LOGP = (5.03, 2.28)              # mean and sigma of log10(P/day), Raghavan et al. 2010
PIXEL_ARCSEC = 3.98
CONFUSION_FLOOR_ARCSEC = 2.0              # half a Kepler pixel
PSF_BLEND_ARCSEC = 1.3                    # KBonus PSF photometry blend scale
ECCENTRICITY_ALLOWANCE = 0.30


@dataclass(frozen=True)
class OccurrenceGrid:
    """Planets per star in each (radius, period) bin of a published per-bin occurrence table."""
    period_edges: tuple[float, ...]       # days
    radius_edges: tuple[float, ...]       # Earth radii
    rate: tuple[tuple[float, ...], ...]   # planets per star per bin, [radius bin][period bin]
    citation: str
    _filled: np.ndarray = field(init=False, repr=False, compare=False)
    _log_p: np.ndarray = field(init=False, repr=False, compare=False)
    _log_r: np.ndarray = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        grid = np.array(self.rate, float)
        if grid.shape != (len(self.radius_edges) - 1, len(self.period_edges) - 1):
            raise ValueError(f"rate is {grid.shape}, the edges need "
                             f"{(len(self.radius_edges) - 1, len(self.period_edges) - 1)}")
        filled = grid.copy()
        for i, row in enumerate(grid):
            finite = np.flatnonzero(np.isfinite(row))
            if finite.size == 0: raise ValueError(f"radius bin {i} has no finite cell")
            for j in np.flatnonzero(~np.isfinite(row)):
                filled[i, j] = row[finite[np.argmin(np.abs(finite - j))]]   # nearest; the shorter period on a tie
        object.__setattr__(self, "_filled", filled)
        object.__setattr__(self, "_log_p", _log_centres(self.period_edges))
        object.__setattr__(self, "_log_r", _log_centres(self.radius_edges))

    def f_pl(self, rp: float, period: float) -> float:
        """The rate at (rp, period): bilinear in (log10 P, log10 Rp) between the bin centres, held at the outermost
        centres beyond them; a blank cell takes its nearest finite period neighbour."""
        i, u = _bracket(self._log_r, math.log10(rp))
        j, t = _bracket(self._log_p, math.log10(period))
        g = self._filled
        return float((1 - u) * ((1 - t) * g[i, j] + t * g[i, j + 1]) + u * ((1 - t) * g[i + 1, j] + t * g[i + 1, j + 1]))

    def total(self, period_max: float, rp_min: float = 0.0, rp_max: float = math.inf) -> float:
        """Planets per star summed over the bins lying wholly at periods up to ``period_max`` and radii in
        [rp_min, rp_max], blank cells taking their held neighbour value as in ``f_pl``."""
        tol = 1e-9
        rows = [i for i in range(len(self.radius_edges) - 1)
                if self.radius_edges[i] >= rp_min * (1 - tol) and self.radius_edges[i + 1] <= rp_max * (1 + tol)]
        cols = [j for j in range(len(self.period_edges) - 1) if self.period_edges[j + 1] <= period_max * (1 + tol)]
        return float(self._filled[np.ix_(rows, cols)].sum())


def _log_centres(edges: tuple[float, ...]) -> np.ndarray:
    e = np.log10(np.asarray(edges, float))
    return (e[:-1] + e[1:]) / 2


def _bracket(centres: np.ndarray, x: float) -> tuple[int, float]:
    """The lower centre index and the fraction towards the next, with x held inside the centres' range."""
    if centres.size == 1: return 0, 0.0
    x = min(max(x, centres[0]), centres[-1])
    i = int(np.clip(np.searchsorted(centres, x, side="right") - 1, 0, centres.size - 2))
    return i, float((x - centres[i]) / (centres[i + 1] - centres[i]))


# Fressin et al. 2013, Table 2 (per period bin), percent / 100. Rows ascending in radius:
# Earths 0.8-1.25, Super-Earths 1.25-2, Small Neptunes 2-4, Large Neptunes 4-6, Giants 6-22 Re.
_FRESSIN = tuple(tuple(v / 100 for v in row) for row in (
    # 0.8-2   2-3.4  3.4-5.9 5.9-10 10-17  17-29  29-50  50-85  85-145 145-245 245-418 d
    (0.18,   0.61,  1.72,   2.70,  2.70,  2.93,  4.08,  3.46,  NAN,   NAN,    NAN),     # "..." beyond 85 d
    (0.17,   0.74,  1.49,   2.90,  4.30,  4.49,  5.29,  3.66,  6.54,  NAN,    NAN),     # "..." beyond 145 d
    (0.035,  0.18,  0.73,   1.93,  3.67,  5.29,  6.45,  5.25,  4.31,  3.09,   NAN),     # "..." beyond 245 d
    (0.004,  0.006, 0.11,   0.091, 0.29,  0.32,  0.49,  0.66,  0.43,  0.53,   0.24),
    (0.015,  0.067, 0.17,   0.18,  0.27,  0.23,  0.35,  0.71,  1.25,  0.94,   1.05),
))

# Dressing and Charbonneau 2015, Table 4 second block (revised radii, the grid of Figure 16), percent / 100.
# "<x" is a one-sided 1 sigma upper limit: x / 2 is stored ("upper limit, half taken"). "..." (recovery below
# 15 percent) is nan.
_DRESSING = tuple(tuple(v / 100 for v in row) for row in (
    # 0.5-1.7  1.7-5.5  5.5-18.2   18.2-60.3  60.3-200 d
    (1.38,     8.42,    20.59,     NAN,       NAN),        # 0.5-1.0 Re; "..." beyond 18.2 d
    (1.95,     9.94,    26.63 / 2, 26.85,     28.85),      # 1.0-1.5 Re; <26.63: upper limit, half taken
    (0.41,     4.15,    23.58 / 2, 24.59,     19.98),      # 1.5-2.0 Re; <23.58: upper limit, half taken
    (0.13 / 2, 2.72,    18.73,     27.58,     18.08),      # 2.0-2.5 Re; <0.13: upper limit, half taken
    (0.11 / 2, 1.59,    8.29,      14.51,     8.61),       # 2.5-3.0 Re; <0.11: upper limit, half taken
    (0.12 / 2, 0.65,    3.25,      3.37,      1.97),       # 3.0-3.5 Re; <0.12: upper limit, half taken
    (0.11 / 2, 0.38,    1.05,      0.56,      2.34 / 2),   # 3.5-4.0 Re; <0.11, <2.34: upper limits, half taken
))

OCCURRENCE = {
    "FGK": OccurrenceGrid(period_edges=(0.8, 2.0, 3.4, 5.9, 10, 17, 29, 50, 85, 145, 245, 418),
                          radius_edges=(0.8, 1.25, 2, 4, 6, 22), rate=_FRESSIN, citation=FRESSIN_2013),
    "M": OccurrenceGrid(period_edges=(0.5, 1.7, 5.5, 18.2, 60.3, 200),
                        radius_edges=(0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0), rate=_DRESSING, citation=DRESSING_2015),
}

CITATIONS = {
    "F_EB": KIRK_2016,
    "F_BIN": f"M: {WINTERS_2019}; FGK: {RAGHAVAN_2010}",
    "F_BIN_M": WINTERS_2019,
    "F_BIN_FGK": RAGHAVAN_2010,
    "RAGHAVAN_LOGP": RAGHAVAN_2010,
    "OCCURRENCE": f"M: {DRESSING_2015}; FGK: {FRESSIN_2013}",
    "OCCURRENCE_M": DRESSING_2015,
    "OCCURRENCE_FGK": FRESSIN_2013,
    "M_TEFF_MAX": DRESSING_2015,
    "PIXEL_ARCSEC": "Kepler plate scale, 3.98 arcsec per pixel (Kepler Instrument Handbook)",
    "CONFUSION_FLOOR_ARCSEC": "half a Kepler pixel: the Gaussian PSF fit does not separate closer sources (spec 4.2)",
    "PSF_BLEND_ARCSEC": "KBonus Kepler PSF photometry blend scale (spec 4.2)",
    "ECCENTRICITY_ALLOWANCE": "SkySift allowance for the duration of an eccentric orbit (spec 4.2)",
    # the scenario model and the references the dossier's stellar and planet parameters rest on
    "FPP_MODEL": MORTON_2012,
    "GAIA_PARALLAX_ZERO_POINT": "Lindegren, L. et al. 2021, A&A 649, A4",
    "GAIA_DISTANCE": "Bailer-Jones, C. A. L. et al. 2021, AJ 161, 147",
    "M_DWARF_RADIUS": "Mann, A. W. et al. 2015, ApJ 804, 64",
    "M_DWARF_MASS": "Mann, A. W. et al. 2019, ApJ 871, 63",
    "DWARF_SEQUENCE": "Pecaut, M. J. and Mamajek, E. E. 2013, ApJS 208, 9",
    "HABITABLE_ZONE": "Kopparapu, R. K. et al. 2013, ApJ 765, 131",
}


def spectral_class(teff: float | None) -> str:
    """"M" below 4,000 K, else "FGK"; "FGK" when the temperature is unknown."""
    return "M" if teff is not None and teff < M_TEFF_MAX else "FGK"


def planet_prior(cls: str, rp: float, period: float) -> tuple[float, str | None]:
    """The planet occurrence prior of a spectral class and the factor-table note: an M dwarf with ``rp`` above the
    Dressing and Charbonneau grid (4 Re) takes the Fressin rate, with ``M_GRID_NOTE``."""
    if cls == "M" and rp > OCCURRENCE["M"].radius_edges[-1]:
        return OCCURRENCE["FGK"].f_pl(rp, period), M_GRID_NOTE
    return OCCURRENCE[cls].f_pl(rp, period), None


def binary_period_fraction(period: float) -> float:
    """The fraction of binaries whose period is within a factor of two of 2 * period: the Raghavan et al. 2010
    log-normal (log10 P/day, mean 5.03, sigma 2.28) between log10(2P) - log10(2) and log10(2P) + log10(2)."""
    mu, sigma = RAGHAVAN_LOGP
    def cdf(x: float) -> float: return 0.5 * (1.0 + math.erf((x - mu) / (sigma * math.sqrt(2.0))))
    return cdf(math.log10(4.0 * period)) - cdf(math.log10(period))
