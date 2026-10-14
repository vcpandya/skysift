"""Start, watch and stop one engine sweep from the dashboard. The sweep is a child `python -m skysift run` (never a
console .exe), its output goes to a log file, and a lock file allows one sweep per data directory. Interrupted
runs need no cleanup: the engine's start_run marks them superseded on the next sweep."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from skysift.config import Thresholds
from skysift.store.retention import RetentionPolicy

PERIODOGRAM_MODES = ("all", "signals", "off")


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


def _pid_alive(pid: int) -> bool:
    if pid <= 0: return False
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)              # PROCESS_QUERY_LIMITED_INFORMATION
        if not h: return False
        try:
            code = ctypes.c_ulong()
            ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
            return bool(ok) and code.value == 259            # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0); return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class SweepLauncher:
    def __init__(self, data_dir: Path, command: list[str] | None = None):
        self.dir = Path(data_dir) / "ui" / "sweeps"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.command = command                     # tests replace the real engine command with a small script
        self._proc: subprocess.Popen | None = None
        self._log_fh = None

    @property
    def lock(self) -> Path:
        return self.dir / "active.json"

    def start(self, req: SweepRequest) -> dict:
        req.validate()
        if self.active() is not None:
            raise SweepActive("a sweep is already running in this data directory")
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        log = self.dir / f"{stamp}.log"
        argv = self.command or req.argv()
        env = {**os.environ, **req.env(), "PYTHONUNBUFFERED": "1"}
        self._log_fh = open(log, "w", encoding="utf-8")       # noqa: SIM115  (handed to the child; closed on finish)
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        self._proc = subprocess.Popen(argv, stdout=self._log_fh, stderr=subprocess.STDOUT, env=env, creationflags=flags)
        info = {"pid": self._proc.pid, "argv": argv, "started": datetime.now(UTC).isoformat(), "log": str(log),
                "request": asdict(req), "stamp": stamp}
        self.lock.write_text(json.dumps(info), encoding="utf-8")
        return info

    def _finish(self, info: dict, exit_code: int | None) -> None:
        """Move the lock to a history record with the exit code and the engine's summary (the last JSON on stdout)."""
        summary = None
        try:
            lines = Path(info["log"]).read_text(encoding="utf-8", errors="replace").splitlines()
            for i in range(len(lines) - 1, -1, -1):
                if lines[i].strip() == "{":
                    try: summary = json.loads("\n".join(lines[i:])); break
                    except json.JSONDecodeError: continue
                if lines[i].startswith("{"):
                    try: summary = json.loads(lines[i]); break
                    except json.JSONDecodeError: continue
        except OSError:
            pass
        record = {**info, "exit_code": exit_code, "finished": datetime.now(UTC).isoformat(), "summary": summary}
        (self.dir / f"{info.get('stamp') or 'unknown'}.json").write_text(json.dumps(record), encoding="utf-8")
        self.lock.unlink(missing_ok=True)
        if self._log_fh: self._log_fh.close(); self._log_fh = None
        self._proc = None

    def active(self) -> dict | None:
        if not self.lock.exists():
            return None
        info = json.loads(self.lock.read_text(encoding="utf-8"))
        if self._proc is not None and self._proc.pid == info["pid"]:
            code = self._proc.poll()
            if code is None: return info
            self._finish(info, code); return None
        if _pid_alive(info["pid"]):                 # started by an earlier dashboard process: re-attach read-only
            return info
        self._finish(info, None); return None

    def tail(self, n: int = 200) -> list[str]:
        info = self.active() or (self.history()[0] if self.history() else None)
        if info is None: return []
        try:
            lines = Path(info["log"]).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
        return [ln.rstrip("\r") for ln in lines[-n:] if ln.strip()]

    def stop(self) -> bool:
        info = self.active()
        if info is None: return False
        if self._proc is not None and self._proc.pid == info["pid"]:
            self._proc.terminate()
            try: code = self._proc.wait(timeout=30)
            except subprocess.TimeoutExpired: self._proc.kill(); code = self._proc.wait(timeout=10)
        else:
            try: os.kill(info["pid"], signal.SIGTERM)
            except OSError: pass
            code = None
        self._finish(info, code)
        return True

    def history(self) -> list[dict]:
        out = []
        for p in sorted(self.dir.glob("*.json"), reverse=True):
            if p.name == "active.json": continue
            try: out.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError): continue
        return out
