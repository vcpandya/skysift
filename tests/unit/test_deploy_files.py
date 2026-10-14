import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_dockerfile_is_editable_and_uses_python_m():
    text = (ROOT / "docker" / "Dockerfile").read_text()
    assert '-e ".[s3,pg,ui]"' in text
    assert 'ENTRYPOINT ["python", "-m", "skysift"]' in text
    assert "skysift.exe" not in text
    assert "chown skysift:skysift /data" in text and "PYTHONUNBUFFERED=1" in text


def test_dockerfile_installs_the_postgres_extra():
    # the AWS template points SKYSIFT_DATABASE_URL at Postgres: the image needs the driver
    text = (ROOT / "docker" / "Dockerfile").read_text()
    assert ".[s3,pg,ui]" in text


def test_windows_task_script_never_uses_the_console_exe():
    text = (ROOT / "deploy" / "windows" / "register-task.ps1").read_text()
    assert "python.exe" in text and "-m skysift run" in text and "Test-Path" in text
    assert r'.venv\Scripts\python.exe' in text
    assert "skysift.exe" not in text


def test_batch_job_definition_is_valid_json_with_run_command():
    job = json.loads((ROOT / "deploy" / "aws" / "batch-job-definition.json").read_text())
    cp = job["containerProperties"]
    assert cp["command"][0] == "run"
    assert any(x["name"] == "SKYSIFT_DATABASE_URL" for x in cp["secrets"])
    assert not any(x["name"] == "SKYSIFT_DATABASE_URL" for x in cp["environment"])


def test_ci_workflow_parses_as_yaml():
    doc = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    assert doc["name"] == "ci" and "test" in doc["jobs"]


def test_compose_file_parses_and_runs_a_sweep():
    doc = yaml.safe_load((ROOT / "docker" / "docker-compose.yml").read_text())
    assert doc["services"]["skysift"]["command"][0] == "run"


def test_pyproject_declares_the_postgres_extra():
    assert "psycopg" in (ROOT / "pyproject.toml").read_text()


def test_dockerignore_excludes_environment_and_venv():
    text = (ROOT / ".dockerignore").read_text()
    assert ".venv" in text and ".env" in text


def test_dockerfile_installs_the_ui_extra():
    text = (ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
    assert '".[s3,pg,ui]"' in text


def test_compose_has_a_loopback_ui_service():
    import yaml
    doc = yaml.safe_load((ROOT / "docker" / "docker-compose.yml").read_text(encoding="utf-8"))
    ui = doc["services"]["ui"]
    assert ui["ports"] == ["127.0.0.1:8765:8765"] and ui["command"][:3] == ["ui", "--host", "0.0.0.0"]
