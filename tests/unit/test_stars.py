import hashlib
import importlib.resources
import math
import sys
import warnings
from dataclasses import replace

import numpy as np
import pytest

from skysift import stars
from skysift.models import Target
from skysift.net import ServiceUnavailable
from skysift.stars import Q_SOURCE, derive, lookup, mamajek_table, query_gaia, query_neighbours, zero_point
from skysift.store.db import Store

# KIC 6146676 = Gaia DR3 2073896294004902272. nu_eff_used_in_astrometry and ecl_lat are the archived Gaia DR3 values
# (docs/candidates/kic-006146676/context/archive-proof/B_gaia_source_candidate.csv); they give the published -0.0317.
GAIA_CSV = (b"source_id,ra,dec,parallax,parallax_error,phot_g_mean_mag,bp_rp,ruwe,nu_eff_used_in_astrometry,"
            b"pseudocolour,ecl_lat,astrometric_params_solved,teff_gspphot,radius_flame,mass_flame\n"
            b"2073896294004902272,297.8687,41.4556,3.268646,0.035821,16.236654,2.0971918,0.9449,1.3330975,,"
            b"60.5697568,31,3855.0,0.5394,0.5595\n")
DIST_CSV = b"r_med_geo,r_lo_geo,r_hi_geo\n301.9,298.6,305.3\n"
TMASS_CSV = b"ks_m,ks_msigcom\n13.030,0.034\n"
# the G limit for a 2165 ppm dip is 16.24 + 2.5 log10(1 / 0.00433) = 22.15: the G 22.6 row is too faint to make it
CONE_CSV = (b"source_id,ra,dec,phot_g_mean_mag,bp_rp,sep_arcsec\n"
            b"2073896294004902144,297.8704,41.4571,19.772,1.814,7.14\n"
            b"2073896294004902400,297.8660,41.4535,12.80,1.1,10.8\n"
            b"2073896294004903000,297.8690,41.4590,22.6,1.5,12.0\n")
RESPONSES = {"CONTAINS": CONE_CSV, "WHERE g.source_id": GAIA_CSV, "gaiaedr3_distance": DIST_CSV,
             "tmass": TMASS_CSV}


def fake_fetch(responses=None, calls=None):
    """Answer each ADQL query by the first routing substring found in its text; log it like ``net.fetch``."""
    responses = RESPONSES if responses is None else responses

    def fetch(url, *, params=None, service, store=None, target_id=None, **_kw):
        assert service == "gaia" and target_id == "kic-006146676"
        query = params["query"]
        if calls is not None: calls.append(query)
        data = next(body for key, body in responses.items() if key in query)
        if store:
            store.log_query(service, url, params, 200, len(data), hashlib.sha256(data).hexdigest(), target_id=target_id)
        return data
    return fetch


def explode(*_a, **_kw):
    raise AssertionError("no query expected")


@pytest.fixture
def store(tmp_path):
    st = Store(f"sqlite:///{tmp_path.as_posix()}/t.db"); st.init_schema()
    return st


def target_6146676() -> Target:
    return Target(source="kbonus_bkg", target_id="kic-006146676", ra=297.8687, dec=41.4556,
                  gaia_id="2073896294004902272", kic=6146676, gmag=16.236654)


def target_kic_only() -> Target:
    return target_6146676().model_copy(update={"gaia_id": None})


def gaia_row_6146676():
    return query_gaia(target_6146676(), fetch=fake_fetch(), store=None)


def test_query_gaia_parses_the_three_tables_and_logs_each(store):
    row = query_gaia(target_6146676(), fetch=fake_fetch(), store=store)
    assert row.parallax == pytest.approx(3.268646) and row.ks == pytest.approx(13.03)
    assert row.r_med_geo == pytest.approx(301.9)
    assert row.pseudocolour is None and row.params_solved == 31
    assert row.zero_point == zero_point(16.236654, 1.3330975, None, 60.5697568, 31)
    assert store.counts()["queries"] == 3


def test_query_gaia_without_zero_point_inputs_uses_the_global_offset():
    two_p = GAIA_CSV.replace(b",60.5697568,31,", b",60.5697568,3,")
    fetch = fake_fetch({**RESPONSES, "WHERE g.source_id": two_p})
    row = query_gaia(target_6146676(), fetch=fetch, store=None)
    assert row.zero_point == -0.017 and row.zero_point_source == "none: missing inputs"
    star, _ = derive(row, 40.29109, 0.048, 2165e-6, np.random.default_rng(1))
    assert star.caveats == (stars.ZERO_POINT_MISSING,)


def test_a_zero_row_answer_is_no_row_but_an_empty_body_is_an_outage(store):
    header_only = GAIA_CSV.split(b"\n")[0] + b"\n"
    assert query_gaia(target_6146676(), fetch=fake_fetch({"WHERE g.source_id": header_only}),
                      store=store) is None
    with pytest.raises(ServiceUnavailable, match="gaia_source: empty response"):
        query_gaia(target_6146676(), fetch=fake_fetch({"WHERE g.source_id": b""}), store=store)
    with pytest.raises(ServiceUnavailable, match="2MASS cross-match: missing column ks_m"):
        query_gaia(target_6146676(), fetch=fake_fetch({**RESPONSES, "tmass": b"ks,ks_msigcom\n13.0,0.03\n"}),
                   store=store)


