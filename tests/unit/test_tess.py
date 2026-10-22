"""The TESS non-detection statement (referee pack spec 10, plan Task 8): the pure helpers of ``skysift.tess`` first,
then the statement block, then ``tools/tess_inputs.py`` with ``net.fetch`` replaced by the canned MAST answers under
``tests/fixtures/tess`` (KIC 6146676, trimmed from the answers of 22 October 2026)."""
import itertools
import json
import math
from pathlib import Path

import numpy as np
import pytest

from skysift import tess

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "tess"


def _numeric_fraction(dx: float, dy: float, sigma: float = 0.75, n: int = 400) -> float:
    """The Gaussian's flux over the five aperture pixels by a midpoint sum, independent of ``math.erf``."""
    total = 0.0
    for px, py in tess.APERTURE_PIXELS:
        xs = px - 0.5 + (np.arange(n) + 0.5) / n
        ys = py - 0.5 + (np.arange(n) + 0.5) / n
        gx = np.exp(-0.5 * ((xs - dx) / sigma) ** 2)
        gy = np.exp(-0.5 * ((ys - dy) / sigma) ** 2)
        total += gx.sum() * gy.sum() / n ** 2
    return total / (2 * math.pi * sigma ** 2)


# ---------------------------------------------------------------------------------------------------- the table

def test_the_sector_table_holds_the_thirteen_kepler_field_sectors():
    assert sorted(tess.SECTORS) == [14, 15, 26, 40, 41, 53, 54, 55, 74, 75, 80, 81, 82]
    assert tess.SECTORS[14] == {"cycle": 2, "cadence_min": 30,
                                "segments": [(1683.354, 1696.375), (1697.354, 1710.187)]}
    assert tess.SECTORS[26]["segments"][-1] == (2023.124, 2035.142)        # the repaired placeholder end
    assert tess.SECTORS[40]["cadence_min"] == 10 and tess.SECTORS[82]["cadence_min"] == pytest.approx(200 / 60)
    for number, s in tess.SECTORS.items():
        segs = s["segments"]
        assert all(a < b for a, b in segs) and all(b1 < a2 for (_a1, b1), (a2, _b2) in itertools.pairwise(segs)), number
        assert len(segs) == (4 if s["cycle"] == 6 else 2)
    assert max(tess.SECTORS) <= tess.LAST_COMPLETED_SECTOR


def test_tmag_from_gaia_is_stassun_2019_equation_1_and_2():
    # KIC 6699887 (research note 3.3): G 16.2329, BP 16.8599, RP 15.4928 gives 15.552
    assert tess.tmag_from_gaia(16.2329, 16.8599 - 15.4928) == pytest.approx(15.552, abs=0.001)
    assert tess.tmag_from_gaia(16.0, None) == pytest.approx(15.570)
    assert tess.tmag_from_gaia(None, 1.0) is None


# ---------------------------------------------------------------------------------------------------- dilution

def test_the_aperture_fraction_at_zero_offset_matches_a_numerical_integral():
    f0 = tess.aperture_fraction(0.0, 0.0)
    assert f0 == pytest.approx(_numeric_fraction(0.0, 0.0), rel=1e-4)
    assert 0.69 < f0 < 0.71                     # between the 4 and 6 pixel circles of the research note (0.68, 0.82)
    assert tess.aperture_fraction(0.3, -0.7) == pytest.approx(_numeric_fraction(0.3, -0.7), rel=1e-4)
    assert tess.aperture_fraction(4.0, 0.0) < 1e-3


