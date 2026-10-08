"""
Entry point for The War Room backend.

Run with:
  cd The_War_Room
  python -m backend.main
"""
import sys
from pathlib import Path

# Ensure the project root is on sys.path so `backend` is importable
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
  sys.path.insert(0, project_root)

import uvicorn

from backend.api.app import create_app
from backend.config import get_settings

app = create_app()


if __name__ == "__main__":
  settings = get_settings()
  uvicorn.run(
    "backend.main:app",
    host=settings.host,
    port=settings.port,
    reload=settings.debug,
    timeout_keep_alive=3660,  # 61 min — optimizer can run up to 60 min with complex math
  )
