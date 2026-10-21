import math

import pytest

from skysift import priors
from skysift.priors import (
    CITATIONS,
    F_BIN,
    F_EB,
    M_GRID_NOTE,
    OCCURRENCE,
    RAGHAVAN_LOGP,
    binary_period_fraction,
    planet_prior,
    spectral_class,
)

BRIEF_CITATIONS = (
    "Dressing, C. D. and Charbonneau, D. 2015, ApJ 807, 45", "Fressin, F. et al. 2013, ApJ 766, 81",
    "Kirk, B. et al. 2016, AJ 151, 68", "Winters, J. G. et al. 2019, AJ 157, 216",
    "Raghavan, D. et al. 2010, ApJS 190, 1", "Morton, T. D. 2012, ApJ 761, 6", "Lindegren, L. et al. 2021, A&A 649, A4",
    "Bailer-Jones, C. A. L. et al. 2021, AJ 161, 147", "Mann, A. W. et al. 2015, ApJ 804, 64",
    "Mann, A. W. et al. 2019, ApJ 871, 63", "Pecaut, M. J. and Mamajek, E. E. 2013, ApJS 208, 9",
    "Kopparapu, R. K. et al. 2013, ApJ 765, 131")
CONSTANTS = ("F_EB", "F_BIN", "RAGHAVAN_LOGP", "PIXEL_ARCSEC", "CONFUSION_FLOOR_ARCSEC", "PSF_BLEND_ARCSEC",
             "ECCENTRICITY_ALLOWANCE", "OCCURRENCE")


def test_f_pl_interpolates_in_log_space_and_holds_at_the_edges():
    g = OCCURRENCE["FGK"]
    inside = g.f_pl(2.6, 40.0); assert 0 < inside < 1
    assert g.f_pl(2.6, 10_000.0) == g.f_pl(2.6, g.period_edges[-1] * 0.999)
    assert g.f_pl(0.1, 40.0) == g.f_pl(g.radius_edges[0] * 1.001, 40.0)


def test_every_constant_has_a_citation():
    for name in ("F_EB", "F_BIN", "RAGHAVAN_LOGP", "OCCURRENCE"):
        assert CITATIONS[name]


def test_every_module_constant_is_cited_and_the_brief_strings_are_verbatim():
    for name in CONSTANTS:
        assert hasattr(priors, name) and CITATIONS[name]
    for text in BRIEF_CITATIONS:
        assert text in CITATIONS.values()
    assert OCCURRENCE["FGK"].citation == "Fressin, F. et al. 2013, ApJ 766, 81"
    assert OCCURRENCE["M"].citation == "Dressing, C. D. and Charbonneau, D. 2015, ApJ 807, 45"
    assert "signed off on 21 October 2026" in priors.__doc__ and chr(0x2014) not in priors.__doc__


def test_constant_values():
    assert F_EB == 0.014 and F_BIN == {"M": 0.26, "FGK": 0.44} and RAGHAVAN_LOGP == (5.03, 2.28)
    assert priors.PIXEL_ARCSEC == 3.98 and priors.CONFUSION_FLOOR_ARCSEC == 2.0
    assert priors.PSF_BLEND_ARCSEC == 1.3 and priors.ECCENTRICITY_ALLOWANCE == 0.30


def test_binary_period_fraction_is_a_probability_that_peaks_near_the_mode():
    assert 0 < binary_period_fraction(1.0) < binary_period_fraction(10 ** 5.03 / 2) < 1


def test_binary_period_fraction_is_the_normal_mass_between_p_and_4p():
    mu, sigma = RAGHAVAN_LOGP
    def cdf(x): return 0.5 * (1 + math.erf((x - mu) / (sigma * math.sqrt(2))))
    assert binary_period_fraction(3.0) == pytest.approx(cdf(math.log10(12.0)) - cdf(math.log10(3.0)))
    assert binary_period_fraction(10 ** 5.03 / 2) == pytest.approx(math.erf(math.log10(2) / (2.28 * math.sqrt(2))))


def test_spectral_class_boundary():
    assert spectral_class(3999.0) == "M" and spectral_class(4000.0) == "FGK" and spectral_class(None) == "FGK"


def test_grid_shapes_and_units():
    for g in OCCURRENCE.values():
        assert len(g.rate) == len(g.radius_edges) - 1
        assert all(len(row) == len(g.period_edges) - 1 for row in g.rate)
        finite = [v for row in g.rate for v in row if not math.isnan(v)]
        assert finite and all(0 <= v < 1 for v in finite)        # percent converted to planets per star
    fgk, m = OCCURRENCE["FGK"], OCCURRENCE["M"]
    assert fgk.period_edges == (0.8, 2.0, 3.4, 5.9, 10, 17, 29, 50, 85, 145, 245, 418)
    assert fgk.radius_edges == (0.8, 1.25, 2, 4, 6, 22)
    assert m.period_edges == (0.5, 1.7, 5.5, 18.2, 60.3, 200)
    assert m.radius_edges == (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0)
    assert fgk.rate[2][5] == pytest.approx(0.0529)               # Small Neptunes, 17-29 d: 5.29 percent
    assert m.rate[1][2] == pytest.approx(0.2663 / 2)              # "<26.63": upper limit, half taken
    assert math.isnan(fgk.rate[0][8]) and math.isnan(m.rate[0][3])     # blank cells stay nan, never guessed


