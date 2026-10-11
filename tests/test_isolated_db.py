"""Application database aliases must be rejected before any schema creation."""

import os
import sqlite3

import pytest

import isolated_db
from src.platform.persistence import database


@pytest.mark.parametrize("alias_kind", ["direct", "symlink", "hardlink", "directory_symlink"])
def test_rejects_application_database_aliases(monkeypatch, tmp_path, alias_kind):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    app_path = app_dir / "application.db"
    with sqlite3.connect(str(app_path)) as conn:
        conn.execute("CREATE TABLE sentinel (value TEXT)")
        conn.execute("INSERT INTO sentinel VALUES ('untouched')")

    alias = tmp_path / "alias.db"
    try:
        if alias_kind == "direct":
            alias = app_path
        elif alias_kind == "symlink":
            alias.symlink_to(app_path)
        elif alias_kind == "hardlink":
            os.link(app_path, alias)
        else:
            alias_dir = tmp_path / "alias-dir"
            alias_dir.symlink_to(app_dir, target_is_directory=True)
            alias = alias_dir / app_path.name
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"filesystem does not support {alias_kind}: {exc}")

    monkeypatch.setattr(isolated_db, "APP_DB_PATH", str(app_path))
    monkeypatch.setattr(isolated_db, "_state", {"isolated": False, "directory": None})
    monkeypatch.setenv("PANWATCH_TEST_DB", str(alias))
    original_engine = database.engine
    original_path = database.DB_PATH
    with pytest.raises(RuntimeError, match="points at the application database"):
        isolated_db.isolate()

    assert database.engine is original_engine
    assert database.DB_PATH == original_path
    with sqlite3.connect(str(app_path)) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("sentinel",)]
        assert conn.execute("SELECT value FROM sentinel").fetchone() == ("untouched",)
