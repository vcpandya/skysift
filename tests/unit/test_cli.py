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


# the round 3 final review: no unclosed dashboard.log (the parked ResourceWarning) under warnings as errors
@pytest.mark.filterwarnings("error")
def test_ui_starts_uvicorn_on_loopback(settings, monkeypatch):
    import uvicorn
    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda application, **kw: seen.update(kw, app=application))
    r = CliRunner().invoke(app, ["ui", "--port", "8899", "--no-browser"])
    assert r.exit_code == 0, r.output
    assert seen["host"] == "127.0.0.1" and seen["port"] == 8899 and seen["app"].title == "SkySift"


# the round 3 final review: no unclosed dashboard.log (the parked ResourceWarning) under warnings as errors
@pytest.mark.filterwarnings("error")
def test_ui_allows_all_interfaces_only_inside_the_container(settings, monkeypatch):
    import uvicorn
    seen = {}
    monkeypatch.setenv("SKYSIFT_UI_ALLOW_CONTAINER", "1")
    monkeypatch.setattr(uvicorn, "run", lambda application, **kw: seen.update(kw, app=application))
    r = CliRunner().invoke(app, ["ui", "--host", "0.0.0.0", "--no-browser"])
    assert r.exit_code == 0, r.output
    assert seen["host"] == "0.0.0.0"
    assert "http://127.0.0.1:8765/" in r.output


# the round 3 final review: no unclosed dashboard.log (the parked ResourceWarning) under warnings as errors
@pytest.mark.filterwarnings("error")
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
    assert out["targets_by_state"] == {"done": 7} and out["rung_counts"]["rung1_detection"]["pass"] == 6


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


class _EmptySource:
    """A source with no targets: the sweep has nothing to fetch, so no worker process starts."""
    name = "fake"

    def list_targets(self, flt):
        return []


@pytest.fixture
def cli_env(settings, monkeypatch):
    from skysift import cli
    monkeypatch.setattr(cli, "_source", lambda name, store, blob: _EmptySource())
    lowered = []
    monkeypatch.setattr(cli, "_lower_own_priority", lambda: lowered.append(1) or True)   # never the test process
    from types import SimpleNamespace
    return SimpleNamespace(settings=settings, lowered=lowered)


runner = CliRunner()


def test_recheck_command_prints_a_report(cli_env):
    r = runner.invoke(app, ["recheck", "--no-pixels"])
    assert r.exit_code == 0 and '"considered"' in r.stdout
    assert json.loads(r.stdout)["considered"] == 0


def test_recheck_command_passes_signal_ids_and_pixels(cli_env, monkeypatch):
    from skysift.recheck import RecheckReport
    seen = {}
    monkeypatch.setattr("skysift.recheck.recheck", lambda *a, **k: seen.update(k) or RecheckReport(considered=2))
    r = runner.invoke(app, ["recheck", "--signal-id", "4", "--signal-id", "9"])
    assert r.exit_code == 0, r.output
    assert seen["signal_ids"] == [4, 9] and seen["pixels"] is True and json.loads(r.stdout)["considered"] == 2
    runner.invoke(app, ["recheck", "--no-pixels"])
    assert seen["signal_ids"] is None and seen["pixels"] is False
    assert cli_env.lowered == [1, 1]                         # every standalone recheck runs below normal priority
    assert seen["retention"].policy.mode == "stream"         # the configured retention releases pixel files
    runner.invoke(app, ["recheck", "--retention", "keep"])
    assert seen["retention"].policy.mode == "keep"


