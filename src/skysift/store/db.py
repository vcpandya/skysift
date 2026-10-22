from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
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
    and_,
    case,
    create_engine,
    event,
    func,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from skysift import jsonutil
from skysift.models import Signal, Target, TestResult

FINAL_STATUS = {"pass": "candidate_stage1", "pass_pixels": "candidate_stage2", "fail": "rejected",
                "undetermined": "undetermined"}
# every final_status the engine writes; a reviewer's decision sits beside it (DECISION_STATUS) and never replaces it,
# except that a rejection also marks the signal rejected
ENGINE_STATUSES = ("pending", "candidate_stage1", "candidate_stage2", "undetermined", "rejected", "superseded")
DECISION_STATUS = {"approve": "approved", "reject": "rejected", "needs_data": "review"}


def utcnow() -> datetime:
    return datetime.now(UTC)


def make_job_key(target_id: str, detector: str, detector_version: str, ladder_version: str) -> str:
    return f"{target_id}|{detector}|{detector_version}|{ladder_version}"


class Base(DeclarativeBase):
    pass


UNAVAILABLE_PREFIX = "unavailable:"      # run error text for a service outage; never counted as an attempt
POOL_BROKE_ERROR = f"{UNAVAILABLE_PREFIX} worker pool broke"     # several runs held at a pool break: none charged
WORKER_DIED_ERROR = "worker died: pool broke"                    # one run held: its target is charged an attempt
PERIODOGRAM_URL = "skysift://periodogram/{target_id}"     # artifact url of a target's periodogram summary
PIXELS_URL = "skysift://pixels/{signal_id}"               # artifact url of rung 5's per-season arrays (pixels.npz)
RECHECK_JOB_PREFIX = "recheck:"     # job keys of the runs the stage-2 recheck starts (a P/2 signal's run)


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
    final_status: Mapped[str] = mapped_column(String(16), default="pending")   # one of ENGINE_STATUSES
    parent_signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"), index=True)   # a recheck's source
    ladder_version: Mapped[str | None] = mapped_column(String(16))      # the ladder that vetted it (None: before 002)


class TestRow(Base):
    __test__ = False  # not a pytest test class
    __tablename__ = "tests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), index=True)
    rung: Mapped[str] = mapped_column(String(32)); status: Mapped[str] = mapped_column(String(16))
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    artifacts: Mapped[list] = mapped_column(JSON, default=list)
    reason: Mapped[str] = mapped_column(Text, default="")
    duration_s: Mapped[float] = mapped_column(Float, default=0.0)
    created: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    recheck_id: Mapped[int | None] = mapped_column(ForeignKey("rechecks.id"))    # the recheck that wrote this row


class RecheckRow(Base):
    """Append-only history of the stage-2 recheck: one row per step applied to a signal. The test rows a step writes
    point back here through ``TestRow.recheck_id``."""
    __tablename__ = "rechecks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), index=True)
    step: Mapped[str] = mapped_column(String(16))              # ladder|period|catalogue|pixels
    ladder_version: Mapped[str] = mapped_column(String(16))
    outcome: Mapped[str] = mapped_column(String(16))           # pass|fail|undetermined|skipped|superseded
    reason: Mapped[str] = mapped_column(Text, default="")
    new_signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"))   # the P/2 signal a period step made
    utc: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


REVIEWER_DECIDED_REASON = "reviewer decided meanwhile"     # a step whose signal a reviewer decided on while it ran
CHILD_EXISTS_REASON = "child already exists"     # a P/2 step that lost the race with an overlapping recheck


@dataclass
class ChildWrite:
    """The signal a recheck step derived (the P/2 re-measure), with its own run and its test rows."""
    job_key: str
    signal: Signal
    ladder_version: str
    branch: str
    tests: list[tuple[TestResult, float]] = field(default_factory=list)


@dataclass
class StepWrite:
    """Everything one recheck step writes, computed before the store is touched: its rechecks row, the test rows on
    the signal, and the derived signal when there is one (``Store.commit_recheck_step`` writes it all at once)."""
    signal_id: int
    step: str
    ladder_version: str
    outcome: str
    reason: str = ""
    tests: list[tuple[TestResult, float]] = field(default_factory=list)
    child: ChildWrite | None = None


