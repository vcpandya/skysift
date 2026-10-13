from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from skysift.models import Signal, Target, TestResult


def utcnow() -> datetime:
    return datetime.now(UTC)


def make_job_key(target_id: str, detector: str, detector_version: str, ladder_version: str) -> str:
    return f"{target_id}|{detector}|{detector_version}|{ladder_version}"


class Base(DeclarativeBase):
    pass


class TargetRow(Base):
    __tablename__ = "targets"
    target_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    ra: Mapped[float] = mapped_column(Float); dec: Mapped[float] = mapped_column(Float)
    gaia_id: Mapped[str | None] = mapped_column(String(32)); kic: Mapped[int | None] = mapped_column(Integer)
    gmag: Mapped[float | None] = mapped_column(Float)
    priority: Mapped[float] = mapped_column(Float, default=0.0)
    state: Mapped[str] = mapped_column(String(16), default="new", index=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class RunRow(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_key: Mapped[str] = mapped_column(String(160), index=True)
    target_id: Mapped[str] = mapped_column(String(64), index=True)
    detector: Mapped[str] = mapped_column(String(32)); detector_version: Mapped[str] = mapped_column(String(16))
    ladder_version: Mapped[str] = mapped_column(String(16))
    started: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16), default="running")   # running|done|error
    error: Mapped[str] = mapped_column(Text, default="")


class SignalRow(Base):
    __tablename__ = "signals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    target_id: Mapped[str] = mapped_column(String(64), index=True)
    detector: Mapped[str] = mapped_column(String(32)); detector_version: Mapped[str] = mapped_column(String(16))
    period: Mapped[float] = mapped_column(Float); t0: Mapped[float] = mapped_column(Float)
    duration: Mapped[float] = mapped_column(Float); depth_ppm: Mapped[float] = mapped_column(Float)
    snr: Mapped[float] = mapped_column(Float); sde: Mapped[float] = mapped_column(Float)
    n_events: Mapped[int] = mapped_column(Integer)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
    branch: Mapped[str] = mapped_column(String(16), default="planet")
    final_status: Mapped[str] = mapped_column(String(16), default="pending")   # pending|rejected|candidate


class TestRow(Base):
    __tablename__ = "tests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), index=True)
    rung: Mapped[str] = mapped_column(String(32)); status: Mapped[str] = mapped_column(String(16))
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    artifacts: Mapped[list] = mapped_column(JSON, default=list)
    reason: Mapped[str] = mapped_column(Text, default="")
    duration_s: Mapped[float] = mapped_column(Float, default=0.0)
    created: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CandidateRow(Base):
    __tablename__ = "candidates"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), unique=True)
    branch: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="review")   # review|approved|rejected|published
    pack_path: Mapped[str] = mapped_column(Text, default=""); doi: Mapped[str] = mapped_column(String(64), default="")
    note: Mapped[str] = mapped_column(Text, default="")


class ArtifactRow(Base):
    __tablename__ = "artifacts"
    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    nbytes: Mapped[int] = mapped_column(Integer); url: Mapped[str] = mapped_column(Text)
    local_path: Mapped[str] = mapped_column(Text); target_id: Mapped[str | None] = mapped_column(String(64), index=True)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    created: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_used: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)


class QueryRow(Base):
    __tablename__ = "queries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    service: Mapped[str] = mapped_column(String(32)); url: Mapped[str] = mapped_column(Text)
    params: Mapped[str] = mapped_column(Text); utc: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    status: Mapped[int | None] = mapped_column(Integer); nbytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64)); error: Mapped[str] = mapped_column(Text, default="")