def test_run_calls_the_recheck_unless_disabled(cli_env, monkeypatch):
    from skysift.recheck import RecheckReport
    calls = []; monkeypatch.setattr("skysift.recheck.recheck", lambda *a, **k: calls.append(k) or RecheckReport())
    r = runner.invoke(app, ["run", "--source", "fake", "--limit", "1"]); assert calls
    assert r.exit_code == 0, r.output
    assert calls[0]["pixels"] is True and json.loads(r.stdout)["recheck"]["considered"] == 0      # all four steps
    assert calls[0]["tpf_loader"].__name__ == "fetch_tpfs" and calls[0]["retention"].policy.mode == "stream"
    assert "recheck: considered 0" in r.stderr
    r = runner.invoke(app, ["run", "--source", "fake", "--limit", "1", "--no-recheck"]); assert len(calls) == 1
    assert r.exit_code == 0 and "recheck" not in json.loads(r.stdout) and "recheck:" not in r.stderr
    r = runner.invoke(app, ["run", "--source", "fake", "--limit", "1", "--no-pixels", "--retention", "keep"])
    assert r.exit_code == 0 and calls[1]["pixels"] is False and calls[1]["retention"].policy.mode == "keep"


def test_the_standalone_recheck_lowers_its_own_priority(monkeypatch):
    import ctypes
    import os

    from skysift import cli
    niced = []
    monkeypatch.setattr(os, "nice", lambda n: niced.append(n) or n, raising=False)       # absent on Windows
    assert cli._lower_own_priority(platform="linux") and niced == [cli.RECHECK_NICE]
    classes = []

    class Kernel32:
        def GetCurrentProcess(self):
            return -1

        def SetPriorityClass(self, handle, cls):
            classes.append((handle, cls)); return 1
    monkeypatch.setattr(ctypes, "windll", type("WinDLL", (), {"kernel32": Kernel32()})(), raising=False)
    assert cli._lower_own_priority(platform="win32") and classes == [(-1, cli.BELOW_NORMAL_PRIORITY_CLASS)]

    def refuse(n):
        raise OSError("not permitted")
    monkeypatch.setattr(os, "nice", refuse, raising=False)
    assert cli._lower_own_priority(platform="linux") is False


def test_the_recheck_line_counts_the_decided_pixel_verdicts_that_changed():
    from skysift.cli import _recheck_line
    from skysift.recheck import RecheckReport
    rep = RecheckReport(considered=2, decided_pixel_changed=1)
    rep.steps["pixels"] += 2
    assert _recheck_line(rep.as_dict()) == ("considered 2; steps: pixels 2; outcomes: none; decided pixel verdicts "
                                            "changed: 1 (each dossier flags it for a reviewer)")
    assert "decided" not in _recheck_line(RecheckReport(considered=1).as_dict())


def test_the_recheck_line_says_when_the_recheck_stopped():
    from skysift.cli import _recheck_line
    from skysift.recheck import RecheckReport
    rep = RecheckReport(considered=1, stopped=True); rep.steps["catalogue"] = 1; rep.outcomes["candidate_stage1"] = 1
    assert _recheck_line(rep.as_dict()) == ("considered 1; steps: catalogue 1; outcomes: candidate_stage1 1; "
                                            "stopped early, the next recheck takes the rest")
    assert _recheck_line({"stopped": True, "error": "interrupted"}) == "stopped, interrupted"
    assert _recheck_line({"error": "RuntimeError('x')"}) == "failed, RuntimeError('x')"


