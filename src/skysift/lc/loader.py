"""The light curve a stored signal was measured on, for the engine (the recheck re-vets on it) and the dashboard (the
signal page plots it). The loader serves the pinned raw file from the blob, or re-fetches it through the adapter with a
SHA-256 check when retention released it (parent spec, data flow step 5)."""
from __future__ import annotations

import hashlib
import logging

from sqlalchemy import select

from skysift.lc.kbonus import parse_kbonus_fits
from skysift.models import LightCurve, Target
from skysift.net import ServiceUnavailable
from skysift.sources import PARSERS
from skysift.sources.factory import make_source
from skysift.store.db import ArtifactRow, Store

log = logging.getLogger(__name__)
TARGET_FIELDS = ("source", "target_id", "ra", "dec", "gaia_id", "kic", "gmag")


class IntegrityError(ValueError):
    """The light curve on hand cannot be trusted as the evidence: the archive file changed since the sweep, or the
    stored raw file does not parse. Distinct from other ValueErrors so the API never reports a bug as an integrity
    failure."""


class _HoldBlob:
    """Stands in for the blob while the adapter re-fetches: keeps the bytes in memory and stores nothing, so the loader
    can verify the hash before anything is recorded."""

    def __init__(self):
        self.data: bytes | None = None
        self.name = "lightcurve.fits"

    def put(self, data: bytes, name_hint: str) -> tuple[str, str]:
        self.data, self.name = data, name_hint
        return hashlib.sha256(data).hexdigest(), f"held:{name_hint}"


class _ProvenanceStore:
    """The real store for the query log (MAST provenance) but with artifact recording switched off."""

    def __init__(self, store: Store):
        self._store = store

    def __getattr__(self, name):
        return getattr(self._store, name)

    def add_artifact(self, *args, **kwargs) -> None:
        return None


class LightCurveLoader:
    def __init__(self, store: Store, blob, source_factory=make_source, parsers: dict | None = None):
        self.store, self.blob, self.source_factory = store, blob, source_factory
        self.parsers = parsers if parsers is not None else PARSERS

    def _raw_rows(self, target_id: str) -> list[ArtifactRow]:
        with self.store.session() as s:
            rows = s.scalars(select(ArtifactRow).where(ArtifactRow.target_id == target_id, ArtifactRow.kind == "raw")
                             .order_by(ArtifactRow.created.desc())).all()
            s.expunge_all()
            return rows

    def load(self, target: dict, pin: bool = True) -> LightCurve:
        """The target's light curve from the stored raw file, else re-fetched and verified. ``pin`` keeps the raw file
        from budget retention, as the signal page needs; a list view (the card sparklines) passes False."""
        tid, source = target["target_id"], target.get("source", "kbonus_bkg")
        parse = self.parsers.get(source, parse_kbonus_fits)
        rows = self._raw_rows(tid)
        for row in rows:
            if row.deleted_at is None and self.blob.exists(row.local_path):
                try:
                    lc = parse(self.blob.get(row.local_path))
                except ServiceUnavailable:
                    raise
                except Exception as e:
                    raise IntegrityError(f"the stored raw file {row.sha256[:12]} for {tid} cannot be read: {e}") from e
                lc.meta.setdefault("target_id", tid)
                if pin:
                    self.store.pin_target_artifacts(tid)  # budget retention must not evict the file being viewed
                return lc
        # released by retention (or never fetched): re-fetch with nothing stored, verify, then record
        hold = _HoldBlob()
        try:
            src = self.source_factory(source, _ProvenanceStore(self.store), hold)
        except ValueError as e:                            # make_source: the source is not live
            raise ServiceUnavailable(f"no live adapter for source {source}: {e}") from e
        tgt = (Target(**{k: target[k] for k in TARGET_FIELDS if target.get(k) is not None},
                      meta=target.get("meta") or {}) if "ra" in target else target)
        lc, sha, url = src.fetch_lightcurve(tgt)
        known = {r.sha256 for r in rows}
        if known and sha not in known:
            raise IntegrityError(f"re-fetched file sha256 {sha[:12]} differs from the recorded {min(known)[:12]}; "
                                 "the archive file changed since the sweep")
        if hold.data is not None:
            _, loc = self.blob.put(hold.data, hold.name)
            self.store.add_artifact(sha, len(hold.data), url, loc, tid, kind="raw")
            self.store.pin_target_artifacts(tid)          # evidence the operator is looking at stays
        else:
            log.warning("adapter for %s returned no bytes; the re-fetched file for %s was not stored", source, tid)
        lc.meta.setdefault("target_id", tid)
        return lc
