"""Run pytest against a disposable database with configured-auth route fixtures.

Run from the repository root. Isolation is shared with tests/conftest.py, so plain
`pytest tests/` is isolated too; this entry point is kept for explicit runs and CI.
The password marker is deliberately unusable for login; it tests the auth guard.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from isolated_db import cleanup, isolate

isolate()

import pytest

code = pytest.main(sys.argv[1:])
cleanup()
sys.exit(code)