def test_cli_crosscheck_export_and_import(settings, monkeypatch, tmp_path):
    from skysift import net
    from skysift.dossier import generate
    from tests.unit.test_crosscheck import EXPORT_RESPONSES, fake_result, store_pixel_files
    from tests.unit.test_dossier import _make_env, fake_fetch
    from tests.unit.ui_seed import STAGE2_TARGET
    env = _make_env(settings, monkeypatch)
    monkeypatch.setattr(net, "fetch", fake_fetch(EXPORT_RESPONSES))         # the neighbours' parameter query
    r = runner.invoke(app, ["crosscheck", "--target", STAGE2_TARGET, "--export", str(tmp_path / "cc")])
    assert r.exit_code == 1 and "skysift dossier --target" in r.output                  # no dossier yet
    generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch)
    r = runner.invoke(app, ["crosscheck", "--target", STAGE2_TARGET, "--export", str(tmp_path / "cc")])
    assert r.exit_code == 2 and "tpf_missing" in r.output and "Traceback" not in r.output     # no pixel file kept
    store_pixel_files(env)
    generate(env.store, env.blob, env.settings, STAGE2_TARGET, fetch=env.fetch, force=True)
    r = runner.invoke(app, ["crosscheck", "--target", STAGE2_TARGET, "--export", str(tmp_path / "cc")])
    assert r.exit_code == 0, r.output
    report = json.loads(r.stdout)
    sha = json.loads((tmp_path / "cc" / "inputs.json").read_text(encoding="utf-8"))["fingerprint"]
    assert report["fingerprint"] == sha and report["inputs"] == str(tmp_path / "cc")
    assert report["quarters"] == [1, 2, 3, 4, 5]
    good = tmp_path / "result.json"
    good.write_text(json.dumps(fake_result(sha)), encoding="utf-8")
    r = runner.invoke(app, ["crosscheck", "--target", STAGE2_TARGET, "--import", str(good)])
    assert r.exit_code == 0, r.output
    report = json.loads(r.stdout)
    assert report["current"] is True and report["fpp"] == 0.012 and report["class"] == "validated"
    assert report["fpp_p16"] == report["fpp_p84"] == 0.012 and report["n_degenerate"] == 0
    assert report["fpp_no_stp_p16"] == report["fpp_no_stp_p84"] == 0.012           # M5: every range, not the FPP's only
    assert report["nfpp_p16"] == report["nfpp_p84"] == 1e-4
    assert report["blocks_live_gate"] is False and report["live_gate_reason"] is None
    assert env.store.dossier(STAGE2_TARGET)["cross_check"]["fpp"] == 0.012
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({**fake_result(sha), "fpp": 2.0}), encoding="utf-8")
    r = runner.invoke(app, ["crosscheck", "--target", STAGE2_TARGET, "--import", str(bad)])
    assert r.exit_code == 2 and "fpp" in r.output and "Traceback" not in r.output
    r = runner.invoke(app, ["crosscheck", "--target", "kic-000000001", "--import", str(good)])
    assert r.exit_code == 1
    for argv in ([], ["--export", "x", "--import", str(good)]):
        r = runner.invoke(app, ["crosscheck", "--target", STAGE2_TARGET, *argv])
        assert r.exit_code == 2 and "one of --export or --import" in r.output


def test_pack_final_needs_zenodo_live():
    """Spec 11.6: --final refuses before anything is read when the draft is not a live one."""
    r = runner.invoke(app, ["pack", "--target", "kic-006146676", "--final"])
    assert r.exit_code == 2 and "--final needs --zenodo-live" in r.output
    r = runner.invoke(app, ["pack", "--target", "kic-006146676", "--final", "--zenodo"])
    assert r.exit_code == 2 and "--final needs --zenodo-live" in r.output


def test_cli_dossier_refuses_a_characterised_star_without_a_stored_extinction(settings, monkeypatch):
    """Spec 12.3: no silent fallback; the run stops with the store instruction (exit 2), and so does a malformed
    file."""
    from skysift import dossier as ds
    from skysift import net
    from tests.unit.test_dossier import _make_env, fake_fetch, write_extinction
    from tests.unit.ui_seed import STAGE2_TARGET
    _make_env(settings, monkeypatch)
    monkeypatch.setattr(net, "fetch", fake_fetch())
    (ds.EXTINCTION_DIR / STAGE2_TARGET / "extinction.json").unlink()
    r = runner.invoke(app, ["dossier", "--target", STAGE2_TARGET])
    assert r.exit_code == 2 and "no stored extinction for kic-006146676" in r.output and "Traceback" not in r.output
    write_extinction(STAGE2_TARGET, {"status": "not available"})
    r = runner.invoke(app, ["dossier", "--target", STAGE2_TARGET])
    assert r.exit_code == 2 and "reason is empty" in r.output and "Traceback" not in r.output
