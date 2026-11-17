"""Test setup: point the app at a fresh throwaway SQLite file BEFORE any
app module is imported (database.py binds its engine at import time).

Note: the task brief asks for in-memory SQLite; plain ``sqlite:///:memory:``
does not survive across the app's pooled connections with this codebase's
engine setup, so tests use a fresh temp-file SQLite DB instead — same
dialect, same schema, deterministic per run. The file is removed at the
start of every pytest session.
"""
import os
import sys
import tempfile

# Make `app` (backend/app) importable regardless of how pytest is invoked.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_TMPDIR = tempfile.mkdtemp(prefix="clinic_ai_test_")
_DB_PATH = os.path.join(_TMPDIR, "test.db")
if os.path.exists(_DB_PATH):
    os.remove(_DB_PATH)

os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["DEMO_MODE"] = "true"
os.environ["SEED_DEMO"] = "0"
os.environ.setdefault("APP_ENV", "test")
