"""Run pytest against a disposable database with configured-auth route fixtures.

Run from the repository root. Rebind persistence before collecting tests, because
the shared schema fixture and a few route tests use the global SessionLocal.
The password marker is deliberately unusable for login; it tests the auth guard.
"""

import os
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine, event
from sqlalchemy.pool import NullPool
import src.platform.persistence.database as database
with tempfile.TemporaryDirectory(prefix='panwatch-rc-d-') as directory:
    database.engine.dispose()
    database.DB_PATH = os.path.join(directory, 'verification.db')
    engine = create_engine(f'sqlite:///{database.DB_PATH}', connect_args={'check_same_thread': False, 'timeout': 5}, poolclass=NullPool)
    @event.listens_for(engine, 'connect')
    def configure(connection, _record):
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('PRAGMA busy_timeout=5000')
    database.engine = engine
    database.SessionLocal.configure(bind=engine)
    from src.platform.persistence.models import Base, AppSettings
    Base.metadata.create_all(engine)
    with database.SessionLocal() as session:
        session.add(AppSettings(key="auth_password_hash", value="isolated-test-auth-configured"))
        session.commit()
    import pytest
    code = pytest.main(sys.argv[1:])
    engine.dispose()
    sys.exit(code)
