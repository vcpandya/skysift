import json

import numpy as np
import pytest

from skysift import calibrate, inject
from skysift.config import Thresholds
from skysift.lc.ops import detrend
from skysift.pipeline import DEFAULT_LADDER_VERSION
from tests.synthetic import make_lightcurve
from tests.unit.test_calibrate import _lines
from tests.unit.test_pipeline import ScriptedSource, _env, _tgt

ROW_KEYS = {"target_id", "search_version", "ladder_version", "period", "t0", "duration", "depth_ppm", "snr_nominal",
            "snr_red", "found", "ranked_first", "hit"}


def test_plan_avoids_own_periods_and_sets_depth_from_the_nominal_snr():
    lc = make_lightcurve(n_days=400, seed=3, noise_ppm=800)
    th = Thresholds(min_period_days=0.5, max_period_days=60.0)
    own = [5.0, 12.3]
    plans = inject.plan_injections(lc, th, np.random.default_rng(7), 40, (7.0, 10.0, 15.0, 25.0), own)
    assert len(plans) == 40 and {"period", "t0", "duration", "depth_ppm", "snr_nominal"} <= set(plans[0])
    assert [p["snr_nominal"] for p in plans[:5]] == [7.0, 10.0, 15.0, 25.0, 7.0]             # cycles through the targets
    fn = detrend(lc)
    good = np.isfinite(fn)
    sigma = 1.4826 * np.median(np.abs(fn[good] - np.median(fn[good])))
    for p in plans:
        assert 0.8 <= p["period"] <= 25.0
        for a in own:
            for r in (0.5, 1.0, 2.0):
                assert abs(p["period"] / a - r) > 0.02 * r
        n_in = good.sum() * p["duration"] / p["period"]
        assert p["depth_ppm"] == pytest.approx(p["snr_nominal"] * sigma * 1e6 / np.sqrt(n_in))      # DEPTH_CAP not reached
        assert lc.time[good].min() <= p["t0"] <= lc.time[good].min() + p["period"]


def test_plan_stays_inside_the_search_window_and_the_baseline():
    lc = make_lightcurve(n_days=60, seed=1)
    th = Thresholds(min_period_days=2.0, max_period_days=15.0)
    periods = [p["period"] for p in inject.plan_injections(lc, th, np.random.default_rng(1), 30, (10.0,), [])]
    assert min(periods) >= 2.0 and max(periods) <= 15.0
    with pytest.raises(ValueError, match="window"):
        inject.plan_injections(lc, Thresholds(min_period_days=30.0, max_period_days=90.0), np.random.default_rng(1),
                               2, (10.0,), [])


def test_inject_box_dims_only_in_transit_points_of_both_fluxes():
    lc = make_lightcurve(n_days=30, seed=2, gaps=False)
    out = inject.inject_box(lc, period=5.0, t0=132.0, duration=0.2, depth_ppm=10000)
    ph = (lc.time - 132.0 + 2.5) % 5.0 - 2.5
    inside = np.abs(ph) < 0.1
    assert inside.sum() > 10
    assert np.allclose(out.flux[inside], lc.flux[inside] * 0.99) and np.array_equal(out.flux[~inside], lc.flux[~inside])
    assert np.allclose(out.flux_alt[inside], lc.flux_alt[inside] * 0.99)
    assert np.array_equal(lc.flux, make_lightcurve(n_days=30, seed=2, gaps=False).flux)         # the input is untouched


def test_red_noise_snr_matches_the_white_expectation_and_drops_with_red_noise():
    rng = np.random.default_rng(5)
    cad = 30.0 / 1440.0
    t = np.arange(0.0, 400.0, cad)
    y = rng.normal(0, 1000e-6, t.size)
    period, dur, depth = 5.0, 0.25, 500.0
    white = depth * np.sqrt(t.size * cad / period) / (1000.0 / np.sqrt(dur / cad))
    assert inject.red_noise_snr(t, y, period, dur, depth) == pytest.approx(white, rel=0.3)
    red = y + 1000e-6 * np.sin(2 * np.pi * t / 0.7) + 600e-6 * np.sin(2 * np.pi * t / 0.45)
    assert inject.red_noise_snr(t, red, period, dur, depth) < 0.7 * white
    assert np.isnan(inject.red_noise_snr(t[:5], y[:5], period, dur, depth))               # no full bin: no estimate