def test_a_cone_answer_without_its_separation_column_is_a_schema_change(store):
    no_sep = CONE_CSV.replace(b",sep_arcsec\n", b",separation\n")
    with pytest.raises(ServiceUnavailable, match="neighbour cone: missing column sep_arcsec"):
        query_neighbours(target_6146676(), 2165e-6, fetch=fake_fetch({"CONTAINS": no_sep}), store=store)


def test_an_error_document_or_a_failed_fetch_is_an_outage(store):
    vot = (b'<?xml version="1.0"?><VOTABLE><RESOURCE><INFO name="QUERY_STATUS" value="ERROR">UsageFault: bad'
           b"</INFO></RESOURCE></VOTABLE>")
    with pytest.raises(ServiceUnavailable, match="UsageFault"):
        query_gaia(target_6146676(), fetch=fake_fetch({"WHERE g.source_id": vot}), store=store)
    down = ServiceUnavailable("gaia https://gea.esac.esa.int -> HTTP 503")

    def failing(*_a, **_kw):
        raise down
    with pytest.raises(ServiceUnavailable) as caught:
        query_neighbours(target_6146676(), 2165e-6, fetch=failing, store=store)
    assert caught.value is down


def test_query_gaia_without_a_numeric_id_returns_none_and_asks_nothing(store):
    assert query_gaia(target_kic_only(), fetch=explode, store=store) is None
    assert query_gaia(target_6146676().model_copy(update={"gaia_id": "1 or 1=1"}), fetch=explode, store=store) is None


def test_neighbours_keep_only_stars_bright_enough_for_the_depth(store):
    calls = []
    ns = query_neighbours(target_6146676(), depth=2165e-6, fetch=fake_fetch(calls=calls), store=store)
    assert [n.gmag for n in ns] == [19.772, 12.8]
    assert ns[0].source_id == "2073896294004902144" and ns[0].sep_arcsec == pytest.approx(7.14)
    assert ns[0].centroid_sep_arcsec is None and store.counts()["queries"] == 1
    assert "source_id != 2073896294004902272" in calls[0] and "CIRCLE('ICRS', 297.8687, 41.4556, " in calls[0]


def test_neighbours_without_a_gaia_id_drop_the_clause_and_the_target_itself(store):
    calls = []
    own = CONE_CSV + b"2073896294004902272,297.8687,41.4556,16.24,2.10,0.05\n"
    ns = query_neighbours(target_kic_only(), depth=2165e-6, fetch=fake_fetch({"CONTAINS": own}, calls), store=store)
    assert "source_id !=" not in calls[0] and [n.gmag for n in ns] == [19.772, 12.8]


def test_zero_point_matches_the_published_value_for_kic_6146676():
    pytest.importorskip("zero_point.zpt")
    assert zero_point(16.236654, 1.3330975, None, 60.5697568, 31) == pytest.approx(-0.0317, abs=0.001)


def test_zero_point_matches_the_installed_package():
    zpt = pytest.importorskip("zero_point.zpt")
    zpt.load_tables()
    cases = [(16.24, 1.333, None, 60.57, 31), (9.5, 1.55, None, -12.0, 31), (12.1, 1.72, None, 85.0, 31),
             (19.8, None, 1.40, 33.3, 95), (14.0, None, 1.30, -70.0, 95), (20.5, 1.20, None, 5.0, 31)]
    for g, nu, pc, lat, aps in cases:
        ref = zpt.get_zpt(np.array([g]), np.array([np.nan if nu is None else nu]),
                          np.array([np.nan if pc is None else pc]), np.array([lat]), np.array([aps]))[0]
        assert zero_point(g, nu, pc, lat, aps) == pytest.approx(float(ref), abs=1e-9)


@pytest.fixture
def no_zero_point_package(monkeypatch):
    monkeypatch.setitem(sys.modules, "zero_point", None)       # a None entry makes the import raise ImportError
    monkeypatch.setitem(sys.modules, "zero_point.zpt", None)
    stars._zpt_module.cache_clear()
    yield
    stars._zpt_module.cache_clear()


def test_zero_point_falls_back_to_the_global_offset_without_the_package(no_zero_point_package):
    assert zero_point(16.236654, 1.3330975, None, 60.5697568, 31) == -0.017
    star, _ = derive(gaia_row_6146676(), 40.29109, 0.048, 2165e-6, np.random.default_rng(1))
    assert star.route == "mann" and star.distance_pc.median == pytest.approx(1000 / (3.268646 + 0.017), abs=0.5)
    fallback = ("Gaia parallax zero point: the global offset of -0.017 mas was used because the "
                "gaiadr3-zeropoint package is not installed")
    assert star.caveats == (fallback,)


