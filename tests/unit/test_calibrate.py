import json
import pickle

import numpy as np
import pytest
from typer.testing import CliRunner

from skysift import calibrate
from skysift.cli import app
from skysift.config import load_settings
from skysift.store import db
from tests.synthetic import inject_transit, make_lightcurve
from tests.unit.test_pipeline import ScriptedSource, _env, _tgt

ROW_KEYS = {"target_id", "search_version", "n_points", "real", "inverted", "scrambled", "real_top", "seconds"}


def _lines(path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def test_calibrate_resumes_and_summarises(settings, monkeypatch):
    # 60 d curves and a 2 to 15 d window: the search can answer (pmax is a third of the baseline) and stays fast
    s, store, blob = _env(monkeypatch, settings, THRESHOLDS__MIN_PERIOD_DAYS="2", THRESHOLDS__MAX_PERIOD_DAYS="15")
    # enumerated lowest priority first (3, 4, 5), so "the first N enumerated" would pick the wrong two
    targets = [_tgt("c", psffrac=0.3, crowdsap=0.3), _tgt("b", crowdsap=0.3), _tgt("a")]
    # t0 134.0, not 133.0: the data start at 133.0 is masked for 1.5 d, and with that epoch gone the 15 d alias of
    # this short curve outranks 5 d (SDE 31.6 against 23.3; 37.1 against 27.5 at 5 d with t0 134.0)
    lcs = {"a": inject_transit(make_lightcurve(n_days=60, seed=1, noise_ppm=800), 5.0, 134.0, 0.15, 4000),
           "b": make_lightcurve(n_days=60, seed=2), "c": make_lightcurve(n_days=60, seed=3)}
    fetched = []
    src = ScriptedSource(targets, lcs, store, blob, on_fetch=lambda t: fetched.append(t.target_id))
    # "c" already has a raw file in the blob (a kept file from an earlier sweep): it is parsed, not fetched. The test's
    # "raw file" is a pickle this test writes itself, so pickle.loads stands in for the FITS parser
    data = pickle.dumps(lcs["c"])
    sha, loc = blob.put(data, "c.fits")
    store.add_artifact(sha, len(data), "https://fake/c", loc, "c")
    out = s.data_dir / "calibration" / "fake.jsonl"

    first = calibrate.run(src, store, blob, s, filter={}, limit=2, out=out, parse=pickle.loads)
    assert first["written"] == 2 and first["skipped"] == 0 and first["errors"] == 0 and fetched == ["a", "b"]
    rows1 = {r["target_id"]: r for r in _lines(out)}
    assert set(rows1) == {"a", "b"}
    with store.session() as ss:                         # the whole source was recorded, as a sweep records it
        assert {r.target_id for r in ss.query(db.TargetRow)} == {"a", "b", "c"}

    second = calibrate.run(src, store, blob, s, filter={}, limit=3, out=out, parse=pickle.loads)
    assert second["written"] == 1 and second["skipped"] == 2 and second["from_cache"] == 1
    assert fetched == ["a", "b"]                        # nothing fetched again, "c" came from the blob
    rows = _lines(out)
    assert len(rows) == 3 and {r["target_id"] for r in rows} == {"a", "b", "c"}
    by_id = {r["target_id"]: r for r in rows}
    assert by_id["a"] == rows1["a"] and by_id["b"] == rows1["b"]         # not recomputed: seconds and all unchanged

    det = calibrate.calibration_detector()
    assert det.mask_iterations == 0 and det.alias_resolution == 0
    version = calibrate.search_version(det, s.thresholds)
    for r in rows:
        assert set(r) == ROW_KEYS and r["search_version"] == version and r["n_points"] > 2000
        for kind in calibrate.KINDS:
            assert 1 <= len(r[kind]) <= 3 and r[kind] == sorted(r[kind], reverse=True)
    # the injected 5 d transit dominates the real search; inverted and scrambled flux carry no transit
    top = by_id["a"]["real_top"]
    assert set(top) == {"period", "duration", "depth_ppm", "sde", "snr"} and abs(top["period"] - 5.0) < 0.05
    assert top["sde"] == by_id["a"]["real"][0] > 15 > max(by_id["a"]["inverted"][0], by_id["a"]["scrambled"][0])

    summary = calibrate.summarise(rows, sde_min=s.thresholds.sde_min)
    json.dumps(summary, allow_nan=False)                # strict JSON
    assert set(summary) == {"search_versions", "rows", "empty", "sde_min", "kinds", "real_at_or_above_sde_min"}
    assert summary["search_versions"] == [version] and summary["rows"] == 3 and summary["empty"] == 0
    for kind in calibrate.KINDS:
        k = summary["kinds"][kind]
        tops = [r[kind][0] for r in rows]
        assert k["n"] == 3 and k["max"] == max(tops) and k["median"] == pytest.approx(float(np.median(tops)))
        assert k["at_or_above"] == {str(level): sum(t >= level for t in tops) for level in calibrate.SDE_LEVELS}
    assert summary["real_at_or_above_sde_min"]["count"] >= 1          # the injected transit


def _row(tid, real, inverted, scrambled, version="1+aaaa0000", n_points=3000):
    return {"target_id": tid, "search_version": version, "n_points": n_points, "real": real, "inverted": inverted,
            "scrambled": scrambled, "real_top": {"period": 5.0, "duration": 0.1, "depth_ppm": 900.0,
                                                 "sde": real[0], "snr": 9.0} if real else None, "seconds": 1.0}


ROWS = [_row("a", [16.0, 9.0, 8.0], [9.5, 8.0, 7.0], [7.2, 7.0, 6.9]),
        _row("b", [10.5, 9.0, 8.5], [12.0, 9.0, 8.0], [8.0, 7.5, 7.0]),
        _row("c", [7.5, 7.0, 6.5], [7.0, 6.0, 5.0], [9.0, 8.0, 7.0]),
        _row("d", [], [], [], n_points=300)]           # too few points: the search returned nothing


def test_summarise_counts_and_refuses_mixed_versions():
    out = calibrate.summarise(ROWS, sde_min=11.0)
    assert out["rows"] == 4 and out["empty"] == 1 and out["sde_min"] == 11.0
    real = out["kinds"]["real"]
    assert real["n"] == 3 and real["median"] == 10.5 and real["max"] == 16.0
    assert real["at_or_above"] == {"7": 3, "8": 2, "9": 2, "10": 2, "11": 1, "12": 1, "15": 1}
    assert out["kinds"]["scrambled"]["max"] == 9.0 and out["kinds"]["scrambled"]["at_or_above"]["8"] == 2
    assert out["real_at_or_above_sde_min"] == {"count": 1, "n": 3, "fraction": pytest.approx(1 / 3)}
    # a file computed with only some nulls: the missing kind counts nothing
    partial = [{k: v for k, v in r.items() if k != "inverted"} for r in ROWS]
    assert calibrate.summarise(partial, sde_min=11.0)["kinds"]["inverted"] == {
        "n": 0, "median": None, "max": None, "at_or_above": {str(x): 0 for x in calibrate.SDE_LEVELS}}

    mixed = [*ROWS, _row("e", [8.0], [8.0], [8.0], version="1+bbbb1111")]
    with pytest.raises(ValueError, match="all-versions"):
        calibrate.summarise(mixed, sde_min=11.0)
    both = calibrate.summarise(mixed, sde_min=11.0, all_versions=True)
    assert both["search_versions"] == ["1+aaaa0000", "1+bbbb1111"] and both["kinds"]["real"]["n"] == 4


def test_scramble_permutes_within_each_quarter_only():
    f = np.array([1.0, 2.0, np.nan, 3.0, 10.0, 20.0, 30.0, 40.0])
    q = np.array([1, 1, 1, 1, 2, 2, 2, 2])
    out = calibrate.scramble_within_quarters(f, q)
    assert np.isnan(out[2]) and sorted(out[[0, 1, 3]]) == [1.0, 2.0, 3.0] and sorted(out[4:]) == [10, 20, 30, 40]
    assert np.array_equal(out, calibrate.scramble_within_quarters(f, q), equal_nan=True)     # seed 1: repeatable


def test_read_rows_skips_a_line_cut_short(tmp_path):
    path = tmp_path / "cut.jsonl"
    path.write_text(json.dumps(ROWS[0]) + "\n\n" + json.dumps(ROWS[1])[:40], encoding="utf-8")
    assert [r["target_id"] for r in calibrate.read_rows(path)] == ["a"]


def test_summarise_ignores_null_sdes():
    rows = [_row("a", [None, 9.0], [8.0], [None]), _row("b", [12.0], [None], [7.5])]
    out = calibrate.summarise(rows, sde_min=11.0)
    assert out["kinds"]["real"]["n"] == 2 and out["kinds"]["real"]["max"] == 12.0 and out["kinds"]["real"]["median"] == 10.5
    assert out["kinds"]["inverted"]["n"] == 1 and out["kinds"]["scrambled"]["n"] == 1


def _fast_env(monkeypatch, settings):
    """60 d curves over 2 to 15 d with pre-whitening off: one target searches in about a second."""
    return _env(monkeypatch, settings, THRESHOLDS__MIN_PERIOD_DAYS="2", THRESHOLDS__MAX_PERIOD_DAYS="15",
                THRESHOLDS__PREWHITEN_MAX_COMPONENTS="0")


def test_calibrate_repairs_a_line_cut_short_and_resumes(settings, monkeypatch):
    s, store, blob = _fast_env(monkeypatch, settings)
    targets = [_tgt("a"), _tgt("b", crowdsap=0.3)]
    lcs = {"a": make_lightcurve(n_days=60, seed=1), "b": make_lightcurve(n_days=60, seed=2)}
    fetched = []
    src = ScriptedSource(targets, lcs, store, blob, on_fetch=lambda t: fetched.append(t.target_id))
    version = calibrate.search_version(calibrate.calibration_detector(), s.thresholds)
    out = s.data_dir / "calibration" / "fake.jsonl"
    out.parent.mkdir(parents=True)
    line_a = json.dumps({**_row("a", [8.0], [8.0], [8.0]), "search_version": version})
    cut_b = json.dumps({**_row("b", [9.0], [9.0], [9.0]), "search_version": version})[:50]     # a hard kill mid-write
    out.write_text(line_a + "\n" + cut_b, encoding="utf-8")

    summary = calibrate.run(src, store, blob, s, filter={}, limit=2, out=out)
    assert summary["skipped"] == 1 and summary["written"] == 1 and fetched == ["b"]
    text = out.read_text(encoding="utf-8")
    assert text.startswith(line_a + "\n") and text.endswith("\n") and text.count('"target_id": "b"') == 1
    rows = [json.loads(x) for x in text.splitlines()]           # every line parses: no corrupted line left
    assert [r["target_id"] for r in rows] == ["a", "b"] and set(rows[1]) == ROW_KEYS


def test_calibrate_counts_a_failed_fetch_and_continues(settings, monkeypatch):
    s, store, blob = _fast_env(monkeypatch, settings)
    targets = [_tgt("a"), _tgt("b", crowdsap=0.3)]
    lcs = {"a": make_lightcurve(n_days=60, seed=1), "b": make_lightcurve(n_days=60, seed=2)}
    src = ScriptedSource(targets, lcs, store, blob, bad_fetch={"a"})       # stores its file, then fails
    out = s.data_dir / "calibration" / "fake.jsonl"
    summary = calibrate.run(src, store, blob, s, filter={}, limit=2, out=out)
    assert summary["errors"] == 1 and summary["written"] == 1
    assert [r["target_id"] for r in _lines(out)] == ["b"]
    with store.session() as ss:                                 # stream retention freed the failed download's file
        arts = [a for a in ss.query(db.ArtifactRow) if a.target_id == "a"]
        assert arts and all(a.deleted_at is not None for a in arts)


def test_calibrate_keeps_a_raw_file_served_from_the_blob(settings, monkeypatch):
    """A cache hit is a file someone kept (an earlier sweep's evidence or a --retention keep pilot): stream retention
    must not delete it just because calibrate read it. A file calibrate downloaded itself still goes."""
    s, store, blob = _fast_env(monkeypatch, settings)
    assert s.retention == "stream"
    targets = [_tgt("kept"), _tgt("fresh", crowdsap=0.3)]
    lcs = {"kept": make_lightcurve(n_days=60, seed=1), "fresh": make_lightcurve(n_days=60, seed=2)}
    data = pickle.dumps(lcs["kept"])
    sha, loc = blob.put(data, "kept.fits")
    store.add_artifact(sha, len(data), "https://fake/kept", loc, "kept")
    src = ScriptedSource(targets, lcs, store, blob)
    out = s.data_dir / "calibration" / "fake.jsonl"
    summary = calibrate.run(src, store, blob, s, filter={}, limit=2, out=out, parse=pickle.loads)
    assert summary["written"] == 2 and summary["from_cache"] == 1
    with store.session() as ss:
        arts = {a.target_id: a for a in ss.query(db.ArtifactRow)}
        assert arts["kept"].deleted_at is None and blob.exists(arts["kept"].local_path)
        assert arts["fresh"].deleted_at is not None


def test_calibrate_never_evicts_the_files_of_targets_in_flight(settings, monkeypatch):
    s, store, blob = _env(monkeypatch, settings, THRESHOLDS__MIN_PERIOD_DAYS="2", THRESHOLDS__MAX_PERIOD_DAYS="15",
                          THRESHOLDS__PREWHITEN_MAX_COMPONENTS="0", RETENTION="budget:0")
    calls = []
    real = calibrate.Retention.enforce_budget

    def spy(self, exclude_targets=None):
        calls.append(set(exclude_targets or ()))
        return real(self, exclude_targets=exclude_targets)
    monkeypatch.setattr(calibrate.Retention, "enforce_budget", spy)
    targets = [_tgt("a"), _tgt("b", crowdsap=0.3)]
    lcs = {"a": make_lightcurve(n_days=60, seed=1), "b": make_lightcurve(n_days=60, seed=2)}
    out = s.data_dir / "calibration" / "fake.jsonl"
    summary = calibrate.run(ScriptedSource(targets, lcs, store, blob), store, blob, s, filter={}, limit=2, out=out)
    assert summary["written"] == 2
    assert calls[:2] == [{"a"}, {"a", "b"}]            # the target just loaded and every one still searching


def test_calibrate_refuses_to_append_another_version(settings, monkeypatch, tmp_path):
    s, store, blob = _env(monkeypatch, settings)
    out = tmp_path / "old.jsonl"
    out.write_text(json.dumps(_row("a", [8.0], [8.0], [8.0], version="1+00000000")) + "\n", encoding="utf-8")
    with pytest.raises(calibrate.VersionMismatch, match="--out"):
        calibrate.run(ScriptedSource([], {}, store, blob), store, blob, s, filter={}, limit=1, out=out)


def test_calibrate_report_prints_the_table(settings, tmp_path):
    path = tmp_path / "cal.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in ROWS), encoding="utf-8")
    r = CliRunner().invoke(app, ["calibrate-report", str(path)])
    assert r.exit_code == 0, r.output
    lines = r.stdout.splitlines()
    real = next(x for x in lines if x.startswith("real"))
    assert real.split() == ["real", "3", "10.50", "16.00", "3", "2", "2", "2", "1", "1", "1"]
    assert any(x.startswith("inverted") for x in lines) and any(x.startswith("scrambled") for x in lines)
    assert "1+aaaa0000" in r.stdout and "1 of 3" in r.stdout and "sde_min 11" in r.stdout

    path.write_text(path.read_text(encoding="utf-8") + json.dumps(_row("e", [8.0], [8.0], [8.0], version="2+x")) + "\n",
                    encoding="utf-8")
    r = CliRunner().invoke(app, ["calibrate-report", str(path)])
    assert r.exit_code != 0 and "--all-versions" in r.output
    r = CliRunner().invoke(app, ["calibrate-report", str(path), "--all-versions"])
    assert r.exit_code == 0 and "2+x" in r.stdout


def test_calibrate_command_passes_options(settings, monkeypatch):
    from skysift import cli

    seen = {}

    def stub(source, store, blob, settings, filter, limit, out, nulls, parse, progress=None):
        seen.update(settings=settings, filter=filter, limit=limit, out=out, nulls=nulls, parse=parse)
        return {"targets": 0, "skipped": 0, "written": 0, "errors": 0, "from_cache": 0}

    monkeypatch.setattr(calibrate, "run", stub)
    monkeypatch.setattr(cli, "_source", lambda name, store, blob: object())
    r = CliRunner().invoke(app, ["calibrate", "--source", "kbonus_bkg", "--limit", "5", "--workers-cpu", "3"])
    assert r.exit_code == 0, r.output
    th = seen["settings"].thresholds
    assert seen["limit"] == 5 and seen["nulls"] == ("scrambled", "inverted") and seen["settings"].workers_cpu == 3
    assert (th.min_period_days, th.max_period_days) == (0.5, 200.0) and seen["parse"] is not None   # production
    assert seen["settings"].thresholds == load_settings().thresholds
    assert seen["out"] == seen["settings"].data_dir / "calibration" / "kbonus_bkg.jsonl"
    assert json.loads(r.stdout)["written"] == 0
    r = CliRunner().invoke(app, ["calibrate", "--source", "kbonus_bkg", "--min-period", "1", "--max-period", "30"])
    assert r.exit_code == 0, r.output
    assert (seen["settings"].thresholds.min_period_days, seen["settings"].thresholds.max_period_days) == (1.0, 30.0)

    r = CliRunner().invoke(app, ["calibrate", "--source", "kbonus_bkg", "--nulls", "inverted,shuffled"])
    assert r.exit_code != 0 and "shuffled" in r.output


def test_calibration_searches_a_restored_transit_as_the_sweep_does(monkeypatch):
    """A wave at exactly the transit frequency makes pre-whitening (shape guard off) take the transit's fundamental
    with it. The sweep puts a detection's removed harmonics back and searches again; the calibration must run that
    same search-plus-restore, or it measures a statistic on flux the sweep never thresholds (the depth about 25
    percent low)."""
    from skysift import pipeline
    from skysift.config import Thresholds
    from skysift.models import LightCurve
    period, t0, duration, depth = 1.5, 132.2, 3 / 24, 1500.0
    lc = inject_transit(make_lightcurve(n_days=500, seed=7, noise_ppm=600), period, t0, duration, depth)
    f = lc.flux * (1 - 300e-6 * np.sin(2 * np.pi * (lc.time - t0) / period))     # odd about mid-transit
    lc = LightCurve(lc.time, f, lc.flux_err, lc.quality, lc.quarter, f.copy(), dict(lc.meta))
    real = pipeline.prewhiten
    monkeypatch.setattr(pipeline, "prewhiten", lambda *a, **kw: real(*a, **{**kw, "shape_guard": False}))
    th = Thresholds(min_period_days=1, max_period_days=5, prewhiten_max_components=2)
    assert any(abs(c["frequency"] - 1 / period) < 0.01 for c in pipeline.whitened_flux(lc, th)[1]["components"])
    row = calibrate.search_target(pickle.dumps(lc), th.model_dump_json(), calibrate.NULLS)
    top = row["real_top"]
    assert abs(top["period"] - period) < 0.01, top
    assert abs(top["depth_ppm"] - depth) < 0.15 * depth, top
    assert set(row) == ROW_KEYS - {"target_id", "search_version"}
