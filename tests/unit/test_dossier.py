"""Dossier generation (dossiers plan Task 4): the JSON, the dossiers row, staleness, the caveats, the pixels section,
the standalone export and the ``skysift dossier`` command, on the dashboard seed with a synthetic light curve, a fake
Gaia fetch and a synthetic null calibration."""
import hashlib
import io
import json
import logging
import math
import re
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from skysift import calibrate as cal
from skysift import dossier as dossier_module
from skysift import net
from skysift.cli import app
from skysift.detectors import get_detector
from skysift.detectors.periodogram import PeriodogramSummary
from skysift.dossier import (
    CLOSE_CALL,
    DOSSIER_VERSION,
    STATUS_ORDER,
    LightCurveReleased,
    NoSignal,
    calibration_version_for,
    caveats,
    centroid_estimate,
    export_html,
    fingerprint,
    gaia_refused,
    generate,
    pick_signal,
    rank_key,
    status_label,
)
from skysift.fpp import FppInputs, compute
from skysift.models import Signal, TestResult, TestStatus
from skysift.net import ServiceUnavailable
from skysift.pipeline import search_version
from skysift.priors import PIXEL_ARCSEC
from skysift.sources import PARSERS
from skysift.store.blob import blob_from_url
from skysift.store.db import PERIODOGRAM_URL, QueryRow, SignalRow, Store, TestRow, make_job_key
from skysift.ui import explain
from tests.synthetic import inject_model, make_lightcurve
from tests.unit.ui_seed import (
    STAGE1_TARGET,
    STAGE2_TARGET,
    VERSION,
    add_older_version,
    seed,
    seed_dossier,
    stage2_pixel_metrics,
)

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
RESPONSES = {"CONTAINS": CONE_CSV, "WHERE g.source_id": GAIA_CSV, "gaiaedr3_distance": DIST_CSV,
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


def _make_env(settings, monkeypatch, responses=None, calibration=True, calibration_version=VERSION):
    """The seed with the stage-2 target's raw file and periodogram stored, the synthetic curve behind the parser and
    220 calibration rows under ``calibration_version``: by default the seed signals' own search version, so their
    FAP is read against a calibration of their search."""
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
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r) + "\n" for r in _calibration_rows(calibration_version)),
                        encoding="utf-8")
    return SimpleNamespace(store=store, blob=blob, settings=settings, ids=ids, fetch=fake_fetch(responses),
                           calibration_path=path, lc=lc, raw_sha=sha)


@pytest.fixture
def env(settings, monkeypatch):
    return _make_env(settings, monkeypatch)


@pytest.fixture
def env_stage1(env):
    return env


@pytest.fixture
def env_no_gaia(settings, monkeypatch):
    return _make_env(settings, monkeypatch, {**RESPONSES, "WHERE g.source_id": GAIA_HEADER})


SEASON_FRAMES = {0: ((7, 9), (2.0, 3.0), (4.5, 3.6)), 2: ((9, 9), (3.0, 4.0), (5.5, 4.6))}   # shape, target, nbr


def _pixels_npz(frames=SEASON_FRAMES, drop: str | None = None) -> bytes:
    """rung 5's pixels.npz: ``seasons`` and, per season, its own frame (union grids differ in shape), the first pixel
    of season 0 invalid; ``drop`` leaves one key out."""
    rng = np.random.default_rng(9)
    arrays = {"seasons": np.array(sorted(frames))}
    for s, (shape, pos_t, pos_n) in frames.items():
        valid = np.ones(shape, bool)
        if s == 0:
            valid[0, 0] = False
        arrays.update({f"diff_{s}": rng.normal(0, 1, shape), f"var_{s}": np.full(shape, 0.25),
                       f"direct_{s}": rng.uniform(10, 20, shape), f"valid_{s}": valid,
                       f"pos_target_{s}": np.array(pos_t), f"pos_neighbour_{s}": np.array(pos_n)})
    arrays.pop(drop, None)
    buf = io.BytesIO()
    np.savez_compressed(buf, **arrays)
    return buf.getvalue()


def _store_pixels(env, data: bytes, name_it: bool = True) -> str:
    """Store ``data`` as the stage-2 signal's pixels.npz and, as rung 5 does, name its sha256 in the rung 5 row."""
    sid = env.ids["stage2"]
    sha, loc = env.blob.put(data, f"pixels_{sid}.npz")
    env.store.add_artifact(sha, len(data), f"skysift://pixels/{sid}", loc, STAGE2_TARGET, pinned=True, kind="pixels")
    if name_it:
        with env.store.session() as s:
            row = s.scalars(select(TestRow).where(TestRow.signal_id == sid, TestRow.rung == "rung5_pixels")
                            .order_by(TestRow.id.desc())).first()
            row.metrics = {**(row.metrics or {}), "pixels_sha256": sha}
            s.commit()
    return sha


