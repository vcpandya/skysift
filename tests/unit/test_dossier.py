"""Dossier generation (dossiers plan Task 4): the JSON, the dossiers row, staleness, the caveats, the pixels section,
the standalone export and the ``skysift dossier`` command, on the dashboard seed with a synthetic light curve, a fake
Gaia fetch and a synthetic null calibration."""
import hashlib
import io
import json
import re
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from typer.testing import CliRunner

from skysift import calibrate as cal
from skysift import net
from skysift.cli import app
from skysift.detectors.periodogram import PeriodogramSummary
from skysift.dossier import (
    DOSSIER_VERSION,
    STATUS_ORDER,
    caveats,
    export_html,
    fingerprint,
    generate,
    pick_signal,
)
from skysift.models import Signal, TestResult, TestStatus
from skysift.sources import PARSERS
from skysift.store.blob import blob_from_url
from skysift.store.db import PERIODOGRAM_URL, QueryRow, SignalRow, Store, make_job_key
from tests.synthetic import inject_model, make_lightcurve
from tests.unit.ui_seed import STAGE1_TARGET, STAGE2_TARGET, VERSION, add_older_version, seed, seed_dossier

EM_DASH = chr(0x2014)
STATIC_DIR = Path(__file__).resolve().parents[2] / "src" / "skysift" / "ui" / "static"

GAIA_CSV = (b"source_id,ra,dec,parallax,parallax_error,phot_g_mean_mag,bp_rp,ruwe,nu_eff_used_in_astrometry,"
            b"pseudocolour,ecl_lat,astrometric_params_solved,teff_gspphot,radius_flame,mass_flame\n"
            b"2101234567890123462,285.9,40.9,3.268646,0.035821,16.236654,2.0971918,0.9449,1.3330975,,"
            b"60.5697568,31,3855.0,0.5394,0.5595\n")
GAIA_HEADER = GAIA_CSV.split(b"\n")[0] + b"\n"
DIST_CSV = b"r_med_geo,r_lo_geo,r_hi_geo\n301.9,298.6,305.3\n"
TMASS_CSV = b"ks_m,ks_msigcom\n13.030,0.034\n"
CONE_CSV = (b"source_id,ra,dec,phot_g_mean_mag,bp_rp,sep_arcsec\n"
            b"2101234567890120001,285.9017,40.9015,19.772,1.814,7.14\n"
            b"2101234567890120002,285.8973,40.8979,12.80,1.1,10.8\n")
RESPONSES = {"CONTAINS": CONE_CSV, "gaia_source WHERE source_id": GAIA_CSV, "gaiaedr3_distance": DIST_CSV,
             "tmass": TMASS_CSV}
PERIOD, T0 = 40.2899, 136.3


def fake_fetch(responses=None):
    """The four Gaia queries, answered by the first routing substring found in the ADQL and logged like net.fetch."""
    responses = RESPONSES if responses is None else responses

    def fetch(url, *, params=None, service, store=None, target_id=None, **_kw):
        assert service == "gaia", f"only Gaia may be asked, not {service}"
        data = next(body for key, body in responses.items() if key in params["query"])
        if store:
            store.log_query(service, url, params, 200, len(data), hashlib.sha256(data).hexdigest(), target_id=target_id)
        return data
    return fetch


def _lightcurve():
    lc = make_lightcurve(noise_ppm=600, seed=3)
    return inject_model(lc, PERIOD, T0, 0.047, 0.31, 74.0)


def _calibration_rows(version: str, n: int = 220) -> list[dict]:
    rng = np.random.default_rng(5)
    return [{"target_id": f"kic-{i:09d}", "search_version": version, "n_points": 60000,
             "real": [float(x) for x in rng.uniform(7, 12, 3)],
             "inverted": [float(x) for x in np.sort(rng.uniform(6, 12, 3))[::-1]],
             "scrambled": [float(x) for x in np.sort(rng.uniform(6, 11, 3))[::-1]], "seconds": 1.0}
            for i in range(n)]


def _periodogram(store, blob, target_id):
    periods = np.geomspace(0.5, 200.0, 20000)
    sde = np.random.default_rng(2).uniform(0, 6, periods.size)
    sde[np.argmin(np.abs(periods - PERIOD))] = 50.5
    pg = PeriodogramSummary.from_spectrum(periods, sde, 0.5, 200.0, [0.5, 200.0],
                                          [{"period": PERIOD, "sde": 50.5, "duration": 0.096, "rank": 1}])
    data = pg.to_npz()
    sha, loc = blob.put(data, f"{target_id}_periodogram.npz")
    store.add_artifact(sha, len(data), PERIODOGRAM_URL.format(target_id=target_id), loc, target_id, pinned=True,
                       kind="summary")


