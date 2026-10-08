"""Pin the typed Bybit exception contract.

CS-001 regression: ``_check_response`` used to raise a plain ``ValueError``
so callers had to parse ``str(e)`` for the retCode (e.g. ``"110043" in str(e)``).
Now it raises ``BybitAPIError`` with structured ``ret_code`` / ``ret_msg``
attributes while remaining a ``ValueError`` subclass for backwards compat.
"""
from __future__ import annotations

import pytest

from backend.exchange.bybit_trading import BybitAPIError, BybitTradingClient


def test_check_response_raises_bybit_api_error_on_nonzero_retcode():
  with pytest.raises(BybitAPIError) as ei:
    BybitTradingClient._check_response({"retCode": 110043, "retMsg": "leverage not modified"})
  assert ei.value.ret_code == 110043
  assert ei.value.ret_msg == "leverage not modified"


def test_check_response_accepts_retcode_zero():
  """No exception on success."""
  BybitTradingClient._check_response({"retCode": 0, "retMsg": "OK"})


def test_bybit_api_error_is_valueerror_subclass():
  """Legacy ``except ValueError`` handlers must still catch."""
  with pytest.raises(ValueError):
    BybitTradingClient._check_response({"retCode": 10001, "retMsg": "bad param"})


def test_str_form_preserves_legacy_format():
  """``"110043" in str(e)`` substring checks in position_manager must keep working."""
  err = BybitAPIError(110043, "leverage not modified")
  assert "110043" in str(err)
  assert "Bybit API error" in str(err)


def test_missing_retmsg_defaults_to_unknown():
  with pytest.raises(BybitAPIError) as ei:
    BybitTradingClient._check_response({"retCode": 99999})
  assert ei.value.ret_msg == "Unknown"


def test_missing_retcode_is_treated_as_error():
  """Malformed response (no retCode) must raise — never silently pass."""
  with pytest.raises(BybitAPIError) as ei:
    BybitTradingClient._check_response({})
  assert ei.value.ret_code == -1


def test_ret_code_is_int_type():
  """Callers should be able to compare ``e.ret_code == 110043`` without str coercion."""
  err = BybitAPIError(110043, "x")
  assert isinstance(err.ret_code, int)
  assert err.ret_code == 110043
