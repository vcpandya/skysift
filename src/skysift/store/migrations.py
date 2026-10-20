"""Hand-rolled schema migrations. `create_all` creates missing tables but never alters existing ones, so every
column added after milestone 1 is added here, once, recorded in `schema_version`. Steps are idempotent: each checks
the catalogue before altering, so a database that already has the column (a fresh create_all) records the step
without touching it. Column types are spelled for both SQLite and Postgres (TIMESTAMP, VARCHAR)."""
from __future__ import annotations

from sqlalchemy import Column, DateTime, MetaData, String, Table, insert, inspect, text
from sqlalchemy.engine import Connection, Engine

from skysift.store.db import RecheckRow, utcnow


def _columns(conn: Connection, table: str) -> set[str]:
    return {c["name"] for c in inspect(conn).get_columns(table)}


def _add_column(conn: Connection, table: str, column: str, ddl: str) -> None:
    if column not in _columns(conn, table):
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


def _step_001_dashboard(conn: Connection) -> None:
    _add_column(conn, "runs", "periodogram_sha256", "VARCHAR(64)")
    _add_column(conn, "candidates", "decided_at", "TIMESTAMP")
    _add_column(conn, "queries", "target_id", "VARCHAR(64)")
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_queries_target_id ON queries (target_id)"))
    _add_column(conn, "artifacts", "kind", "VARCHAR(16) NOT NULL DEFAULT 'raw'")
    # the decisions table itself is created by Base.metadata.create_all, which init_schema runs first


def _step_002_recheck(conn: Connection) -> None:
    # create_all (init_schema) makes the table first; checkfirst lets migrate alone bring an old database up to date,
    # and the table must exist before tests.recheck_id references it
    RecheckRow.__table__.create(conn, checkfirst=True)
    _add_column(conn, "signals", "parent_signal_id", "INTEGER REFERENCES signals (id)")
    _add_column(conn, "signals", "ladder_version", "VARCHAR(16)")
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_signals_parent_signal_id ON signals (parent_signal_id)"))
    _add_column(conn, "tests", "recheck_id", "INTEGER REFERENCES rechecks (id)")


# a typed table so the driver gets a bound DateTime (sqlite3's default datetime adapter is deprecated); the stored
# value is naive UTC, like every ORM DateTime column in db.py
_SCHEMA_VERSION = Table("schema_version", MetaData(), Column("step", String(64), primary_key=True),
                        Column("applied", DateTime))

STEPS = [("001_dashboard", _step_001_dashboard), ("002_recheck", _step_002_recheck)]


def migrate(engine: Engine) -> list[str]:
    """Apply every step not yet recorded; returns the names applied (empty when the schema is current)."""
    applied = []
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS schema_version (step VARCHAR(64) PRIMARY KEY, applied TIMESTAMP)"))
        done = {r[0] for r in conn.execute(text("SELECT step FROM schema_version"))}
        for name, fn in STEPS:
            if name in done: continue
            fn(conn)
            conn.execute(insert(_SCHEMA_VERSION).values(step=name, applied=utcnow().replace(tzinfo=None)))
            applied.append(name)
    return applied
