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


def test_run_rejects_bad_retention(settings):
    r = CliRunner().invoke(app, ["run", "--source", "kbonus_bkg", "--retention", "bogus"])
    assert r.exit_code != 0 and "bad retention" in r.output


def test_vet_pins_artifacts_and_keeps_stdout_json(settings, monkeypatch):
    from skysift import cli, pipeline
    from skysift.store.db import Store

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
    monkeypatch.setattr(pipeline, "_cpu_work", lambda *a: json.dumps(payload))
    monkeypatch.setattr(Store, "pin_target_artifacts", lambda self, tid: pinned.append(tid))
    r = CliRunner().invoke(app, ["vet", "--source", "kbonus_bkg", "--target-id", "T1"])
    assert r.exit_code == 0, r.output
    assert pinned == ["T1"] and json.loads(r.stdout)["signals"][0]["rungs"]["r1"]["x"] == 1
    assert Store(settings.database_url).counts()["queries"] == 1       # vet logs the rungs' catalogue queries
    assert "wrote" in r.stderr and (settings.data_dir / "vet" / "T1.json").exists()


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