def _make_env(settings, monkeypatch, responses=None, calibration=True):
    store = Store(settings.database_url); store.init_schema()
    blob = blob_from_url(settings.blob_url)
    ids = seed(store, blob)
    raw = b"KBonus FITS bytes for kic-006146676"
    sha, loc = blob.put(raw, "kic-006146676.fits")
    store.add_artifact(sha, len(raw), "https://archive.stsci.edu/kic-006146676.fits", loc, STAGE2_TARGET,
                       pinned=True, kind="raw")
    _periodogram(store, blob, STAGE2_TARGET)
    lc = _lightcurve()
    monkeypatch.setitem(PARSERS, "kbonus_bkg", lambda _data: lc)
    path = cal.default_path(settings.data_dir, "kbonus_bkg")
    if calibration:
        version = cal.search_version(cal.calibration_detector(), settings.thresholds)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r) + "\n" for r in _calibration_rows(version)), encoding="utf-8")
    return SimpleNamespace(store=store, blob=blob, settings=settings, ids=ids, fetch=fake_fetch(responses),
                           calibration_path=path, lc=lc)


@pytest.fixture
def env(settings, monkeypatch):
    return _make_env(settings, monkeypatch)


@pytest.fixture
def env_stage1(env):
    return env


@pytest.fixture
def env_no_gaia(settings, monkeypatch):
    return _make_env(settings, monkeypatch, {**RESPONSES, "gaia_source WHERE source_id": GAIA_HEADER})


@pytest.fixture
def env_with_npz(env):
    sid = env.ids["stage2"]
    shape = (2, 5, 5)
    rng = np.random.default_rng(9)
    valid = np.ones(shape, bool); valid[0, 0, 0] = False
    buf = io.BytesIO()
    np.savez_compressed(buf, diff=rng.normal(0, 1, shape), var=np.full(shape, 0.25), direct=rng.uniform(10, 20, shape),
                        valid=valid, seasons=np.array([0, 2]), pos_target=np.array([2.0, 2.0]),
                        pos_neighbour=np.array([4.5, 2.6]))
    data = buf.getvalue()
    sha, loc = env.blob.put(data, f"pixels_{sid}.npz")
    env.store.add_artifact(sha, len(data), f"skysift://pixels/{sid}", loc, STAGE2_TARGET, pinned=True, kind="pixels")
    return env


def _data(rep) -> dict:
    return json.loads(rep.data_path.read_text(encoding="utf-8"))


def test_pick_signal_prefers_stage_two_then_approved_then_sde(settings):
    store = Store(settings.database_url); store.init_schema()
    ids = seed(store)
    assert STATUS_ORDER == ("candidate_stage2", "approved", "candidate_stage1")
    first = ids["candidate"]                                   # stage 1, SDE 21.1
    stronger = add_older_version(store, STAGE1_TARGET)         # stage 1, SDE 30.0, same target
    assert pick_signal(store, STAGE1_TARGET)["id"] == stronger
    store.decide(first, "approve", "looks real")
    assert pick_signal(store, STAGE1_TARGET)["id"] == first
    rid = store.start_run(make_job_key(STAGE1_TARGET, "box_dip", VERSION, "4b"), STAGE1_TARGET, "box_dip", VERSION, "4")
    sig = Signal(target_id=STAGE1_TARGET, detector="box_dip", detector_version=VERSION, period=2.2, t0=136.0,
                 duration=0.08, depth_ppm=900.0, snr=12.0, sde=11.0, n_events=600, extra={})
    item = {"signal": sig.model_dump(), "outcome": "pass", "timings": [0.01],
            "tests": [TestResult(rung="rung5_pixels", status=TestStatus.PASS).model_dump()]}
    store.record_results(rid, STAGE1_TARGET, [item], [], pin_evidence=False)
    with store.session() as s:
        weak_stage2 = s.query(SignalRow.id).filter(SignalRow.run_id == rid).scalar()
    assert store.signal_status(weak_stage2) == "candidate_stage2"
    assert pick_signal(store, STAGE1_TARGET)["id"] == weak_stage2
    assert pick_signal(store, STAGE1_TARGET, signal_id=first)["id"] == first
    with pytest.raises(KeyError):
        pick_signal(store, "kic-000000001")
    with pytest.raises(KeyError):
        pick_signal(store, STAGE1_TARGET, signal_id=ids["stage2"])     # a signal of another target


