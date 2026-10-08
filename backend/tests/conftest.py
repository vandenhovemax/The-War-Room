"""Keep the test suite away from real data.

App start-up (FastAPI lifespan → `init_db()`) creates and migrates the
database at `settings.database_url`, which defaults to `data/antigravity.db`
— the real research database. Components also write heartbeat files under
`data/heartbeats/`, the app log under `logs/`, and every optimizer build is
auto-saved as JSON into `backend/data/saved_runs/` (the Saved Runs tab: ~80
files per full run). Point all four at a throwaway folder before the tests
run, so running them can never touch real data (2026-10-08).

Set DATABASE_URL yourself to run against a specific database on purpose.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TEST_DATA = Path(tempfile.mkdtemp(prefix="warroom-tests-"))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TEST_DATA / 'antigravity.db'}")
os.environ.setdefault("LOG_DIR", str(_TEST_DATA / "logs"))

from backend.api.routes import cross_strategy as _cross_strategy  # noqa: E402  (after the env vars)
from backend.utils import heartbeats as _heartbeats  # noqa: E402

_heartbeats.HEARTBEAT_DIR = _TEST_DATA / "heartbeats"
_cross_strategy._SAVED_RUNS_DIR = _TEST_DATA / "saved_runs"
