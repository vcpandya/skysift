import json
import os
import sys
import time

import pytest

from skysift.ui.sweeps import SweepActive, SweepLauncher, SweepRequest, _pid_alive, _process_created

SLEEPER = [sys.executable, "-c", "import sys, time; print('started', flush=True); time.sleep(20)"]
QUICK = [sys.executable, "-c", "print('{\"processed\": 1}')"]
PRETTY = [sys.executable, "-c",
          "import json; print('warming up'); print(json.dumps({'processed': 2, 'x': [{'a': 1}]}, indent=2))"]
FAILING = [sys.executable, "-c", "import sys; print('{\"processed\": 1}'); sys.exit(3)"]


def wait_finished(launcher):
    for _ in range(100):
        if launcher.active() is None: break
        time.sleep(0.1)


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
    # an omitted end of the window falls back to the configured thresholds, not the engine defaults
    from skysift.config import Thresholds
    narrow = Thresholds(max_period_days=20.0)
    SweepRequest(source="kbonus_bkg", min_period=30.0).validate()                       # fine against the defaults
    with pytest.raises(ValueError): SweepRequest(source="kbonus_bkg", min_period=30.0).validate(narrow)
    SweepRequest(source="kbonus_bkg", min_period=1.0).validate(narrow)


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
    assert hist[0]["reason"] == "stopped"


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
    wait_finished(launcher)
    hist = launcher.history()
    assert hist[0]["exit_code"] == 0 and hist[0]["summary"] == {"processed": 1} and hist[0]["reason"] == "exited"


def test_stale_lock_from_dead_pid_is_removed(tmp_path):
    d = tmp_path / "ui" / "sweeps"; d.mkdir(parents=True)
    (d / "active.json").write_text(json.dumps({"pid": 999999999, "argv": [], "started": "2026-10-14T00:00:00+00:00",
                                                "log": str(d / "x.log"), "request": {"source": "kbonus_bkg"},
                                                "stamp": "20261014T000000000000Z"}), encoding="utf-8")
    launcher = SweepLauncher(tmp_path, command=SLEEPER)
    assert launcher.active() is None and not (d / "active.json").exists()
    rec = launcher.history()[0]
    assert rec["exit_code"] is None and rec["reason"] == "stale-lock"          # recorded as unknown, not lost
    launcher.start(SweepRequest(source="kbonus_bkg"))
    launcher.stop()


def test_second_launcher_reattaches_and_stops_a_live_sweep(tmp_path):
    first = SweepLauncher(tmp_path, command=SLEEPER)
    info = first.start(SweepRequest(source="kbonus_bkg"))
    proc = first._proc
    try:
        second = SweepLauncher(tmp_path, command=SLEEPER)
        assert second.active()["pid"] == info["pid"]
        with pytest.raises(SweepActive):
            second.start(SweepRequest(source="kbonus_bkg"))
        assert second.stop() is True
        assert second.active() is None and not (tmp_path / "ui" / "sweeps" / "active.json").exists()
        rec = second.history()[0]
        assert rec["reason"] == "stopped" and rec["pid"] == info["pid"] and rec["exit_code"] is None
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_corrupt_lock_is_retired_and_start_succeeds(tmp_path):
    d = tmp_path / "ui" / "sweeps"; d.mkdir(parents=True)
    (d / "active.json").write_text("{not json", encoding="utf-8")
    launcher = SweepLauncher(tmp_path, command=SLEEPER)
    assert launcher.active() is None and not (d / "active.json").exists()
    hist = launcher.history()
    assert len(hist) == 1 and hist[0]["reason"] == "corrupt-lock" and hist[0]["exit_code"] is None
    assert not (d / "unknown.json").exists()
    launcher.start(SweepRequest(source="kbonus_bkg"))
    assert launcher.stop() is True


def test_lock_missing_required_keys_is_corrupt(tmp_path):
    d = tmp_path / "ui" / "sweeps"; d.mkdir(parents=True)
    (d / "active.json").write_text(json.dumps({"pid": 1}), encoding="utf-8")
    launcher = SweepLauncher(tmp_path, command=SLEEPER)
    assert launcher.active() is None and launcher.history()[0]["reason"] == "corrupt-lock"