def test_fingerprint_changes_with_tests_status_and_decision():
    tests = [{"id": 3}, {"id": 1}]
    base = fingerprint(tests, "candidate_stage2", None)
    assert base == fingerprint([{"id": 1}, {"id": 3}], "candidate_stage2", None) and len(base) == 64
    assert base != fingerprint([{"id": 1}, {"id": 4}], "candidate_stage2", None)
    assert base != fingerprint(tests, "candidate_stage1", None)
    assert base != fingerprint(tests, "candidate_stage2", "approve")


def test_generate_writes_the_json_and_the_row(env):
    rep = generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch)
    data = _data(rep)
    assert rep.generated and set(data) >= {"signal", "plots", "tests", "why", "fap", "fpp", "pixels", "star", "planet",
                                           "look", "caveats", "provenance"}
    assert rep.data_path == env.settings.data_dir / "dossiers" / STAGE2_TARGET / "dossier_data.json"
    row = env.store.dossier(STAGE2_TARGET)
    assert row["signal_id"] == rep.signal_id and row["fap"] == pytest.approx(rep.fap.fap)
    assert row["fpp"] == pytest.approx(rep.fpp.value)
    assert row["dossier_version"] == DOSSIER_VERSION and row["status_at_generation"] == "candidate_stage2"
    assert row["fpp_detail"]["scenarios"][0]["name"] == "PL" and row["fap_detail"]["n"] == 440
    assert data["fap"]["sentence_not_covered"].startswith("This is about noise.")
    assert data["fpp"]["sentence_not_covered"] == (
        "This counts the common impostors with simple priors. It is not a vespa or a Kepler DR25 false-positive "
        "probability, and it has no radial-velocity or imaging input.")
    assert "discover" not in json.dumps(data).lower() and EM_DASH not in json.dumps(data)
    # the content the page draws from
    assert data["target"]["name"] == "KIC 6146676" and data["status"]["label"] == "Candidate, stage 2"
    assert data["fap"]["words"] == "1 in 440" and data["fap"]["calibrated"]
    assert data["fpp"]["pixels"] and data["fpp"]["inputs"]["centroid_sigma_arcsec"] > 0
    assert all(n["centroid_sep_arcsec"] is not None for n in data["fpp"]["inputs"]["neighbours"])
    assert data["star"]["route"] == "mann" and data["look"]["star_colour"] == "red"
    plots = data["plots"]
    assert plots["series_bin_days"] == 0.25 and len(plots["series_binned"]) > 1000
    assert plots["events"] and plots["event_gallery"][0]["pts"] and 0 < len(plots["spectrum"]) <= 1500
    assert [w["rung"] for w in data["why"]][-1] == "rung5_pixels" and data["caveats"][-1]["title"].startswith(
        "This is a candidate")
    assert len(data["provenance"]["commands"]) == 3 and data["provenance"]["git_commit"]
    assert data["pixels"] is None                                # the rung 5 row exists, its npz does not


def test_generate_again_is_a_no_op_until_the_tests_change(env):
    first = generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch)
    mtime = first.data_path.stat().st_mtime_ns
    second = generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch)
    assert not second.generated and second.data_path.stat().st_mtime_ns == mtime
    env.store.add_test(first.signal_id, TestResult(rung="rung5_pixels", status="undetermined",
                                                   reason="pixels: 2 usable transits < 3"), 1.0)
    third = generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch)
    assert third.generated
    assert any(c["title"] == "The pixels could not show which star the dip is on" for c in caveats(_data(third)))
    assert generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch, force=True).generated


def test_generate_without_a_calibration_file(env):
    env.calibration_path.unlink()
    rep = generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch)
    data = _data(rep)
    assert data["fap"]["words"] == "not yet calibrated"
    assert any("not yet calibrated" in c["text"] for c in data["caveats"])
    assert env.store.dossier(STAGE2_TARGET)["fap"] is None


def test_generate_without_pixels_labels_the_fpp(env_stage1):
    env = env_stage1
    data = _data(generate(env.store, env.blob, env.settings, STAGE1_TARGET, fetch=env.fetch))
    assert data["pixels"] is None and data["fpp"]["label"] == "False positive (no pixel data yet)"
    assert data["caveats"][0]["title"].startswith("Which star is dimming")
    assert data["caveats"][1]["title"] == "The false-positive estimate has no pixel exclusion"


def test_generate_for_a_star_without_gaia_parameters(env_no_gaia):
    env = env_no_gaia
    data = _data(generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch))
    assert data["star"]["route"] == "none" and data["look"] is None and data["planet"]["rp_rearth"] is None
    assert any(c["title"].startswith("The star cannot be characterized") for c in data["caveats"])


