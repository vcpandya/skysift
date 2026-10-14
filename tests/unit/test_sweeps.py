import json
import sys
import time

import pytest

from skysift.ui.sweeps import SweepActive, SweepLauncher, SweepRequest

SLEEPER = [sys.executable, "-c", "import sys, time; print('started', flush=True); time.sleep(20)"]
QUICK = [sys.executable, "-c", "print('{\"processed\": 1}')"]


def test_request_argv_and_env():
    req = SweepRequest(source="kbonus_bkg", limit=50, min_period=1.0, max_period=30.0, retention="budget:20",
                       workers_cpu=3, store_periodogram="signals", background_only=False, gmag_max=18.0)
    argv = req.argv(python="PY")
    assert argv[:4] == ["PY", "-m", "skysift", "run"] and "--source" in argv and "kbonus_bkg" in argv
    assert argv[argv.index("--limit") + 1] == "50" and argv[argv.index("--min-period") + 1] == "1.0"
    assert "--all" in argv and argv[argv.index("--gmag-max") + 1] == "18.0" and argv[argv.index("--periodogram") + 1] == "signals"
    env = req.env()
    assert env["SKYSIFT_RETENTION"] == "budget:20" and env["SKYSIFT_WORKERS_CPU"] == "3" and "SKYSIFT_WORKERS_IO" not in env
    with pytest.raises(ValueError): SweepRequest(source="kbonus_bkg", min_period=5, max_period=2).validate()
    with pytest.raises(ValueError): SweepRequest(source="kbonus_bkg", retention="bogus").validate()
    with pytest.raises(ValueError): SweepRequest(source="kbonus_bkg", store_periodogram="maybe").validate()


def test_start_tail_stop_and_history(tmp_path):
    launcher = SweepLauncher(tmp_path, command=SLEEPER)
    info = launcher.start(SweepRequest(source="kbonus_bkg", limit=5))
    assert info["pid"] > 0 and (tmp_path / "ui" / "sweeps" / "active.json").exists()
    for _ in range(50):
        if launcher.tail() == ["started"]: break
        time.sleep(0.1)
    assert launcher.tail() == ["started"] and launcher.active()["request"]["limit"] == 5
    assert launcher.stop() is True
    assert launcher.active() is None
    hist = launcher.history()
    assert len(hist) == 1 and hist[0]["exit_code"] is not None and hist[0]["log"].endswith(".log")


def test_second_start_refused_while_active(tmp_path):
    launcher = SweepLauncher(tmp_path, command=SLEEPER)
    launcher.start(SweepRequest(source="kbonus_bkg"))
    try:
        with pytest.raises(SweepActive):
            launcher.start(SweepRequest(source="kbonus_bkg"))
    finally:
        launcher.stop()


def test_finished_child_is_moved_to_history_with_summary(tmp_path):
    launcher = SweepLauncher(tmp_path, command=QUICK)
    launcher.start(SweepRequest(source="kbonus_bkg"))
    for _ in range(50):
        if launcher.active() is None: break
        time.sleep(0.1)
    hist = launcher.history()
    assert hist[0]["exit_code"] == 0 and hist[0]["summary"] == {"processed": 1}


def test_stale_lock_from_dead_pid_is_removed(tmp_path):
    d = tmp_path / "ui" / "sweeps"; d.mkdir(parents=True)
    (d / "active.json").write_text(json.dumps({"pid": 999999999, "argv": [], "started": "2026-10-14T00:00:00+00:00",
                                                "log": str(d / "x.log"), "request": {"source": "kbonus_bkg"}}), encoding="utf-8")
    launcher = SweepLauncher(tmp_path, command=SLEEPER)
    assert launcher.active() is None and not (d / "active.json").exists()
    assert launcher.history()[0]["exit_code"] is None          # recorded as unknown, not lost
    launcher.start(SweepRequest(source="kbonus_bkg"))
    launcher.stop()