def test_dilution_for_one_equal_magnitude_neighbour_one_pixel_away():
    d = tess.dilution(15.0, [(15.0, 1.0, 0.0, 21.0)])
    c = _numeric_fraction(1.0, 0.0) / _numeric_fraction(0.0, 0.0)
    assert d["c"] == pytest.approx(c, rel=1e-4) and 0.66 < d["c"] < 0.70
    assert d["t_total"] == pytest.approx(15.0 - 2.5 * math.log10(1 + c), rel=1e-6)
    assert d["c_hard42"] == pytest.approx(1.0)              # within 42 arcsec, unit weight
    assert d["aperture_pixels"] == 5 and d["n_neighbours"] == 1 and d["n_without_tmag"] == 0


def test_dilution_weights_by_magnitude_skips_a_missing_one_and_cuts_hard_at_42_arcsec():
    d = tess.dilution(15.0, [(12.5, 0.0, 0.0, 0.0), (None, 0.0, 0.0, 0.0), (14.0, 3.0, 0.0, 63.0)])
    bright = 10 ** (-0.4 * (12.5 - 15.0))
    assert d["c"] == pytest.approx(bright + 10 ** 0.4 * tess.aperture_fraction(3.0, 0.0) / tess.aperture_fraction(0, 0))
    assert d["c_hard42"] == pytest.approx(bright)           # the 63 arcsec one is outside the hard cut
    assert d["n_neighbours"] == 2 and d["n_without_tmag"] == 1
    assert tess.dilution(15.0, [])["c"] == 0.0 and tess.dilution(15.0, [])["t_total"] == 15.0


# ---------------------------------------------------------------------------------------------------- noise

def test_the_nominal_noise_is_the_tic_polynomial():
    assert tess.sigma_1h_nominal(15.0) == pytest.approx(5190, rel=0.01)
    assert tess.sigma_1h_nominal(12.0) == pytest.approx(595, rel=0.01)
    assert tess.sigma_1h_nominal(16.0) == pytest.approx(13957, rel=0.01)


def test_the_conservative_noise_is_the_spoc_table_interpolated_and_held_at_the_ends():
    assert tess.sigma_1h_conservative(15.0) == pytest.approx(8804)
    assert tess.sigma_1h_conservative(15.25) == pytest.approx((8804 + 14449) / 2)
    assert tess.sigma_1h_conservative(12.5) == pytest.approx((790 + 1625) / 2)
    assert tess.sigma_1h_conservative(10.0) == 790 and tess.sigma_1h_conservative(17.5) == 24151


def test_scaling_to_the_duration():
    assert tess.scale_to_duration(1000.0, 4.0) == pytest.approx(500.0)                    # white noise
    assert tess.scale_to_duration(1000.0, 2.0, empirical=True) == pytest.approx(752.0)
    assert tess.scale_to_duration(1000.0, 6.0, empirical=True) == pytest.approx(514.0)
    ratio = 1 + (0.752 - 1) * math.log(1.5) / math.log(2)                                  # linear in log T
    assert tess.scale_to_duration(1000.0, 1.5, empirical=True) == pytest.approx(1000 * ratio)
    assert tess.scale_to_duration(1000.0, 0.5, empirical=True) == pytest.approx(1000 * math.sqrt(2))
    assert tess.scale_to_duration(1000.0, 9.0, empirical=True) == pytest.approx(514.0)     # held beyond 6 h


# ---------------------------------------------------------------------------------------------------- transits

SEGMENTS = [(10.0, 20.0), (21.0, 30.0)]


def test_the_transit_count_on_a_hand_made_window():
    # mid-times 0.5 + 3n: 12.5, 15.5, 18.5 in the first segment, 21.5, 24.5, 27.5 in the second; 9.5 and 30.5 miss
    got = tess.count_transits(0.5, 3.0, 2.0, SEGMENTS, sigma_t0=0.001, sigma_p=0.0001)
    assert got["n_transits"] == 6 and got["epochs"] == [4, 5, 6, 7, 8, 9]
    assert got["timing_error_days"] == pytest.approx(math.hypot(0.001, 9 * 0.0001)) and got["uncertain"] is False
    # a mid-time just past a segment's end is not counted; one just inside its start is (its second half is covered)
    edge = tess.count_transits(10.01, 30.0, 2.0, [(10.0, 20.0)], sigma_t0=0.0, sigma_p=0.0)
    assert edge["n_transits"] == 1
    past = tess.count_transits(20.01, 30.0, 2.0, [(10.0, 20.0)], sigma_t0=0.0, sigma_p=0.0)
    assert past["n_transits"] == 0


