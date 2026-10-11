"""Bind persistence to a disposable database for the whole test session.

The shared schema fixture and a few route tests use the global SessionLocal, so
without isolation they would run against the application database. Isolating the
engine here keeps every invocation path safe, including plain `pytest tests/`.

Set PANWATCH_TEST_DB to an explicit disposable path when a specific database is
required; the application database is rejected, because schema creation and tests
can still modify an existing file.
"""

from __future__ import annotations

import os
import shutil
import tempfile

from sqlalchemy import create_engine, event
from sqlalchemy.pool import NullPool

import src.platform.persistence.database as database

AUTH_SEED_KEY = "auth_password_hash"
# Deliberately unusable for login: it only exercises the auth guard.
AUTH_SEED_VALUE = "isolated-test-auth-configured"

# Captured at import, before any rebinding, so the guard compares against the
# application database rather than whatever isolate() later points at.
APP_DB_PATH = os.path.abspath(database.DB_PATH)

_state: dict[str, object] = {"isolated": False, "directory": None}


def isolate():
    """Rebind the shared engine to a disposable database. Idempotent."""
    if _state["isolated"]:
        return None

    explicit = os.getenv("PANWATCH_TEST_DB")
    if explicit:
        db_path = os.path.abspath(explicit)
        is_app_db = os.path.realpath(db_path) == os.path.realpath(APP_DB_PATH)
        if not is_app_db and os.path.exists(db_path) and os.path.exists(APP_DB_PATH):
            is_app_db = os.path.samefile(db_path, APP_DB_PATH)
        if is_app_db:
            raise RuntimeError(
                f"PANWATCH_TEST_DB points at the application database ({APP_DB_PATH}). "
                "create_all() and the tests can modify it; point the variable at a "
                "disposable copy instead."
            )
        directory = None
    else:
        directory = tempfile.mkdtemp(prefix="panwatch-test-")
        db_path = os.path.join(directory, "verification.db")

    database.engine.dispose()
    database.DB_PATH = db_path

    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 5},
        poolclass=NullPool,
    )

    @event.listens_for(engine, "connect")
    def configure(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")

    database.engine = engine
    database.SessionLocal.configure(bind=engine)

    from src.platform.persistence.models import Base, AppSettings

    Base.metadata.create_all(engine)
    with database.SessionLocal() as session:
        # An explicit PANWATCH_TEST_DB may already carry real settings; only seed
        # the marker when it is absent, preserving settings in a disposable copy.
        if session.query(AppSettings).filter(AppSettings.key == AUTH_SEED_KEY).first() is None:
            session.add(AppSettings(key=AUTH_SEED_KEY, value=AUTH_SEED_VALUE))
            session.commit()

    _state["isolated"] = True
    _state["directory"] = directory
    return engine


def cleanup() -> None:
    """Dispose the isolated engine and reset state; delete only a directory we created."""
    if not _state["isolated"]:
        return
    database.engine.dispose()
    directory = _state.get("directory")
    if directory:
        shutil.rmtree(directory, ignore_errors=True)
    _state["isolated"] = False
    _state["directory"] = None