@pytest.fixture
def env_with_npz(env):
    _store_pixels(env, _pixels_npz())
    return env


def _data(rep) -> dict:
    return json.loads(rep.data_path.read_text(encoding="utf-8"))


def test_pick_signal_prefers_stage_two_then_approved_then_sde(settings):
    store = Store(settings.database_url); store.init_schema()
    ids = seed(store)
    assert STATUS_ORDER == ("candidate_stage2", "approved", "candidate_stage1", "undetermined", "pending", "rejected",
                            "superseded")
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
    with pytest.raises(NoSignal):
        pick_signal(store, "kic-000000001")
    with pytest.raises(NoSignal):
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
    assert p["pos_target"] == [3.0, 4.0]
    assert p["quarters"] == []      # the seeded metrics name no quarters; none are worked out from quarter mod 4
    assert p["valid"][0][0] is True and data["pixels"][0]["direct"][0][0] is None


def test_pixels_section_reads_the_npz_the_latest_rung5_row_names(env_with_npz):
    """A newer npz at the same URL that no rung 5 row names (its step's rows never landed) is not drawn, and a row
    that names no npz draws none."""
    env = env_with_npz
    with np.load(io.BytesIO(_pixels_npz())) as z:
        arrays = {k: z[k] for k in z.files}
    arrays["seasons"] = np.array([0])
    buf = io.BytesIO()
    np.savez_compressed(buf, **arrays)
    _store_pixels(env, buf.getvalue(), name_it=False)                   # newer, but unnamed
    data = _data(generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch))
    assert [p["season"] for p in data["pixels"]] == [0, 2]
    with env.store.session() as s:
        row = s.scalars(select(TestRow).where(TestRow.signal_id == env.ids["stage2"], TestRow.rung == "rung5_pixels")
                        .order_by(TestRow.id.desc())).first()
        row.metrics = {**row.metrics, "pixels_sha256": None}
        s.commit()
    data = _data(generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch, force=True))
    assert data["pixels"] is None and "pixels" not in data["section_errors"]


def test_pixel_seasons_keep_their_own_frame_shapes(env_with_npz):
    env = env_with_npz
    data = _data(generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch))
    assert "pixels" not in data["section_errors"]
    for p in data["pixels"]:
        shape, pos_t, pos_n = SEASON_FRAMES[p["season"]]
        assert p["shape"] == list(shape) and p["pos_target"] == list(pos_t) and p["pos_neighbour"] == list(pos_n)
        for key in ("snr", "direct", "valid"):
            assert len(p[key]) == shape[0] and all(len(row) == shape[1] for row in p[key])
    assert [p["shape"] for p in data["pixels"]] == [[7, 9], [9, 9]]


@pytest.mark.parametrize("drop", ["seasons", "var_2", "pos_neighbour_0"])
def test_a_missing_pixel_key_becomes_a_section_error(env, drop):
    _store_pixels(env, _pixels_npz(drop=drop))
    data = _data(generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch))
    assert data["pixels"] is None and data["section_errors"]["pixels"]["error"]


def test_a_mis_shaped_pixel_frame_becomes_a_section_error(env):
    with np.load(io.BytesIO(_pixels_npz())) as z:
        arrays = {k: z[k] for k in z.files}
    arrays["var_0"] = np.full((9, 9), 0.25)                        # not the 7x9 frame of season 0
    buf = io.BytesIO()
    np.savez_compressed(buf, **arrays)
    _store_pixels(env, buf.getvalue())
    out = _data(generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch))
    assert out["pixels"] is None and "season 0" in out["section_errors"]["pixels"]["error"]


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
    assert forced["generated"] and cli_env.store.dossier(STAGE2_TARGET)["export_path"] == ""   # the old export is stale


def test_cli_dossier_fails_plainly_for_a_target_without_signals(cli_env):
    r = CliRunner().invoke(app, ["dossier", "--target", "kic-000000001"])
    assert r.exit_code == 1 and "no signal" in r.output and "Traceback" not in r.output


def test_cli_dossier_exits_2_when_the_light_curve_was_released(cli_env):
    cli_env.store.mark_artifact_deleted(cli_env.raw_sha)
    r = CliRunner().invoke(app, ["dossier", "--target", STAGE2_TARGET])
    assert r.exit_code == 2 and "--retention keep" in r.output and "Traceback" not in r.output


