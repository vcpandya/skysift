"""The Dossiers page's background jobs (dossiers spec 5.4 and 5.9): ``python -m skysift dossier`` or ``pack`` for one
target, started from the dashboard, one at a time. The launcher is the sweep's (``ui.sweeps.SweepLauncher``: a lock
created before the child is spawned, a log, a history record when it ends), in its own directory,
``<data_dir>/ui/dossier_jobs``, so a dossier job and a sweep never share a lock and a dossier job never touches the
sweep's stop request."""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from skysift.config import Thresholds
from skysift.ui.sweeps import SweepActive, SweepLauncher

KINDS = ("dossier", "pack")
# a target id as the engine writes them (kic-006146676, gaia-dr3-...): a subset of the queue search's characters
# (routes.review.SEARCH) that starts with a letter or digit, so it is never "..", a path or an option
TARGET_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}")
JobBusy = SweepActive


def valid_target_id(target_id: str) -> bool:
    return isinstance(target_id, str) and TARGET_ID.fullmatch(target_id) is not None and ".." not in target_id


@dataclass
class DossierRequest:
    kind: str            # "dossier" | "pack"
    target_id: str
    force: bool = False

    def validate(self, thresholds: Thresholds | None = None) -> None:
        """Raise ValueError for a kind other than the two words or a target id that is not one."""
        if self.kind not in KINDS:
            raise ValueError(f"unknown job {self.kind!r}; one of {', '.join(KINDS)}")
        if not valid_target_id(self.target_id):
            raise ValueError("a target id is 1 to 64 letters, digits and the characters _ . : -, starting with a "
                             "letter or a digit")

    def argv(self, python: str = sys.executable) -> list[str]:
        a = [python, "-m", "skysift", self.kind, "--target", self.target_id]
        return a + ["--force"] if self.force else a

    def env(self) -> dict:
        return {}


class DossierJobs(SweepLauncher):
    """``SweepLauncher`` in ``<data_dir>/ui/dossier_jobs``: ``start`` raises ``JobBusy`` while a job runs, ``active``
    returns the lock with its ``request`` (``kind``, ``target_id``, ``force``), ``history`` the finished jobs newest
    first. ``command`` replaces the engine command in tests."""

    def __init__(self, data_dir: Path, command: list[str] | None = None):
        super().__init__(data_dir, command=command)
        self.dir = Path(data_dir) / "ui" / "dossier_jobs"
        self.dir.mkdir(parents=True, exist_ok=True)

    @property
    def stop_file(self) -> Path:
        """The launcher removes its stop file on every start and retirement; this one is the job directory's own, so
        a dossier job never deletes the stop request a running sweep is waiting for."""
        return self.dir / "stop-request"

    def start(self, req: DossierRequest, thresholds: Thresholds | None = None) -> dict:
        return super().start(req, thresholds)

    def tail(self, n: int = 50) -> list[str]:
        return super().tail(n)

    def for_target(self, target_id: str) -> tuple[dict | None, dict | None]:
        """(the running job when it is for this target, the target's most recent finished job), either None."""
        active = self.active()
        running = active if active and (active.get("request") or {}).get("target_id") == target_id else None
        last = next((h for h in self.history() if (h.get("request") or {}).get("target_id") == target_id), None)
        return running, last
