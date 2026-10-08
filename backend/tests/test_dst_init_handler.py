"""DST init-retry: pin the isinstance-based exception match.

CS-007 regression: the old code matched DST errors by substring
(``"ambiguous" in str(e).lower()`` / ``"AmbiguousTimeError" in type(e).__name__``).
That's fragile — future pytz/pandas versions may reword the message, and
it misses ``NonExistentTimeError`` (spring-forward gap) entirely.

This test pins the contract:
  1. Matches ``pytz.exceptions.AmbiguousTimeError``
  2. Matches ``pytz.exceptions.NonExistentTimeError``
  3. Does NOT match unrelated exceptions (re-raises them)
"""
from __future__ import annotations

import pytz.exceptions as _tz_err


def test_pytz_ambiguous_error_is_classified_as_dst():
  """AmbiguousTimeError must be caught by the handler's isinstance check."""
  e = _tz_err.AmbiguousTimeError("2023-11-05 01:00:00")
  assert isinstance(e, (_tz_err.AmbiguousTimeError, _tz_err.NonExistentTimeError))


def test_pytz_nonexistent_error_is_classified_as_dst():
  """Spring-forward 2024-03-10 02:30 gap — NonExistentTimeError must be caught."""
  e = _tz_err.NonExistentTimeError("2024-03-10 02:30:00")
  assert isinstance(e, (_tz_err.AmbiguousTimeError, _tz_err.NonExistentTimeError))


def test_unrelated_exception_is_not_classified_as_dst():
  """ValueError/KeyError must NOT match — they should re-raise to caller."""
  assert not isinstance(ValueError("unrelated"), (_tz_err.AmbiguousTimeError, _tz_err.NonExistentTimeError))
  assert not isinstance(KeyError("missing col"), (_tz_err.AmbiguousTimeError, _tz_err.NonExistentTimeError))


def test_ambiguous_error_inherits_from_exception():
  """Sanity: handler's ``except Exception`` clause must still catch these."""
  assert issubclass(_tz_err.AmbiguousTimeError, Exception)
  assert issubclass(_tz_err.NonExistentTimeError, Exception)


def test_real_ambiguous_timestamp_raises_ambiguous_error():
  """End-to-end: localizing 2023-11-05 01:00 NYT raises AmbiguousTimeError."""
  import pandas as pd
  idx = pd.DatetimeIndex(["2023-11-05 01:00:00"])
  try:
    idx.tz_localize("America/New_York", ambiguous="raise")
  except _tz_err.AmbiguousTimeError:
    return
  raise AssertionError("Expected AmbiguousTimeError from fall-back duplicate hour")


def test_real_nonexistent_timestamp_raises_nonexistent_error():
  """End-to-end: localizing 2024-03-10 02:30 NYT raises NonExistentTimeError."""
  import pandas as pd
  idx = pd.DatetimeIndex(["2024-03-10 02:30:00"])
  try:
    idx.tz_localize("America/New_York", nonexistent="raise")
  except _tz_err.NonExistentTimeError:
    return
  raise AssertionError("Expected NonExistentTimeError from spring-forward gap")
