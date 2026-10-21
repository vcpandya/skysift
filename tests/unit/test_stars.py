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
from skysift.stars import derive, lookup, mamajek_table, query_gaia, query_neighbours, zero_point
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
    assert star.radius.median == pytest.approx(0.509, abs=0.002) and star.mass.median == pytest.approx(0.507, abs=0.002)
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
    assert star.caveats == ("no Gaia parallax or colour: the star cannot be characterized from photometry",)


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