def test_zero_point_rejects_a_two_parameter_solution():
    with pytest.raises(ValueError):
        zero_point(16.0, None, None, 10.0, 3)


def test_derive_reproduces_the_hand_built_dossier_within_its_errors():
    star, planet = derive(gaia_row_6146676(), period=40.29109, fit_k=0.048, depth=2165e-6, rng=np.random.default_rng(1))
    assert star.route == "mann" and star.spectral_type.startswith("M1")
    assert star.teff.median == pytest.approx(3650, abs=90) and star.radius.median == pytest.approx(0.50, abs=0.03)
    assert star.distance_pc.median == pytest.approx(303, abs=4)
    assert planet.rp_rearth.median == pytest.approx(2.61, abs=0.15)
    assert planet.a_au.median == pytest.approx(0.182, abs=0.004)
    assert planet.teq_k.median == pytest.approx(267, abs=12)
    assert planet.insolation_earth.median == pytest.approx(1.2, abs=0.1)
    # between the optimistic (recent Venus) and conservative (runaway greenhouse) inner edges, as the hand-built
    # page says: outside the conservative zone, inside the optimistic one
    assert planet.in_habitable_zone is False and planet.in_optimistic_habitable_zone is True
    assert planet.hz_optimistic_inner_au < planet.a_au.median < planet.hz_inner_au < planet.hz_outer_au
    assert star.alt_radius_e02.median > star.radius.median and "0.2" in star.reddening_note
    assert star.gaia_cross_check["teff_gspphot"] == pytest.approx(3855.0) and star.gaia_cross_check["note"]
    assert star.teff.as_dict()["source"] == star.teff.source


def test_derive_fixed_seed_regression():
    star, _ = derive(gaia_row_6146676(), 40.29109, 0.048, 2165e-6, np.random.default_rng(1))
    # the round 3 final review, C1(ii): A_Ks 0 at the unreddened end, not 0 to 0.07 (0.509 and 0.507 before)
    assert star.radius.median == pytest.approx(0.503, abs=0.002) and star.mass.median == pytest.approx(0.501, abs=0.002)
    assert star.distance_pc.median == pytest.approx(303.0, abs=0.3) and star.caveats == ()


def test_derive_is_seeded_by_the_rng():
    a = derive(gaia_row_6146676(), 40.29109, 0.048, 2165e-6, np.random.default_rng(7))
    b = derive(gaia_row_6146676(), 40.29109, 0.048, 2165e-6, np.random.default_rng(7))
    assert a == b


def test_derive_without_a_fit_uses_the_depth_and_says_so():
    _, planet = derive(gaia_row_6146676(), 40.29109, None, 2165e-6, np.random.default_rng(1))
    assert "depth" in planet.rp_rearth.source and planet.rp_rearth.median == pytest.approx(2.56, abs=0.15)


def test_derive_without_ks_uses_mamajek_with_a_caveat():
    star, _ = derive(replace(gaia_row_6146676(), ks=None, ks_err=None), 40.29, 0.048, 2165e-6, np.random.default_rng(1))
    assert star.route == "mamajek" and star.caveats == ("Ks missing: Mamajek radius with doubled scatter",)
    mann, _ = derive(gaia_row_6146676(), 40.29, 0.048, 2165e-6, np.random.default_rng(1))
    assert star.radius.minus > 2 * mann.radius.minus and "+- 0.20" in star.radius.source


def test_a_distant_star_is_off_the_mann_calibration_and_takes_the_table_route():
    far = replace(gaia_row_6146676(), parallax=0.6, parallax_error=0.05)
    star, planet = derive(far, 40.29109, 0.048, 2165e-6, np.random.default_rng(1))
    assert star.route == "mamajek" and "Mann" not in star.radius.source
    assert any(c.startswith("Mann relations not used: M_Ks") for c in star.caveats)
    assert any(c.startswith("absolute G") for c in star.caveats) and planet.a_au is not None


def test_a_parallax_near_zero_gives_finite_values_and_no_runtime_warning():
    faint = replace(gaia_row_6146676(), parallax=0.15, parallax_error=0.10)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        star, planet = derive(faint, 40.29109, 0.048, 2165e-6, np.random.default_rng(1))
    assert [w for w in caught if issubclass(w.category, RuntimeWarning)] == []
    assert star.route in ("mamajek", "none")
    values = [star.teff, star.radius, star.mass, star.density_gcc, star.distance_pc, star.alt_radius_e02,
              planet.rp_rearth, planet.a_au, planet.a_over_r, planet.teq_k, planet.insolation_earth,
              planet.transit_probability]
    for v in values:
        assert v is None or all(math.isfinite(x) for x in (v.median, v.minus, v.plus))
    assert any("percent of non-positive parallax draws" in c for c in star.caveats)
    assert any(c.startswith("parallax over error") for c in star.caveats)