def test_generate_records_a_gaia_outage_and_carries_on(settings, monkeypatch):
    env = _make_env(settings, monkeypatch, {"CONTAINS": b"", "gaia_source": b""})
    data = _data(generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch))
    assert data["star"]["route"] == "none" and data["fpp"]["inputs"]["neighbours"] == []
    assert any(c["text"].startswith("Gaia could not be reached: ") for c in data["caveats"])
    assert env.store.signal_status(env.ids["stage2"]) == "candidate_stage2"        # a dossier never changes a status


def test_pixels_section_comes_from_the_npz_artifact(env_with_npz):
    env = env_with_npz
    data = _data(generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch))
    assert len(data["pixels"]) == 2 and data["pixels"][0]["snr"][0][0] is None   # an invalid pixel
    p = data["pixels"][1]
    assert p["season"] == 2 and p["nearer"] == "target" and p["depth_target_ppm"] == [2213, 78]
    assert p["pos_target"] == [2.0, 2.0] and p["quarters"] and all(q % 4 == 2 for q in p["quarters"])
    assert p["valid"][0][0] is True and data["pixels"][0]["direct"][0][0] is None


def test_every_query_in_provenance_belongs_to_the_target(env):
    data = _data(generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch))
    queries = data["provenance"]["queries"]
    with env.store.session() as s:
        n = s.query(QueryRow).filter(QueryRow.target_id == STAGE2_TARGET).count()
        others = s.query(QueryRow).filter(QueryRow.target_id != STAGE2_TARGET).count()
    assert len(queries) == n == 4 and others > 0
    assert all(q["target_id"] == STAGE2_TARGET for q in queries)
    files = data["provenance"]["files"]
    assert files and all(f["sha256"] and f["url"] for f in files) and not any(f["kind"] == "dossier" for f in files)


def test_export_is_one_file_without_external_urls(env):
    data = _data(generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch))
    html = export_html(data, STATIC_DIR)
    assert "<script src=" not in html and "<link " not in html and "data:font/woff2;base64," in html
    assert "https://" not in re.sub(r'<a [^>]*href="https://[^"]*"', "", html)        # only citation links remain
    assert "data-level" in html                                                        # its own toggle
    assert "/static/" not in html and EM_DASH not in html
    blob = re.search(r'<script id="dossier-data" type="application/json">(.*?)</script>', html, re.DOTALL).group(1)
    assert json.loads(blob) == data                                                    # the JSON round-trips


def test_seed_dossier_has_the_keys_generate_writes(env):
    real = _data(generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch))
    fake = seed_dossier(env.store, env.settings.data_dir, target_id=STAGE2_TARGET)
    assert set(fake) == set(real)
    for key in ("target", "status", "fap", "fpp", "star", "planet", "look", "provenance", "plots", "ephemeris"):
        assert set(fake[key]) == set(real[key]), key
    assert fake["fpp"]["words"] == "about 3 percent" and fake["fap"]["words"] == "1 in 1,700"
    row = env.store.dossier(STAGE2_TARGET)
    assert json.loads(Path(row["data_path"]).read_text(encoding="utf-8")) == fake and row["fpp"] == 0.03


@pytest.fixture
def cli_env(settings, monkeypatch):
    env = _make_env(settings, monkeypatch)
    monkeypatch.setattr(net, "fetch", env.fetch)
    return env


def test_cli_dossier_prints_json_when_piped(cli_env, tmp_path):
    out = tmp_path / "export" / "dossier.html"
    r = CliRunner().invoke(app, ["dossier", "--target", STAGE2_TARGET, "--export", str(out)])
    assert r.exit_code == 0, r.output
    report = json.loads(r.stdout)
    assert report["target_id"] == STAGE2_TARGET and report["generated"] and report["fpp"]["words"]
    assert out.is_file() and "data-level" in out.read_text(encoding="utf-8")
    assert cli_env.store.dossier(STAGE2_TARGET)["export_path"] == str(out)
    again = json.loads(CliRunner().invoke(app, ["dossier", "--target", STAGE2_TARGET]).stdout)
    assert not again["generated"]
    forced = json.loads(CliRunner().invoke(app, ["dossier", "--target", STAGE2_TARGET, "--force"]).stdout)
    assert forced["generated"]


def test_cli_dossier_fails_plainly_for_a_target_without_signals(cli_env):
    r = CliRunner().invoke(app, ["dossier", "--target", "kic-000000001"])
    assert r.exit_code == 1 and "no signal" in r.output and "Traceback" not in r.output
