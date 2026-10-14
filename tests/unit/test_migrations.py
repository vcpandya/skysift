from sqlalchemy import inspect, text

from skysift.store import migrations
from skysift.store.db import Base, Store


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
        assert [r[0] for r in c.execute(text("select step from schema_version"))] == ["001_dashboard"]


def test_migrate_is_idempotent(tmp_path):
    s = Store(f"sqlite:///{tmp_path.as_posix()}/t.db"); s.init_schema()
    assert migrations.migrate(s.engine) == []          # nothing left to apply on a fresh schema
    assert migrations.migrate(s.engine) == []
