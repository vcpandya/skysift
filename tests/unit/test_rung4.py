import numpy as np
import pytest

from skysift.config import Thresholds
from skysift.models import LightCurve, Signal, Target, TestStatus
from skysift.net import ServiceUnavailable
from skysift.vetting.base import DataBundle
from skysift.vetting.rung4_novelty import Rung4Novelty, _csv_rows, _to_float, _tsv_rows

SIG = Signal(target_id="kic-005689121", detector="box_dip", detector_version="1", period=13.7695, t0=136.3, duration=0.09, depth_ppm=1883, snr=18, sde=14, n_events=69)
TGT = Target(source="kbonus_bkg", target_id="kic-005689121", ra=285.907827, dec=40.941988, gaia_id="2103792461826460288", kic=5689121)
# VizieR asu-tsv layout: comment lines, header, units, dashes, then rows
EB_EMPTY = b"#RESOURCE=yCat_J/AJ/151/68\n#Name: J/AJ/151/68/kepeb\nKIC\tPer\n\td\n---\t---\n"
EB_TSV = EB_EMPTY + b"5689121\t13.77\n"
EMPTY = {"cumulative": b"kepid,kepoi_name,koi_period\n", "q1_q17_dr25_tce": b"kepid,tce_plnt_num,tce_period\n", "toi": b"tid,toi,pl_orbper\n",
         "J/AJ/151/68": EB_EMPTY, "vari_eclipsing_binary": b"source_id,frequency\n", "nss_two_body_orbit": b"source_id,period\n"}


def bundle(responses, raise_for=None, calls=None, target=TGT):
    def fetch(url, *, params=None, service, **kw):
        if calls is not None:
            calls.append((service, params))
        if raise_for and raise_for in (service, (params or {}).get("query", ""), url):
            raise ServiceUnavailable(service)
        key = next(k for k in responses if k in (params or {}).get("query", "") or k in (params or {}).get("-source", "") or k in url)
        return responses[key]
    lc = LightCurve(np.arange(10.0), np.ones(10), np.ones(10), np.zeros(10, int), np.ones(10, int))
    return DataBundle(lc=lc, fnorm=np.ones(10), target=target, thresholds=Thresholds(), fetch=fetch)


def test_novel_when_all_empty():
    assert Rung4Novelty().run(SIG, bundle(EMPTY)).status == TestStatus.PASS


def test_known_koi_at_same_period_fails():
    resp = dict(EMPTY); resp["cumulative"] = b"kepid,kepoi_name,koi_period\n5689121,K99999.01,13.77\n"
    r = Rung4Novelty().run(SIG, bundle(resp))
    assert r.status == TestStatus.FAIL and r.reason.startswith("known:cumulative")


def test_outage_is_undetermined():
    r = Rung4Novelty().run(SIG, bundle(EMPTY, raise_for="vizier"))
    assert r.status == TestStatus.UNDETERMINED and r.reason.startswith("outage")


def test_error_document_is_undetermined():
    resp = dict(EMPTY); resp["toi"] = b'<VOTABLE><INFO name="QUERY_STATUS" value="ERROR">UsageFault: Internal error</INFO></VOTABLE>'
    r = Rung4Novelty().run(SIG, bundle(resp))
    assert r.status == TestStatus.UNDETERMINED and "UsageFault" in r.reason


def test_known_reason_names_table_and_identifier():
    resp = dict(EMPTY); resp["cumulative"] = b"kepid,kepoi_name,koi_period\n5689121,K99999.01,13.77\n"
    assert Rung4Novelty().run(SIG, bundle(resp)).reason == "known:cumulative:K99999.01"


def test_known_tce_reason_is_kepid_dot_planet_number():
    resp = dict(EMPTY); resp["q1_q17_dr25_tce"] = b"kepid,tce_plnt_num,tce_period\n5689121,1,13.77\n"
    assert Rung4Novelty().run(SIG, bundle(resp)).reason == "known:q1_q17_dr25_tce:5689121.1"


@pytest.mark.parametrize("cat_period", [27.54, 6.885])  # 2x and 0.5x aliases of the signal period
def test_alias_period_fails(cat_period):
    resp = dict(EMPTY); resp["toi"] = f"tid,toi,pl_orbper\n5689121,1234.01,{cat_period}\n".encode()
    r = Rung4Novelty().run(SIG, bundle(resp))
    assert r.status == TestStatus.FAIL and r.reason == "known:toi:1234.01"