def _item(period, outcome="pass", status="pass", depth=1000.0, sde=12.0):
    return {"signal": {"period": period, "depth_ppm": depth, "sde": sde, "snr": 9.0, "n_events": 20, "extra": {}},
            "outcome": outcome,
            "tests": [{"rung": "rung1_detection", "status": "pass", "reason": ""},
                      {"rung": "rung2_shape", "status": status, "reason": "" if status == "pass" else "odd_even (x)"}]}


INJ = {"period": 5.0, "t0": 1.0, "duration": 0.2, "depth_ppm": 1000.0, "snr_nominal": 10.0}


def test_score_marks_found_and_ranked_first():
    first = inject.score(INJ, [_item(5.02), _item(9.0)])
    assert first["found"] and first["ranked_first"] and first["hit"]["rank"] == 0 and first["hit"]["ratio"] == 1.0
    assert first["hit"]["outcome"] == "pass" and first["hit"]["period"] == 5.02 and first["hit"]["sde"] == 12.0
    assert first["hit"]["rungs"][0] == ["rung1_detection", "pass", ""]
    second = inject.score(INJ, [_item(9.0), _item(10.04, outcome="fail", status="fail")])      # the 2x alias, ranked second
    assert second["found"] and not second["ranked_first"] and second["hit"]["rank"] == 1 and second["hit"]["ratio"] == 2.0
    assert second["hit"]["outcome"] == "fail" and second["hit"]["rungs"][1][1] == "fail"
    for q, ratio in ((2.5, 0.5), (15.0, 3.0), (5.0 / 3.0, 1 / 3)):
        assert inject.score(INJ, [_item(q)])["hit"]["ratio"] == pytest.approx(ratio)
    miss = inject.score(INJ, [_item(7.0), _item(5.1)])                                         # 2 percent off: not found
    assert miss == {"found": False, "ranked_first": False, "hit": None}
    assert inject.score(INJ, []) == {"found": False, "ranked_first": False, "hit": None}


def test_run_writes_one_line_per_injection_and_resumes(settings, monkeypatch):
    s, store, blob = _env(monkeypatch, settings, THRESHOLDS__MIN_PERIOD_DAYS="2", THRESHOLDS__MAX_PERIOD_DAYS="15")
    lcs = {"a": make_lightcurve(n_days=60, seed=1, noise_ppm=800)}
    src = ScriptedSource([_tgt("a")], lcs, store, blob)
    out = s.data_dir / "injections" / "fake.jsonl"
    first = inject.run(src, store, blob, s, filter={}, limit=1, per_star=2, snr_targets=(10.0, 25.0), out=out,
                       workers_cpu=1)
    assert first["written"] == 2 and first["skipped"] == 0 and first["errors"] == 0
    rows = _lines(out)
    assert len(rows) == 2 and all(set(r) == ROW_KEYS for r in rows)
    from skysift.detectors.base import get_detector
    from skysift.pipeline import search_version
    version = search_version(get_detector("box_dip"), s.thresholds)
    for r, snr in zip(rows, (10.0, 25.0), strict=True):
        assert r["target_id"] == "a" and r["search_version"] == version and r["ladder_version"] == DEFAULT_LADDER_VERSION
        assert r["snr_nominal"] == snr and 2.0 <= r["period"] <= 15.0 and r["depth_ppm"] > 0
        assert isinstance(r["found"], bool) and (r["hit"] is None) == (not r["found"])
        assert r["snr_red"] is None or r["snr_red"] > 0
    json.dumps(rows, allow_nan=False)
    second = inject.run(src, store, blob, s, filter={}, limit=1, per_star=2, snr_targets=(10.0, 25.0), out=out,
                        workers_cpu=1)
    assert second["written"] == 0 and second["skipped"] == 1
    assert _lines(out) == rows

    # a file written by another search version is not appended to
    out2 = s.data_dir / "injections" / "other.jsonl"
    out2.parent.mkdir(parents=True, exist_ok=True)
    out2.write_text(json.dumps({"target_id": "z", "search_version": "1+00000000",
                                "ladder_version": DEFAULT_LADDER_VERSION}) + "\n", encoding="utf-8")
    with pytest.raises(calibrate.VersionMismatch):
        inject.run(src, store, blob, s, filter={}, limit=1, per_star=2, snr_targets=(10.0,), out=out2, workers_cpu=1)


