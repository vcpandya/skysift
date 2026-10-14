"""Start, watch and stop one engine sweep from the dashboard. The sweep is a child `python -m skysift run` (never a
console .exe), its output goes to a log file, and a lock file allows one sweep per data directory. Interrupted
runs need no cleanup: the engine's start_run marks them superseded on the next sweep.

Guarantees: the lock is created atomically before the child is spawned (two starts cannot both win); a lock names
its process by (pid, creation time) so a reused pid is never signalled; a corrupt or stale lock is retired into the
history instead of raising into a request. Stopping kills only the sweep process itself: engine spawn workers that
outlive it exit on their own at the end of their current target (at most about 250 s), and the next sweep's
start_run marks the interrupted run superseded."""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from skysift.config import Thresholds
from skysift.store.retention import RetentionPolicy

PERIODOGRAM_MODES = ("all", "signals", "off")
STAMP_FORMAT = "%Y%m%dT%H%M%S%fZ"                 # microseconds: two starts never share a stamp
PROVISIONAL_TTL_S = 30.0                          # a lock without a pid older than this belongs to a dead starter
STOP_WAIT_S = 5.0


class SweepActive(RuntimeError):
    pass


@dataclass
class SweepRequest:
    source: str
    limit: int = 500
    min_period: float | None = None
    max_period: float | None = None
    retention: str = "stream"
    workers_io: int | None = None
    workers_cpu: int | None = None
    background_only: bool = True
    gmag_max: float = 19.0
    store_periodogram: str = "all"

    def validate(self) -> None:
        if not self.source or not self.source.replace("_", "").isalnum():
            raise ValueError("source name is required")
        if self.limit is not None and self.limit <= 0:
            raise ValueError("limit must be positive")
        RetentionPolicy.parse(self.retention)                      # raises ValueError
        if self.store_periodogram not in PERIODOGRAM_MODES:
            raise ValueError(f"store_periodogram must be one of {PERIODOGRAM_MODES}")
        if self.min_period is not None or self.max_period is not None:
            base = Thresholds()
            Thresholds(min_period_days=self.min_period if self.min_period is not None else base.min_period_days,
                       max_period_days=self.max_period if self.max_period is not None else base.max_period_days)
        for w in (self.workers_io, self.workers_cpu):
            if w is not None and w <= 0: raise ValueError("worker counts must be positive")

    def argv(self, python: str = sys.executable) -> list[str]:
        a = [python, "-m", "skysift", "run", "--source", self.source, "--gmag-max", str(self.gmag_max),
             "--periodogram", self.store_periodogram, "--background-only" if self.background_only else "--all"]
        if self.limit is not None: a += ["--limit", str(self.limit)]
        if self.min_period is not None: a += ["--min-period", str(self.min_period)]
        if self.max_period is not None: a += ["--max-period", str(self.max_period)]
        return a

    def env(self) -> dict:
        env = {"SKYSIFT_RETENTION": self.retention}
        if self.workers_io is not None: env["SKYSIFT_WORKERS_IO"] = str(self.workers_io)
        if self.workers_cpu is not None: env["SKYSIFT_WORKERS_CPU"] = str(self.workers_cpu)
        return env


# ---- process identity ------------------------------------------------------------------------------------------

_K32 = None


def _kernel32():
    global _K32
    if _K32 is None:
        import ctypes
        from ctypes import wintypes as wt

        class FileTime(ctypes.Structure):
            _fields_ = [("lo", wt.DWORD), ("hi", wt.DWORD)]

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        k.OpenProcess.restype = wt.HANDLE
        k.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
        k.GetExitCodeProcess.restype = wt.BOOL
        k.GetProcessTimes.argtypes = [wt.HANDLE] + [ctypes.POINTER(FileTime)] * 4
        k.GetProcessTimes.restype = wt.BOOL
        k.CloseHandle.argtypes = [wt.HANDLE]
        k.CloseHandle.restype = wt.BOOL
        k.FileTime = FileTime
        _K32 = k
    return _K32


def _probe(pid: int) -> tuple[bool, int | None]:
    """(alive, creation time) of a process; the creation time is None where the OS cannot tell."""
    if pid <= 0 or pid > 0xFFFFFFFF: return False, None
    if sys.platform == "win32":
        import ctypes
        k = _kernel32()
        h = k.OpenProcess(0x1000, False, pid)                # PROCESS_QUERY_LIMITED_INFORMATION
        if not h: return False, None
        try:
            code = ctypes.wintypes.DWORD()
            if not k.GetExitCodeProcess(h, ctypes.byref(code)) or code.value != 259:     # 259 = STILL_ACTIVE
                return False, None
            ft = [k.FileTime() for _ in range(4)]                # creation, exit, kernel, user
            if not k.GetProcessTimes(h, *(ctypes.byref(f) for f in ft)):
                return True, None
            return True, (ft[0].hi << 32) | ft[0].lo
        finally:
            k.CloseHandle(h)
    if sys.platform.startswith("linux"):
        try:
            text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False, None
        fields = text.rsplit(")", 1)[-1].split()               # fields[0] is field 3 (state); starttime is field 22
        if len(fields) < 20: return False, None
        return fields[0] != "Z", int(fields[19])
    try:
        os.kill(pid, 0)
        return True, None
    except ProcessLookupError:
        return False, None
    except (PermissionError, OverflowError):
        return True, None