def _get(t, key: str):
    return t[key] if isinstance(t, dict) else getattr(t, key)


def _latest_per_rung(tests) -> dict:
    """The newest row (highest id) of each rung. Rows not yet stored (no id) are ordered by their list position."""
    out: dict[str, tuple] = {}
    for i, t in enumerate(tests):
        rid = t.get("id") if isinstance(t, dict) else t.id
        key = i if rid is None else rid
        rung = _get(t, "rung")
        if rung not in out or key > out[rung][0]:
            out[rung] = (key, t)
    return {rung: t for rung, (_key, t) in out.items()}


# the undetermined reasons only the pixels can settle (recheck.PIXEL_REASON_PREFIXES, which sends them to step 4; a
# test holds the two equal, as recheck imports this module)
PIXEL_SETTLED_PREFIXES = ("season-dependent depth", "aperture-dependent depth", "systematic_alias", "period matches")


def pixels_settle(latest: dict) -> bool:
    """Spec 4.3: a pixel pass promotes a signal that was undetermined only for reasons the pixels settle. True when
    the latest ``rung5_pixels`` row passed and every other latest row that gave no verdict gave one of those."""
    pixels = latest.get("rung5_pixels")
    if pixels is None or str(_get(pixels, "status")) != "pass":
        return False
    return all(str((t.get("reason") if isinstance(t, dict) else t.reason) or "").startswith(PIXEL_SETTLED_PREFIXES)
               for rung, t in latest.items() if rung != "rung5_pixels" and str(_get(t, "status")) == "undetermined")


def status_from_tests(tests: list[dict] | list[TestRow]) -> str:
    """The engine outcome the latest row of each rung gives: "fail" if any failed, else "undetermined" if any gave
    no verdict (unless a pixel pass settles every such row, ``pixels_settle``), else "pass"; "pending" when the
    signal has no test rows."""
    latest = _latest_per_rung(tests)
    statuses = {str(_get(t, "status")) for t in latest.values()}
    if not statuses:
        return "pending"
    if "fail" in statuses:
        return "fail"
    return "undetermined" if "undetermined" in statuses and not pixels_settle(latest) else "pass"


def final_status_for(outcome: str, tests) -> str:
    """The final status an engine outcome means, through FINAL_STATUS: a pass whose latest ``rung5_pixels`` row
    passed is a ``pass_pixels`` (stage 2), any other pass is stage 1; "pending" stays pending."""
    if outcome == "pending":
        return "pending"
    if outcome == "pass":
        pixels = _latest_per_rung(tests).get("rung5_pixels")
        if pixels is not None and str(_get(pixels, "status")) == "pass":
            outcome = "pass_pixels"
    return FINAL_STATUS[outcome]


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
    # raw (a light curve, released in stream mode) | summary (periodogram envelope) | dossier | tpf (a Kepler target
    # pixel file, evicted only by budget unless pinned) | pixels (rung 5's season arrays, pinned with the signal)
    kind: Mapped[str] = mapped_column(String(16), default="raw", server_default="raw")


class QueryRow(Base):
    __tablename__ = "queries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    service: Mapped[str] = mapped_column(String(32)); url: Mapped[str] = mapped_column(Text)
    params: Mapped[str] = mapped_column(Text); utc: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    status: Mapped[int | None] = mapped_column(Integer); nbytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64)); error: Mapped[str] = mapped_column(Text, default="")
    target_id: Mapped[str | None] = mapped_column(String(64), index=True)


