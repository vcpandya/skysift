from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select

from skysift.store.blob import Blob
from skysift.store.db import ArtifactRow, Store


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
        if self.blob.exists(art["local_path"]):
            self.blob.delete(art["local_path"])
        self.store.mark_artifact_deleted(art["sha256"])
        return art["nbytes"]

    def release_target(self, target_id: str) -> int:
        if self.policy.mode != "stream":
            return 0
        return sum(self._delete(a) for a in self.store.unpinned_artifacts(target_id))

    def cached_bytes(self) -> int:
        return sum(a["nbytes"] for a in self.store.unpinned_artifacts()) + self._pinned_bytes()

    def _pinned_bytes(self) -> int:
        with self.store.session() as s:
            return s.scalar(select(func.coalesce(func.sum(ArtifactRow.nbytes), 0)).where(
                ArtifactRow.pinned.is_(True), ArtifactRow.deleted_at.is_(None)))

    def enforce_budget(self) -> int:
        if self.policy.mode != "budget":
            return 0
        freed = 0
        for art in self.store.unpinned_artifacts():        # least recently used first
            if self.cached_bytes() <= self.policy.budget_bytes:
                break
            freed += self._delete(art)
        if self.cached_bytes() > self.policy.budget_bytes:
            raise DiskBudgetExceeded(
                f"cache {self.cached_bytes()} B over budget {self.policy.budget_bytes} B and everything left is pinned evidence; "
                "raise SKYSIFT_RETENTION budget or move packs to S3")
        return freed