def _process_created(pid: int) -> int | None:
    return _probe(pid)[1]


def _pid_alive(pid: int, created: int | None = None) -> bool:
    """Alive, and (when both sides know it) the same process: a reused pid has a different creation time."""
    alive, observed = _probe(pid)
    if not alive: return False
    return not (created is not None and observed is not None and created != observed)


# ---- files -----------------------------------------------------------------------------------------------------

def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        for attempt in range(5):                              # Windows refuses replace while a reader has it open
            try:
                os.replace(tmp, path); return
            except PermissionError:
                if attempt == 4: raise
                time.sleep(0.05)
    finally:
        tmp.unlink(missing_ok=True)


def _valid_lock(info) -> bool:
    if not isinstance(info, dict) or not isinstance(info.get("log"), str) or not isinstance(info.get("stamp"), str):
        return False
    pid = info.get("pid")
    if isinstance(pid, int) and not isinstance(pid, bool): return True
    return pid is None and info.get("provisional") is True


def _summary_from_log(log: str) -> dict | None:
    """The engine's summary: a one-line JSON object as the last line, or the pretty-printed block that ends the log."""
    try:
        lines = Path(log).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    lines = [ln.rstrip("\r") for ln in lines if ln.strip()]
    if not lines: return None
    last = lines[-1].strip()
    starts = []
    if last.startswith("{") and last.endswith("}") and len(lines[-1]) > 1:
        starts = [len(lines) - 1]
    elif last == "}":
        starts = [i for i in range(len(lines) - 2, -1, -1) if lines[i] == "{"]       # the block opens at column 0
    for i in starts:
        try: value = json.loads("\n".join(lines[i:]))
        except json.JSONDecodeError: continue
        if isinstance(value, dict): return value
    return None