# ---------------------------------------------------------------------------------------------------- fix round 1

def test_fap_rows_of_another_search_read_as_an_older_calibration(settings, monkeypatch):
    env = _make_env(settings, monkeypatch, calibration_version="0+older")
    data = _data(generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch))
    fap = data["fap"]
    assert fap["words"] == "calibration is for an older search" and not fap["version_matches"]
    assert fap["fap"] == pytest.approx(1 / 441) and fap["versions"] == ["0+older"]       # still printed in Expert
    assert fap["signal_search_version"] == VERSION == fap["calibration_version"]


def test_calibration_version_follows_the_signal_search(settings):
    th = settings.thresholds
    today = {"detector": "box_dip", "detector_version": search_version(get_detector("box_dip"), th)}
    assert calibration_version_for(today, th) == cal.search_version(cal.calibration_detector(), th)
    assert calibration_version_for({"detector": "box_dip", "detector_version": VERSION}, th) == VERSION
    assert calibration_version_for({"detector": "gone", "detector_version": "9+x"}, th) == "9+x"


def test_stored_fpp_inputs_recompute_the_stored_value(env):
    data = _data(generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch))
    inputs = data["fpp"]["inputs"]
    assert len(inputs["neighbours"]) == 2 and isinstance(inputs["neighbours"][0], dict)
    assert compute(FppInputs(**inputs)).value == pytest.approx(data["fpp"]["value"], rel=1e-6)
    assert data["fpp"]["signed_off"] is True


def test_a_released_light_curve_is_not_fetched_again(env):
    env.store.mark_artifact_deleted(env.raw_sha)
    with pytest.raises(LightCurveReleased, match=r"kic-006146676 .*--retention keep"):
        generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch)
    assert env.store.dossier(STAGE2_TARGET) is None


def test_pick_signal_ranks_a_reviewer_rejection_last(settings):
    store = Store(settings.database_url); store.init_schema()
    ids = seed(store)
    stage1 = add_older_version(store, STAGE2_TARGET)          # stage 1, SDE 30, beside the stage-2 signal
    assert pick_signal(store, STAGE2_TARGET)["id"] == ids["stage2"]
    store.decide(ids["stage2"], "reject", "a neighbour after all")
    assert pick_signal(store, STAGE2_TARGET)["id"] == stage1


def test_status_label_comes_from_the_glossary():
    glossary = dict(explain.STATUS_GLOSSARY)
    approved = explain.verdict({"final_status": "candidate_stage1"}, [], {"status": "approved"},
                               [{"decision": "approve"}])
    assert status_label(approved) == ("Approved", glossary["Approved / Rejected by reviewer / Needs data"])
    stage2 = explain.verdict({"final_status": "candidate_stage2"}, [], None, [])
    assert status_label(stage2) == ("Candidate, stage 2", glossary["Candidate, stage 2"])
    needs = explain.verdict({"final_status": "undetermined"}, [], {"status": "review"}, [{"decision": "needs_data"}])
    assert status_label(needs)[0] == "Needs data"


def test_an_unreadable_pixels_file_becomes_a_section_error(env):
    sid = env.ids["stage2"]
    bad = b"not an npz archive"
    assert sid == env.ids["stage2"]
    _store_pixels(env, bad)
    data = _data(generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch))
    assert data["pixels"] is None and data["section_errors"]["pixels"]["error"]
    hit = [c for c in data["caveats"] if c["title"] == "The pixels section could not be built"]
    assert hit and hit[0]["text"].startswith("The pixels section could not be built: ")


def test_an_unchanged_dossier_reports_its_stored_fap_and_a_version_bump_regenerates(env, monkeypatch):
    seeded = seed_dossier(env.store, env.settings.data_dir, target_id=STAGE2_TARGET)
    env.calibration_path.unlink()                      # the stored FAP is reported, not the file's
    rep = generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch)
    assert not rep.generated and rep.fap.words() == "1 in 1,700" == seeded["fap"]["words"]
    assert rep.fpp.value == pytest.approx(compute(FppInputs(**seeded["fpp"]["inputs"])).value)
    monkeypatch.setattr(dossier_module, "DOSSIER_VERSION", "3")
    again = generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch)
    assert again.generated and "format changed" in again.stale_reason
    assert env.store.dossier(STAGE2_TARGET)["dossier_version"] == "3"