def test_a_timing_error_above_half_the_duration_marks_the_count_uncertain():
    got = tess.count_transits(0.5, 3.0, 2.0, SEGMENTS, sigma_t0=0.001, sigma_p=0.01)
    assert got["timing_error_days"] * 24 > 1.0 and got["uncertain"] is True
    unknown = tess.count_transits(0.5, 3.0, 2.0, SEGMENTS)
    assert unknown["timing_error_days"] is None and unknown["uncertain"] is True
    assert tess.timing_error(100, 0.003, 0.0004) == pytest.approx(math.hypot(0.003, 0.04))
    assert tess.timing_error(100, None, 0.0004) is None


def test_the_expected_number_from_phase_coverage():
    assert tess.expected_transits(SEGMENTS, 3.0) == pytest.approx(19.0 / 3.0)


def test_significance_folds_with_the_square_root_of_n():
    assert tess.significance(300.0, 100.0, 4) == pytest.approx((3.0, 6.0))
    assert tess.significance(300.0, 100.0, 0) == pytest.approx((3.0, 0.0))


@pytest.mark.parametrize(("n", "nominal", "conservative", "word"), [
    (0, 50.0, 20.0, "not observed in transit"),
    (5, 20.0, 8.0, "should have detected"),
    (5, 12.0, 7.1, "should have detected"),
    (5, 2.5, 1.0, "not expected"),
    (5, 3.0, 1.2, "marginal"),
    (5, 8.0, 5.0, "marginal"),
])
def test_the_four_verdicts(n, nominal, conservative, word):
    assert tess.verdict(n, nominal, conservative) == word
    assert word in tess.VERDICTS


def test_the_thresholds_are_the_cited_constants():
    assert tess.SHOULD_DETECT_S == 7.1 and tess.NOT_EXPECTED_S == 3.0
    assert "Christ" in tess.SHOULD_DETECT_DOC and "Christ" in tess.NOT_EXPECTED_DOC


# ---------------------------------------------------------------------------------------------------- the block

EPHEMERIS = {"period_days": 40.290806, "period_err_days": 0.00014775705, "t0_bjd": 2454968.795182,
             "t0_bjd_err": 0.0031128049, "duration_h": 3.8647124, "depth_ppm": 2186.63}
META = {"queried": "2026-10-22", "tic": 169822650, "tmag": 15.1926, "tmag_source": "TIC", "ra": 297.86876,
        "dec": 41.45565, "sectors": [14, 15, 41, 54, 55, 74, 75, 81, 82],
        "neighbours": [{"tic": 169822655, "dst_arcsec": 11.7, "dra_pix": 0.40, "ddec_pix": -0.39, "tmag": 12.3923,
                        "tmag_source": "TIC"},
                       {"tic": 508690015, "dst_arcsec": 13.2, "dra_pix": -0.5, "ddec_pix": 0.3, "tmag": None,
                        "tmag_source": None}]}


def test_without_inputs_the_block_says_they_were_not_fetched():
    assert tess.statement(None, EPHEMERIS) == {"available": False, "caveats": ["TESS inputs not fetched"]}
    assert tess.note_sentence({"available": False}) == "TESS coverage was not assessed."
    assert tess.exofop_line({"available": False, "caveats": []}) is None
    assert tess.exofop_line(None) is None and tess.note_sentence(None) == "TESS coverage was not assessed."


