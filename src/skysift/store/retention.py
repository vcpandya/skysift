from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import func, select

from skysift.store.blob import Blob
from skysift.store.db import ArtifactRow, Store

log = logging.getLogger(__name__)


class DiskBudgetExceeded(RuntimeError):
    pass


@dataclass
class RetentionPolicy:
    mode: str                 # keep | stream | budget
    budget_bytes: int = 0

    @classmethod
    def parse(cls, text: str) -> RetentionPolicy:
        text = text.strip().lower()
        if text in ("keep", "stream"):
            return cls(text)
        if text.startswith("budget:"):
            return cls("budget", int(float(text.split(":", 1)[1]) * 1024**3))
        raise ValueError(f"bad retention: {text}")


class Retention:
    def __init__(self, policy: RetentionPolicy, store: Store, blob: Blob):
        self.policy, self.store, self.blob = policy, store, blob

    def _delete(self, art: dict) -> int:
        """Delete one file and mark its row deleted; returns the bytes freed. A file the OS will not let go of
        (an antivirus lock on Windows) keeps its live row, so it is counted and retried by a later sweep."""
        try:
            if self.blob.exists(art["local_path"]):
                self.blob.delete(art["local_path"])
        except OSError as e:
            log.warning("could not delete %s (%s); keeping its row for a later sweep", art["local_path"], e)
            return 0
        self.store.mark_artifact_deleted(art["sha256"])
        return art["nbytes"]

    def release_target(self, target_id: str) -> int:
        """Stream mode: delete the target's unpinned raw files once its run is written. Summaries (periodogram
        envelopes, a few kB) stay: they are the null set the threshold calibration needs and are evicted only by budget."""
        if self.policy.mode != "stream":
            return 0
        return sum(self._delete(a) for a in self.store.unpinned_artifacts(target_id, kind="raw"))

    def cached_bytes(self) -> int:
        return sum(a["nbytes"] for a in self.store.unpinned_artifacts()) + self._pinned_bytes()

    def _pinned_bytes(self) -> int:
        with self.store.session() as s:
            return s.scalar(select(func.coalesce(func.sum(ArtifactRow.nbytes), 0)).where(
                ArtifactRow.pinned.is_(True), ArtifactRow.deleted_at.is_(None)))

    def enforce_budget(self, exclude_targets: set[str] | None = None) -> int:
        """Evict unpinned files, least recently used first, until the cache fits the budget. Files of
        ``exclude_targets`` (in flight: fetched, not yet vetted) are never evicted, and being over budget only
        because of them is not an error: they are released or become evictable once their target is written."""
        if self.policy.mode != "budget":
            return 0
        freed = 0
        for art in self.store.unpinned_artifacts(exclude_targets=exclude_targets):     # least recently used first
            if self.cached_bytes() <= self.policy.budget_bytes:
                break
            freed += self._delete(art)
        in_flight = sum(a["nbytes"] for a in self.store.unpinned_artifacts()
                        if exclude_targets and a["target_id"] in exclude_targets)
        if self.cached_bytes() - in_flight > self.policy.budget_bytes:
            raise DiskBudgetExceeded(
                f"cache {self.cached_bytes()} B over budget {self.policy.budget_bytes} B and everything left is pinned "
                "evidence or could not be deleted; raise SKYSIFT_RETENTION budget or move packs to S3")
        return freed