def test_rank_key_orders_stage_two_approved_stage_one_and_rejected_last():
    def sig(i, status, sde, cand=None):
        return {"id": i, "final_status": status, "sde": sde, "candidate_status": cand}
    rows = [(sig(1, "candidate_stage1", 40.0), None), (sig(2, "candidate_stage1", 12.0, "approved"), "approve"),
            (sig(3, "candidate_stage2", 9.0), None), (sig(4, "candidate_stage2", 50.0, "rejected"), "reject"),
            (sig(5, "candidate_stage1", 41.0), "approve")]
    order = [s["id"] for s, d in sorted(rows, key=lambda r: rank_key(*r))]
    assert order == [3, 5, 2, 1, 4]            # stage 2, approved by SDE, stage 1, a rejection last whatever its SDE
    assert rank_key(sig(6, "candidate_stage2", 9.0, "approved"), "approve")[1] == 0
    # an undetermined signal (the pixel stage could not decide) outranks every closed sibling whatever the SDE, so
    # the Dossiers tile and pick_signal name the same signal (review of 47823bc)
    closed = [(sig(7, "undetermined", 20.0), None), (sig(8, "superseded", 50.0), None), (sig(9, "rejected", 60.0), None),
              (sig(10, "pending", 70.0), None)]
    assert [s["id"] for s, d in sorted(closed, key=lambda r: rank_key(*r))] == [7, 10, 9, 8]
    assert rank_key(sig(1, "candidate_stage1", 1.0), None) < rank_key(sig(7, "undetermined", 99.0), None)


def _with_seasons(data: dict, seasons: list[dict]) -> dict:
    return {**data, "tests": [{"rung": "rung5_pixels", "status": "pass", "metrics": {"seasons": seasons}}]}


@pytest.mark.parametrize(("seasons", "close"), [
    ([{"season": 0, "voting": True, "separation_sigma": 3.2}], True),
    ([{"season": 0, "voting": True, "separation_sigma": -3.4}], True),
    ([{"season": 0, "voting": True, "separation_sigma": 4.1}], False),
    ([{"season": 0, "voting": False, "separation_sigma": 3.2}], False),
    ([{"season": 0, "separation_sigma": 3.2}], False),                  # a vote not recorded is not a vote
])
def test_a_voting_season_near_three_sigma_gets_the_monte_carlo_caveat(env, seasons, close):
    rep = generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch)
    found = [c for c in caveats(_with_seasons(_data(rep), seasons)) if c.get("key") == "close_call"]
    assert bool(found) is close
    if close:
        assert found[0]["text"] == CLOSE_CALL
        assert "200 Monte Carlo draws" in found[0]["text"] and "—" not in found[0]["text"]


def _partial(season: int, bias: float, *, target: int = 6, neighbour: int = 9, voting: bool = True) -> dict:
    return {"season": season, "voting": voting, "separation_sigma": 8.0, "box_partial": True,
            "box_pixels_target": target, "box_pixels_neighbour": neighbour, "edge_bias_pix": bias}


def _complete(season: int) -> dict:
    return {"season": season, "voting": True, "separation_sigma": 8.0, "box_partial": False,
            "box_pixels_target": 9, "box_pixels_neighbour": 9, "edge_bias_pix": 0.0}


def test_partial_box_sentence_names_the_seasons_and_the_largest_allowance():
    seasons = [_partial(0, 0.10), _complete(1), _partial(2, 0.07), _partial(3, 0.30, voting=False)]
    assert dossier_module.partial_box_sentence(seasons) == (
        "In seasons 0 and 2 this star's 3 x 3 box is cut by the stamp edge; the centroid there is measured on the "
        "pixels present and the shift the cut would give a point source (0.10 px at most) is added to its error.")
    assert dossier_module.partial_box_sentence([_complete(1)]) == ""
    assert dossier_module.partial_box_sentence([{"season": 0, "voting": True}]) == ""       # stored before the keys
    assert dossier_module.partial_box_seasons(seasons) == [seasons[0], seasons[2]]


def test_partial_box_sentence_names_the_star_and_lists_three_seasons():
    assert dossier_module.partial_box_sentence([_partial(0, 0.1, target=9, neighbour=4)]).startswith(
        "In season 0 the neighbour's 3 x 3 box is cut by the stamp edge;")
    both = dossier_module.partial_box_sentence([_partial(0, 0.1), _partial(2, 0.1, target=9, neighbour=3),
                                         _partial(3, 0.1, target=5, neighbour=5)])
    assert both.startswith("In seasons 0, 2 and 3 both stars' 3 x 3 boxes are cut by the stamp edge;")
    assert chr(0x2014) not in both