def test_the_statement_for_kic_6146676():
    b = tess.statement(META, EPHEMERIS)
    assert b["available"] is True and b["tic"] == 169822650 and b["queried"] == "2026-10-22"
    assert [s["sector"] for s in b["sectors"]] == META["sectors"]
    assert b["n_transits"] == sum(s["n_transits"] for s in b["sectors"])
    assert b["n_expected"] == pytest.approx(
        sum(b - a for n in META["sectors"] for a, b in tess.SECTORS[n]["segments"]) / EPHEMERIS["period_days"])
    dil = b["dilution"]
    assert dil["c"] > 5 and dil["depth_tess_ppm"] == pytest.approx(2186.63 / (1 + dil["c"]))
    assert dil["c_hard42"] >= 10 ** (-0.4 * (12.3923 - 15.1926))
    assert dil["depth_tess_hard42_ppm"] == pytest.approx(2186.63 / (1 + dil["c_hard42"]))
    noise = b["noise"]
    assert noise["nominal_1h_ppm"] == pytest.approx(tess.sigma_1h_nominal(dil["t_total"]))
    assert noise["conservative_t14_ppm"] == pytest.approx(
        tess.scale_to_duration(tess.sigma_1h_conservative(dil["t_total"]), 3.8647124, empirical=True))
    sig = b["significance"]
    assert sig["folded_nominal"] == pytest.approx(sig["per_transit_nominal"] * math.sqrt(b["n_transits"]))
    assert sig["folded_conservative"] < sig["folded_nominal"]
    assert b["verdict"] == tess.verdict(b["n_transits"], sig["folded_nominal"], sig["folded_conservative"])
    assert b["timing_error_hours_at_tess"] == pytest.approx(
        24 * max(s["timing_error_days"] for s in b["sectors"]))
    assert any("no TESS light curve was examined" in c for c in b["caveats"])
    assert any("1 neighbour without a TESS magnitude" in c for c in b["caveats"])
    assert b["sentence"] == tess.note_sentence(b) and "—" not in b["sentence"]
    json.dumps(b, allow_nan=False)


def test_the_note_sentence_carries_every_number_of_spec_10_8():
    b = tess.statement(META, EPHEMERIS)
    text = tess.note_sentence(b)
    assert text.startswith("TESS observed the star in 9 sectors (14, 15, 41, 54, 55, 74, 75, 81 and 82; ")
    assert "full-frame images at 30 minutes, 10 minutes and 200 seconds" in text
    assert f"{b['n_transits']} Kepler-ephemeris transits fall in its windows" in text
    assert f"a diluted depth of {b['dilution']['depth_tess_ppm']:,.0f} ppm" in text
    assert f"a 1-hour noise of {b['noise']['nominal_1h_ppm']:,.0f} to {b['noise']['conservative_1h_ppm']:,.0f} ppm" \
           in text
    assert f"at T = {b['dilution']['t_total']:.2f} with the neighbours' light (the star alone 15.19)" in text
    assert "a folded significance of " in text and text.endswith(f"so {tess.VERDICT_PHRASES[b['verdict']]}.")
    tex = tess.note_sentence(b, tex=True)
    assert "Kepler-ephemeris" in tex and "\\%" not in tex


@pytest.mark.parametrize("verdict", ["should have detected", "marginal", "not expected"])
def test_each_verdict_gets_its_phrase_and_an_exofop_line(verdict, monkeypatch):
    monkeypatch.setattr(tess, "verdict", lambda *_a: verdict)
    b = tess.statement(META, EPHEMERIS)
    assert b["verdict"] == verdict and tess.note_sentence(b).endswith(f"so {tess.VERDICT_PHRASES[verdict]}.")
    line = tess.exofop_line(b)
    assert line.startswith(f"TESS: {verdict}; {b['n_transits']} transits in sectors 14, 15, 41")
    assert "; folded S/N " in line and "-" in line.split("folded S/N ")[1]