def test_pid_reuse_is_stale_and_never_signalled(tmp_path, monkeypatch):
    d = tmp_path / "ui" / "sweeps"; d.mkdir(parents=True)
    (d / "active.json").write_text(json.dumps(
        {"pid": os.getpid(), "created": 1, "argv": [], "started": "2026-10-14T00:00:00+00:00",
         "log": str(d / "x.log"), "request": {"source": "kbonus_bkg"}, "stamp": "20261014T000000000001Z"}),
        encoding="utf-8")
    signalled = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: signalled.append((pid, sig)))
    launcher = SweepLauncher(tmp_path, command=SLEEPER)
    assert launcher.stop() is False
    assert launcher.active() is None and signalled == []
    assert launcher.history()[0]["reason"] == "stale-lock"


def test_process_identity_check():
    created = _process_created(os.getpid())
    assert _pid_alive(os.getpid(), created) and _pid_alive(os.getpid(), None)
    if created is not None:
        assert not _pid_alive(os.getpid(), created + 1)


def test_stamps_of_immediate_restarts_differ(tmp_path):
    launcher = SweepLauncher(tmp_path, command=SLEEPER)
    stamps = []
    for _ in range(2):
        stamps.append(launcher.start(SweepRequest(source="kbonus_bkg"))["stamp"])
        assert launcher.stop() is True
    assert stamps[0] != stamps[1] and len(launcher.history()) == 2


def test_failed_spawn_leaves_no_lock_or_log(tmp_path):
    launcher = SweepLauncher(tmp_path, command=[str(tmp_path / "no-such-program")])
    with pytest.raises(OSError):
        launcher.start(SweepRequest(source="kbonus_bkg"))
    d = tmp_path / "ui" / "sweeps"
    assert not (d / "active.json").exists() and list(d.glob("*.log")) == [] and launcher.active() is None
    launcher.command = QUICK
    launcher.start(SweepRequest(source="kbonus_bkg"))
    wait_finished(launcher)
    assert launcher.history()[0]["exit_code"] == 0


def test_summary_from_pretty_block_and_only_on_clean_exit(tmp_path):
    launcher = SweepLauncher(tmp_path, command=PRETTY)
    launcher.start(SweepRequest(source="kbonus_bkg"))
    wait_finished(launcher)
    assert launcher.history()[0]["summary"] == {"processed": 2, "x": [{"a": 1}]}

    other = SweepLauncher(tmp_path / "b", command=FAILING)
    other.start(SweepRequest(source="kbonus_bkg"))
    wait_finished(other)
    rec = other.history()[0]
    assert rec["exit_code"] == 3 and rec["summary"] is None


# a stand-in engine: runs until the stop file (argv[1]) appears, then winds down like an engine finishing its in-flight
# targets (it says so, then holds until the release file argv[2] appears) and prints a summary like a graceful exit
POLLER_SCRIPT = """import os, sys, time; print('started', flush=True)
for _ in range(600):
    if os.path.exists(sys.argv[1]): break
    time.sleep(0.05)
print('winding down', flush=True)
for _ in range(600):
    if os.path.exists(sys.argv[2]): break
    time.sleep(0.05)
print('{"processed": 2, "stopped": "graceful"}')
"""
POLLER = [sys.executable, "-c", POLLER_SCRIPT]


def stop_path(tmp_path):
    return tmp_path / "ui" / "sweeps" / "stop.request"


def wait_for(condition, what, timeout=30.0):
    """Poll until ``condition()`` is truthy; fail loudly (naming ``what``) instead of falling through on a timeout."""
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.05)


def test_request_stop_writes_the_stop_file_and_the_child_finishes_on_its_own(tmp_path):
    release = tmp_path / "release"
    launcher = SweepLauncher(tmp_path, command=POLLER + [str(stop_path(tmp_path)), str(release)])
    launcher.start(SweepRequest(source="kbonus_bkg"))
    wait_for(lambda: launcher.tail() == ["started"], "the child to start")
    assert launcher.request_stop() is True
    assert stop_path(tmp_path).exists()
    # the child reacts to the stop file by itself, and holds in its wind-down so the lock is observably still live
    wait_for(lambda: launcher.tail()[-1:] == ["winding down"], "the child to notice the stop file")
    active = launcher.active()
    assert active is not None and active["stop_requested_at"]            # still running: the lock stays until it exits
    release.write_text("go", encoding="utf-8")                            # the in-flight work is done: let the child finish
    wait_for(lambda: launcher.active() is None, "the child to exit and its lock to retire")
    rec = launcher.history()[0]
    assert rec["exit_code"] == 0 and rec["reason"] == "exited" and rec["summary"]["stopped"] == "graceful"
    assert rec["stop_requested_at"] == active["stop_requested_at"]
    assert not stop_path(tmp_path).exists()                                # retired: the request is consumed