class Store:
    def __init__(self, database_url: str):
        self.engine = create_engine(database_url, future=True)

    def init_schema(self) -> None:
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self):
        with Session(self.engine) as s, s.begin():
            yield s

    def upsert_target(self, t: Target) -> None:
        with self.session() as s:
            row = s.get(TargetRow, t.target_id)
            if row is None:
                s.add(TargetRow(target_id=t.target_id, source=t.source, ra=t.ra, dec=t.dec, gaia_id=t.gaia_id,
                                kic=t.kic, gmag=t.gmag, meta=t.meta))
            else:
                row.ra, row.dec, row.gaia_id, row.kic, row.gmag, row.meta = t.ra, t.dec, t.gaia_id, t.kic, t.gmag, t.meta

    def set_target_state(self, target_id: str, state: str) -> None:
        with self.session() as s:
            s.get(TargetRow, target_id).state = state

    def job_done(self, job_key: str) -> bool:
        with self.session() as s:
            return s.scalar(select(func.count()).select_from(RunRow).where(RunRow.job_key == job_key, RunRow.status == "done")) > 0

    def start_run(self, job_key, target_id, detector, detector_version, ladder_version) -> int:
        with self.session() as s:
            r = RunRow(job_key=job_key, target_id=target_id, detector=detector, detector_version=detector_version,
                       ladder_version=ladder_version)
            s.add(r); s.flush(); return r.id

    def finish_run(self, run_id: int, status: str, error: str = "") -> None:
        with self.session() as s:
            r = s.get(RunRow, run_id); r.status, r.error, r.finished = status, error, utcnow()

    def add_signal(self, sig: Signal, run_id: int) -> int:
        with self.session() as s:
            existing = s.scalars(select(SignalRow).where(SignalRow.run_id == run_id, SignalRow.detector == sig.detector,
                                                         SignalRow.period.between(sig.period - 1e-4, sig.period + 1e-4))).first()
            if existing:
                return existing.id
            row = SignalRow(run_id=run_id, **sig.model_dump())
            s.add(row); s.flush(); return row.id

    def set_signal_status(self, signal_id: int, final_status: str, branch: str | None = None) -> None:
        with self.session() as s:
            row = s.get(SignalRow, signal_id); row.final_status = final_status
            if branch: row.branch = branch

    def add_test(self, signal_id: int, r: TestResult, duration_s: float) -> None:
        with self.session() as s:
            s.add(TestRow(signal_id=signal_id, rung=r.rung, status=r.status.value, metrics=r.metrics,
                          artifacts=r.artifacts, reason=r.reason, duration_s=duration_s))

    def add_artifact(self, sha256, nbytes, url, local_path, target_id=None, pinned=False) -> None:
        with self.session() as s:
            if s.get(ArtifactRow, sha256) is None:
                s.add(ArtifactRow(sha256=sha256, nbytes=nbytes, url=url, local_path=local_path, target_id=target_id, pinned=pinned))

    def touch_artifact(self, sha256: str) -> None:
        with self.session() as s:
            row = s.get(ArtifactRow, sha256)
            if row: row.last_used = utcnow()

    def mark_artifact_deleted(self, sha256: str) -> None:
        with self.session() as s:
            s.get(ArtifactRow, sha256).deleted_at = utcnow()

    def unpinned_artifacts(self, target_id: str | None = None) -> list[dict]:
        with self.session() as s:
            q = select(ArtifactRow).where(ArtifactRow.pinned.is_(False), ArtifactRow.deleted_at.is_(None))
            if target_id: q = q.where(ArtifactRow.target_id == target_id)
            return [{"sha256": a.sha256, "nbytes": a.nbytes, "local_path": a.local_path, "url": a.url, "last_used": a.last_used}
                    for a in s.scalars(q.order_by(ArtifactRow.last_used))]

    def pin_target_artifacts(self, target_id: str) -> None:
        with self.session() as s:
            for a in s.scalars(select(ArtifactRow).where(ArtifactRow.target_id == target_id)):
                a.pinned = True

    def log_query(self, service, url, params: dict, status, nbytes, sha256=None, error="") -> None:
        with self.session() as s:
            s.add(QueryRow(service=service, url=url, params=json.dumps(params, default=str), status=status,
                           nbytes=nbytes, sha256=sha256, error=error))

    def counts(self) -> dict:
        with self.session() as s:
            out = {}
            for name, model in [("targets", TargetRow), ("runs", RunRow), ("signals", SignalRow), ("tests", TestRow),
                                ("candidates", CandidateRow), ("artifacts", ArtifactRow), ("queries", QueryRow)]:
                out[name] = s.scalar(select(func.count()).select_from(model))
            return out