def test_caveats_list_a_partial_box_after_the_close_call(env):
    rep = generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch)
    data = _with_seasons(_data(rep), [{**_partial(0, 0.10), "separation_sigma": 3.2}])
    found = [c for c in caveats(data) if c.get("key") == "partial_box"]
    assert len(found) == 1 and found[0]["title"] == "A season's box is cut by the stamp edge"
    assert found[0]["text"] == dossier_module.partial_box_sentence(data["tests"][0]["metrics"]["seasons"])
    keys = [c.get("key") for c in caveats(data)]
    assert keys.index("close_call") + 1 == keys.index("partial_box")
    plain = [c.get("key") for c in caveats(_with_seasons(_data(rep), [_partial(0, 0.10)]))]
    assert "partial_box" in plain and "close_call" not in plain
    assert "partial_box" not in [c.get("key") for c in caveats(_with_seasons(_data(rep), [_complete(0)]))]


REPO = Path(__file__).resolve().parents[2]
HAND_BUILT =REPO / "docs" / "candidates" / "kic-006146676" / "dossier_data.json"
GENERATED = REPO / "tests" / "golden" / "kic-006146676-generated.json"     # tools/dossier_compare.py --write-golden


def test_hand_built_values_agree_within_errors():
    """The real run of 21 October 2026 (docs/superpowers/notes/2026-10-21-dossier-comparison.md) against the hand-built
    KIC 6146676 study: Rp, a and Teq within the study's quoted errors, the per-season centroid within 0.1 pixel of the
    study's, the habitable-zone flags, and the stored FPP reproduced from its stored inputs."""
    ref = json.loads(HAND_BUILT.read_text(encoding="utf-8"))
    gen = json.loads(GENERATED.read_text(encoding="utf-8"))
    pb = ref["context"]["summary"]["planet_baseline"]
    planet, teq = gen["planet"], pb["Teq_K_A0.3_full_redistribution"]
    assert abs(planet["rp_rearth"]["median"] - pb["Rp_from_k_Rearth"]["median"]) <= pb["Rp_from_k_Rearth"]["plus"]
    assert abs(planet["a_au"]["median"] - pb["a_AU"]["median"]) <= pb["a_AU"]["plus"]
    assert abs(planet["teq_k"]["median"] - teq["median"]) <= teq["plus"]
    assert planet["in_habitable_zone"] is False and planet["in_optimistic_habitable_zone"] is True
    to_candidate = ref["pixels"]["summary"]["seasons_snrbox_centroid_dist_to_candidate_pix"]
    rung5 = next(t for t in gen["tests"] if t["rung"] == "rung5_pixels")
    assert rung5["status"] == "pass" and rung5["metrics"]["tpf_missing"] == []
    for s in rung5["metrics"]["seasons"]:
        assert abs(s["dist_target_pix"] - to_candidate[str(s["season"])]) <= 0.1
    # the FPP band of spec 4.2 (1 to 60 percent) did not survive Task 2: with the spec's priors the localized BEB term
    # vanishes and the on-target EB term is killed by the secondary and odd/even limits, so KIC 6146676 reads
    # "under 1 percent"; the number is a sign-off item (ledger), and this test pins reproducibility, not the band
    assert gen["fpp"]["value"] == pytest.approx(compute(FppInputs(**gen["fpp"]["inputs"])).value, rel=1e-6)
    assert gen["fpp"]["signed_off"] is True
    # the stored centroid error is the one the code computes from the stored rung 5 seasons
    est, inputs = centroid_estimate(rung5["metrics"]), gen["fpp"]["inputs"]
    assert (est.sigma_stat, est.sigma_scatter, est.sigma) == pytest.approx(
        (inputs["sigma_c_stat_arcsec"], inputs["sigma_c_scatter_arcsec"], inputs["centroid_sigma_arcsec"]), rel=1e-5)
    assert est.source == inputs["sigma_c_source"]


def test_the_compare_tool_reads_the_golden_block(monkeypatch, tmp_path, capsys):
    monkeypatch.syspath_prepend(str(REPO))
    monkeypatch.chdir(tmp_path)
    from tools import dossier_compare
    argv = ["--data", str(GENERATED), "--reference", str(HAND_BUILT)]
    assert dossier_compare.main(argv) == 0
    printed = capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []                                  # without --out it prints and writes nothing
    rungs = json.loads(GENERATED.read_text(encoding="utf-8"))["provenance"]["ladder_versions"]
    assert len(rungs) == 6 and all(f"| {rung} | " in printed for rung in rungs)
    out = tmp_path / "c.md"
    assert dossier_compare.main([*argv, "--out", str(out)]) == 0
    assert capsys.readouterr().out == "" and out.read_text(encoding="utf-8") + "\n" == printed


