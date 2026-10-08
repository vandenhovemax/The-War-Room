"""CS-045: ``iloc[-1]`` on equity/series must never run against an
empty input.

Two sites:
  * ``batch_selection_scoring`` — every public metric calls
    ``_active_days(pnl) < _MIN_TRADING_DAYS → return _SKIP`` before any
    ``equity.iloc[-1]`` read. An all-filtered group (zero non-zero PnL
    days) therefore returns ``_SKIP`` (= -999.0) instead of raising.
  * ``btc_context.btc_change_between`` — narrowed the bare ``except
    Exception`` to ``(KeyError, IndexError, ValueError, TypeError)`` so
    a genuine bug surfaces instead of a silent ``None``.

This test exercises the behaviour end-to-end — empty series and a
single-zero series must never raise ``IndexError``.
"""
from __future__ import annotations

import inspect

import pandas as pd

from backend.analysis import batch_selection_scoring as scoring


def test_empty_pnl_series_returns_skip_not_indexerror():
  """All metrics must short-circuit via ``_active_days < _MIN_TRADING_DAYS``."""
  empty = pd.Series([], dtype=float)

  for fn_name in (
    "metric_lake_ratio", "metric_calmar", "metric_pf",
    "metric_sortino", "metric_tail_ratio",
  ):
    fn = getattr(scoring, fn_name, None)
    if fn is None:
      continue
    try:
      result = fn(empty)
    except IndexError:
      raise AssertionError(
        f"CS-045 REGRESSION: {fn_name} raised IndexError on empty "
        "series. The _active_days guard is missing or mis-ordered — "
        "an all-filtered group will crash scoring instead of returning "
        "the skip sentinel."
      )
    # Skip sentinel = -999.0
    assert result == scoring._SKIP, (
      f"{fn_name} on empty series returned {result}, expected "
      f"{scoring._SKIP} (skip sentinel)."
    )


def test_all_zero_pnl_series_returns_skip_not_indexerror():
  """A series with all-zero PnL has zero active days → must skip."""
  all_zero = pd.Series([0.0] * 50)

  try:
    result = scoring.metric_calmar(all_zero)
  except IndexError:
    raise AssertionError(
      "CS-045 REGRESSION: metric_calmar raised IndexError on all-zero "
      "PnL. The active-days guard is broken."
    )
  assert result == scoring._SKIP


def test_btc_context_except_is_typed_not_bare():
  """btc_change_between must no longer swallow arbitrary exceptions
  with a bare except Exception — that silently hid IndexError on
  empty argsort results AND masked schema bugs.
  """
  from backend.analysis import btc_context

  src = inspect.getsource(btc_context.btc_change_between)
  # Typed except must be present
  assert "IndexError" in src or "KeyError" in src, (
    "CS-045 REGRESSION: btc_change_between no longer has a typed "
    "except clause. A bare except Exception here would silently hide "
    "real bugs (schema drift, empty argsort) as `None`."
  )
  # Forbid `except Exception:` — a bare catch-all is exactly what the
  # audit flagged.
  except_lines = [
    ln.strip() for ln in src.split("\n") if ln.strip().startswith("except ")
  ]
  for ex in except_lines:
    assert ex != "except Exception:", (
      "CS-045 REGRESSION: btc_change_between uses `except Exception:`. "
      "The bare catch-all silently returns None for any error — narrow "
      "to (KeyError, IndexError, ValueError, TypeError)."
    )
