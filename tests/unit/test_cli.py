import subprocess
import sys
from pathlib import Path

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
