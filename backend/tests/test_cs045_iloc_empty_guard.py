"""CS-045: ``iloc[-1]`` on equity without ``.empty`` guard.

The current defense is a single line at the top of every public
metric:

    if _active_days(pnl) < _MIN_TRADING_DAYS:
        return _SKIP

``_active_days`` returns ``0`` on an empty Series, so an empty input
short-circuits before reaching ``equity.iloc[-1]``. Every metric in
this module is *supposed* to start with that guard.

The risk flagged by the audit is silent: a future contributor adds a
new ``metric_X`` and forgets the guard → an empty-group request
raises ``IndexError`` deep inside scoring. No test today catches that.

This suite freezes the invariant:

  1. Every public metric returns a *finite number* (not a raise) when
     handed an empty Series.
  2. Every public metric returns ``_SKIP`` when handed a Series shorter
     than ``_MIN_TRADING_DAYS``.
  3. ``btc_change_between`` returns ``None`` on empty / bad input
     rather than raising — it used to swallow a bare ``except
     Exception``; CS-045 narrowed that to specific types. Make sure
     the narrowed version still handles the empty case.
"""
from __future__ import annotations

import inspect

import numpy as np
import pandas as pd
import pytest

from backend.analysis import batch_selection_scoring as bss


# ── Discover every public metric_* dynamically ─────────────────────

_METRICS = [
  (name, fn)
  for name, fn in inspect.getmembers(bss, inspect.isfunction)
  if name.startswith("metric_")
]


def test_metrics_are_actually_discovered():
  """Meta-sanity: the discovery must not return an empty list. If the
  module is refactored to rename ``metric_*`` → something else, we'd
  silently stop testing anything.
  """
  assert len(_METRICS) >= 20, (
    f"CS-045 REGRESSION: only {len(_METRICS)} public metric_* "
    "functions discovered — expected ≥20. Has the naming convention "
    "changed?"
  )


# ── Empty-input contract ────────────────────────────────────────────

@pytest.mark.parametrize("name,fn", _METRICS, ids=[n for n, _ in _METRICS])
def test_metric_handles_empty_series_without_raising(name, fn):
  """Every metric must survive an empty input. The ``_active_days`` +
  ``_MIN_TRADING_DAYS`` guard is what protects the downstream
  ``iloc[-1]`` reads; this test trips the moment a future metric
  forgets to call it.
  """
  empty = pd.Series([], dtype=float)
  try:
    result = fn(empty)
  except Exception as e:  # noqa: BLE001 — we want to catch-all here
    pytest.fail(
      f"CS-045 REGRESSION: {name}(pd.Series([], dtype=float)) raised "
      f"{type(e).__name__}: {e}. The guard at the top of the metric "
      "(`if _active_days(pnl) < _MIN_TRADING_DAYS: return _SKIP`) is "
      "missing or was removed. An all-filtered group will crash the "
      "whole scoring pass."
    )
  assert isinstance(result, (int, float)) and np.isfinite(result), (
    f"CS-045 REGRESSION: {name} on empty input returned "
    f"non-finite/non-numeric {result!r}. Expected the SKIP sentinel "
    f"({bss._SKIP})."
  )
  assert result == bss._SKIP, (
    f"CS-045 REGRESSION: {name} on empty input returned {result!r}, "
    f"expected _SKIP ({bss._SKIP}). The empty-input guard has been "
    "reimplemented in a way that no longer returns the shared "
    "sentinel — downstream ranking can't distinguish 'skipped' from "
    "'zero', silently keeping empty groups in the pool."
  )


# ── Below-min-trading-days contract ────────────────────────────────

@pytest.mark.parametrize("name,fn", _METRICS, ids=[n for n, _ in _METRICS])
def test_metric_returns_skip_below_min_trading_days(name, fn):
  """A Series with fewer than ``_MIN_TRADING_DAYS`` non-zero entries
  must return ``_SKIP`` — the sentinel the downstream ranker uses to
  drop the group from consideration. If a new metric forgets this,
  tiny-sample noise starts winning the CAL rank.
  """
  too_few = pd.Series([1.0] * (bss._MIN_TRADING_DAYS - 1), dtype=float)
  result = fn(too_few)
  assert result == bss._SKIP, (
    f"CS-045 REGRESSION: {name} on a {len(too_few)}-day Series "
    f"returned {result!r}, expected _SKIP ({bss._SKIP}). The "
    "_MIN_TRADING_DAYS guard is missing — tiny samples will rank."
  )


# ── Minimum-valid contract ─────────────────────────────────────────

