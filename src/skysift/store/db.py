from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    event,
    func,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from skysift import jsonutil
from skysift.models import Signal, Target, TestResult

FINAL_STATUS = {"pass": "candidate_stage1", "fail": "rejected", "undetermined": "undetermined"}


def utcnow() -> datetime:
    return datetime.now(UTC)


def make_job_key(target_id: str, detector: str, detector_version: str, ladder_version: str) -> str:
    return f"{target_id}|{detector}|{detector_version}|{ladder_version}"


class Base(DeclarativeBase):
    pass


UNAVAILABLE_PREFIX = "unavailable:"      # run error text for a service outage; never counted as an attempt


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
    detector: Mapped[str] = mapped_column(String(32)); detector_version: Mapped[str] = mapped_column(String(32))
    ladder_version: Mapped[str] = mapped_column(String(16))
    started: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16), default="running")   # running|done|error
    error: Mapped[str] = mapped_column(Text, default="")
    periodogram_sha256: Mapped[str | None] = mapped_column(String(64))


# an 'error' run counts as an attempt unless it was superseded (interrupted sweep) or a service outage
_ATTEMPT_FILTERS = (RunRow.error != "superseded", RunRow.error.not_like(f"{UNAVAILABLE_PREFIX}%"))


class SignalRow(Base):
    __tablename__ = "signals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    target_id: Mapped[str] = mapped_column(String(64), index=True)
    detector: Mapped[str] = mapped_column(String(32)); detector_version: Mapped[str] = mapped_column(String(32))
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
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)


class DecisionRow(Base):
    """Append-only operator decisions; the candidate row carries only the current state."""
    __tablename__ = "decisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), index=True)
    decision: Mapped[str] = mapped_column(String(16))        # approve|reject|needs_data
    note: Mapped[str] = mapped_column(Text, default="")
    who: Mapped[str] = mapped_column(String(64), default="operator")
    utc: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ArtifactRow(Base):
    __tablename__ = "artifacts"
    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    nbytes: Mapped[int] = mapped_column(Integer); url: Mapped[str] = mapped_column(Text)
    local_path: Mapped[str] = mapped_column(Text); target_id: Mapped[str | None] = mapped_column(String(64), index=True)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    created: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_used: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)
    kind: Mapped[str] = mapped_column(String(16), default="raw", server_default="raw")


class QueryRow(Base):
    __tablename__ = "queries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    service: Mapped[str] = mapped_column(String(32)); url: Mapped[str] = mapped_column(Text)
    params: Mapped[str] = mapped_column(Text); utc: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    status: Mapped[int | None] = mapped_column(Integer); nbytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64)); error: Mapped[str] = mapped_column(Text, default="")
    target_id: Mapped[str | None] = mapped_column(String(64), index=True)