# ---------------------------------------------------------------------------------------------------- Task 7 fix round

def _season(season: int, dist: float, sigma: float, voting: bool = True) -> dict:
    return {"season": season, "dist_target_pix": dist, "centroid_sigma_pix": sigma, "voting": voting,
            "nearer": "target" if voting else "unknown"}


def test_the_centroid_estimate_uses_the_voting_seasons_and_the_larger_error():
    est = centroid_estimate({"seasons": [_season(0, 0.2, 0.05), _season(1, 0.2, 0.05), _season(3, 2.0, 0.01, False)]})
    assert est.n_seasons == 2 and est.offset == pytest.approx(0.2 * PIXEL_ARCSEC)     # season 3 would pull it to 1.9
    assert est.source == "statistical" and est.sigma_scatter == pytest.approx(0.0, abs=1e-12)
    assert est.sigma == est.sigma_stat == pytest.approx(0.05 / math.sqrt(2) * PIXEL_ARCSEC)
    spread = centroid_estimate({"seasons": [_season(0, 0.1, 0.01), _season(1, 0.5, 0.01)]})
    # mean 0.3 px, sample scatter (ddof 1) 0.283 px, over sqrt(2): 0.2 px against a statistical 0.0071 px
    assert spread.source == "scatter" and spread.sigma == pytest.approx(0.2 * PIXEL_ARCSEC)
    assert spread.sigma_stat == pytest.approx(0.01 / math.sqrt(2) * PIXEL_ARCSEC)
    assert centroid_estimate({"seasons": [_season(0, 0.2, 0.05)]}).sigma_scatter == 0.0     # one season: no scatter
    assert centroid_estimate({"seasons": [_season(3, 0.2, 0.05, voting=False)]}) is None


def test_the_fpp_inputs_record_how_the_centroid_error_was_chosen(env):
    data = _data(generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch))
    inputs = data["fpp"]["inputs"]
    assert inputs["sigma_c_source"] in ("statistical", "scatter")
    assert inputs["centroid_sigma_arcsec"] == pytest.approx(max(inputs["sigma_c_stat_arcsec"],
                                                                inputs["sigma_c_scatter_arcsec"]))
    assert compute(FppInputs(**inputs)).value == pytest.approx(data["fpp"]["value"], rel=1e-6)


def test_provenance_gives_the_ladder_of_each_rung(env):
    data = _data(generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch))
    prov = data["provenance"]
    # the seed sweeps under ladder "1" and its pixel recheck row is ladder "4"
    assert prov["ladder_version"] == "4" and prov["ladder_versions"]["rung5_pixels"] == "4"
    assert prov["ladder_versions"]["rung2_shape"] == "1"
    assert env.store.dossier(STAGE2_TARGET)["ladder_version"] == "4"


def test_the_fap_block_splits_the_counts_by_null_kind(env):
    fap = _data(generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch))["fap"]
    assert set(fap["k_by_kind"]) == set(fap["n_by_kind"]) == {"inverted", "scrambled"}
    assert sum(fap["k_by_kind"].values()) == fap["k"] and fap["n_by_kind"] == {"inverted": 220, "scrambled": 220}


def _refusing(fetch):
    """``fetch`` with the Gaia star query refused as the archive refused the old one (HTTP 400)."""
    def refuse(url, *, params=None, service, store=None, target_id=None, **kw):
        if "WHERE g.source_id" in (params or {}).get("query", ""):
            raise ServiceUnavailable(f"{service} {url} -> HTTP 400")
        return fetch(url, params=params, service=service, store=store, target_id=target_id, **kw)
    return refuse


def test_a_refused_gaia_query_is_loud_and_gets_its_own_caveat(env, caplog):
    lines = []
    with caplog.at_level(logging.WARNING, logger="skysift.dossier"):
        rep = generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=_refusing(env.fetch), log=lines.append)
    assert rep.gaia_errors and rep.gaia_errors[0].startswith("star row: gaia ") and "HTTP 400" in rep.gaia_errors[0]
    assert any("HTTP 400" in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)
    assert any(line.startswith("Gaia query failed: star row:") for line in lines)
    data = _data(rep)
    refused = [c for c in data["caveats"] if c.get("key") == "gaia_refused"]
    assert len(refused) == 1 and refused[0]["text"].startswith(
        "A Gaia query was refused (HTTP 400): the star block comes from the fallback route.")
    assert not any("when the archive answers" in c["text"] for c in data["caveats"])
    assert gaia_refused("neighbour cone: gaia x -> HTTP 503") is None and gaia_refused(rep.gaia_errors[0]) == "400"
    assert rep.as_dict()["gaia_errors"] == rep.gaia_errors


