import json
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from skysift.cli import app


def test_sources_lists_registry():
    r = CliRunner().invoke(app, ["sources"])
    assert r.exit_code == 0 and "kbonus_bkg" in r.stdout and "live" in r.stdout


def test_status_on_empty_db(settings):
    r = CliRunner().invoke(app, ["status"])
    assert r.exit_code == 0 and "targets" in r.stdout


def test_help_runs():
    r = CliRunner().invoke(app, ["--help"])
    assert r.exit_code == 0 and "run" in r.stdout and "status" in r.stdout


def test_run_rejects_unknown_source(settings):
    r = CliRunner().invoke(app, ["run", "--source", "nonexistent"])
    assert r.exit_code != 0 and "not live" in r.output


def test_python_m_entry_point():
    repo_root = Path(__file__).resolve().parents[2]
    r = subprocess.run([sys.executable, "-m", "skysift", "--help"], check=False, capture_output=True, text=True,
                       timeout=120, cwd=repo_root)
    assert r.returncode == 0 and "run" in r.stdout


def test_run_passes_limit_as_max_targets_and_period_overrides(settings, monkeypatch):
    from skysift import cli, pipeline

    seen = {}

    def stub(source, store, blob, settings, filter, **kw):
        seen.update(settings=settings, filter=filter, **kw)
        return {"processed": 0, "skipped": 0, "errors": 0, "signals": 0}

    monkeypatch.setattr(pipeline, "run", stub)
    monkeypatch.setattr(cli, "_source", lambda name, store, blob: object())
    r = CliRunner().invoke(app, ["run", "--source", "kbonus_bkg", "--limit", "5", "--min-period", "5", "--max-period", "40"])
    assert r.exit_code == 0, r.output
    assert seen["max_targets"] == 5 and "limit" not in seen["filter"]
    assert seen["settings"].thresholds.min_period_days == 5.0 and seen["settings"].thresholds.max_period_days == 40.0
    assert json.loads(r.stdout)["processed"] == 0          # stdout is the JSON only
    assert "period window: 5 to 40 days" in r.stderr


def test_run_targets_file_becomes_the_target_ids_filter(settings, monkeypatch, tmp_path):
    from skysift import cli, pipeline

    seen = {}

    def stub(source, store, blob, settings, filter, **kw):
        seen.update(filter=filter, **kw)
        return {"processed": 0, "skipped": 0, "errors": 0, "signals": 0}

    monkeypatch.setattr(pipeline, "run", stub)
    monkeypatch.setattr(cli, "_source", lambda name, store, blob: object())
    f = tmp_path / "list.txt"
    f.write_text("# brightest first\nkic-005689121\n\ngaia-dr3-2051294213285341056  \nkic-005689121\n", encoding="utf-8")
    r = CliRunner().invoke(app, ["run", "--source", "kbonus_bkg", "--targets", str(f)])
    assert r.exit_code == 0, r.output
    assert seen["filter"]["target_ids"] == ["kic-005689121", "gaia-dr3-2051294213285341056"]   # comments, blanks, duplicates dropped
    r = CliRunner().invoke(app, ["run", "--source", "kbonus_bkg", "--targets", str(tmp_path / "missing.txt")])
    assert r.exit_code != 0


def test_run_warns_when_sde_min_is_not_calibrated_for_its_search_version(settings, monkeypatch):
    """The warning compares the calibration file with the version calibrate would write for this sweep's window
    (calibrate searches with the masked pass and alias resolution off, so its version is never the sweep's own)."""
    from skysift import calibrate, cli, pipeline
    monkeypatch.setattr(pipeline, "run", lambda *a, **kw: {"processed": 0})
    monkeypatch.setattr(cli, "_source", lambda name, store, blob: object())
    warning = "sde_min is not calibrated for this search version; run skysift calibrate"
    path = settings.data_dir / "calibration" / "kbonus_bkg.jsonl"

    r = CliRunner().invoke(app, ["run", "--source", "kbonus_bkg"])                  # no file at all
    assert r.exit_code == 0 and warning in r.stderr and str(path) in r.stderr
    assert len([x for x in r.stderr.splitlines() if warning in x]) == 1 and json.loads(r.stdout) == {"processed": 0}

    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"target_id": "a", "search_version": "1+00000000"}) + "\n", encoding="utf-8")
    r = CliRunner().invoke(app, ["run", "--source", "kbonus_bkg"])                  # another version
    assert r.exit_code == 0 and warning in r.stderr and "1+00000000" in r.stderr

    current = calibrate.search_version(calibrate.calibration_detector(), settings.thresholds)
    path.write_text(json.dumps({"target_id": "a", "search_version": current}) + "\n", encoding="utf-8")
    r = CliRunner().invoke(app, ["run", "--source", "kbonus_bkg"])                  # calibrated: silent
    assert r.exit_code == 0 and warning not in r.stderr
    r = CliRunner().invoke(app, ["run", "--source", "kbonus_bkg", "--max-period", "30"])     # another window
    assert r.exit_code == 0 and warning in r.stderr


