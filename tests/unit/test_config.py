from skysift.config import load_settings


def test_defaults_load_from_yaml(tmp_path, monkeypatch):
    monkeypatch.delenv("SKYSIFT_RETENTION", raising=False)
    s = load_settings()
    assert s.retention == "stream"
    assert s.thresholds.sde_min == 11.0
    assert s.thresholds.min_events == 3


def test_env_overrides_yaml(monkeypatch, tmp_path):
    monkeypatch.setenv("SKYSIFT_RETENTION", "budget:50")
    monkeypatch.setenv("SKYSIFT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SKYSIFT_THRESHOLDS__SDE_MIN", "12.5")
    s = load_settings()
    assert s.retention == "budget:50"
    assert s.data_dir == tmp_path
    assert s.thresholds.sde_min == 12.5


def test_thresholds_require_a_positive_increasing_period_window():
    import pytest
    from pydantic import ValidationError

    from skysift.config import Thresholds

    assert Thresholds(min_period_days=0.5, max_period_days=1.0).max_period_days == 1.0
    for lo, hi in ((0.0, 10.0), (-1.0, 10.0), (10.0, 10.0), (20.0, 10.0)):
        with pytest.raises(ValidationError):
            Thresholds(min_period_days=lo, max_period_days=hi)