def test_cli_dossier_exits_3_when_a_gaia_query_failed(cli_env, monkeypatch):
    monkeypatch.setattr(net, "fetch", _refusing(cli_env.fetch))
    r = CliRunner().invoke(app, ["dossier", "--target", STAGE2_TARGET])
    assert r.exit_code == 3 and "Gaia query failed: star row:" in r.output and "Traceback" not in r.output
    assert cli_env.store.dossier(STAGE2_TARGET)["data_path"]                # the dossier is still written
    again = CliRunner().invoke(app, ["dossier", "--target", STAGE2_TARGET])   # current, but still incomplete
    assert again.exit_code == 3 and "Gaia query failed: star row:" in again.output


def test_pixel_seasons_are_the_archive_seasons_rung_5_wrote(env):
    frames = {0: ((7, 9), (2.0, 3.0), (4.5, 3.6)), 3: ((5, 7), (0.7, 3.0), (3.5, 2.2))}
    sha = _store_pixels(env, _pixels_npz(frames))
    env.store.add_test(env.ids["stage2"], TestResult(rung="rung5_pixels", status="pass", metrics={
        **stage2_pixel_metrics(quarters=True), "pixels_sha256": sha}), 1.0)
    pixels = _data(generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch))["pixels"]
    by_season = {p["season"]: p for p in pixels}
    assert set(by_season) == {0, 3}
    assert by_season[0]["quarters"] == [2, 6, 10, 14] and by_season[3]["quarters"] == [1, 5, 9, 13]   # Q1, Q5: season 3
    assert by_season[3]["nearer"] == "unknown"                     # the seeded archive season 3 does not vote


# ---------------------------------------------------------------------------------------------------- final review

def _refusing_cone(fetch, status: int = 503):
    """``fetch`` with the Gaia neighbour cone failing (an outage by default)."""
    def refuse(url, *, params=None, service, store=None, target_id=None, **kw):
        if "CONTAINS(" in (params or {}).get("query", ""):
            raise ServiceUnavailable(f"{service} {url} -> HTTP {status}")
        return fetch(url, params=params, service=service, store=store, target_id=target_id, **kw)
    return refuse


def test_a_failed_neighbour_cone_leaves_the_fpp_not_computed(env):
    known = generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch)
    assert known.fpp.neighbours_known and _data(known)["fpp"]["value"] is not None
    rep = generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=_refusing_cone(env.fetch), force=True)
    data, row = _data(rep), env.store.dossier(STAGE2_TARGET)
    assert rep.gaia_errors and rep.gaia_errors[0].startswith("neighbour cone:")
    assert data["fpp"]["inputs"]["neighbours_known"] is False and data["fpp"]["value"] is None
    assert data["fpp"]["words"] == "not computed (no neighbour data)" and row["fpp"] is None
    assert row["fpp_detail"]["label"] == "False positive (no neighbour data)"
    assert data["fpp"]["value_without_beb"] is not None                     # the other terms, for the factor table
    assert any(c.get("key") == "no_neighbours" for c in data["caveats"])
    assert rep.as_dict()["fpp"]["value"] is None
    # the stored inputs recompute to the same state, so the no-op report says the same
    again = generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch)
    assert not again.generated and again.fpp.words() == "not computed (no neighbour data)"


def test_no_target_g_leaves_the_fpp_not_computed(env):
    rep = generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch)
    signal = pick_signal(env.store, STAGE2_TARGET)
    tests = env.store.latest_tests(signal["id"])
    star, planet = SimpleNamespace(teff=None, radius=None, mass=None), SimpleNamespace(rp_rearth=None)
    inputs = dossier_module.fpp_inputs(signal, tests, star, planet, [], None)
    assert inputs.neighbours_known is False and compute(inputs).reported_value is None
    assert dossier_module.fpp_inputs(signal, tests, star, planet, [], 16.2).neighbours_known is True
    assert rep.fpp.neighbours_known


def test_the_pack_texts_say_the_fpp_is_not_computed_without_neighbours(env):
    from skysift import pack
    from skysift.zenodo import probability_sentences
    rep = generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=_refusing_cone(env.fetch))
    data = _data(rep)
    _fap, fpp_text = probability_sentences(data)
    assert fpp_text.startswith("False-positive probability: not computed, since the Gaia neighbour list")
    assert "percent" not in fpp_text
    assert "the false-positive probability is not computed" in pack.abstract_text(data)