def test_lookup_without_parallax_returns_an_unknown_star():
    star, planet = derive(replace(gaia_row_6146676(), parallax=None), 40.29, 0.048, 2165e-6, np.random.default_rng(1))
    assert star.route == "none" and star.radius is None and planet.rp_rearth is None
    assert star.caveats == ("no Gaia parallax or colour: the star cannot be characterised from photometry",)


def test_derive_without_a_gaia_row_returns_an_unknown_star():
    star, planet = derive(None, 40.29, 0.048, 2165e-6, np.random.default_rng(1))
    assert star.route == "none" and planet.in_habitable_zone is None


def test_hot_star_uses_the_fgk_route_without_mann():
    hot = replace(gaia_row_6146676(), bp_rp=0.82, parallax=10.0)
    star, _ = derive(hot, 10.0, 0.05, 1000e-6, np.random.default_rng(1))
    assert star.route == "mamajek" and star.teff.median > 5000


def test_lookup_runs_the_four_queries_and_derives(store):
    star, planet, ns, row = lookup(target_6146676(), 40.29109, 0.048, 2165e-6, fetch=fake_fetch(), store=store)
    assert star.route == "mann" and len(ns) == 2 and row.source_id == "2073896294004902272"
    assert store.counts()["queries"] == 4 and planet.a_au is not None


def test_data_files_ship_with_the_package():
    assert mamajek_table().shape[1] == 6
    data = importlib.resources.files("skysift.data")
    assert data.joinpath("mamajek_dwarf_table.txt").is_file()
    assert not data.joinpath("gaia_zpt").is_dir()     # LGPL: the zero point is imported, never vendored


# Gaia DR3 columns the star query may take from each table (Gaia DR3 data model, gaiadr3.gaia_source and
# gaiadr3.astrophysical_parameters); asking gaia_source for a FLAME column is an HTTP 400 from the archive
GAIA_SOURCE_COLUMNS = {"source_id", "ra", "dec", "parallax", "parallax_error", "phot_g_mean_mag", "bp_rp", "ruwe",
                       "nu_eff_used_in_astrometry", "pseudocolour", "ecl_lat", "astrometric_params_solved",
                       "teff_gspphot", "non_single_star", "ebpminrp_gspphot"}
ASTROPHYSICAL_COLUMNS = {"radius_flame", "mass_flame"}


def test_the_star_query_takes_each_column_from_its_dr3_table():
    select, rest = Q_SOURCE.split(" FROM ", 1)
    columns = [c.strip() for c in select.removeprefix("SELECT ").split(",")]
    by_table = {alias: {c.split(".", 1)[1] for c in columns if c.startswith(alias + ".")} for alias in ("g", "ap")}
    assert len(by_table["g"]) + len(by_table["ap"]) == len(columns)          # every column is qualified
    assert by_table["g"] <= GAIA_SOURCE_COLUMNS and by_table["ap"] == ASTROPHYSICAL_COLUMNS
    assert {"ruwe", "non_single_star", "ebpminrp_gspphot"} <= by_table["g"]      # spec 11.6: the star sentence
    assert rest.startswith("gaiadr3.gaia_source AS g LEFT OUTER JOIN gaiadr3.astrophysical_parameters AS ap "
                           "ON ap.source_id = g.source_id WHERE g.source_id = {id}")


def test_the_star_carries_ruwe_the_nss_flag_and_the_reddening():
    """Spec 11.6: RUWE, the Gaia non-single-star flag and GSP-Phot E(BP-RP) reach the star block (None where Gaia has
    no value)."""
    extra = GAIA_CSV.replace(b",radius_flame,mass_flame\n", b",radius_flame,mass_flame,non_single_star,"
                             b"ebpminrp_gspphot\n").replace(b",0.5394,0.5595\n", b",0.5394,0.5595,0,0.1203\n")
    row = query_gaia(target_6146676(), fetch=fake_fetch({**RESPONSES, "WHERE g.source_id": extra}), store=None)
    assert (row.ruwe, row.non_single_star, row.ebpminrp_gspphot) == (pytest.approx(0.9449), 0, pytest.approx(0.1203))
    star, _ = derive(row, 40.29109, 0.048, 2165e-6, np.random.default_rng(1))
    assert (star.ruwe, star.non_single_star, star.ebpminrp_gspphot) == (pytest.approx(0.9449), 0,
                                                                         pytest.approx(0.1203))
    plain = gaia_row_6146676()
    assert plain.non_single_star is None and plain.ebpminrp_gspphot is None
    assert derive(plain, 40.29109, 0.048, 2165e-6, np.random.default_rng(1))[0].ebpminrp_gspphot is None


# ---------------------------------------------------------------------------------------------------- Task 16 (spec 12.3)

EXT = {"ebv": 0.05, "ebv_p16": 0.03, "ebv_p84": 0.07, "source": "a test block", "map": "bayestar2019",
       "queried_utc": "2026-10-23T20:00:00+00:00", "distance_pc": 303.0}
