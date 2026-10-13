from skysift.config import load_settings


def test_defaults_load_from_yaml(tmp_path, monkeypatch):
    monkeypatch.delenv("SKYSIFT_RETENTION", raising=False)
    s = load_settings()
    assert s.retention == "stream"
    assert s.thresholds.sde_min == 9.0
    assert s.thresholds.min_events == 3


def test_env_overrides_yaml(monkeypatch, tmp_path):
    monkeypatch.setenv("SKYSIFT_RETENTION", "budget:50")
    monkeypatch.setenv("SKYSIFT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SKYSIFT_THRESHOLDS__SDE_MIN", "11")
    s = load_settings()
    assert s.retention == "budget:50"
    assert s.data_dir == tmp_path
    assert s.thresholds.sde_min == 11.0
