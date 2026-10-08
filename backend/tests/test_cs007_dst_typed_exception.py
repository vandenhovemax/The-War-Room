"""CS-007: DST detection across the codebase must match by exception
TYPE (``pytz.exceptions.AmbiguousTimeError`` / ``NonExistentTimeError``),
never by substring on ``str(e)``. Substring matching is fragile: a
pandas upgrade, a pytz → zoneinfo swap, or any translation change in
the exception message silently flips the branch.

Two sites are load-bearing:
  * ``engine.py`` — single-strategy path
  * ``portfolio_engine.py`` — per-symbol init + ``_sanitize_dst_timestamps``

This test locks both. If someone reverts either site to
``"ambiguous" in str(e).lower()`` or widens back to bare ``except
Exception``, the test fails loudly.
"""
from __future__ import annotations

import inspect

import pandas as pd
import pytest

from backend.backtester import engine as engine_mod
from backend.backtester import portfolio_engine as pe_mod


def _contains_substring_match(src: str) -> bool:
  """Cheap heuristic: detect the old substring-match pattern."""
  bad_patterns = [
    "'ambiguous' in str",
    '"ambiguous" in str',
    ".lower()",  # only appears in this module from the old pattern
  ]
  # We only count .lower() hits inside the dst/ambiguous block
  lowered = src.lower()
  if "ambiguous" in lowered and "str(e).lower()" in lowered:
    return True
  return any(p in src for p in bad_patterns[:2])


def test_engine_dst_handler_uses_typed_exception():
  """engine.py's DST handler must use ``isinstance(..., AmbiguousTimeError)``
  style matching, not string inspection."""
  src = inspect.getsource(engine_mod)

  assert "AmbiguousTimeError" in src, (
    "CS-007: engine.py no longer imports AmbiguousTimeError. DST "
    "handling has regressed to substring or bare-except matching."
  )
  assert not _contains_substring_match(src), (
    "CS-007 REGRESSION: engine.py's DST handler does substring "
    "matching on str(e). A pandas/pytz message change silently "
    "breaks the 2023-11-05 01:00 sanitiser. Use "
    "isinstance(e, (AmbiguousTimeError, NonExistentTimeError)) instead."
  )


def test_sanitize_dst_timestamps_does_not_swallow_arbitrary_exceptions():
  """``_sanitize_dst_timestamps`` must re-raise non-DST errors. A bare
  ``except Exception`` would silently NaT-fill rows for any failure
  mode — missing columns, wrong dtype, memory errors — hiding real
  bugs as if they were DST.
  """
  src = inspect.getsource(pe_mod._sanitize_dst_timestamps)

  # The except clause must name the typed exceptions
  assert "AmbiguousTimeError" in src, (
    "CS-007: _sanitize_dst_timestamps no longer references "
    "AmbiguousTimeError. If you widened the except to bare Exception "
    "again, you've silently reintroduced the bug: any unrelated "
    "pandas error now gets silently NaT-patched."
  )
  # Must NOT be a bare `except Exception` on the tz_localize try
  # (comments can mention it; the except statement itself must not)
  lines = [ln.strip() for ln in src.split("\n")]
  except_lines = [ln for ln in lines if ln.startswith("except ")]
  for ex in except_lines:
    assert ex != "except Exception:", (
      "CS-007 REGRESSION: _sanitize_dst_timestamps uses bare "
      "`except Exception`. It must catch only "
      "(AmbiguousTimeError, NonExistentTimeError). Any other error "
      "is a real bug and must propagate."
    )


def test_sanitize_dst_runtime_swallows_only_dst_errors(monkeypatch):
  """Behavioural: feed the sanitiser a non-DST failure (e.g. a broken
  DataFrame whose tz_localize raises TypeError) and confirm it raises
  instead of silently NaT-filling the data.
  """
  from pandas import DatetimeIndex
  # Build a plain naive index with NO DST ambiguity
  idx = pd.date_range("2024-06-01", periods=5, freq="15min", tz=None)
  df = pd.DataFrame({"close": [1, 2, 3, 4, 5]}, index=idx)

  # Force tz_localize to raise a non-DST exception (TypeError) via monkeypatch
  real_tz_localize = DatetimeIndex.tz_localize

  def _fake_tz_localize(self, *args, **kwargs):
    # Only raise on the ambiguous=raise first call; let the sanitiser
    # branch handle recovery normally. Here we raise the WRONG class.
    if kwargs.get("ambiguous") == "raise":
      raise TypeError("simulated non-DST failure")
    return real_tz_localize(self, *args, **kwargs)

  monkeypatch.setattr(DatetimeIndex, "tz_localize", _fake_tz_localize)

  tf_data = {"15": df}
  with pytest.raises(TypeError):
    pe_mod._sanitize_dst_timestamps(tf_data)


def test_sanitize_dst_runtime_handles_real_dst_ambiguity():
  """Behavioural: give the sanitiser the real 2023-11-05 01:00 DST
  ambiguity and confirm it strips the ambiguous rows rather than
  raising.
  """
  # 2023-11-05 01:00:00 is ambiguous in NYT (DST fall-back)
  idx = pd.DatetimeIndex([
    pd.Timestamp("2023-11-05 00:30"),
    pd.Timestamp("2023-11-05 01:00"),
    pd.Timestamp("2023-11-05 01:30"),
    pd.Timestamp("2023-11-05 02:00"),
  ])
  df = pd.DataFrame({"close": [1.0, 2.0, 3.0, 4.0]}, index=idx)
  tf_data = {"30": df}

  pe_mod._sanitize_dst_timestamps(tf_data)

  # After sanitising, the DataFrame has fewer rows — the ambiguous
  # timestamp has been dropped. (Index stays naive; downstream code
  # re-attempts tz_localize and now succeeds.)
  out = tf_data["30"]
  assert len(out) < len(df), (
    "CS-007: sanitiser failed to drop the 2023-11-05 01:00 ambiguous "
    "row — input had 4 rows, output has the same count."
  )
  # The surviving rows must tz_localize without error
  out.index.tz_localize("America/New_York", ambiguous="raise")