WINDOW = 2 * math.log10(2) * math.log10(1.3 / 0.7)                 # dex^2: P/2..2P by 0.7..1.3 Rp


def density(rate: float, p_lo: float, p_hi: float, r_lo: float, r_hi: float) -> float:
    """A paper cell (planets per star in the bin) as planets per star per dex of period and dex of radius."""
    return rate / (math.log10(p_hi / p_lo) * math.log10(r_hi / r_lo))


def test_f_pl_is_the_cell_density_times_the_window_at_a_bin_centre():
    g = OCCURRENCE["M"]
    p = math.sqrt(5.5 * 18.2); r = math.sqrt(2.0 * 2.5)
    d = density(0.1873, 5.5, 18.2, 2.0, 2.5)                        # 18.73 percent in a 0.52 x 0.097 dex cell
    assert g.density(r, p) == pytest.approx(d)
    assert g.f_pl(r, p) == pytest.approx(d * WINDOW)
    assert g.f_pl(r, p) == pytest.approx(0.602, rel=1e-3)           # by hand: 3.719 per dex^2 x 0.1619 dex^2
    p2 = math.sqrt(18.2 * 60.3)
    mid = g.f_pl(r, math.sqrt(p * p2))                             # halfway in log P between two centres
    assert mid == pytest.approx((d + density(0.2758, 18.2, 60.3, 2.0, 2.5)) / 2 * WINDOW)
    assert priors.PL_PERIOD_WINDOW_DEX == pytest.approx(2 * math.log10(2))
    assert priors.PL_RADIUS_WINDOW_DEX == pytest.approx(math.log10(1.3 / 0.7))


def test_the_two_grids_read_on_one_scale():
    # a cell of the same planets-per-star rate in a Fressin bin (0.23 x 0.30 dex) and a Dressing bin (0.52 x 0.08
    # dex) must not give the same f_pl: the density differs by the ratio of the bin areas
    fgk, m = OCCURRENCE["FGK"], OCCURRENCE["M"]
    rf = fgk.density(math.sqrt(2 * 4), math.sqrt(17 * 29)) / fgk.rate[2][5]
    rm = m.density(math.sqrt(2.0 * 2.5), math.sqrt(5.5 * 18.2)) / m.rate[3][2]
    assert rf == pytest.approx(1 / (math.log10(29 / 17) * math.log10(4 / 2)))
    assert rm == pytest.approx(1 / (math.log10(18.2 / 5.5) * math.log10(2.5 / 2.0)))


def test_a_blank_cell_holds_its_nearest_finite_period_neighbour():
    fgk, m = OCCURRENCE["FGK"], OCCURRENCE["M"]
    # Earths beyond 85 d: the 50-85 d density (3.46 percent over that cell) held out to the last edge
    assert fgk.f_pl(math.sqrt(0.8 * 1.25), math.sqrt(245 * 418)) == pytest.approx(
        density(0.0346, 50, 85, 0.8, 1.25) * WINDOW)
    # M dwarfs of 0.5-1 Re beyond 18.2 d: the 5.5-18.2 d density held
    assert m.f_pl(math.sqrt(0.5 * 1.0), 100.0) == pytest.approx(density(0.2059, 5.5, 18.2, 0.5, 1.0) * WINDOW)
    for g in (fgk, m):
        for rp in (0.6, 1.1, 1.7, 3.3, 5.0, 12.0):
            for period in (0.6, 3.0, 20.0, 150.0, 400.0):
                assert math.isfinite(g.f_pl(rp, period)) and g.f_pl(rp, period) >= 0


def test_m_dwarf_total_matches_the_paper_headline():
    # Dressing and Charbonneau 2015: 2.5 +- 0.2 planets per M dwarf of 1-4 Re with P < 200 d
    total = OCCURRENCE["M"].total(200.0, rp_min=1.0, rp_max=4.0)
    assert total == pytest.approx(2.5, rel=0.30)


def test_planet_prior_falls_back_to_fressin_above_the_m_dwarf_grid():
    assert planet_prior("M", 2.2, 10.0) == (OCCURRENCE["M"].f_pl(2.2, 10.0), None)
    assert planet_prior("FGK", 2.2, 10.0) == (OCCURRENCE["FGK"].f_pl(2.2, 10.0), None)
    assert planet_prior("M", 4.0, 10.0)[1] is None
    assert planet_prior("M", 5.0, 10.0) == (OCCURRENCE["FGK"].f_pl(5.0, 10.0), M_GRID_NOTE)
    assert M_GRID_NOTE == "radius above the M-dwarf grid; Fressin rate used"