def test_no_transit_in_a_window_is_a_statement_about_coverage():
    eph = {**EPHEMERIS, "period_days": 400.0, "t0_bjd": 2457000.0 + 1650.0}   # one transit every 400 days, none in
    b = tess.statement({**META, "sectors": [14, 15]}, eph)
    assert b["n_transits"] == 0 and b["verdict"] == "not observed in transit"
    text = tess.note_sentence(b)
    assert "no transit fell in a TESS window" in text and "folded significance" not in text
    assert tess.exofop_line(b) == "TESS: not observed in transit; 0 transits in sectors 14 and 15"


def test_a_sector_outside_the_table_is_recorded_and_skipped():
    b = tess.statement({**META, "sectors": [14, 109]}, EPHEMERIS)
    assert [s["sector"] for s in b["sectors"]] == [14] and b["sectors_skipped"] == [109]
    assert any("Sector 109" in c for c in b["caveats"])


def test_an_ephemeris_without_errors_marks_the_count_uncertain():
    eph = {k: v for k, v in EPHEMERIS.items() if not k.endswith("_err") and not k.endswith("_err_days")}
    b = tess.statement(META, eph)
    assert b["timing_error_hours_at_tess"] is None and b["uncertain"] is True
    assert all(s["uncertain"] for s in b["sectors"])
    assert "expected from phase coverage" in tess.note_sentence(b)
    assert any("no error" in c for c in b["caveats"])


def test_no_sector_at_all():
    b = tess.statement({**META, "sectors": []}, EPHEMERIS)
    assert b["verdict"] == "not observed in transit" and b["sectors"] == []
    assert tess.note_sentence(b) == ("No TESS sector up to 108 observed the star, so no transit fell in a TESS "
                                     "window.")


def test_a_missing_magnitude_or_ephemeris_leaves_the_block_unavailable():
    b = tess.statement({**META, "tmag": None}, EPHEMERIS)
    assert b["available"] is False and b["caveats"] == ["no TESS magnitude for the star"]
    b = tess.statement(META, {**EPHEMERIS, "duration_h": None})
    assert b["available"] is False and "duration" in b["caveats"][0]


# ---------------------------------------------------------------------------------------------------- the tool

def _canned():
    return {name: (FIXTURES / f"{name}_6146676.json").read_bytes() for name in ("tic", "cone", "sector")}


def _fake_fetch(calls: list, canned: dict):
    def fetch(url, *, params=None, service, store=None, target_id=None, **_kw):
        calls.append((url, dict(params or {}), service, target_id))
        if url.endswith("/sector"):
            return canned["sector"]
        req = json.loads(params["request"])
        return canned["tic"] if req["service"] == "Mast.Catalogs.Filtered.Tic" else canned["cone"]
    return fetch


@pytest.fixture
def tool():
    import sys
    repo = str(Path(__file__).resolve().parents[2])
    if repo not in sys.path:
        sys.path.insert(0, repo)
    from tools import tess_inputs
    return tess_inputs


def _store_with_target(tmp_path):
    from skysift.models import Target
    from skysift.store.db import Store
    url = f"sqlite:///{(tmp_path / 'engine.db').as_posix()}"
    store = Store(url); store.init_schema()
    store.upsert_target(Target(source="kbonus_bkg", target_id="kic-006146676", ra=297.8688, dec=41.4557,
                               kic=6146676, meta={"crowdsap": 0.62, "psffrac": 0.81}))
    return store, url