def test_request_stop_without_a_sweep_does_nothing(tmp_path):
    launcher = SweepLauncher(tmp_path, command=SLEEPER)
    assert launcher.request_stop() is False and not stop_path(tmp_path).exists()


def test_stale_stop_file_is_removed_on_start_and_on_hard_stop(tmp_path):
    launcher = SweepLauncher(tmp_path, command=SLEEPER)
    stop_path(tmp_path).write_text("stale", encoding="utf-8")
    launcher.start(SweepRequest(source="kbonus_bkg"))
    assert not stop_path(tmp_path).exists()                                # a leftover must not stop the new sweep
    assert launcher.request_stop() is True and stop_path(tmp_path).exists()
    assert launcher.stop() is True                                         # the hard stop still works while finishing
    assert launcher.history()[0]["reason"] == "stopped" and not stop_path(tmp_path).exists()


def test_spawn_options_lower_the_priority_only_when_asked():
    from skysift.ui.sweeps import BELOW_NORMAL_PRIORITY_CLASS, CREATE_NEW_PROCESS_GROUP, spawn_options
    assert (CREATE_NEW_PROCESS_GROUP, BELOW_NORMAL_PRIORITY_CLASS) == (0x200, 0x4000)
    assert spawn_options(False, "win32") == {"creationflags": CREATE_NEW_PROCESS_GROUP}
    assert spawn_options(True, "win32") == {"creationflags": CREATE_NEW_PROCESS_GROUP | BELOW_NORMAL_PRIORITY_CLASS}
    assert spawn_options(False, "linux") == {"creationflags": 0}
    assert spawn_options(True, "linux") == {"creationflags": 0}     # no preexec_fn: the nice is set after Popen


def test_lower_priority_sets_the_nice_after_the_start_off_windows(monkeypatch):
    import skysift.ui.sweeps as sw
    calls = []
    monkeypatch.setattr(sw.os, "setpriority", lambda *a: calls.append(a), raising=False)
    monkeypatch.setattr(sw.os, "PRIO_PROCESS", 0, raising=False)
    sw.lower_priority(4321, "win32")
    assert calls == []
    sw.lower_priority(4321, "linux")
    assert calls == [(0, 4321, sw.NICE_INCREMENT)]

    def refuse(*_a):
        raise PermissionError("not allowed")
    monkeypatch.setattr(sw.os, "setpriority", refuse, raising=False)
    sw.lower_priority(4321, "linux")            # logged, not raised: the child keeps the normal priority


def test_the_sweep_keeps_normal_priority_and_passes_its_flags_to_popen(tmp_path, monkeypatch):
    import skysift.ui.sweeps as sw
    seen = {}

    def fake_popen(argv, **kwargs):
        seen.update(argv=argv, **kwargs)
        raise OSError("not started in this test")
    monkeypatch.setattr(sw.subprocess, "Popen", fake_popen)
    launcher = SweepLauncher(tmp_path, command=["engine", "run"])
    assert launcher.below_normal is False
    with pytest.raises(OSError):
        launcher.start(SweepRequest(source="kbonus_bkg"))
    assert seen["argv"] == ["engine", "run"]
    assert {k: seen[k] for k in sw.spawn_options(False)} == sw.spawn_options(False)
    low = SweepLauncher(tmp_path, command=["engine", "run"], below_normal=True)
    with pytest.raises(OSError):
        low.start(SweepRequest(source="kbonus_bkg"))
    if sys.platform == "win32":
        assert seen["creationflags"] == sw.CREATE_NEW_PROCESS_GROUP | sw.BELOW_NORMAL_PRIORITY_CLASS
    assert "preexec_fn" not in seen
    assert launcher.active() is None
