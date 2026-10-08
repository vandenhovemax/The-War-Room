"""Structured logging with rotating file handler + console output."""
import io
import logging
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from backend.config import get_settings

# ── Shared handler singletons ──────────────────────────────────
# One file handler + one console handler shared across ALL loggers.
# Prevents multiple RotatingFileHandlers competing for the same file,
# which caused PermissionError on Windows during log rotation.
_shared_file_handler: RotatingFileHandler | None = None
_shared_console_handler: logging.StreamHandler | None = None
_fmt = logging.Formatter(
  "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
  datefmt="%Y-%m-%d %H:%M:%S",
)


class _WindowsSafeRotatingHandler(RotatingFileHandler):
  """RotatingFileHandler that retries rename on Windows lock conflicts."""

  def doRollover(self) -> None:
    try:
      super().doRollover()
    except PermissionError:
      # Another thread/handle has the file open — wait briefly and retry once
      time.sleep(0.05)
      try:
        super().doRollover()
      except PermissionError:
        # Still locked — skip rotation this time, log will rotate next time
        # Re-open the stream so logging continues uninterrupted
        if self.stream:
          self.stream.close()
        self.stream = self._open()


def _ensure_shared_handlers() -> tuple[logging.StreamHandler, RotatingFileHandler]:
  """Create shared handlers once, reuse across all loggers."""
  global _shared_file_handler, _shared_console_handler

  if _shared_console_handler is None:
    _stream = sys.stderr
    if hasattr(_stream, "buffer"):
      _stream = io.TextIOWrapper(
        _stream.buffer, encoding="utf-8", errors="replace", line_buffering=True
      )
    _shared_console_handler = logging.StreamHandler(_stream)
    _shared_console_handler.setFormatter(_fmt)

  if _shared_file_handler is None:
    settings = get_settings()
    log_dir = Path(settings.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    _shared_file_handler = _WindowsSafeRotatingHandler(
      log_dir / "warroom.log",
      maxBytes=10 * 1024 * 1024,
      backupCount=5,
      encoding="utf-8",
    )
    _shared_file_handler.setFormatter(_fmt)

  return _shared_console_handler, _shared_file_handler


def get_logger(name: str) -> logging.Logger:
  logger = logging.getLogger(name)

  if logger.handlers:
    return logger

  settings = get_settings()
  logger.setLevel(logging.DEBUG if settings.debug else logging.INFO)

  ch, fh = _ensure_shared_handlers()
  logger.addHandler(ch)
  logger.addHandler(fh)

  return logger
