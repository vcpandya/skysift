import pytest

from skysift.config import load_settings


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYSIFT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SKYSIFT_BLOB_URL", f"file://{tmp_path.as_posix()}/blobs")
    monkeypatch.setenv("SKYSIFT_DATABASE_URL", f"sqlite:///{tmp_path.as_posix()}/engine.db")
    return load_settings()


@pytest.fixture(autouse=True)
def _default_explain_thresholds():
    """create_app points explain's "usual" limits at its settings; put the defaults back so no test leaks them."""
    yield
    from skysift.config import Thresholds
    from skysift.ui import explain
    explain.configure(Thresholds())