def test_other_period_passes_and_is_listed():
    resp = dict(EMPTY); resp["cumulative"] = b"kepid,kepoi_name,koi_period\n5689121,K99999.01,3.1\n"
    r = Rung4Novelty().run(SIG, bundle(resp))
    assert r.status == TestStatus.PASS
    assert r.metrics["cumulative"] == 1
    assert r.metrics["other_period_objects"] == [("cumulative", "K99999.01", 3.1)]


def test_other_period_lists_eb_and_gaia_rows_too():
    resp = dict(EMPTY)
    resp["J/AJ/151/68"] = EB_EMPTY + b"5689121\t5.0\n"
    resp["nss_two_body_orbit"] = b"source_id,period\n2103792461826460288,7.0\n"
    r = Rung4Novelty().run(SIG, bundle(resp))
    assert r.status == TestStatus.PASS
    assert ("kepler_eb", "5689121", 5.0) in r.metrics["other_period_objects"]
    assert ("gaia_nss_two_body_orbit", "2103792461826460288", 7.0) in r.metrics["other_period_objects"]


def test_unparseable_periods_are_not_listed():
    resp = dict(EMPTY); resp["cumulative"] = b"kepid,kepoi_name,koi_period\n1,A,nan\n1,B,0\n1,C,\n1,D,abc\n"
    r = Rung4Novelty().run(SIG, bundle(resp))
    assert r.status == TestStatus.PASS and r.metrics["cumulative"] == 4 and r.metrics["other_period_objects"] == []


def test_period_just_outside_tolerance_is_not_a_match():
    resp = dict(EMPTY); resp["cumulative"] = b"kepid,kepoi_name,koi_period\n5689121,K99999.01,13.95\n"  # 1.3 percent off
    assert Rung4Novelty().run(SIG, bundle(resp)).status == TestStatus.PASS


def test_empty_catalogues_report_counts():
    r = Rung4Novelty().run(SIG, bundle(EMPTY))
    assert r.metrics["cumulative"] == r.metrics["q1_q17_dr25_tce"] == r.metrics["toi"] == 0
    assert r.metrics["kepler_eb"] == 0 and r.metrics["gaia_vari_eclipsing_binary"] == 0 and r.metrics["gaia_nss_two_body_orbit"] == 0


def test_vizier_header_without_rows_is_a_legitimate_zero():
    r = Rung4Novelty().run(SIG, bundle(EMPTY))
    assert r.status == TestStatus.PASS and r.metrics["kepler_eb"] == 0


EB_METADATA_ONLY = (b"#\n#   VizieR Astronomical Server vizier.cds.unistra.fr\n#RESOURCE=yCat_51510068\n#Name: J/AJ/151/68\n"
                    b"#Table\tJ_AJ_151_68_catalog:\n#Name: J/AJ/151/68/catalog\n"
                    b"#Column\tKIC\t(A8)\tKepler Input Catalog number\t[ucd=meta.id;meta.main]\n"
                    b"#Column\tPer\t(F12.7)\tOrbital period\t[ucd=time.period]\n")


def test_vizier_metadata_only_response_is_a_legitimate_zero():
    """A VizieR cone search with no match sends only the table metadata (no header, units or separator line);
    that is zero known binaries, not an outage, and the column names come from the #Column lines."""
    assert _tsv_rows(EB_METADATA_ONLY) == (["KIC", "Per"], [])
    resp = dict(EMPTY); resp["J/AJ/151/68"] = EB_METADATA_ONLY
    r = Rung4Novelty().run(SIG, bundle(resp))
    assert r.status == TestStatus.PASS and r.metrics["kepler_eb"] == 0


def test_vizier_source_is_the_catalog_table():
    calls = []
    Rung4Novelty().run(SIG, bundle(EMPTY, calls=calls))
    assert [p["-source"] for s, p in calls if s == "vizier"] == ["J/AJ/151/68/catalog"]


def test_kepler_eb_match_fails():
    resp = dict(EMPTY); resp["J/AJ/151/68"] = EB_TSV
    r = Rung4Novelty().run(SIG, bundle(resp))
    assert r.status == TestStatus.FAIL and r.reason == "known:kepler_eb:5689121"


def test_gaia_nss_orbit_match_fails():
    resp = dict(EMPTY); resp["nss_two_body_orbit"] = b"source_id,period\n2103792461826460288,13.77\n"
    r = Rung4Novelty().run(SIG, bundle(resp))
    assert r.status == TestStatus.FAIL and r.reason == "known:gaia_nss_two_body_orbit:2103792461826460288"


def test_gaia_eclipsing_binary_frequency_match_fails():
    resp = dict(EMPTY); resp["vari_eclipsing_binary"] = b"source_id,frequency\n2103792461826460288,0.0726\n"  # 1/0.0726 = 13.77 d
    r = Rung4Novelty().run(SIG, bundle(resp))
    assert r.status == TestStatus.FAIL and r.reason == "known:gaia_vari_eclipsing_binary:2103792461826460288"