def test_the_tool_fetches_by_get_and_stores_the_inputs_in_meta(tool, tmp_path, monkeypatch):
    from skysift import net
    from skysift.store.db import TargetRow
    store, url = _store_with_target(tmp_path)
    calls: list = []
    monkeypatch.setattr(net, "fetch", _fake_fetch(calls, _canned()))
    assert tool.main(["--target", "kic-006146676", "--db", url, "--today", "2026-10-22"]) == 0
    assert [c[0] for c in calls] == [tool.INVOKE_URL, tool.INVOKE_URL, tool.TESSCUT_SECTOR_URL]
    first = json.loads(calls[0][1]["request"])
    assert first["service"] == "Mast.Catalogs.Filtered.Tic"
    assert first["params"]["filters"] == [{"paramName": "KIC", "values": ["6146676"]}]
    cone = json.loads(calls[1][1]["request"])
    assert cone["service"] == "Mast.Catalogs.Tic.Cone" and cone["params"]["radius"] == tess.CONE_DEG
    assert cone["params"]["ra"] == pytest.approx(297.86875936733)          # the TIC's position, not the row's
    assert calls[2][1] == {"ra": pytest.approx(297.86875936733), "dec": pytest.approx(41.4556456852551)}
    assert all(c[3] == "kic-006146676" for c in calls)                    # every request logged against the target
    with store.session() as s:
        meta = dict(s.get(TargetRow, "kic-006146676").meta)
    assert meta["crowdsap"] == 0.62 and meta["psffrac"] == 0.81          # the KBonus header values are kept
    t = meta["tess"]
    assert t["queried"] == "2026-10-22" and t["tic"] == 169822650 and t["tmag"] == pytest.approx(15.1926)
    assert t["tmag_source"] == "TIC" and t["sectors"] == [14, 15, 41, 54, 55, 74, 75, 81, 82]
    ids = [n["tic"] for n in t["neighbours"]]
    assert 169822650 not in ids and 169822655 in ids                       # the target is dropped by its ID
    assert 1879962382 not in ids                                           # a DUPLICATE row is dropped
    bright = next(n for n in t["neighbours"] if n["tic"] == 169822655)
    assert bright["tmag"] == pytest.approx(12.3923) and bright["dst_arcsec"] == pytest.approx(11.7, abs=0.1)
    assert math.hypot(bright["dra_pix"], bright["ddec_pix"]) * tess.PIXEL_ARCSEC == pytest.approx(
        bright["dst_arcsec"], rel=0.01)
    gaia = next(n for n in t["neighbours"] if n["tic"] == 508690015)       # no Tmag in the fixture row
    assert gaia["tmag_source"] == tess.TMAG_EQ2 and gaia["tmag"] == pytest.approx(19.7241 - 0.430)
    assert set(t) == {"queried", "tic", "tmag", "tmag_source", "ra", "dec", "sectors", "neighbours"}
    assert set(bright) == {"tic", "dst_arcsec", "dra_pix", "ddec_pix", "tmag", "tmag_source"}
    assert tess.statement(t, EPHEMERIS)["available"] is True


def test_the_tool_s_dry_run_prints_the_urls_and_fetches_nothing(tool, tmp_path, monkeypatch, capsys):
    from skysift import net
    from skysift.store.db import TargetRow
    store, url = _store_with_target(tmp_path)
    monkeypatch.setattr(net, "fetch", lambda *a, **k: pytest.fail("the dry run fetched"))
    assert tool.main(["--target", "kic-006146676", "--db", url, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "https://mast.stsci.edu/api/v0/invoke?request=" in out and "Mast.Catalogs.Filtered.Tic" in out
    assert "https://mast.stsci.edu/tesscut/api/v0.1/sector?ra=297.8688&dec=41.4557" in out
    with store.session() as s:
        assert "tess" not in s.get(TargetRow, "kic-006146676").meta


def test_the_tool_reports_a_star_the_tic_does_not_know(tool, tmp_path, monkeypatch, capsys):
    from skysift import net
    _store, url = _store_with_target(tmp_path)
    empty = json.dumps({"status": "COMPLETE", "msg": "", "data": [], "fields": []}).encode()
    monkeypatch.setattr(net, "fetch", lambda *a, **k: empty)
    assert tool.main(["--target", "kic-006146676", "--db", url]) == 1
    assert "no TIC row for KIC 6146676" in capsys.readouterr().err
