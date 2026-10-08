"""Trade and row hashing for duplicate/identity detection."""
from __future__ import annotations

import hashlib

import pandas as pd


def hash_trade(trade) -> str:
  """Deterministic hash of trade identity fields.

  Uses: symbol, entry_time, exit_time, entry_price, exit_price, quantity.
  """
  parts = [
    str(getattr(trade, "symbol", "")),
    str(getattr(trade, "entry_time", "")),
    str(getattr(trade, "exit_time", "")),
    f"{getattr(trade, 'entry_price', 0):.8f}",
    f"{getattr(trade, 'exit_price', 0):.8f}",
    f"{getattr(trade, 'quantity', 0):.8f}",
  ]
  raw = "|".join(parts).encode("utf-8")
  return hashlib.sha256(raw).hexdigest()[:16]


def hash_row(row: pd.Series) -> str:
  """Hash a daily PnL row (date + all values)."""
  parts = [str(row.name)] + [f"{v:.8f}" for v in row.values]
  raw = "|".join(parts).encode("utf-8")
  return hashlib.sha256(raw).hexdigest()[:16]


def find_exact_duplicates(
  is_hashes: set[str], oos_hashes: set[str],
) -> set[str]:
  """Return hashes present in both IS and OOS."""
  return is_hashes & oos_hashes


def hash_trades_bulk(trades: list) -> dict[str, list]:
  """Hash all trades, return {hash: [trade_indices]}."""
  result: dict[str, list] = {}
  for i, t in enumerate(trades):
    h = hash_trade(t)
    result.setdefault(h, []).append(i)
  return result