COLUMN = {"status": "ok", "ebv": 0.10, "ebv_mean": 0.09, "ebv_std": 0.01, "stats_radius_arcmin": 5.0,
          "source": "IRSA DUST, Schlafly and Finkbeiner 2011", "queried_utc": "2026-10-23T20:00:00+00:00",
          "ra": 297.86871, "dec": 41.455567, "url": "https://irsa.ipac.caltech.edu/cgi-bin/DUST/nph-dust"}
# the live state (the controller's addenda): no 3D value, the IRSA full column stored as the upper bound
NOT_AVAILABLE = {"target": "kic-006146676", "status": "not available", "reason": "a test reason",
                 "queried_utc": "2026-10-23T20:00:00+00:00", "distance_pc": 303.0, "column": COLUMN}


def test_the_gaia_dr3_law_turns_e_b_v_into_e_bp_rp_a_g_and_a_ks():
    out = stars.gaia_extinction(0.05, 2.0971918)                 # KIC 6146676's BP-RP
    assert out["a0"] == pytest.approx(0.155) and out["colour_held"] is False
    assert out["bp_rp0"] == pytest.approx(2.0361684, abs=1e-6)
    assert out["ebprp"] == pytest.approx(0.0610234, abs=1e-6) and out["a_g"] == pytest.approx(0.1125808, abs=1e-6)
    assert out["a_ks"] == pytest.approx(0.018135, abs=1e-9)
    zero = stars.gaia_extinction(0.0, 2.0971918)
    assert (zero["ebprp"], zero["a_g"], zero["a_ks"], zero["bp_rp0"]) == (0.0, 0.0, 0.0, 2.0971918)
    held = stars.gaia_extinction(0.05, 2.7)                      # (BP-RP)_0 2.63 is above the fit's 2.5
    assert held["colour_held"] is True and held["ebprp"] == pytest.approx(0.0689518, abs=1e-6)
    hot = stars.gaia_extinction(0.30, 0.8)
    assert hot["ebprp"] == pytest.approx(0.4187245, abs=1e-6) and hot["a_g"] == pytest.approx(0.8434732, abs=1e-6)
    many = stars.gaia_extinction(np.array([0.0, 0.05]), 2.0971918)
    assert many["ebprp"] == pytest.approx([0.0, 0.0610234], abs=1e-6)


def test_a_ks_over_a0_is_ccm_at_the_2mass_ks_wavelength():
    x = 1 / 2.159                                                # um^-1; CCM 1989 infrared: a = 0.574 x^1.61
    assert stars.A_KS_OVER_A0 == round(0.574 * x ** 1.61 - 0.527 * x ** 1.61 / 3.1, 3) == 0.117


def test_derive_with_a_stored_extinction_dereddens_and_keeps_the_unreddened_values():
    gaia = gaia_row_6146676()
    plain, plain_planet = derive(gaia, 40.29109, 0.048, 2165e-6, np.random.default_rng(1))
    star, planet = derive(gaia, 40.29109, 0.048, 2165e-6, np.random.default_rng(1), extinction=EXT)
    assert star.unreddened["teff"] == plain.teff.as_dict() and star.unreddened["radius"] == plain.radius.as_dict()
    assert star.unreddened["rp_rearth"] == plain_planet.rp_rearth.as_dict() and star.unreddened["bp_rp0"] == (
        gaia.bp_rp)
    assert star.teff.median > plain.teff.median                  # a bluer intrinsic colour is a hotter star
    e = star.extinction
    assert e["ebv"] == 0.05 and e["ebprp"]["median"] == pytest.approx(0.061, abs=0.003)
    assert e["a_ks"]["median"] == pytest.approx(0.018, abs=0.002) and e["bp_rp0"] == pytest.approx(2.036, abs=0.002)
    assert e["law"] == stars.GAIA_LAW and e["r_v"] == 3.1 and e["state"] == "adopted"
    assert "Bayestar19" in star.radius.source and "A_Ks 0 to 0.07" not in star.radius.source
    assert "dereddened by the Bayestar19 E(BP-RP)" in star.reddening_note
    assert derive(gaia, 40.29109, 0.048, 2165e-6, np.random.default_rng(1), extinction=EXT) == (star, planet)
    assert plain.extinction is None and plain.unreddened is None
    manual = derive(gaia, 40.29109, 0.048, 2165e-6, np.random.default_rng(1),
                    extinction={**EXT, "status": "manual", "map": "by hand"})[0]
    assert manual.extinction["state"] == "manual" and manual.teff == star.teff


