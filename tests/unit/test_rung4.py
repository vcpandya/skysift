import numpy as np
import pytest

from skysift.config import Thresholds
from skysift.models import LightCurve, Signal, Target, TestStatus
from skysift.net import ServiceUnavailable
from skysift.vetting.base import DataBundle
from skysift.vetting.rung4_novelty import Rung4Novelty, _csv_rows, _tsv_rows

SIG = Signal(target_id="kic-005689121", detector="box_dip", detector_version="1", period=13.7695, t0=136.3, duration=0.09, depth_ppm=1883, snr=18, sde=14, n_events=69)
TGT = Target(source="kbonus_bkg", target_id="kic-005689121", ra=285.907827, dec=40.941988, gaia_id="2103792461826460288", kic=5689121)
EMPTY = {"cumulative": b"kepid,kepoi_name,koi_period\n", "q1_q17_dr25_tce": b"kepid,tce_plnt_num,tce_period\n", "toi": b"tid,toi,pl_orbper\n",
         "J/AJ/151/68": b"", "vari_summary": b"source_id\n", "nss_two_body_orbit": b"source_id,period\n"}
EB_TSV = (b"#RESOURCE=yCat_J/AJ/151/68\n#Name: J/AJ/151/68/kepeb\n"
          b"KIC\tPer\n\t\nd\td\n---\t---\n"
          b"5689121\t13.77\n")


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


def test_period_just_outside_tolerance_is_not_a_match():
    resp = dict(EMPTY); resp["cumulative"] = b"kepid,kepoi_name,koi_period\n5689121,K99999.01,13.95\n"  # 1.3 percent off
    assert Rung4Novelty().run(SIG, bundle(resp)).status == TestStatus.PASS


def test_empty_catalogues_report_counts():
    r = Rung4Novelty().run(SIG, bundle(EMPTY))
    assert r.metrics["cumulative"] == r.metrics["q1_q17_dr25_tce"] == r.metrics["toi"] == 0
    assert r.metrics["kepler_eb"] == 0 and r.metrics["gaia_vari_summary"] == 0 and r.metrics["gaia_nss_two_body_orbit"] == 0


def test_kepler_eb_match_fails():
    resp = dict(EMPTY); resp["J/AJ/151/68"] = EB_TSV
    r = Rung4Novelty().run(SIG, bundle(resp))
    assert r.status == TestStatus.FAIL and r.reason == "known:kepler_eb:5689121"


def test_gaia_nss_orbit_match_fails():
    resp = dict(EMPTY); resp["nss_two_body_orbit"] = b"source_id,period\n2103792461826460288,13.77\n"
    r = Rung4Novelty().run(SIG, bundle(resp))
    assert r.status == TestStatus.FAIL and r.reason == "known:gaia_nss_two_body_orbit:2103792461826460288"


@pytest.mark.parametrize("key", ["cumulative", "q1_q17_dr25_tce", "J/AJ/151/68", "vari_summary"])
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
    assert len(queries) == 3 and all("CIRCLE" in q for q in queries)
    assert not any(s == "gaia" for s, _ in calls)


def test_kic_target_queries_archive_by_kepid():
    calls = []
    Rung4Novelty().run(SIG, bundle(EMPTY, calls=calls))
    queries = [p["query"] for s, p in calls if s == "exoarchive"]
    assert "kepid=5689121" in queries[0] and "kepid=5689121" in queries[1] and "CIRCLE" in queries[2]


def test_tsv_parser_is_tolerant():
    assert _tsv_rows(b"") == []
    assert _tsv_rows(b"#c\nKIC\tPer\n---\t---\n") == []          # fewer than 3 non-comment lines
    short = b"KIC\tPer\n\t\nd\td\n---\t---\n1\t2\n3\n4\t5\n"      # one malformed row
    assert _tsv_rows(short) == [{"KIC": "1", "Per": "2"}, {"KIC": "4", "Per": "5"}]
    assert _tsv_rows(EB_TSV) == [{"KIC": "5689121", "Per": "13.77"}]


def test_csv_parser_handles_trailing_blank_line_and_comments():
    assert _csv_rows(b"a,b\n1,2\n\n") == [{"a": "1", "b": "2"}]
    assert _csv_rows(b"# comment\na,b\n1,2\n") == [{"a": "1", "b": "2"}]
    assert _csv_rows(b"") == []