def test_the_null_calibration_size_is_in_the_fingerprint(env):
    tests = [{"id": 3}, {"id": 1}]
    assert fingerprint(tests, "candidate_stage2", None) == fingerprint(tests, "candidate_stage2", None, 0)
    assert fingerprint(tests, "candidate_stage2", None, 0) != fingerprint(tests, "candidate_stage2", None, 120)
    first = generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch)
    assert first.generated
    assert not generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch).generated
    path = cal.default_path(env.settings.data_dir, "kbonus_bkg")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:                       # a null search arrives
        fh.write(json.dumps({"target_id": "kic-0", "search_version": "x", "sde_inverted": 1.0,
                             "sde_scrambled": 1.0}) + "\n")
    assert dossier_module.calibration_rows(env.settings.data_dir, "kbonus_bkg") >= 1
    again = generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch)
    assert again.generated
    assert again.stale_reason == "the test rows, the reviewer decision or the null calibration changed"


def test_a_regeneration_clears_the_pack_link_and_its_steps(env):
    from datetime import datetime
    generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch)
    env.store.set_pack(STAGE2_TARGET, "packs/x", datetime(2026, 10, 21, 10, 0))
    env.store.set_pack_steps(STAGE2_TARGET, {"1": True})
    generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch, force=True)
    row = env.store.dossier(STAGE2_TARGET)
    assert row["pack_path"] == "" and row["pack_built"] is None and row["pack_steps"] == {}


def test_public_paths_cuts_a_path_whole_or_inside_a_sentence(tmp_path):
    root = tmp_path.resolve()
    out = dossier_module.public_paths({
        "whole": str(root / "calibration" / "kbonus_bkg.jsonl"),
        "quoted": f"[Errno 2] No such file or directory: '{root / 'my blobs' / 'a.npz'}'",
        "bare": r"could not open C:\Users\someone\data\x.fits while reading",
        "posix": "read /home/someone/data/x.fits failed",
        "elsewhere": "D:/other/place/file.csv",
        "kept": ["https://gea.esac.esa.int/tap-server/tap/sync", "on the target in 3/3 seasons", "ppm / sigma",
                 "and/or"]}, root)
    assert out["whole"] == "calibration/kbonus_bkg.jsonl"
    assert out["quoted"] == "[Errno 2] No such file or directory: 'my blobs/a.npz'"
    assert out["bare"] == "could not open x.fits while reading" and out["posix"] == "read x.fits failed"
    assert out["elsewhere"] == "file.csv"
    assert out["kept"] == ["https://gea.esac.esa.int/tap-server/tap/sync", "on the target in 3/3 seasons",
                           "ppm / sigma", "and/or"]


def test_the_export_and_the_stored_provenance_name_no_local_folder(env):
    rep = generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch)
    data = _data(rep)
    root = Path(env.settings.data_dir).resolve()
    calib = data["provenance"]["calibration_file"]
    assert calib is None or not Path(calib).is_absolute()
    data["section_errors"] = {"pixels": {"error": f"cannot read '{root / 'blobs' / 'z.npz'}'"}}
    text = export_html(data, data_dir=root)
    assert "cannot read 'blobs\\/z.npz'" in text and root.parent.name not in text


def test_one_voting_season_keeps_the_no_pixel_rule(env):
    # rung 5 passes on two voting seasons at least; the FPP takes its centroid only on the same footing
    signal = pick_signal(env.store, STAGE2_TARGET)
    tests = [t for t in env.store.latest_tests(signal["id"]) if t["rung"] != "rung5_pixels"]
    star, planet = SimpleNamespace(teff=None, radius=None, mass=None), SimpleNamespace(rp_rearth=None)
    one = tests + [{"rung": "rung5_pixels", "status": "undetermined",
                    "metrics": {"seasons": [_season(0, 0.2, 0.05), _season(1, 0.3, 0.05, voting=False)]}}]
    two = tests + [{"rung": "rung5_pixels", "status": "pass",
                    "metrics": {"seasons": [_season(0, 0.2, 0.05), _season(1, 0.25, 0.05)]}}]
    assert dossier_module.MIN_VOTING_SEASONS == 2
    assert dossier_module.fpp_inputs(signal, one, star, planet, [], 16.2).centroid_sigma_arcsec is None
    assert dossier_module.fpp_inputs(signal, two, star, planet, [], 16.2).centroid_sigma_arcsec is not None