class DossierRow(Base):
    """One generated dossier per target (dossiers spec 5.4, 5.6): where its ``dossier_data.json`` is, the signal and
    versions it was built from, the two probabilities with their detail, and the fingerprint of the test rows, status
    and decision it saw (the dossier is stale when the signal's fingerprint today differs). The pack columns are
    filled by ``skysift pack``; ``cross_check`` by ``skysift crosscheck --import`` only (the TRICERATOPS result with
    the inputs fingerprint it was made for), and a regeneration leaves it as it is."""
    __tablename__ = "dossiers"
    target_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), index=True)
    generated: Mapped[datetime] = mapped_column(DateTime)
    data_path: Mapped[str] = mapped_column(Text)
    search_version: Mapped[str] = mapped_column(String(32))
    ladder_version: Mapped[str | None] = mapped_column(String(16))
    dossier_version: Mapped[str] = mapped_column(String(16))
    fap: Mapped[float | None] = mapped_column(Float)
    fpp: Mapped[float | None] = mapped_column(Float)
    fap_detail: Mapped[dict] = mapped_column(JSON, default=dict)      # counts and versions
    fpp_detail: Mapped[dict] = mapped_column(JSON, default=dict)      # the factor table
    tests_fingerprint: Mapped[str] = mapped_column(String(64))
    status_at_generation: Mapped[str] = mapped_column(String(16))
    export_path: Mapped[str] = mapped_column(Text, default="")
    pack_path: Mapped[str] = mapped_column(Text, default="")
    pack_steps: Mapped[dict] = mapped_column(JSON, default=dict)      # {"1": true, ...}
    pack_built: Mapped[datetime | None] = mapped_column(DateTime)
    cross_check: Mapped[dict | None] = mapped_column(JSON)         # crosscheck.import_result's block, or None


