import pytest
from sqlalchemy import MetaData, Table, inspect, text

from skysift.models import TestResult, TestStatus
from skysift.store import migrations
from skysift.store.db import Base, Store
from skysift.store.migrations import migrate


def _milestone1_database(tmp_path):
    """A database as milestone 1 left it: current tables minus everything this milestone adds."""
    url = f"sqlite:///{tmp_path.as_posix()}/m1.db"
    s = Store(url); Base.metadata.create_all(s.engine)
    with s.engine.begin() as c:
        c.execute(text("ALTER TABLE runs DROP COLUMN periodogram_sha256"))
        c.execute(text("ALTER TABLE candidates DROP COLUMN decided_at"))
        c.execute(text("DROP INDEX ix_queries_target_id"))            # SQLite refuses to drop an indexed column
        c.execute(text("ALTER TABLE queries DROP COLUMN target_id"))
        c.execute(text("ALTER TABLE artifacts DROP COLUMN kind"))
        c.execute(text("DROP TABLE decisions"))
        c.execute(text("INSERT INTO artifacts (sha256, nbytes, url, local_path, pinned, created, last_used) "
                       "VALUES (lower(hex(randomblob(32))), 1, 'u', 'p', 0, '2026-10-13 00:00:00', '2026-10-13 00:00:00')"))
    return url


def test_migrate_milestone1_database_in_place(tmp_path):
    url = _milestone1_database(tmp_path)
    s = Store(url); s.init_schema()
    insp = inspect(s.engine)
    assert "periodogram_sha256" in {c["name"] for c in insp.get_columns("runs")}
    assert "decided_at" in {c["name"] for c in insp.get_columns("candidates")}
    assert "target_id" in {c["name"] for c in insp.get_columns("queries")}
    assert "kind" in {c["name"] for c in insp.get_columns("artifacts")}
    assert "decisions" in insp.get_table_names() and "schema_version" in insp.get_table_names()
    with s.engine.connect() as c:
        assert c.execute(text("select kind from artifacts")).scalar() == "raw"       # existing rows get the default
        assert [r[0] for r in c.execute(text("select step from schema_version"))] == ["001_dashboard", "002_recheck"]


def test_migrate_is_idempotent(tmp_path):
    s = Store(f"sqlite:///{tmp_path.as_posix()}/t.db"); s.init_schema()
    assert migrations.migrate(s.engine) == []          # nothing left to apply on a fresh schema
    assert migrations.migrate(s.engine) == []


RECHECK_COLUMNS = {"signals": {"parent_signal_id", "ladder_version"}, "tests": {"recheck_id"}}


@pytest.fixture
def old_schema_engine(tmp_path):
    """A database as the dashboard milestone left it: step 001 recorded, no rechecks table, no recheck columns, one
    signal with one test row. Built from a copy of today's tables without the new columns, because SQLite refuses
    to drop a column that is part of a foreign key."""
    old = MetaData()
    for t in Base.metadata.sorted_tables:
        if t.name != "rechecks":
            Table(t.name, old, *[c._copy() for c in t.columns if c.name not in RECHECK_COLUMNS.get(t.name, ())])
    engine = Store(f"sqlite:///{tmp_path.as_posix()}/old.db").engine
    old.create_all(engine)
    with engine.begin() as c:
        c.execute(text("CREATE TABLE schema_version (step VARCHAR(64) PRIMARY KEY, applied TIMESTAMP)"))
        c.execute(text("INSERT INTO schema_version VALUES ('001_dashboard', '2026-10-19 00:00:00')"))
        c.execute(text("INSERT INTO runs (id, job_key, target_id, detector, detector_version, ladder_version, started, "
                       "status, error) VALUES (1, 'k', 'kic-1', 'box_dip', 'v', '3', '2026-10-19 00:00:00', 'done', '')"))
        c.execute(text("INSERT INTO signals (id, run_id, target_id, detector, detector_version, period, t0, duration, "
                       "depth_ppm, snr, sde, n_events, extra, branch, final_status) "
                       "VALUES (1, 1, 'kic-1', 'box_dip', 'v', 3.0, 1.0, 0.1, 800, 12, 11, 9, '{}', 'planet', 'undetermined')"))
        c.execute(text("INSERT INTO tests (signal_id, rung, status, metrics, artifacts, reason, duration_s, created) "
                       "VALUES (1, 'rung4_novelty', 'undetermined', '{}', '[]', 'outage:x', 0.1, '2026-10-19 00:00:00')"))
    return engine


def test_step_002_adds_recheck_columns_to_an_old_database(old_schema_engine):
    applied = migrate(old_schema_engine)
    assert "002_recheck" in applied
    cols = {c["name"] for c in inspect(old_schema_engine).get_columns("signals")}
    assert {"parent_signal_id", "ladder_version"} <= cols
    assert "recheck_id" in {c["name"] for c in inspect(old_schema_engine).get_columns("tests")}
    assert "rechecks" in inspect(old_schema_engine).get_table_names()


def test_step_002_keeps_old_rows_indexes_the_parent_and_serves_the_store(old_schema_engine):
    assert migrate(old_schema_engine) == ["002_recheck"] and migrate(old_schema_engine) == []
    insp = inspect(old_schema_engine)
    assert "ix_signals_parent_signal_id" in {i["name"] for i in insp.get_indexes("signals")}
    store = Store(str(old_schema_engine.url)); store.init_schema()        # create_all and migrate leave it alone
    assert store.signal_status(1) == "undetermined" and store.children(1) == []
    assert [t["recheck_id"] for t in store.latest_tests(1)] == [None]
    rid = store.add_recheck(1, "catalogue", "4", "pass")
    store.add_test(1, TestResult(rung="rung4_novelty", status=TestStatus.PASS), 0.1, recheck_id=rid)
    assert store.refresh_status(1) == "candidate_stage1" and store.rechecks(1)[0]["outcome"] == "pass"