def test_without_a_3d_value_the_unreddened_star_is_adopted_and_the_full_column_bounds_it():
    """The controller's addendum 2: no 3D value, so the unreddened star stays adopted (the version 5 numbers); the
    IRSA full-column E(B-V) runs through the same law and draws to the upper end of the bracket."""
    gaia = gaia_row_6146676()
    plain, plain_planet = derive(gaia, 40.29109, 0.048, 2165e-6, np.random.default_rng(1))
    star, planet = derive(gaia, 40.29109, 0.048, 2165e-6, np.random.default_rng(1), extinction=NOT_AVAILABLE)
    assert (star.teff, star.radius, star.mass, planet) == (plain.teff, plain.radius, plain.mass, plain_planet)
    assert star.reddening_note == plain.reddening_note
    e = star.extinction
    assert e["state"] == "not available" and e["reason"] == "a test reason" and "ebprp" not in e
    col = e["column"]
    assert col["ebv"] == 0.10 and col["ebprp"]["median"] == pytest.approx(0.122, abs=0.003)
    # A_Ks 0.036 moves the Ks radius a little and the colour a lot
    assert col["teff"]["median"] > plain.teff.median and col["rp_rearth"]["median"] >= plain_planet.rp_rearth.median
    assert col["route"] == "mann" and set(col) >= {"radius", "mass", "insolation_earth", "a_g", "a_ks", "bp_rp0"}
    assert star.unreddened["radius"] == plain.radius.as_dict()
    unreachable = {**NOT_AVAILABLE, "column": {"status": "not available", "reason": "IRSA timed out",
                                               "queried_utc": "2026-10-23T20:00:00+00:00"}}
    bare = derive(gaia, 40.29109, 0.048, 2165e-6, np.random.default_rng(1), extinction=unreachable)[0]
    assert bare.teff == plain.teff and bare.extinction["column"] == unreachable["column"]
    none = derive(None, 40.29109, 0.048, 2165e-6, np.random.default_rng(1), extinction=NOT_AVAILABLE)[0]
    assert none.route == "none" and none.extinction is None and none.unreddened is None


def test_load_extinction_reads_validates_and_refuses(tmp_path):
    import json
    folder = tmp_path / "kic-006146676"
    folder.mkdir()
    assert stars.load_extinction("kic-006146676", tmp_path) is None
    for good in (EXT, {**EXT, "status": "manual"}, NOT_AVAILABLE, {**NOT_AVAILABLE, "column": {
            "status": "not available", "reason": "IRSA timed out", "queried_utc": "2026-10-23T20:00:00+00:00"}}):
        (folder / "extinction.json").write_text(json.dumps(good), encoding="utf-8")
        assert stars.load_extinction("kic-006146676", tmp_path) == good
    for bad, words in (({**EXT, "ebv": None}, "ebv is not a finite number"),
                       ({**EXT, "ebv_p16": 0.06}, "not 0 <= ebv_p16 <= ebv <= ebv_p84"),
                       ({**EXT, "source": " "}, "source is empty"),
                       ({k: v for k, v in EXT.items() if k != "map"}, "map is missing"),
                       ({**EXT, "status": "guessed"}, "status 'guessed' is not one of"),
                       ({**NOT_AVAILABLE, "reason": ""}, "reason is empty"),
                       ({**NOT_AVAILABLE, "column": {**COLUMN, "ebv": -0.1}}, "column ebv is not a finite number"),
                       ([EXT], "not a JSON object")):
        (folder / "extinction.json").write_text(json.dumps(bad), encoding="utf-8")
        with pytest.raises(stars.ExtinctionError, match=words):
            stars.load_extinction("kic-006146676", tmp_path)
    (folder / "extinction.json").write_text("{", encoding="utf-8")
    with pytest.raises(stars.ExtinctionError, match="does not read as JSON"):
        stars.load_extinction("kic-006146676", tmp_path)


def test_the_galactic_latitude_and_the_height_clause():
    """Task 16 fix round 1, ruling (c): z = d sin|b| from the Gaia distance, its clause by the bands of the round 3
    final review (M1)."""
    assert stars.galactic_latitude(297.86871, 41.455567) == pytest.approx(7.4198, abs=1e-3)   # astropy: 7.4198
    assert stars.galactic_latitude(266.40499, -28.93617) == pytest.approx(0.0, abs=0.01)     # Sgr A* region
    gaia = gaia_row_6146676()
    near = derive(gaia, 40.29109, 0.048, 2165e-6, np.random.default_rng(1), extinction=NOT_AVAILABLE)[0].extinction
    assert 35 <= near["height_pc"] <= 42 and near["b_deg"] == pytest.approx(7.42, abs=0.01)
    assert near["height_clause"] == (f"the star sits about {near['height_pc']} pc above the plane, inside the dust "
                                     "layer, so its true reddening lies well inside the bracket")
    far_row = replace(gaia, parallax=0.5)                         # about 1,900 pc: z about 250 pc
    far = derive(far_row, 40.29109, 0.048, 2165e-6, np.random.default_rng(1), extinction=NOT_AVAILABLE)[0].extinction
    assert far["height_pc"] > stars.HEIGHT_BANDS_PC[1] == 200
    assert far["height_clause"] == (f"the star sits about {far['height_pc']} pc above the plane, above most of the "
                                    "dust, so the full-column value is probably close to its true reddening")