def _hit(outcome="pass", ratio=1.0, depth=900.0, rank=0):
    return {"rank": rank, "ratio": ratio, "period": 5.0, "depth_ppm": depth, "sde": 12.0, "snr": 9.0, "n_events": 9,
            "outcome": outcome, "rungs": [], "alias_resolved": False, "restored": 0}


def _row(snr, red, period, hit, depth=1000.0, version="1+aaaa0000"):
    return {"target_id": "a", "search_version": version, "ladder_version": "3", "period": period, "t0": 1.0,
            "duration": 0.2, "depth_ppm": depth, "snr_nominal": snr, "snr_red": red, "found": hit is not None,
            "ranked_first": hit is not None and hit["rank"] == 0, "hit": hit}


ROWS = [_row(7.0, 4.0, 1.5, None), _row(7.0, 6.0, 4.0, _hit("fail", rank=1)), _row(10.0, 8.0, 12.0, _hit()),
        _row(10.0, 10.0, 30.0, _hit(depth=800.0)), _row(25.0, 22.0, 2.0, _hit(depth=1100.0, ratio=2.0)),
        _row(25.0, None, 9.0, _hit())]


def test_summarise_tables_by_nominal_red_period_and_depth_ratio():
    out = inject.summarise(ROWS)
    json.dumps(out, allow_nan=False)
    assert out["rows"] == 6 and out["search_versions"] == ["1+aaaa0000"] and out["ladder_versions"] == ["3"]
    nominal = {g["label"]: g for g in out["by_nominal"]}
    assert list(nominal) == ["7", "10", "25"]
    assert (nominal["7"]["n"], nominal["7"]["found"], nominal["7"]["ranked_first"], nominal["7"]["passed"]) == (2, 1, 0, 0)
    assert (nominal["10"]["n"], nominal["10"]["found"], nominal["10"]["ranked_first"], nominal["10"]["passed"]) == (2, 2, 2, 2)
    assert (nominal["25"]["n"], nominal["25"]["found"], nominal["25"]["passed"]) == (2, 2, 2)
    red = {g["label"]: g for g in out["by_red"]}
    assert list(red) == ["0-5", "5-7", "7-9", "9-11", "11-14", "14-20", "20+"]
    assert [red[k]["n"] for k in red] == [1, 1, 1, 1, 0, 0, 1] and red["20+"]["found"] == 1 and red["0-5"]["found"] == 0
    period = {g["label"]: g for g in out["by_period"]}
    assert list(period) == ["0.8-3", "3-10", "10-25", "25+"]
    assert [period[k]["n"] for k in period] == [2, 2, 1, 1] and period["25+"]["found"] == 1
    # depth ratio over recovered injections at the injected period (ratio 1.0): 900 and 800 and 900 over 1000
    assert out["median_depth_ratio"] == pytest.approx(0.9)
    assert inject.summarise([])["median_depth_ratio"] is None
    mixed = [*ROWS, _row(7.0, 5.0, 3.0, None, version="1+bbbb1111")]
    with pytest.raises(ValueError, match="all-versions"):
        inject.summarise(mixed)
    assert inject.summarise(mixed, all_versions=True)["rows"] == 7


def test_format_table_lists_every_group():
    text = inject.format_table(inject.summarise(ROWS))
    for label in ("nominal SNR", "red-noise SNR", "period (d)", "20+", "25+", "0.8-3", "median depth ratio 0.90"):
        assert label in text
    assert "1+aaaa0000" in text and "ladder 3" in text
