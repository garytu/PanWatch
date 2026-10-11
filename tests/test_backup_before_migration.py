"""Pre-migration backup: consistency, ordering, and failure behaviour.

Every test runs against a disposable database; nothing here touches the app DB.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime

import pytest
from sqlalchemy import create_engine, event, text

from src.platform.persistence import database
from src.platform.persistence.models import Base


def _bind_temp_db(monkeypatch, path: str):
    """Point persistence at a disposable database and return its engine."""
    monkeypatch.setattr(database, "DB_PATH", path)
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": 5},
        poolclass=database.NullPool,
    )

    @event.listens_for(engine, "connect")
    def configure(connection, _record):
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")

    monkeypatch.setattr(database, "engine", engine)
    return engine


def _backups(path: str) -> list[str]:
    return sorted(f for f in os.listdir(os.path.dirname(path)) if f.startswith(os.path.basename(path) + ".bak."))


def test_committed_wal_content_survives_the_backup(monkeypatch, tmp_path):
    """A file copy would miss this: the row is committed but still only in the WAL."""
    db_path = str(tmp_path / "wal.db")
    engine = _bind_temp_db(monkeypatch, db_path)

    conn = engine.connect()
    conn.execute(text("PRAGMA journal_mode=WAL"))
    conn.execute(text("CREATE TABLE probe (v TEXT)"))
    conn.execute(text("INSERT INTO probe VALUES ('committed-in-wal')"))
    conn.commit()

    wal = db_path + "-wal"
    assert os.path.exists(wal) and os.path.getsize(wal) > 0, "expected uncheckpointed WAL content"

    backup = database._backup_db_before_migration(["test: wal content"])

    # Reading the backup with the WAL ignored proves the committed content was folded in.
    check = sqlite3.connect(f"file:{backup}?immutable=1", uri=True)
    try:
        rows = check.execute("SELECT v FROM probe").fetchall()
    finally:
        check.close()
    assert rows == [("committed-in-wal",)]
    assert not os.path.exists(backup + "-wal") and not os.path.exists(backup + "-shm"), (
        "the backup should be self-contained, not dependent on WAL sidecars"
    )

    # The main file alone does not yet contain it, which is what shutil.copy2 would have copied.
    main_only = sqlite3.connect(f"file:{db_path}?immutable=1", uri=True)
    try:
        try:
            main_rows = main_only.execute("SELECT v FROM probe").fetchall()
        except sqlite3.OperationalError:
            main_rows = []
    finally:
        main_only.close()
    assert main_rows != [("committed-in-wal",)]

    conn.close()
    engine.dispose()


def test_legacy_only_change_triggers_a_backup(monkeypatch, tmp_path):
    """Legacy work alone must trigger a backup, not just pending versioned migrations."""
    db_path = str(tmp_path / "legacy.db")
    engine = _bind_temp_db(monkeypatch, db_path)

    # Fresh database: no backup is taken.
    with engine.connect() as conn:
        assert database._pending_migration_work(conn) == []
    database.init_db()
    assert _backups(db_path) == []

    # Re-create the legacy soft-delete column the migration would remove.
    engine.dispose()
    conn = sqlite3.connect(db_path)
    conn.execute("ALTER TABLE stocks ADD COLUMN enabled INTEGER DEFAULT 1")
    conn.commit()
    conn.close()

    engine = _bind_temp_db(monkeypatch, db_path)
    with engine.connect() as conn:
        reasons = database._pending_migration_work(conn)
    assert reasons == ["remove stocks.enabled"], reasons

    database.init_db()
    created = _backups(db_path)
    assert len(created) == 1, created

    # The backup still holds the legacy column; the live DB no longer does.
    check = sqlite3.connect(str(tmp_path / created[0]))
    try:
        assert "enabled" in [r[1] for r in check.execute("PRAGMA table_info(stocks)")]
    finally:
        check.close()
    with engine.connect() as conn:
        assert not database._has_column(conn, "stocks", "enabled")
    engine.dispose()


def test_unchanged_startup_creates_no_backup(monkeypatch, tmp_path):
    db_path = str(tmp_path / "unchanged.db")
    engine = _bind_temp_db(monkeypatch, db_path)
    database.init_db()
    assert _backups(db_path) == []

    # Second startup on the same, already-migrated database.
    database.init_db()
    assert _backups(db_path) == []
    engine.dispose()


def test_backup_failure_prevents_mutations(monkeypatch, tmp_path):
    """A required backup failure aborts the migration instead of logging a warning."""
    db_path = str(tmp_path / "failure.db")
    engine = _bind_temp_db(monkeypatch, db_path)
    database.init_db()

    conn = sqlite3.connect(db_path)
    conn.execute("ALTER TABLE stocks ADD COLUMN enabled INTEGER DEFAULT 1")
    conn.execute("INSERT INTO stocks (symbol, name, market, enabled) VALUES ('2330', '台積電', 'TW', 1)")
    conn.commit()
    conn.close()

    engine = _bind_temp_db(monkeypatch, db_path)

    real_connect = sqlite3.connect

    def failing_connect(*args, **kwargs):
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(database.sqlite3, "connect", failing_connect)

    with pytest.raises(sqlite3.OperationalError):
        database.init_db()

    # Nothing was migrated: the legacy column and its row are still there.
    check = real_connect(db_path)
    try:
        assert check.execute("PRAGMA table_info(stocks)").fetchone()
        cols = [r[1] for r in check.execute("PRAGMA table_info(stocks)")]
        assert "enabled" in cols
        assert check.execute("SELECT COUNT(*) FROM stocks WHERE enabled = 1").fetchone()[0] == 1
    finally:
        check.close()
    assert _backups(db_path) == []
    engine.dispose()


def test_detection_does_not_create_schema_migrations(monkeypatch, tmp_path):
    """Detection must stay read-only, unlike has_pending_migrations()."""
    db_path = str(tmp_path / "readonly.db")
    engine = _bind_temp_db(monkeypatch, db_path)

    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE stocks (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    engine = _bind_temp_db(monkeypatch, db_path)
    with engine.connect() as conn:
        database._pending_migration_work(conn)
    check = sqlite3.connect(db_path)
    try:
        tables = [r[0] for r in check.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    finally:
        check.close()
    assert "schema_migrations" not in tables
    assert tables == ["stocks"]
    engine.dispose()


def test_backup_uses_the_app_database_path(monkeypatch, tmp_path):
    """Guard against the old behaviour: the backup must be a real SQLite copy."""
    db_path = str(tmp_path / "guard.db")
    engine = _bind_temp_db(monkeypatch, db_path)
    Base.metadata.create_all(engine)
    backup = database._backup_db_before_migration(["test"])
    check = sqlite3.connect(backup)
    try:
        assert check.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    finally:
        check.close()
    engine.dispose()


def test_retry_retains_original_backup_in_the_same_second(monkeypatch, tmp_path):
    db_path = str(tmp_path / "retry.db")
    engine = _bind_temp_db(monkeypatch, db_path)
    database.init_db()
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE stocks ADD COLUMN enabled INTEGER DEFAULT 1"))
        conn.execute(text(
            "INSERT INTO stocks (symbol, name, market, sort_order) "
            "VALUES ('TEST', 'original', 'TW', 1)"
        ))

    class FixedDatetime:
        @classmethod
        def now(cls):
            return datetime(2026, 10, 11, 12, 0, 0)

    attempts = 0
    original_migrate = database._migrate

    def fail_after_committed_change(bound_engine):
        nonlocal attempts
        original_migrate(bound_engine)
        attempts += 1
        if attempts == 1:
            with bound_engine.begin() as conn:
                conn.execute(text("UPDATE stocks SET name = 'partially-migrated'"))
            raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(database, "datetime", FixedDatetime)
    monkeypatch.setattr(database, "_migrate", fail_after_committed_change)
    monkeypatch.setattr(database.time, "sleep", lambda delay: None)
    database.init_db()

    assert attempts == 2
    backups = _backups(db_path)
    assert len(backups) == 2
    snapshots = set()
    for backup in backups:
        with sqlite3.connect(str(tmp_path / backup)) as check:
            snapshots.add(check.execute("SELECT name FROM stocks").fetchone()[0])
    assert snapshots == {"original", "partially-migrated"}
    engine.dispose()


@pytest.mark.skipif(os.name == "nt", reason="POSIX file permissions")
def test_backup_is_private_even_with_permissive_umask(monkeypatch, tmp_path):
    db_path = str(tmp_path / "private.db")
    engine = _bind_temp_db(monkeypatch, db_path)
    Base.metadata.create_all(engine)
    os.chmod(db_path, 0o600)
    original_umask = os.umask(0o022)
    try:
        backup = database._backup_db_before_migration(["test: private backup"])
    finally:
        os.umask(original_umask)
    assert os.stat(backup).st_mode & 0o777 == 0o600
    engine.dispose()