class Store:
    def __init__(self, database_url: str):
        if database_url.startswith("sqlite"):
            # I/O threads record artifacts while the main thread writes results: wait for locks, let readers through
            self.engine = create_engine(database_url, future=True, connect_args={"timeout": 30},
                                        json_serializer=jsonutil.dumps)

            @event.listens_for(self.engine, "connect")
            def _sqlite_pragmas(dbapi_conn, _record):
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL"); cur.execute("PRAGMA busy_timeout=30000"); cur.close()
        else:
            self.engine = create_engine(database_url, future=True, json_serializer=jsonutil.dumps)

    def init_schema(self) -> None:
        from skysift.store import migrations
        Base.metadata.create_all(self.engine)
        migrations.migrate(self.engine)

    @contextmanager
    def session(self):
        with Session(self.engine) as s, s.begin():
            yield s

    def upsert_target(self, t: Target) -> None:
        self.upsert_targets([t])

    def upsert_targets(self, targets: list[Target]) -> None:
        """Insert or update many targets in one transaction."""
        with self.session() as s:
            for t in targets:
                row = s.get(TargetRow, t.target_id)
                if row is None:
                    s.add(TargetRow(target_id=t.target_id, source=t.source, ra=t.ra, dec=t.dec, gaia_id=t.gaia_id,
                                    kic=t.kic, gmag=t.gmag, meta=t.meta))
                else:
                    row.ra, row.dec, row.gaia_id, row.kic, row.gmag, row.meta = (t.ra, t.dec, t.gaia_id, t.kic, t.gmag,
                                                                                 t.meta)

    def set_target_state(self, target_id: str, state: str) -> None:
        with self.session() as s:
            s.get(TargetRow, target_id).state = state

    def job_done(self, job_key: str) -> bool:
        with self.session() as s:
            return s.scalar(select(func.count()).select_from(RunRow).where(RunRow.job_key == job_key, RunRow.status == "done")) > 0

    def attempts(self, job_key: str) -> int:
        """Failed attempts of a job: its 'error' runs, not counting runs superseded after an interruption nor runs
        that failed because a service was unavailable (error text starting ``unavailable:``): an outage is not the
        target's fault and must not use up its three tries."""
        with self.session() as s:
            return s.scalar(select(func.count()).select_from(RunRow).where(
                RunRow.job_key == job_key, RunRow.status == "error", *_ATTEMPT_FILTERS))

    def error_capped_targets(self, max_attempts: int) -> int:
        """Targets whose latest job key has at least ``max_attempts`` failed attempts and no done run."""
        with self.session() as s:
            latest = select(func.max(RunRow.id)).group_by(RunRow.target_id)
            keys = select(RunRow.job_key).where(RunRow.id.in_(latest))
            done = select(RunRow.job_key).where(RunRow.status == "done")
            capped = (select(RunRow.job_key)
                      .where(RunRow.job_key.in_(keys), RunRow.job_key.not_in(done), RunRow.status == "error",
                             *_ATTEMPT_FILTERS)
                      .group_by(RunRow.job_key).having(func.count() >= max_attempts))
            return s.scalar(select(func.count()).select_from(capped.subquery()))

    def start_run(self, job_key, target_id, detector, detector_version, ladder_version) -> int:
        """Start a run. Earlier runs of the same job still marked 'running' were interrupted (a crash or a stopped
        sweep); they become 'error' / 'superseded' so status shows them and their pending rows are never mistaken
        for live work."""
        with self.session() as s:
            for old in s.scalars(select(RunRow).where(RunRow.job_key == job_key, RunRow.status == "running")):
                old.status, old.error, old.finished = "error", "superseded", utcnow()
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
            self._add_test_row(s, signal_id, r, duration_s)

    def _add_test_row(self, s: Session, signal_id: int, r: TestResult, duration_s: float) -> None:
        s.add(TestRow(signal_id=signal_id, rung=r.rung, status=r.status.value, metrics=r.metrics,
                      artifacts=r.artifacts, reason=r.reason, duration_s=duration_s))

    def record_results(self, run_id: int, target_id: str, items: list[dict], queries: list[dict],
                       pin_evidence: bool) -> dict:
        """Write one target's outcome in ONE transaction: signals with their tests and final statuses, the catalogue
        queries the worker made, evidence pins, the finished run and the target state. Either all of it lands or
        none does, so an exception or crash never leaves pending signals that a rerun would duplicate.

        ``items`` are the worker's signal entries ({"signal", "outcome", "tests", "timings"}); ``queries`` are the
        keyword arguments of ``log_query`` (plus an optional ISO ``utc``). Returns counts per outcome."""
        counts = {"pass": 0, "fail": 0, "undetermined": 0}
        with self.session() as s:
            for item in items:
                sig = Signal(**item["signal"])
                row = SignalRow(run_id=run_id, final_status=FINAL_STATUS[item["outcome"]], **sig.model_dump())
                s.add(row); s.flush()
                for r, dt in zip(item["tests"], item["timings"], strict=True):
                    self._add_test_row(s, row.id, TestResult(**r), dt)
                counts[item["outcome"]] += 1
            for q in queries:
                utc = datetime.fromisoformat(q["utc"]) if q.get("utc") else utcnow()
                s.add(QueryRow(service=q["service"], url=q["url"], params=jsonutil.dumps(q.get("params") or {}, default=str),
                               status=q.get("status"), nbytes=q.get("nbytes") or 0, sha256=q.get("sha256"),
                               error=q.get("error") or "", utc=utc, target_id=target_id))
            if pin_evidence:
                for a in s.scalars(select(ArtifactRow).where(ArtifactRow.target_id == target_id,
                                                              ArtifactRow.deleted_at.is_(None))):
                    a.pinned = True
            run = s.get(RunRow, run_id); run.status, run.error, run.finished = "done", "", utcnow()
            s.get(TargetRow, target_id).state = "done"
        return counts

    def add_artifact(self, sha256, nbytes, url, local_path, target_id=None, pinned=False, kind="raw") -> None:
        """Record a stored file. A known sha256 is resurrected: a row retention marked deleted describes a file that is
        back on disk after a re-fetch, so it must be counted, released and evictable again."""
        with self.session() as s:
            self._upsert_artifact(s, sha256, nbytes, url, local_path, target_id, pinned, kind)

    def _upsert_artifact(self, s: Session, sha256, nbytes, url, local_path, target_id, pinned, kind) -> None:
        row = s.get(ArtifactRow, sha256)
        if row is None:
            s.add(ArtifactRow(sha256=sha256, nbytes=nbytes, url=url, local_path=local_path, target_id=target_id,
                              pinned=pinned, kind=kind))
            return
        if row.deleted_at is not None: row.pinned = False      # a deleted row's old pin protects nothing
        row.deleted_at, row.local_path, row.nbytes, row.last_used, row.kind = None, local_path, nbytes, utcnow(), kind
        if target_id is not None: row.target_id = target_id
        if pinned: row.pinned = True

    def touch_artifact(self, sha256: str) -> None:
        with self.session() as s:
            row = s.get(ArtifactRow, sha256)
            if row: row.last_used = utcnow()

    def mark_artifact_deleted(self, sha256: str) -> None:
        with self.session() as s:
            s.get(ArtifactRow, sha256).deleted_at = utcnow()

    def unpinned_artifacts(self, target_id: str | None = None, exclude_targets: set[str] | None = None,
                           kind: str | None = None) -> list[dict]:
        """Live unpinned artifacts, least recently used first; ``exclude_targets`` leaves out in-flight targets' files."""
        with self.session() as s:
            q = select(ArtifactRow).where(ArtifactRow.pinned.is_(False), ArtifactRow.deleted_at.is_(None))
            if target_id: q = q.where(ArtifactRow.target_id == target_id)
            if exclude_targets:
                q = q.where(ArtifactRow.target_id.is_(None) | ArtifactRow.target_id.not_in(sorted(exclude_targets)))
            if kind: q = q.where(ArtifactRow.kind == kind)
            return [{"sha256": a.sha256, "nbytes": a.nbytes, "local_path": a.local_path, "url": a.url,
                     "last_used": a.last_used, "target_id": a.target_id, "kind": a.kind}
                    for a in s.scalars(q.order_by(ArtifactRow.last_used))]

    def pin_target_artifacts(self, target_id: str) -> None:
        with self.session() as s:
            for a in s.scalars(select(ArtifactRow).where(ArtifactRow.target_id == target_id,
                                                          ArtifactRow.deleted_at.is_(None))):
                a.pinned = True

    def log_query(self, service, url, params: dict, status, nbytes, sha256=None, error="", target_id=None) -> None:
        with self.session() as s:
            s.add(QueryRow(service=service, url=url, params=jsonutil.dumps(params, default=str), status=status,
                           nbytes=nbytes, sha256=sha256, error=error, target_id=target_id))

    def counts(self) -> dict:
        with self.session() as s:
            out = {}
            for name, model in [("targets", TargetRow), ("runs", RunRow), ("signals", SignalRow), ("tests", TestRow),
                                ("candidates", CandidateRow), ("artifacts", ArtifactRow), ("queries", QueryRow)]:
                out[name] = s.scalar(select(func.count()).select_from(model))
            return out
