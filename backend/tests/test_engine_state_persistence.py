"""Tests for the engine state persistence helpers in strategy_executor.

Covers the round-trip save/load cycle that lets the live engine resume
from saved state on restart instead of full-replaying every historical
bar (the 30-minute warmup the user hit with 30 paper portfolios).

These tests use a temp directory for the state files (monkeypatched via
_engine_state_file) so they don't pollute the real `data/live_engine_state/`.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.exchange import strategy_executor as se


# ── Test fixtures ──────────────────────────────────────────────────

@pytest.fixture
def tmp_state_root(tmp_path, monkeypatch):
  """Redirect engine state files to a temp dir for the duration of the test."""
  state_dir = tmp_path / "live_engine_state"
  monkeypatch.setattr(se, "_engine_state_root", lambda: state_dir)
  return state_dir


def _make_executor():
  """Minimal StrategyExecutor — only the state methods are exercised, so
  most dependencies can be None / mocks."""
  return se.StrategyExecutor(
    candle_aggregator=None,
    db_factory=lambda: None,
    on_signal=None,
    position_key_check=None,
  )


class _FakeStrategy:
  """Stand-in for a strategy instance — has the public attributes that
  BacktestEngine._capture_strategy_state_static will pick up."""
  def __init__(self):
    self._bar = 12345
    self._consumed_exits = {1: 100, 2: 200}
    self._consecutive_wins = 3
    self._consecutive_losses = 0
    self._closed_trades_count = 5
    # Public attrs (no underscore prefix) — these get captured
    self.current_range_high = 4500.5
    self.current_range_low = 4400.0
    self.last_swing_idx = 12300
    # An attribute that holds a non-trivial Python value
    self.recent_swings = [{"idx": 1, "px": 100.0}, {"idx": 2, "px": 110.0}]


# ── 1. Round-trip ──────────────────────────────────────────────────

def test_save_then_load_roundtrip_preserves_state(tmp_state_root):
  ex = _make_executor()
  strategy = _FakeStrategy()
  # Recent timestamp — _load_strategy_state rejects state older than
  # ENGINE_STATE_MAX_AGE_DAYS, so a hardcoded past date would time-bomb
  # this test as the calendar moves forward.
  last_bar_ts = datetime.now(timezone.utc) - timedelta(hours=1)

  ex._save_strategy_state(28, "BTCUSDT", strategy, last_bar_ts)

  # File should exist on disk
  fp = tmp_state_root / "28" / "BTCUSDT.json"
  assert fp.exists()
  payload = json.loads(fp.read_text(encoding="utf-8"))
  assert payload["version"] == se.ENGINE_STATE_VERSION
  assert payload["portfolio_id"] == 28
  assert payload["symbol"] == "BTCUSDT"
  assert payload["last_bar_index"] == 12345
  # Public attrs survived the round trip
  assert payload["state"]["current_range_high"] == 4500.5
  assert payload["state"]["recent_swings"] == [
    {"idx": 1, "px": 100.0}, {"idx": 2, "px": 110.0},
  ]

  # Loading should return the same payload
  loaded = ex._load_strategy_state(28, "BTCUSDT")
  assert loaded is not None
  assert loaded["last_bar_index"] == 12345
  assert loaded["state"]["current_range_low"] == 4400.0


# ── 2. Missing file ────────────────────────────────────────────────

def test_load_returns_none_when_file_missing(tmp_state_root):
  ex = _make_executor()
  assert ex._load_strategy_state(99, "DOESNTEXIST") is None


# ── 3. Stale state ─────────────────────────────────────────────────

def test_load_returns_none_when_state_too_old(tmp_state_root):
  ex = _make_executor()
  strategy = _FakeStrategy()
  # Simulate a state saved 10 days ago — past the 7-day threshold
  old_ts = datetime.now(timezone.utc) - timedelta(days=10)
  ex._save_strategy_state(28, "BTCUSDT", strategy, old_ts)

  loaded = ex._load_strategy_state(28, "BTCUSDT")
  assert loaded is None, (
    "state >7 days old should be treated as stale (force full replay)"
  )


def test_load_returns_state_when_recent(tmp_state_root):
  ex = _make_executor()
  strategy = _FakeStrategy()
  # 3 days ago — within the 7-day threshold
  recent_ts = datetime.now(timezone.utc) - timedelta(days=3)
  ex._save_strategy_state(28, "BTCUSDT", strategy, recent_ts)

  loaded = ex._load_strategy_state(28, "BTCUSDT")
  assert loaded is not None


# ── 4. Version mismatch ────────────────────────────────────────────

def test_load_returns_none_on_version_mismatch(tmp_state_root):
  ex = _make_executor()
  strategy = _FakeStrategy()
  ex._save_strategy_state(28, "BTCUSDT", strategy, datetime.now(timezone.utc))

  # Bump the on-disk version to something the code doesn't recognize
  fp = tmp_state_root / "28" / "BTCUSDT.json"
  data = json.loads(fp.read_text(encoding="utf-8"))
  data["version"] = 999
  fp.write_text(json.dumps(data), encoding="utf-8")

  loaded = ex._load_strategy_state(28, "BTCUSDT")
  assert loaded is None


# ── 5. Atomic write ────────────────────────────────────────────────

def test_save_writes_atomically(tmp_state_root):
  """Save uses tmp + rename, so a crash mid-write can't leave a half-
  written file. After save completes, no .tmp file should remain."""
  ex = _make_executor()
  strategy = _FakeStrategy()
  ex._save_strategy_state(28, "ETHUSDT", strategy, datetime.now(timezone.utc))

  fp = tmp_state_root / "28" / "ETHUSDT.json"
  assert fp.exists()
  tmp_fp = fp.with_suffix(".json.tmp")
  assert not tmp_fp.exists(), "tmp file must be cleaned up after rename"


# ── 6. Save errors are swallowed ────────────────────────────────────

def test_save_swallows_errors_does_not_raise(tmp_state_root):
  """A state save must NEVER break live trading. Even if the save fails
  catastrophically, the function returns silently with a logged warning."""
  ex = _make_executor()
  strategy = _FakeStrategy()
  with patch.object(Path, "write_text", side_effect=OSError("disk full")):
    # Must not raise
    ex._save_strategy_state(28, "BTCUSDT", strategy, datetime.now(timezone.utc))


# ── 7. Load errors are swallowed ────────────────────────────────────

def test_load_swallows_errors_returns_none(tmp_state_root):
  """If the saved file is corrupt JSON or unreadable, load returns None
  instead of raising — caller falls back to full warmup."""
  ex = _make_executor()
  # Write a corrupt state file by hand
  fp = tmp_state_root / "28" / "BTCUSDT.json"
  fp.parent.mkdir(parents=True, exist_ok=True)
  fp.write_text("this is not json at all", encoding="utf-8")

  loaded = ex._load_strategy_state(28, "BTCUSDT")
  assert loaded is None


# ── 8. Per-(portfolio, symbol) isolation ────────────────────────────

def test_state_isolated_per_portfolio_and_symbol(tmp_state_root):
  ex = _make_executor()
  s1 = _FakeStrategy()
  s1.current_range_high = 1.0

  s2 = _FakeStrategy()
  s2.current_range_high = 2.0

  s3 = _FakeStrategy()
  s3.current_range_high = 3.0

  now = datetime.now(timezone.utc)
  ex._save_strategy_state(28, "BTCUSDT", s1, now)
  ex._save_strategy_state(28, "ETHUSDT", s2, now)
  ex._save_strategy_state(29, "BTCUSDT", s3, now)

  l1 = ex._load_strategy_state(28, "BTCUSDT")
  l2 = ex._load_strategy_state(28, "ETHUSDT")
  l3 = ex._load_strategy_state(29, "BTCUSDT")

  assert l1["state"]["current_range_high"] == 1.0
  assert l2["state"]["current_range_high"] == 2.0
  assert l3["state"]["current_range_high"] == 3.0