def test_run_passes_periodogram_mode(settings, monkeypatch):
    from skysift import cli, pipeline
    seen = {}
    monkeypatch.setattr(pipeline, "run", lambda source, store, blob, settings, filter, **kw: seen.update(settings=settings) or {"processed": 0})
    monkeypatch.setattr(cli, "_source", lambda name, store, blob: object())
    r = CliRunner().invoke(app, ["run", "--source", "kbonus_bkg", "--periodogram", "off"])
    assert r.exit_code == 0, r.output
    assert seen["settings"].store_periodogram == "off"
    assert CliRunner().invoke(app, ["run", "--source", "kbonus_bkg", "--periodogram", "sometimes"]).exit_code != 0


def test_run_rejects_bad_retention(settings):
    r = CliRunner().invoke(app, ["run", "--source", "kbonus_bkg", "--retention", "bogus"])
    assert r.exit_code != 0 and "bad retention" in r.output


def test_vet_pins_artifacts_and_keeps_stdout_json(settings, monkeypatch):
    from skysift import cli, pipeline
    from skysift.store.db import QueryRow, Store

    class Target:
        target_id = "T1"

        def model_dump_json(self):
            return "{}"

    class Src:
        def list_targets(self, flt):
            return [Target()]

        def fetch_lightcurve(self, target):
            return {"lc": 1}, "abc", "http://example/lc"

    pinned = []
    monkeypatch.setattr(cli, "_source", lambda name, store, blob: Src())
    payload = {"signals": [{"rungs": {"r1": {"x": 1}}}],
               "queries": [{"service": "vizier", "url": "https://v", "params": {}, "status": 200, "nbytes": 3}]}
    calls = []
    monkeypatch.setattr(pipeline, "_cpu_work", lambda *a, **kw: calls.append(kw) or json.dumps(payload))
    monkeypatch.setattr(Store, "pin_target_artifacts", lambda self, tid: pinned.append(tid))
    r = CliRunner().invoke(app, ["vet", "--source", "kbonus_bkg", "--target-id", "T1"])
    assert r.exit_code == 0, r.output
    assert pinned == ["T1"] and json.loads(r.stdout)["signals"][0]["rungs"]["r1"]["x"] == 1
    assert Store(settings.database_url).counts()["queries"] == 1       # vet logs the rungs' catalogue queries
    with Store(settings.database_url).session() as ss:
        assert ss.query(QueryRow).one().target_id == "T1"               # every request carries its target
    assert "wrote" in r.stderr and (settings.data_dir / "vet" / "T1.json").exists()
    assert calls[-1] == {"store_periodogram": "all", "spectrum_path": None}
    r = CliRunner().invoke(app, ["vet", "--source", "kbonus_bkg", "--target-id", "T1", "--spectrum"])
    assert r.exit_code == 0, r.output
    assert calls[-1]["spectrum_path"] == str(settings.data_dir / "vet" / "T1_spectrum.npz")
    assert "T1_spectrum.npz" in r.stderr


def test_run_rejects_a_bad_period_window(settings, monkeypatch):
    from skysift import cli, pipeline

    monkeypatch.setattr(pipeline, "run", lambda *a, **kw: pytest.fail("must not run"))
    monkeypatch.setattr(cli, "_source", lambda name, store, blob: object())
    for args in (["--min-period", "40", "--max-period", "5"], ["--min-period", "0"], ["--min-period", "-1"]):
        r = CliRunner().invoke(app, ["run", "--source", "kbonus_bkg", *args])
        assert r.exit_code == 2 and "min_period_days < max_period_days" in r.output, (args, r.output)
        assert "Traceback" not in r.output