@pytest.mark.parametrize("name,fn", _METRICS, ids=[n for n, _ in _METRICS])
def test_metric_returns_finite_on_min_valid_input(name, fn):
  """A Series with exactly ``_MIN_TRADING_DAYS`` non-zero positive
  entries must produce a finite, non-SKIP result. Guards that are
  accidentally off-by-one (``<=`` instead of ``<``) would reject the
  boundary case and silently zero-out production.
  """
  rng = np.random.default_rng(0)
  vals = (rng.standard_normal(bss._MIN_TRADING_DAYS) + 0.5) * 10
  # Ensure no zero entries in the sample.
  vals[vals == 0] = 0.01
  series = pd.Series(vals)

  result = fn(series)
  assert isinstance(result, (int, float)) and np.isfinite(result), (
    f"CS-045 REGRESSION: {name} on a {bss._MIN_TRADING_DAYS}-day "
    f"valid sample returned non-finite {result!r}. The guard may have "
    "drifted off-by-one."
  )
  assert result != bss._SKIP, (
    f"CS-045 REGRESSION: {name} rejected a valid "
    f"{bss._MIN_TRADING_DAYS}-day sample as SKIP. Boundary drift."
  )


# ── Source-level guard check ───────────────────────────────────────

@pytest.mark.parametrize("name,fn", _METRICS, ids=[n for n, _ in _METRICS])
def test_every_metric_source_calls_active_days_guard(name, fn):
  """Static check: the source of every ``metric_*`` must contain the
  ``_active_days(...) < _MIN_TRADING_DAYS`` guard. Runtime checks
  above would also trip — but this static one makes the failure mode
  explicit in review, not 'a random parametrized test failed'.
  """
  src = inspect.getsource(fn)
  assert "_active_days" in src, (
    f"CS-045 REGRESSION: {name} source does not reference "
    "`_active_days`. Every public metric must call the empty-input "
    "guard at the top (see the docstring on `_active_days` in "
    "batch_selection_scoring.py)."
  )
  assert "_MIN_TRADING_DAYS" in src or "_SKIP" in src, (
    f"CS-045 REGRESSION: {name} references `_active_days` but neither "
    "`_MIN_TRADING_DAYS` nor `_SKIP` — the guard is malformed."
  )


# ── btc_context side ───────────────────────────────────────────────

def test_btc_change_between_narrowed_except_covers_empty_df():
  """CS-045 narrowed ``except Exception`` on btc_change_between to
  specific types. An empty/degenerate DataFrame must still return
  None — not raise. Uses monkeypatch so the test doesn't hit the
  network.
  """
  from backend.analysis import btc_context
  from datetime import datetime

  # Force fetch_btc_daily to return an empty DataFrame so the early
  # `if df.empty or len(df) < 2: return None` guard is exercised.
  def _empty_fetch(_start_ms, _end_ms):
    return pd.DataFrame()

  orig = btc_context.fetch_btc_daily
  btc_context.fetch_btc_daily = _empty_fetch
  try:
    result = btc_context.btc_change_between(
      datetime(2024, 1, 1), datetime(2024, 3, 31),
    )
  finally:
    btc_context.fetch_btc_daily = orig

  assert result is None, (
    "CS-045 REGRESSION: btc_change_between should return None on an "
    f"empty DataFrame — got {result!r}. The empty-path guard "
    "(df.empty or len(df) < 2) has been changed."
  )


def test_btc_change_between_narrowed_except_not_bare():
  """Lock the CS-045 fix: the ``except`` clause must not be bare
  ``except Exception``. A regression to bare-except would silently
  hide real bugs again.
  """
  from backend.analysis import btc_context
  # Strip comments and docstrings so our own explanatory prose doesn't
  # trip the guard. Only real code is inspected.
  src = inspect.getsource(btc_context.btc_change_between)
  code_lines = [
    ln for ln in src.splitlines()
    if ln.strip() and not ln.strip().startswith("#")
  ]
  code_only = "\n".join(code_lines)
  if '"""' in code_only:
    parts = code_only.split('"""')
    code_only = parts[0] + ("".join(parts[2:]) if len(parts) > 2 else "")

  assert "except Exception" not in code_only, (
    "CS-045 REGRESSION: btc_change_between has regressed to "
    "`except Exception:`. The CS-045 fix narrowed it to "
    "`(KeyError, IndexError, ValueError, TypeError)` — keep that "
    "narrow list so genuine bugs surface."
  )
  # Positive check: the narrowed tuple must still be in place.
  for err_type in ("KeyError", "IndexError"):
    assert err_type in code_only, (
      f"CS-045 REGRESSION: btc_change_between no longer catches "
      f"{err_type}. The original bare-except covered this; dropping "
      "it will expose a real production failure path."
    )