def test_gaia_eclipsing_binary_other_frequency_is_listed_as_period():
    resp = dict(EMPTY); resp["vari_eclipsing_binary"] = b"source_id,frequency\n2103792461826460288,0.2\n"
    r = Rung4Novelty().run(SIG, bundle(resp))
    assert r.status == TestStatus.PASS
    assert r.metrics["other_period_objects"] == [("gaia_vari_eclipsing_binary", "2103792461826460288", pytest.approx(5.0))]


@pytest.mark.parametrize("key", ["cumulative", "q1_q17_dr25_tce", "J/AJ/151/68", "vari_eclipsing_binary"])
def test_error_document_never_passes_whichever_service_returns_it(key):
    resp = dict(EMPTY); resp[key] = b'<VOTABLE><INFO name="QUERY_STATUS" value="ERROR">Boom: bad</INFO></VOTABLE>'
    r = Rung4Novelty().run(SIG, bundle(resp))
    assert r.status == TestStatus.UNDETERMINED and r.reason.startswith("outage") and "Boom" in r.reason


def test_html_error_page_is_undetermined():
    resp = dict(EMPTY); resp["nss_two_body_orbit"] = b"<!DOCTYPE html><html><head><title>503 Service Unavailable</title></head></html>"
    r = Rung4Novelty().run(SIG, bundle(resp))
    assert r.status == TestStatus.UNDETERMINED and "503" in r.reason


def test_outage_names_the_service():
    r = Rung4Novelty().run(SIG, bundle(EMPTY, raise_for="gaia"))
    assert r.status == TestStatus.UNDETERMINED and r.reason.startswith("outage:gaia")


def test_empty_response_is_undetermined_not_pass():
    resp = dict(EMPTY); resp["cumulative"] = b""
    r = Rung4Novelty().run(SIG, bundle(resp))
    assert r.status == TestStatus.UNDETERMINED and r.reason.startswith("schema:cumulative")


@pytest.mark.parametrize("key", ["q1_q17_dr25_tce", "toi", "J/AJ/151/68", "vari_eclipsing_binary", "nss_two_body_orbit"])
def test_empty_body_from_any_table_is_undetermined(key):
    resp = dict(EMPTY); resp[key] = b""
    r = Rung4Novelty().run(SIG, bundle(resp))
    assert r.status == TestStatus.UNDETERMINED and r.reason.startswith("schema:") and "empty response" in r.reason


def test_missing_period_column_is_undetermined_not_pass():
    resp = dict(EMPTY); resp["cumulative"] = b"kepid,kepoi_name\n5689121,K1.01\n"
    r = Rung4Novelty().run(SIG, bundle(resp))
    assert r.status == TestStatus.UNDETERMINED and r.reason == "schema:cumulative:missing koi_period"


def test_missing_frequency_column_is_undetermined():
    resp = dict(EMPTY); resp["vari_eclipsing_binary"] = b"source_id\n2103792461826460288\n"
    r = Rung4Novelty().run(SIG, bundle(resp))
    assert r.status == TestStatus.UNDETERMINED and r.reason == "schema:gaia_vari_eclipsing_binary:missing frequency"


def test_results_are_cached_across_signals_of_one_target():
    calls = []
    b = bundle(EMPTY, calls=calls)
    Rung4Novelty().run(SIG, b)
    n = len(calls)
    assert n == 6
    Rung4Novelty().run(SIG.model_copy(update={"period": 5.0}), b)
    assert len(calls) == n


def test_error_document_is_not_cached():
    calls = []
    resp = dict(EMPTY); resp["toi"] = b'<VOTABLE><INFO name="QUERY_STATUS" value="ERROR">Boom</INFO></VOTABLE>'
    b = bundle(resp, calls=calls)
    Rung4Novelty().run(SIG, b)
    assert not any(k[1] == "toi" for k in b.cache)


def test_target_without_kic_or_gaia_queries_by_cone():
    calls = []
    tgt = TGT.model_copy(update={"kic": None, "gaia_id": None, "target_id": "tic-1"})
    r = Rung4Novelty().run(SIG, bundle(EMPTY, calls=calls, target=tgt))
    assert r.status == TestStatus.PASS
    queries = [p["query"] for s, p in calls if s == "exoarchive"]
    assert len(queries) == 3 and all("ra between" in q and "dec between" in q and "CONTAINS" not in q for q in queries)
    assert not any(s == "gaia" for s, _ in calls)