class SweepLauncher:
    def __init__(self, data_dir: Path, command: list[str] | None = None):
        self.dir = Path(data_dir) / "ui" / "sweeps"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.command = command                     # tests replace the real engine command with a small script
        self._proc: subprocess.Popen | None = None
        self._mu = threading.RLock()

    @property
    def lock(self) -> Path:
        return self.dir / "active.json"

    # -- internals
    def _note(self, message: str) -> None:
        """Problems with the launcher's own files go here, never into the web request."""
        try:
            with open(self.dir / "launcher-errors.log", "a", encoding="utf-8") as fh:
                fh.write(f"{datetime.now(UTC).isoformat()} {message}\n")
        except OSError:
            pass

    def _create_lock(self, text: str) -> None:
        """Create active.json only if it does not exist, with its content already complete (FileExistsError if taken)."""
        tmp = self.dir / f"active.json.{uuid.uuid4().hex[:8]}.new"
        tmp.write_text(text, encoding="utf-8")
        try:
            try:
                os.link(tmp, self.lock)                      # atomic, and never visible half written
            except FileExistsError:
                raise
            except (OSError, NotImplementedError):           # filesystem without hard links: exclusive create
                with open(self.lock, "x", encoding="utf-8") as fh:
                    fh.write(text)
        finally:
            tmp.unlink(missing_ok=True)

    def _drop_lock(self) -> None:
        try: self.lock.unlink(missing_ok=True)
        except OSError as exc: self._note(f"could not remove the lock: {exc!r}")

    def _lock_mtime(self) -> float:
        try: return self.lock.stat().st_mtime
        except OSError: return time.time()

    def _retire(self, info: dict, exit_code: int | None, reason: str, mtime: float | None = None) -> None:
        """Move the lock to a history record (exit code, reason, and the engine's summary of a clean exit)."""
        stamp = info.get("stamp")
        if not isinstance(stamp, str) or not re.fullmatch(r"[0-9A-Za-z]{1,40}", stamp):
            stamp = datetime.fromtimestamp(mtime if mtime is not None else self._lock_mtime(), UTC).strftime(STAMP_FORMAT)
        log = info.get("log") if isinstance(info.get("log"), str) else ""
        summary = _summary_from_log(log) if exit_code == 0 and log else None
        record = {"pid": None, "argv": [], "started": None, "request": {}, **info, "stamp": stamp, "log": log,
                  "exit_code": exit_code, "reason": reason, "finished": datetime.now(UTC).isoformat(),
                  "summary": summary}
        try:
            _write_atomic(self.dir / f"{stamp}.json", json.dumps(record))
        except (OSError, TypeError, ValueError) as exc:
            self._note(f"could not write the history record {stamp}: {exc!r}")
        self._drop_lock()
        self._proc = None

    def _reap(self) -> None:
        """No lock any more: let go of a finished child that someone else already retired."""
        if self._proc is not None and self._proc.poll() is not None:
            self._proc = None

    def _active(self) -> dict | None:
        raw = None
        for attempt in range(5):
            try:
                raw = self.lock.read_text(encoding="utf-8"); break
            except FileNotFoundError:
                self._reap(); return None
            except UnicodeDecodeError:
                break                                         # not text: corrupt
            except OSError as exc:                            # e.g. a sharing violation while another process replaces it
                if attempt == 4:
                    self._note(f"could not read the lock: {exc!r}"); return None      # not retired: it may be live
                time.sleep(0.05)
        mtime = self._lock_mtime()
        try: info = json.loads(raw) if raw is not None else None
        except ValueError: info = None
        if not _valid_lock(info):
            self._retire(info if isinstance(info, dict) else {}, None, "corrupt-lock", mtime); return None
        pid = info["pid"]
        if pid is None:                                       # another process is between lock and spawn
            if time.time() - mtime < PROVISIONAL_TTL_S: return info
            self._retire(info, None, "stale-lock", mtime); return None
        if self._proc is not None and self._proc.pid == pid:
            code = self._proc.poll()
            if code is None: return info
            self._retire(info, code, "exited", mtime); return None
        created = info.get("created") if isinstance(info.get("created"), int) else None
        if _pid_alive(pid, created):                          # started by an earlier dashboard process: re-attach
            return info
        self._retire(info, None, "stale-lock", mtime); return None

    # -- public
    def start(self, req: SweepRequest) -> dict:
        req.validate()
        with self._mu:
            stamp = datetime.now(UTC).strftime(STAMP_FORMAT)
            log = self.dir / f"{stamp}.log"
            argv = self.command or req.argv()
            env = {**os.environ, **req.env(), "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1"}
            provisional = {"pid": None, "provisional": True, "argv": argv, "started": datetime.now(UTC).isoformat(),
                           "log": str(log), "request": asdict(req), "stamp": stamp}
            for attempt in (0, 1):
                try:
                    self._create_lock(json.dumps(provisional)); break
                except FileExistsError:
                    if attempt or self._active() is not None:
                        raise SweepActive("a sweep is already running in this data directory") from None
            flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
            try:
                with open(log, "w", encoding="utf-8") as fh:     # the child holds its own handle
                    proc = subprocess.Popen(argv, stdout=fh, stderr=subprocess.STDOUT, env=env, creationflags=flags)
            except BaseException:
                log.unlink(missing_ok=True); self._drop_lock(); raise
            self._proc = proc
            info = {**provisional, "pid": proc.pid, "created": _process_created(proc.pid)}
            info.pop("provisional")
            try:
                _write_atomic(self.lock, json.dumps(info))
            except BaseException:
                proc.kill(); proc.wait(timeout=10)
                self._proc = None; log.unlink(missing_ok=True); self._drop_lock(); raise
            return info

    def active(self) -> dict | None:
        with self._mu:
            return self._active()

    def tail(self, n: int = 200) -> list[str]:
        with self._mu:
            info = self._active()
            if info is None:
                hist = self.history()
                info = hist[0] if hist else None
        if not info or not isinstance(info.get("log"), str) or not info["log"]: return []
        try:
            lines = Path(info["log"]).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
        return [ln.rstrip("\r") for ln in lines[-n:] if ln.strip()]

    def stop(self) -> bool:
        """True when the sweep is gone and recorded as stopped. False when there is none, or when a sweep started by
        another dashboard process is still alive after the signal (the lock is kept; try again)."""
        with self._mu:
            info = self._active()
            if info is None or info["pid"] is None: return False
            pid = info["pid"]
            if self._proc is not None and self._proc.pid == pid:
                self._proc.terminate()
                try: code = self._proc.wait(timeout=30)
                except subprocess.TimeoutExpired: self._proc.kill(); code = self._proc.wait(timeout=10)
                self._retire(info, code, "stopped"); return True
            created = info.get("created") if isinstance(info.get("created"), int) else None
            if not _pid_alive(pid, created):                 # not our sweep any more: never signal an unrelated pid
                self._retire(info, None, "stale-lock"); return False
            try: os.kill(pid, signal.SIGTERM)
            except OSError: pass
            deadline = time.monotonic() + STOP_WAIT_S
            while time.monotonic() < deadline:
                if not _pid_alive(pid, created): break
                time.sleep(0.1)
            else:
                return False
            self._retire(info, None, "stopped")
            return True

    def history(self) -> list[dict]:
        out = []
        for p in sorted(self.dir.glob("*.json"), reverse=True):
            if p.name == "active.json": continue
            try: rec = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError): continue
            if isinstance(rec, dict): out.append(rec)
        return out