def test_the_height_clause_comes_in_three_bands():
    """The round 3 final review, M1: below 100 pc inside the dust layer, 100 to 200 pc within its upper part, above
    200 pc above most of it. KIC 6699887 at 149 pc takes the middle band, which one threshold at 150 pc put inside."""
    inside, upper, above = (stars.HEIGHT_INSIDE.format(z="{z}", side="above"),
                            stars.HEIGHT_UPPER.format(z="{z}", side="above"),
                            stars.HEIGHT_ABOVE.format(z="{z}", side="above"))
    assert upper.endswith("within the upper dust layer, so its true reddening may lie anywhere in the bracket")
    assert above.endswith("above most of the dust, so the full-column value is probably close to its true reddening")
    kic_6699887 = stars.height_fields(294.14563, 42.187986, 840.6)
    assert kic_6699887["height_pc"] == 149 and kic_6699887["height_clause"] == upper.format(z=149)
    sin_b = math.sin(math.radians(stars.galactic_latitude(297.86871, 41.455567)))
    for z, words in ((99, inside), (100, upper), (200, upper), (201, above)):
        got = stars.height_fields(297.86871, 41.455567, z / sin_b)
        assert got["height_pc"] == z and got["height_clause"] == words.format(z=z), z


def test_the_adopted_3d_value_settles_the_height_clause_and_carries_the_mann_range_flag():
    """Task 17 (spec 12.5): with a 3D value adopted the height clause stays, as it describes the geometry, and ends
    with ``HEIGHT_SETTLED``; the adopted star's ``outside_mann_range`` is recorded in the block, as the column end's
    is, so the texts can print the "indicative" clause when the dereddened star leaves the relations' range."""
    gaia = gaia_row_6146676()
    star = derive(gaia, 40.29109, 0.048, 2165e-6, np.random.default_rng(1), extinction={**EXT, "column": COLUMN})[0]
    e = star.extinction
    assert stars.HEIGHT_SETTLED == "the 3D value settles it"
    assert e["height_clause"] == (f"the star sits about {e['height_pc']} pc above the plane, inside the dust layer, "
                                  "so its true reddening lies well inside the bracket; the 3D value settles it")
    assert e["outside_mann_range"] is False and star.route == "mann"
    assert e["outside_mann_range"] == stars.outside_mann_range(star)
    assert stars.MANN_TEFF_RANGE == (2700.0, 4100.0)
    manual = derive(gaia, 40.29109, 0.048, 2165e-6, np.random.default_rng(1),
                    extinction={**EXT, "status": "manual", "map": "by hand"})[0].extinction
    assert manual["height_clause"].endswith("; the 3D value settles it")
    bare = derive(gaia, 40.29109, 0.048, 2165e-6, np.random.default_rng(1), extinction=NOT_AVAILABLE)[0].extinction
    assert not bare["height_clause"].endswith(stars.HEIGHT_SETTLED) and "outside_mann_range" not in bare


def _with_column(ebv: float, std: float) -> dict:
    return {**NOT_AVAILABLE, "column": {**COLUMN, "ebv": ebv, "ebv_std": std}}


def test_the_unreddened_end_is_e_b_v_zero_with_no_ks_extinction():
    """The round 3 final review, C1(ii): the unreddened star draws A_Ks = 0 exactly, not 0 to 0.07, and its sources
    say so; a full column of E(B-V) 0 then gives the unreddened star itself, the two ends on one footing."""
    gaia = gaia_row_6146676()
    star, planet = derive(gaia, 40.29109, 0.048, 2165e-6, np.random.default_rng(1))
    assert "A_Ks 0 (unreddened (E(B-V) = 0))" in star.radius.source and "0 to 0.07" not in star.radius.source
    assert star.teff.source.endswith(", unreddened (E(B-V) = 0)") and "0.1" not in star.reddening_note
    assert star.reddening_note.startswith("Teff and spectral type assume no reddening (E(B-V) = 0) at the observed")
    # M_Ks = Ks - DM with no extinction: 13.030 - 5 log10(303 pc / 10 pc)
    assert star.abs_ks == pytest.approx(13.030 - (5 * math.log10(1000 / (3.268646 + 0.0317)) - 5), abs=1e-3)
    zero = derive(gaia, 40.29109, 0.048, 2165e-6, np.random.default_rng(1), extinction=_with_column(0.0, 0.0))[0]
    col = zero.extinction["column"]

    def nums(v) -> tuple:
        v = v if isinstance(v, dict) else v.as_dict()
        return v["median"], v["minus"], v["plus"]
    for key, adopted in (("radius", star.radius), ("teff", star.teff), ("mass", star.mass),
                         ("rp_rearth", planet.rp_rearth), ("insolation_earth", planet.insolation_earth)):
        assert nums(col[key]) == nums(adopted), key