def test_run_disk_budget_exceeded_prints_summary_and_exits_3(settings, monkeypatch):
    from skysift import cli, pipeline
    from skysift.store.retention import DiskBudgetExceeded

    def boom(*a, **kw):
        e = DiskBudgetExceeded("cache 20 B over budget 0 B")
        e.summary = {"processed": 4, "skipped": 0, "gave_up": 0, "errors": 0, "signals": 2}
        raise e

    monkeypatch.setattr(pipeline, "run", boom)
    monkeypatch.setattr(cli, "_source", lambda name, store, blob: object())
    r = CliRunner().invoke(app, ["run", "--source", "kbonus_bkg"])
    assert r.exit_code == 3 and not isinstance(r.exception, DiskBudgetExceeded)
    assert json.loads(r.stdout)["processed"] == 4
    assert "stopped: cache 20 B over budget 0 B" in r.stderr and "Traceback" not in r.output


def test_ui_refuses_non_loopback_host(settings, monkeypatch):
    monkeypatch.delenv("SKYSIFT_UI_ALLOW_CONTAINER", raising=False)
    r = CliRunner().invoke(app, ["ui", "--host", "0.0.0.0", "--no-browser"])
    assert r.exit_code != 0 and "loopback" in r.output


def test_ui_starts_uvicorn_on_loopback(settings, monkeypatch):
    import uvicorn
    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda application, **kw: seen.update(kw, app=application))
    r = CliRunner().invoke(app, ["ui", "--port", "8899", "--no-browser"])
    assert r.exit_code == 0, r.output
    assert seen["host"] == "127.0.0.1" and seen["port"] == 8899 and seen["app"].title == "SkySift"


def test_ui_allows_all_interfaces_only_inside_the_container(settings, monkeypatch):
    import uvicorn
    seen = {}
    monkeypatch.setenv("SKYSIFT_UI_ALLOW_CONTAINER", "1")
    monkeypatch.setattr(uvicorn, "run", lambda application, **kw: seen.update(kw, app=application))
    r = CliRunner().invoke(app, ["ui", "--host", "0.0.0.0", "--no-browser"])
    assert r.exit_code == 0, r.output
    assert seen["host"] == "0.0.0.0"
    assert "http://127.0.0.1:8765/" in r.output


def test_ui_brackets_an_ipv6_host_in_the_printed_url(settings, monkeypatch):
    import uvicorn
    monkeypatch.setattr(uvicorn, "run", lambda application, **kw: None)
    r = CliRunner().invoke(app, ["ui", "--host", "::1", "--no-browser"])
    assert r.exit_code == 0, r.output
    assert "http://[::1]:8765/" in r.output


def test_status_reports_rungs_and_states(settings, tmp_path):
    from skysift.store.blob import LocalBlob
    from skysift.store.db import Store
    from tests.unit.ui_seed import seed
    store = Store(settings.database_url); store.init_schema(); seed(store, LocalBlob(tmp_path / "blobs"))
    r = CliRunner().invoke(app, ["status"])
    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout)
    assert out["targets_by_state"] == {"done": 3} and out["rung_counts"]["rung1_detection"]["pass"] == 3


def test_run_passes_a_stop_check_that_sees_the_stop_file_and_the_first_ctrl_c(settings, monkeypatch):
    import signal

    from skysift import cli, pipeline
    from skysift.config import stop_request_path

    seen = {}

    def stub(source, store, blob, settings, filter, **kw):
        seen.update(**kw)
        seen["handler"] = signal.getsignal(signal.SIGINT)        # installed while the sweep runs
        return {"processed": 0, "skipped": 0, "errors": 0, "signals": 0}

    monkeypatch.setattr(pipeline, "run", stub)
    monkeypatch.setattr(cli, "_source", lambda name, store, blob: object())
    before = signal.getsignal(signal.SIGINT)
    r = CliRunner().invoke(app, ["run", "--source", "kbonus_bkg"])
    assert r.exit_code == 0, r.output
    assert signal.getsignal(signal.SIGINT) is before                  # restored after the sweep
    check, handler = seen["stop_requested"], seen["handler"]
    assert isinstance(handler, cli.InterruptOnce) and check() is False
    handler(signal.SIGINT, None)                                       # first Ctrl+C: ask for a graceful stop
    assert check() is True
    with pytest.raises(KeyboardInterrupt):                             # second Ctrl+C: the hard stop
        handler(signal.SIGINT, None)
    path = stop_request_path(settings.data_dir)
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text("x", encoding="utf-8")
    assert cli.InterruptOnce(lambda text: None, path).requested is False and seen["stop_requested"]() is True