DOSSIER_FIELDS = tuple(c.name for c in DossierRow.__table__.columns)


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

    def pool_breaks(self, job_key: str) -> int:
        """Runs of a job that ended in a broken CPU pool, charged (``WORKER_DIED_ERROR``) or not
        (``POOL_BROKE_ERROR``). A break that held several runs charges none of them, so a target that always kills its
        worker would otherwise be retried every sweep; the sweep gives a job up after a few of these."""
        with self.session() as s:
            return s.scalar(select(func.count()).select_from(RunRow).where(
                RunRow.job_key == job_key, RunRow.status == "error",
                RunRow.error.in_((POOL_BROKE_ERROR, WORKER_DIED_ERROR))))

    def error_capped_targets(self, max_attempts: int, max_pool_breaks: int | None = None,
                             ladders: tuple[str, ...] | None = None) -> int:
        """Targets left for a human: their latest sweep job (search and ladder of the target's newest run outside the
        recheck) has no done run and either at least ``max_attempts`` failed attempts or, when ``max_pool_breaks`` is
        given, at least that many runs lost in a broken CPU pool. The same give-up rules the sweep's target selection
        applies: the runs of every ladder version in ``ladders`` (default: the current ladder and the versions
        ``LADDER_COMPATIBLE`` lists for it) count as one job, so attempts under ladder 3 and ladder 4 add up and a done
        run under either clears the target. A run under any other ladder version counts on its own."""
        if ladders is None:
            from skysift.pipeline import DEFAULT_LADDER_VERSION, LADDER_COMPATIBLE  # pipeline imports this module
            ladders = (DEFAULT_LADDER_VERSION, *LADDER_COMPATIBLE.get(DEFAULT_LADDER_VERSION, ()))
        group = case((RunRow.ladder_version.in_(ladders), ladders[0]), else_=RunRow.ladder_version)
        sweep = RunRow.job_key.not_like(f"{RECHECK_JOB_PREFIX}%")
        latest = select(func.max(RunRow.id)).where(sweep).group_by(RunRow.target_id)
        job = (select(RunRow.target_id, RunRow.detector, RunRow.detector_version, group.label("ladders"))
               .where(RunRow.id.in_(latest)).subquery())
        same_job = and_(RunRow.target_id == job.c.target_id, RunRow.detector == job.c.detector,
                        RunRow.detector_version == job.c.detector_version, group == job.c.ladders, sweep)
        done = select(job.c.target_id).join(RunRow, same_job).where(RunRow.status == "done").correlate(None)

        def reached(limit: int, *errors):
            return (select(job.c.target_id).join(RunRow, same_job)
                    .where(job.c.target_id.not_in(done), RunRow.status == "error", *errors)
                    .group_by(job.c.target_id).having(func.count() >= limit))

        given_up = reached(max_attempts, *_ATTEMPT_FILTERS)
        if max_pool_breaks is not None:
            given_up = given_up.union(reached(max_pool_breaks, RunRow.error.in_((POOL_BROKE_ERROR, WORKER_DIED_ERROR))))
        with self.session() as s:
            return s.scalar(select(func.count()).select_from(given_up.subquery()))

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

    def add_signal(self, sig: Signal, run_id: int, parent_signal_id: int | None = None,
                   ladder_version: str | None = None, branch: str | None = None) -> int:
        """Add a signal to a run, or return the run's signal at the same period (within 1e-4 d). A recheck's
        re-measured signal names the signal it came from (``parent_signal_id``), the ladder that vets it and its
        ``branch`` ("recheck:P/2"; the column default when None)."""
        with self.session() as s:
            existing = s.scalars(select(SignalRow).where(SignalRow.run_id == run_id, SignalRow.detector == sig.detector,
                                                         SignalRow.period.between(sig.period - 1e-4, sig.period + 1e-4))).first()
            if existing:
                return existing.id
            row = SignalRow(run_id=run_id, parent_signal_id=parent_signal_id, ladder_version=ladder_version,
                            **({"branch": branch} if branch else {}), **sig.model_dump())
            s.add(row); s.flush(); return row.id

    def signal_status(self, signal_id: int) -> str:
        with self.session() as s:
            return s.scalar(select(SignalRow.final_status).where(SignalRow.id == signal_id))

    def children(self, signal_id: int) -> list[dict]:
        """Signals a recheck derived from this one (``parent_signal_id``), oldest first, as dicts of their columns."""
        with self.session() as s:
            rows = s.scalars(select(SignalRow).where(SignalRow.parent_signal_id == signal_id).order_by(SignalRow.id))
            return [{c.key: getattr(r, c.key) for c in SignalRow.__table__.columns} for r in rows]

    def set_signal_status(self, signal_id: int, final_status: str, branch: str | None = None) -> None:
        with self.session() as s:
            row = s.get(SignalRow, signal_id); row.final_status = final_status
            if branch: row.branch = branch

    def decide(self, signal_id: int, decision: str, note: str = "", who: str = "operator") -> dict:
        """Record an operator decision in one transaction: append a decisions row and move the candidate row to
        the new current state. Nothing is deleted; a later decision appends another row. A rejection needs a note
        and marks the signal rejected; approving or asking for data re-derives the engine's status from the latest
        tests (as ``refresh_status`` does), so the signal always carries the status its checks give it (a superseded
        signal stays superseded) and the reviewer's word sits beside it on the candidate row."""
        if decision not in DECISION_STATUS:
            raise ValueError(f"unknown decision {decision!r}; one of {sorted(DECISION_STATUS)}")
        note = note.strip()
        if decision == "reject" and not note:
            raise ValueError("a rejection needs a note saying why")
        now = utcnow()
        with self.session() as s:
            sig = s.get(SignalRow, signal_id)
            if sig is None:
                raise KeyError(signal_id)
            s.add(DecisionRow(signal_id=signal_id, decision=decision, note=note, who=who, utc=now))
            cand = s.scalars(select(CandidateRow).where(CandidateRow.signal_id == signal_id)).first()
            if cand is None:
                cand = CandidateRow(signal_id=signal_id, branch=sig.branch); s.add(cand)
            cand.status, cand.note, cand.decided_at = DECISION_STATUS[decision], note, now
            if decision == "reject":
                sig.final_status = "rejected"
            else:
                s.flush()                       # the new decision is the latest one the derivation looks at
                self._refresh_status(s, sig)
            return {"signal_id": signal_id, "decision": decision, "status": cand.status, "final_status": sig.final_status,
                    "note": note, "who": who, "decided_at": now.replace(tzinfo=None).isoformat()}

    def decisions(self, signal_id: int) -> list[dict]:
        with self.session() as s:
            rows = s.scalars(select(DecisionRow).where(DecisionRow.signal_id == signal_id)
                             .order_by(DecisionRow.id.desc()))
            return [{"decision": d.decision, "note": d.note, "who": d.who, "utc": d.utc.isoformat()} for d in rows]

    def add_test(self, signal_id: int, r: TestResult, duration_s: float, recheck_id: int | None = None) -> None:
        with self.session() as s:
            self._add_test_row(s, signal_id, r, duration_s, recheck_id)

    def _add_test_row(self, s: Session, signal_id: int, r: TestResult, duration_s: float,
                      recheck_id: int | None = None) -> None:
        s.add(TestRow(signal_id=signal_id, rung=r.rung, status=r.status.value, metrics=r.metrics,
                      artifacts=r.artifacts, reason=r.reason, duration_s=duration_s, recheck_id=recheck_id))

    @staticmethod
    def _latest_test_rows(s: Session, signal_ids) -> dict[int, list[dict]]:
        """The newest test row of each rung for every signal in ``signal_ids`` (a list or a select of ids), in
        ladder order: rung names sort in ladder order (rung1_detection < rung1_events < ... < rung5_pixels), so a
        late single-rung retry keeps its place."""
        newest = (select(func.max(TestRow.id)).where(TestRow.signal_id.in_(signal_ids))
                  .group_by(TestRow.signal_id, TestRow.rung))
        out: dict[int, list[dict]] = {}
        for t in s.scalars(select(TestRow).where(TestRow.id.in_(newest)).order_by(TestRow.rung)):
            out.setdefault(t.signal_id, []).append(
                {"id": t.id, "signal_id": t.signal_id, "rung": t.rung, "status": t.status, "metrics": t.metrics,
                 "artifacts": t.artifacts, "reason": t.reason, "duration_s": t.duration_s, "created": t.created,
                 "recheck_id": t.recheck_id})
        return out

    def latest_tests(self, signal_id: int) -> list[dict]:
        with self.session() as s:
            return self._latest_test_rows(s, [signal_id]).get(signal_id, [])

    def _refresh_status(self, s: Session, sig: SignalRow) -> str:
        """The one status derivation. A signal whose latest reviewer decision is a rejection keeps its status; a
        superseded one (by its stored status or by a recheck row with outcome "superseded", so the word survives a
        reject and a later approve) is superseded; any other takes the status its latest test rows give."""
        last = s.scalar(select(DecisionRow.decision).where(DecisionRow.signal_id == sig.id)
                        .order_by(DecisionRow.id.desc()).limit(1))
        if last == "reject":
            return sig.final_status
        superseded = s.scalar(select(RecheckRow.id).where(RecheckRow.signal_id == sig.id,
                                                          RecheckRow.outcome == "superseded").limit(1))
        if sig.final_status == "superseded" or superseded is not None:
            sig.final_status = "superseded"
            return sig.final_status
        tests = self._latest_test_rows(s, [sig.id]).get(sig.id, [])
        sig.final_status = final_status_for(status_from_tests(tests), tests)
        return sig.final_status

    def refresh_status(self, signal_id: int) -> str:
        """Re-derive a signal's final status from its latest tests (after a recheck wrote rows) and store it."""
        with self.session() as s:
            sig = s.get(SignalRow, signal_id)
            if sig is None:
                raise KeyError(signal_id)
            return self._refresh_status(s, sig)

    def add_recheck(self, signal_id: int, step: str, ladder_version: str, outcome: str, reason: str = "",
                    new_signal_id: int | None = None) -> int:
        with self.session() as s:
            row = RecheckRow(signal_id=signal_id, step=step, ladder_version=ladder_version, outcome=outcome,
                             reason=reason, new_signal_id=new_signal_id)
            s.add(row); s.flush(); return row.id

    def commit_recheck_step(self, write: StepWrite) -> dict:
        """Write one recheck step in ONE transaction: the derived signal's run, the signal itself and its tests (the
        run finished "done"), the rechecks row (naming the derived signal), the signal's test rows pointing at it,
        and both statuses derived again. Either all of it lands or none does, so a failure never leaves a pending
        child, a running run or a rechecks row without its tests for the next run to mistake for done work. When a
        reviewer decided on the signal while the step ran (any decision: approve, needs_data or reject; the queue
        leaves out every decided signal, as the recheck's ``_decided`` does), only a rechecks row "skipped" with
        ``REVIEWER_DECIDED_REASON`` is written. When the step derives a signal and the signal already has a child on
        that branch (two rechecks overlapped: the other one committed its P/2 signal after this one decided to make
        it), only a rechecks row "skipped" with ``CHILD_EXISTS_REASON`` is written, so one parent never has two P/2
        children. Returns {"recheck_id", "child_id", "status", "written"}."""
        with self.session() as s:
            sig = s.get(SignalRow, write.signal_id)
            if sig is None:
                raise KeyError(write.signal_id)
            decided = s.scalar(select(DecisionRow.id).where(DecisionRow.signal_id == sig.id).limit(1))
            if decided is not None:
                row = RecheckRow(signal_id=sig.id, step=write.step, ladder_version=write.ladder_version,
                                 outcome="skipped", reason=REVIEWER_DECIDED_REASON)
                s.add(row); s.flush()
                return {"recheck_id": row.id, "child_id": None, "status": sig.final_status, "written": False}
            child = run = None
            if write.child is not None:
                c = write.child
                twin = s.scalar(select(SignalRow.id).where(SignalRow.parent_signal_id == sig.id,
                                                           SignalRow.branch == c.branch).limit(1))
                if twin is not None:
                    row = RecheckRow(signal_id=sig.id, step=write.step, ladder_version=write.ladder_version,
                                     outcome="skipped", reason=CHILD_EXISTS_REASON)
                    s.add(row); s.flush()
                    return {"recheck_id": row.id, "child_id": None, "status": sig.final_status, "written": False}
                for old in s.scalars(select(RunRow).where(RunRow.job_key == c.job_key, RunRow.status == "running")):
                    old.status, old.error, old.finished = "error", "superseded", utcnow()
                run = RunRow(job_key=c.job_key, target_id=c.signal.target_id, detector=c.signal.detector,
                             detector_version=c.signal.detector_version, ladder_version=c.ladder_version)
                s.add(run); s.flush()
                child = SignalRow(run_id=run.id, parent_signal_id=sig.id, ladder_version=c.ladder_version,
                                  branch=c.branch, **c.signal.model_dump())
                s.add(child); s.flush()
            row = RecheckRow(signal_id=sig.id, step=write.step, ladder_version=write.ladder_version,
                             outcome=write.outcome, reason=write.reason,
                             new_signal_id=None if child is None else child.id)
            s.add(row); s.flush()
            for r, dt in write.tests:
                self._add_test_row(s, sig.id, r, dt, row.id)
            if child is not None:
                for r, dt in write.child.tests:
                    self._add_test_row(s, child.id, r, dt, row.id)
                s.flush()
                self._refresh_status(s, child)
                run.status, run.finished = "done", utcnow()
            s.flush()
            status = self._refresh_status(s, sig)
            return {"recheck_id": row.id, "child_id": None if child is None else child.id, "status": status,
                    "written": True}

    def rechecks(self, signal_id: int) -> list[dict]:
        """A signal's recheck history, oldest first."""
        with self.session() as s:
            rows = s.scalars(select(RecheckRow).where(RecheckRow.signal_id == signal_id).order_by(RecheckRow.id))
            return [{"id": r.id, "step": r.step, "ladder_version": r.ladder_version, "outcome": r.outcome,
                     "reason": r.reason, "new_signal_id": r.new_signal_id, "utc": r.utc.isoformat()} for r in rows]

    def recheck_queue(self, detector_version: str) -> list[dict]:
        """Signals the stage-2 recheck takes, by id: undetermined or stage-1 candidates of this search version that
        no reviewer has decided on, each with its latest test rows."""
        queued = (select(SignalRow.id).outerjoin(DecisionRow, DecisionRow.signal_id == SignalRow.id)
                  .where(SignalRow.final_status.in_(("undetermined", "candidate_stage1")),
                         SignalRow.detector_version == detector_version, DecisionRow.id.is_(None)))
        with self.session() as s:
            rows = s.scalars(select(SignalRow).where(SignalRow.id.in_(queued)).order_by(SignalRow.id)).all()
            tests = self._latest_test_rows(s, queued)
            return [{"id": r.id, "target_id": r.target_id, "final_status": r.final_status,
                     "ladder_version": r.ladder_version, "branch": r.branch, "period": r.period, "t0": r.t0,
                     "duration": r.duration, "depth_ppm": r.depth_ppm, "extra": r.extra,
                     "latest_tests": tests.get(r.id, [])} for r in rows]

    def record_results(self, run_id: int, target_id: str, items: list[dict], queries: list[dict],
                       pin_evidence: bool, periodogram: tuple[str, int, str] | None = None) -> dict:
        """Write one target's outcome in ONE transaction: signals with their tests, final statuses and the run's
        ladder version, the catalogue queries the worker made, the periodogram summary's artifact row, evidence pins,
        the finished run and the target state. Either all of it lands or none does, so an exception or crash never
        leaves pending signals that a rerun would duplicate.

        ``items`` are the worker's signal entries ({"signal", "outcome", "tests", "timings"}); ``queries`` are the
        keyword arguments of ``log_query`` (plus an optional ISO ``utc``); ``periodogram`` is (sha256, nbytes,
        local_path) of the summary file already written through the blob, recorded as a ``summary`` artifact and on
        the run row. Returns counts per outcome."""
        counts = {"pass": 0, "fail": 0, "undetermined": 0}
        run_pg = None
        with self.session() as s:
            run = s.get(RunRow, run_id)
            if run is None:
                raise KeyError(run_id)
            for item in items:
                sig = Signal(**item["signal"])
                row = SignalRow(run_id=run_id, final_status=final_status_for(item["outcome"], item["tests"]),
                                ladder_version=run.ladder_version, **sig.model_dump())
                s.add(row); s.flush()
                for r, dt in zip(item["tests"], item["timings"], strict=True):
                    self._add_test_row(s, row.id, TestResult(**r), dt)
                counts[item["outcome"]] += 1
            for q in queries:
                utc = datetime.fromisoformat(q["utc"]) if q.get("utc") else utcnow()
                s.add(QueryRow(service=q["service"], url=q["url"], params=jsonutil.dumps(q.get("params") or {}, default=str),
                               status=q.get("status"), nbytes=q.get("nbytes") or 0, sha256=q.get("sha256"),
                               error=q.get("error") or "", utc=utc, target_id=target_id))
            if periodogram is not None:
                sha, nbytes, loc = periodogram
                self._upsert_artifact(s, sha, nbytes, PERIODOGRAM_URL.format(target_id=target_id), loc, target_id,
                                      pin_evidence, "summary")
                run_pg = sha
            if pin_evidence:
                for a in s.scalars(select(ArtifactRow).where(ArtifactRow.target_id == target_id,
                                                              ArtifactRow.deleted_at.is_(None))):
                    a.pinned = True
            run.status, run.error, run.finished = "done", "", utcnow()
            run.periodogram_sha256 = run_pg
            s.get(TargetRow, target_id).state = "done"
        return counts

    def add_artifact(self, sha256, nbytes, url, local_path, target_id=None, pinned=False,
                     kind: str | None = None) -> None:
        """Record a stored file. A known sha256 is resurrected: a row retention marked deleted describes a file that is
        back on disk after a re-fetch, so it must be counted, released and evictable again. `kind` is "raw" for a new row
        and changes an existing row only when given, so a plain re-add never turns a summary into evictable raw."""
        with self.session() as s:
            self._upsert_artifact(s, sha256, nbytes, url, local_path, target_id, pinned, kind)

    def _upsert_artifact(self, s: Session, sha256, nbytes, url, local_path, target_id, pinned,
                         kind: str | None) -> None:
        row = s.get(ArtifactRow, sha256)
        if row is None:
            s.add(ArtifactRow(sha256=sha256, nbytes=nbytes, url=url, local_path=local_path, target_id=target_id,
                              pinned=pinned, kind=kind or "raw"))
            return
        if row.deleted_at is not None: row.pinned = False      # a deleted row's old pin protects nothing
        row.deleted_at, row.local_path, row.nbytes, row.last_used = None, local_path, nbytes, utcnow()
        if kind is not None: row.kind = kind
        if target_id is not None: row.target_id = target_id
        if pinned: row.pinned = True

    def live_artifact(self, url: str) -> dict | None:
        """The newest undeleted artifact recorded for ``url`` ({"sha256", "local_path", "nbytes", "kind", "pinned"}),
        or None."""
        with self.session() as s:
            a = s.scalars(select(ArtifactRow).where(ArtifactRow.url == url, ArtifactRow.deleted_at.is_(None))
                          .order_by(ArtifactRow.created.desc())).first()
            return None if a is None else {"sha256": a.sha256, "local_path": a.local_path, "nbytes": a.nbytes,
                                           "kind": a.kind, "pinned": a.pinned}

    def kept_artifacts(self, kind: str, url_prefix: str) -> dict[str, str]:
        """{url: local_path} of the undeleted ``kind`` artifacts whose url starts with ``url_prefix``, whichever target
        they were kept for, the latest row per url (by ``created``)."""
        with self.session() as s:
            rows = s.execute(select(ArtifactRow.url, ArtifactRow.local_path)
                             .where(ArtifactRow.kind == kind, ArtifactRow.deleted_at.is_(None),
                                    ArtifactRow.url.startswith(url_prefix, autoescape=True))
                             .order_by(ArtifactRow.url, ArtifactRow.created)).all()
        return {url: path for url, path in rows}       # ordered by created: the last row per url wins

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

    def upsert_dossier(self, **fields) -> None:
        """Insert or update the target's dossier row with the given columns. Columns left out keep their value (a new
        row takes the column defaults). Raises TypeError on a name that is not a column."""
        unknown = sorted(set(fields) - set(DOSSIER_FIELDS))
        if unknown:
            raise TypeError(f"not a dossiers column: {', '.join(unknown)}")
        with self.session() as s:
            row = s.get(DossierRow, fields["target_id"])
            if row is None:
                s.add(DossierRow(**fields))
                return
            for key, value in fields.items():
                setattr(row, key, value)

    @staticmethod
    def _dossier_dict(row: DossierRow) -> dict:
        return {name: getattr(row, name) for name in DOSSIER_FIELDS}

    def dossier(self, target_id: str) -> dict | None:
        with self.session() as s:
            row = s.get(DossierRow, target_id)
            return None if row is None else self._dossier_dict(row)

    def dossiers(self) -> list[dict]:
        """Every dossier row by target id, with its signal's ``final_status`` and the latest reviewer decision on
        that signal (``decision`` and ``decision_utc``, None when nobody has decided)."""
        newest = (select(DecisionRow.signal_id, func.max(DecisionRow.id).label("id"))
                  .group_by(DecisionRow.signal_id).subquery())
        q = (select(DossierRow, SignalRow.final_status, DecisionRow.decision, DecisionRow.utc)
             .join(SignalRow, SignalRow.id == DossierRow.signal_id)
             .outerjoin(newest, newest.c.signal_id == DossierRow.signal_id)
             .outerjoin(DecisionRow, DecisionRow.id == newest.c.id)
             .order_by(DossierRow.target_id))
        with self.session() as s:
            return [{**self._dossier_dict(row), "final_status": status, "decision": decision, "decision_utc": utc}
                    for row, status, decision, utc in s.execute(q)]

    def set_pack(self, target_id: str, pack_path: str, built: datetime) -> None:
        with self.session() as s:
            row = s.get(DossierRow, target_id)
            if row is None:
                raise KeyError(target_id)
            row.pack_path, row.pack_built = pack_path, built

    def set_cross_check(self, target_id: str, block: dict | None) -> None:
        """Store the cross-check block on the target's dossier row (None clears it). Raises KeyError without a row."""
        with self.session() as s:
            row = s.get(DossierRow, target_id)
            if row is None:
                raise KeyError(target_id)
            row.cross_check = None if block is None else dict(block)

    def set_pack_steps(self, target_id: str, steps: dict) -> None:
        with self.session() as s:
            row = s.get(DossierRow, target_id)
            if row is None:
                raise KeyError(target_id)
            row.pack_steps = dict(steps)

    def counts(self) -> dict:
        with self.session() as s:
            out = {}
            for name, model in [("targets", TargetRow), ("runs", RunRow), ("signals", SignalRow), ("tests", TestRow),
                                ("candidates", CandidateRow), ("artifacts", ArtifactRow), ("queries", QueryRow)]:
                out[name] = s.scalar(select(func.count()).select_from(model))
            return out

    def set_candidate_pack(self, signal_id: int, pack_path: str) -> bool:
        """Record the submission pack's folder on the signal's candidate row (dossiers spec 5.9). A signal no reviewer
        has decided on has no candidate row, and none is made for it (a new row would read as "needs data"): nothing
        is written and False is returned."""
        with self.session() as s:
            row = s.scalars(select(CandidateRow).where(CandidateRow.signal_id == signal_id)).first()
            if row is None:
                return False
            row.pack_path = pack_path
            return True