def test_the_column_end_keeps_the_adopted_route_and_flags_the_mann_range():
    """The round 3 final review, C1(i): KIC 6146676 at the full column E(B-V) of 0.674 dereddens to 4,657 K, above
    the Mann range, but the upper end stays on the Mann relations with Ks dereddened by A_Ks (about 0.54 Rsun, not
    the colour table's 0.72), flagged ``outside_mann_range``; at E(B-V) 0.10 it stays inside the range."""
    gaia = gaia_row_6146676()
    star, planet = derive(gaia, 40.29109, 0.0455, 2165e-6, np.random.default_rng(1),
                          extinction=_with_column(0.6741, 0.0344))
    col = star.extinction["column"]
    assert col["route"] == "mann" and col["teff"]["median"] > stars.MANN_TEFF_RANGE[1] == 4100
    assert col["outside_mann_range"] is True and "A_Ks 0.244 from the full column" in col["radius"]["source"]
    assert col["radius"]["median"] == pytest.approx(0.54, abs=0.01) and col["rp_rearth"]["median"] == pytest.approx(
        2.7, abs=0.06)
    assert col["radius"]["median"] > star.radius.median and col["rp_rearth"]["median"] > planet.rp_rearth.median
    mild = derive(gaia, 40.29109, 0.0455, 2165e-6, np.random.default_rng(1), extinction=NOT_AVAILABLE)[0]
    assert mild.extinction["column"]["route"] == "mann" and mild.extinction["column"]["outside_mann_range"] is False
    hot = replace(gaia, bp_rp=0.82, parallax=10.0)                # an FGK star: both ends on the colour table
    warm = derive(hot, 10.0, 0.05, 1000e-6, np.random.default_rng(1), extinction=_with_column(0.3, 0.01))[0]
    assert warm.route == warm.extinction["column"]["route"] == "mamajek"
    assert warm.extinction["column"]["outside_mann_range"] is False


# the five stored records' stars (docs/candidates/<target>/extinction.json), their Gaia rows rebuilt from the live
# version 5 dossiers: ra, dec, G, BP-RP, M_Ks, distance (pc), zero point (mas), parallax error with the systematic
# (mas), period (d), radius ratio
STORED_STARS = {
    "kic-005689121": (285.90784, 40.941985, 16.282413, 2.194, 5.651, 290.7, -0.033, 0.0425, 13.769509, 0.0451),
    "kic-006146676": (297.86871, 41.455567, 16.236654, 2.097, 5.648, 303.0, -0.0317, 0.0388, 40.291087, 0.0455),
    "kic-006699887": (294.14563, 42.187986, 16.219015, 1.35, 4.249, 840.6, -0.0256, 0.0357, 15.113832, 0.0358),
    "kic-009243078": (299.92285, 45.69381, 16.080002, 1.223, 2.614, 1860.1, -0.0247, 0.035, 6.1132021, 0.4345),
    "kic-010352243": (296.47484, 47.478915, 16.702513, 1.224, 4.318, 1149.7, -0.0261, 0.0458, 0.92826648, 0.0194)}


def _stored_row(ra, dec, g, bp_rp, abs_ks, d, zp, eplx) -> stars.GaiaRow:
    return replace(gaia_row_6146676(), ra=ra, dec=dec, gmag=g, bp_rp=bp_rp, ks=abs_ks + 5 * math.log10(d) - 5,
                   ks_err=0.03, parallax=1000 / d + zp, parallax_error=math.sqrt(eplx ** 2 - 0.015 ** 2),
                   zero_point=zp, zero_point_source=stars.ZP_PACKAGE)


@pytest.mark.parametrize("target", sorted(STORED_STARS))
def test_the_upper_end_is_at_or_above_the_adopted_end_for_every_stored_record(target):
    """The round 3 final review, C1(iii): on one footing the full-column end never falls below the adopted one."""
    from pathlib import Path
    *row, period, k = STORED_STARS[target]
    block = stars.load_extinction(target, Path(__file__).resolve().parents[2] / "docs" / "candidates")
    assert stars.column_ebv(block) is not None
    star, planet = derive(_stored_row(*row), period, k, 0.002, np.random.default_rng(20261021), block)
    col = star.extinction["column"]
    assert col["route"] == star.route
    for key, adopted in (("radius", star.radius), ("teff", star.teff), ("rp_rearth", planet.rp_rearth),
                         ("insolation_earth", planet.insolation_earth)):
        assert col[key]["median"] >= adopted.median, key


def test_the_column_end_carries_the_spread_of_its_e_b_v():
    """Minor 8: the column E(B-V) is drawn from a normal with the stored std, held at 0 and above, so the upper end's
    extinctions carry an error; without a std it is the single value."""
    gaia = gaia_row_6146676()
    col = derive(gaia, 40.29109, 0.048, 2165e-6, np.random.default_rng(1), extinction=NOT_AVAILABLE)[0]
    e = col.extinction["column"]
    assert e["ebprp"]["minus"] > 0 and e["ebprp"]["plus"] > 0 and e["ebprp"]["median"] == pytest.approx(0.122, abs=0.003)
    bare = {**NOT_AVAILABLE, "column": {k: v for k, v in COLUMN.items() if k != "ebv_std"}}
    flat = derive(gaia, 40.29109, 0.048, 2165e-6, np.random.default_rng(1), extinction=bare)[0].extinction["column"]
    assert flat["ebprp"]["minus"] == flat["ebprp"]["plus"] == 0.0