def test_first_ctrl_c_explains_itself(settings):
    import signal

    from skysift import cli
    lines = []
    h = cli.InterruptOnce(lines.append, settings.data_dir / "no-such-file")
    h(signal.SIGINT, None)
    assert h.requested and lines == ["stop requested: finishing in-flight targets, press Ctrl+C again to stop now"]
    assert h.check() is True


def test_inject_help_lists_its_options():
    r = CliRunner().invoke(app, ["inject", "--help"])
    assert r.exit_code == 0
    for opt in ("--source", "--limit", "--per-star", "--snr", "--workers-cpu", "--out", "--min-period", "--max-period",
                "--gmag-max", "--background-on", "--all", "--seed"):
        assert opt in r.stdout


def test_inject_passes_options_to_run_and_prints_its_summary(settings, monkeypatch):
    from skysift import cli, inject
    seen = {}

    def stub(source, store, blob, settings, filter, limit, per_star, snr_targets, out, workers_cpu, seed=1, **kw):
        seen.update(settings=settings, filter=filter, limit=limit, per_star=per_star, snr_targets=snr_targets, out=out,
                    workers_cpu=workers_cpu, seed=seed)
        return {"targets": 0, "written": 0}

    monkeypatch.setattr(inject, "run", stub)
    monkeypatch.setattr(cli, "_source", lambda name, store, blob: object())
    r = CliRunner().invoke(app, ["inject", "--source", "kbonus_bkg", "--limit", "5", "--per-star", "3", "--snr",
                                 "8,12", "--workers-cpu", "2", "--seed", "4", "--min-period", "1", "--max-period", "20"])
    assert r.exit_code == 0, r.output
    assert seen["limit"] == 5 and seen["per_star"] == 3 and seen["snr_targets"] == (8.0, 12.0) and seen["seed"] == 4
    assert seen["workers_cpu"] == 2 and seen["out"] == settings.data_dir / "injections" / "kbonus_bkg.jsonl"
    assert seen["settings"].thresholds.min_period_days == 1.0 and seen["settings"].thresholds.max_period_days == 20.0
    assert json.loads(r.stdout) == {"targets": 0, "written": 0}
    assert CliRunner().invoke(app, ["inject", "--source", "kbonus_bkg", "--snr", "seven"]).exit_code != 0


def test_inject_report_prints_the_table(tmp_path):
    hit = {"rank": 0, "ratio": 1.0, "period": 5.0, "depth_ppm": 900.0, "sde": 12.0, "snr": 9.0, "n_events": 9,
           "outcome": "pass", "rungs": [], "alias_resolved": False, "restored": 0}
    base = {"target_id": "a", "search_version": "1+aaaa0000", "ladder_version": "3", "period": 5.0, "t0": 1.0,
            "duration": 0.2, "depth_ppm": 1000.0}
    rows = [{**base, "snr_nominal": 10.0, "snr_red": 8.0, "found": True, "ranked_first": True, "hit": hit},
            {**base, "snr_nominal": 7.0, "snr_red": 4.0, "found": False, "ranked_first": False, "hit": None}]
    path = tmp_path / "inj.jsonl"
    path.write_text("".join(json.dumps(x) + "\n" for x in rows), encoding="utf-8")
    r = CliRunner().invoke(app, ["inject-report", str(path)])
    assert r.exit_code == 0, r.output
    assert "1+aaaa0000" in r.stdout and "nominal SNR" in r.stdout and "red-noise SNR" in r.stdout and "0-5" in r.stdout
    assert CliRunner().invoke(app, ["inject-report", str(tmp_path / "missing.jsonl")]).exit_code != 0