def test_box_query_rows_outside_the_circle_are_dropped():
    """The archive's Cloudflare filter rejects ADQL cone searches, so the cone is a box plus an exact circle on the
    rows: a KOI at the signal's period 25 arcsec away (inside the 20 arcsec box corner, outside the circle) must not
    count as known, while one 10 arcsec away must."""
    NL = "\n"
    far = "kepid,kepoi_name,koi_period,ra,dec" + NL + f"1,K1.01,13.7695,{TGT.ra + 25 / 3600 / 0.7555},{TGT.dec}" + NL   # cos(40.94 deg)
    near = "kepid,kepoi_name,koi_period,ra,dec" + NL + f"1,K1.01,13.7695,{TGT.ra},{TGT.dec + 10 / 3600}" + NL
    tgt = TGT.model_copy(update={"kic": None, "gaia_id": None, "target_id": "tic-1"})
    r = Rung4Novelty().run(SIG, bundle({**EMPTY, "cumulative": far.encode()}, target=tgt))
    assert r.status == TestStatus.PASS and r.metrics["cumulative"] == 0
    r = Rung4Novelty().run(SIG, bundle({**EMPTY, "cumulative": near.encode()}, target=tgt))
    assert r.status == TestStatus.FAIL and r.reason == "known:cumulative:K1.01"


def test_box_handles_ra_wrap_and_poles():
    from skysift.vetting.rung4_novelty import _box, _separation_deg
    assert "or ra between" in _box(0.001, 10.0, 0.01) and "or ra between" in _box(359.999, 10.0, 0.01)
    assert _box(10.0, 89.999, 0.01).startswith("dec between")
    assert abs(_separation_deg(10.0, 20.0, 10.0, 20.0 + 1 / 3600) * 3600 - 1.0) < 1e-6


def test_kic_target_queries_archive_by_kepid():
    calls = []
    Rung4Novelty().run(SIG, bundle(EMPTY, calls=calls))
    queries = [p["query"] for s, p in calls if s == "exoarchive"]
    assert "kepid=5689121" in queries[0] and "kepid=5689121" in queries[1] and "ra between" in queries[2]


def test_tsv_parser_anchors_on_separator_line():
    assert _tsv_rows(b"") == (None, [])
    assert _tsv_rows(b"#c\nKIC\tPer\n") == (None, [])                        # no separator line
    assert _tsv_rows(b"KIC\tPer\n---\t---\n1\t2\n") == (None, [])             # separator without units line
    assert _tsv_rows(EB_EMPTY) == (["KIC", "Per"], [])
    short = b"KIC\tPer\n\td\n---\t---\n1\t2\n3\n4\t5\n"                       # one malformed row
    assert _tsv_rows(short) == (["KIC", "Per"], [{"KIC": "1", "Per": "2"}, {"KIC": "4", "Per": "5"}])
    assert _tsv_rows(EB_TSV) == (["KIC", "Per"], [{"KIC": "5689121", "Per": "13.77"}])


def test_csv_parser_handles_trailing_blank_line_and_comments():
    assert _csv_rows(b"a,b\n1,2\n\n") == (["a", "b"], [{"a": "1", "b": "2"}])
    assert _csv_rows(b"# comment\na,b\n1,2\n") == (["a", "b"], [{"a": "1", "b": "2"}])
    assert _csv_rows(b"a,b\n") == (["a", "b"], [])
    assert _csv_rows(b"") == (None, [])


@pytest.mark.parametrize("value,expected", [("13.77", 13.77), (13.77, 13.77), ("nan", 0.0), ("inf", 0.0), ("-inf", 0.0), ("-3", 0.0),
                                            (0, 0.0), ("0", 0.0), ("", 0.0), ("abc", 0.0), (None, 0.0), (float("nan"), 0.0)])
def test_to_float(value, expected):
    assert _to_float(value) == expected


def test_gaia_checks_run_only_for_a_numeric_source_id():
    calls = []
    r = Rung4Novelty().run(SIG, bundle(EMPTY, calls=calls))
    assert r.status == TestStatus.PASS and "gaia_skipped" not in r.metrics
    gaia = [p["query"] for s, p in calls if s == "gaia"]
    assert len(gaia) == 2 and all(q.endswith("source_id=2103792461826460288") for q in gaia)
    calls.clear()
    tgt = TGT.model_copy(update={"gaia_id": "1 or 1=1"})
    r = Rung4Novelty().run(SIG, bundle(EMPTY, calls=calls, target=tgt))
    assert r.status == TestStatus.PASS and r.metrics["gaia_skipped"] == "non-numeric source_id"
    assert not any(s == "gaia" for s, _ in calls)
