"""CS-030: if a strategy self-applies martingale on entry qty AND engine
risk controls are active, the audit warned that the engine would silently
multiply again → ``qty × multiplier²`` on losing streaks.

Tracing current code: ``_post_next_risk_resize`` at engine.py:835 does
``pos.qty = new_qty`` — it REPLACES the strategy-computed qty, not
multiplies. The engine computes ``new_qty = (risk_amt / sl_distance) ×
multiplier^streak`` from scratch. Whatever martingale the strategy
applied internally is discarded along with the rest of the strategy's
sizing.

These tests LOCK that invariant. If someone later refactors
``_post_next_risk_resize`` to multiply onto the existing qty instead
of replacing, the tests fail immediately.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.backtester.base_strategy import BaseStrategy
from backend.backtester.engine import BacktestEngine, BacktestConfig


class _SelfMartingaleStrategy(BaseStrategy):
  """Simulates a PineScript strategy that self-applies a 2x martingale
  on every loss — passes a wildly inflated qty to the engine. If the
  engine multiplied on top, qty would explode combinatorially.
  """

  def init(self):
    self._entry_count = 0

  def next(self):
    bar = self._bar
    # Open + close every 3 bars so a losing streak forms
    if bar > 0 and bar % 3 == 0:
      self._entry_count += 1
      # Strategy's "internal" martingale: 2x per consecutive loss
      streak = self._consecutive_losses
      self_mg_qty = 1.0 * (2.0 ** min(streak, 5))
      self.entry(id=f"E{bar}", direction="long", qty=self_mg_qty)

    # Register SL 1% below entry so engine can risk-size
    for pid, pos in self._positions.items():
      if pos.side == "long":
        self.exit(id=f"SL_{pid}", from_entry=pid,
                  stop=pos.entry_price * 0.99)


def _make_declining_ohlc(n: int = 60) -> pd.DataFrame:
  """Price declines linearly so every long position loses → creates
  a consecutive-losses streak to test martingale growth."""
  idx = pd.date_range("2024-01-01", periods=n, freq="15min")
  base = 100.0 - np.arange(n) * 0.5
  return pd.DataFrame({
    "open": base, "high": base + 0.1, "low": base - 0.1, "close": base,
    "volume": 1000.0,
  }, index=idx)


def test_post_resize_replaces_not_multiplies():
  """End-to-end: run a strategy that self-applies martingale with engine
  risk controls active. The engine must REPLACE qty, so pos.qty matches
  the engine's formula, NOT ``strategy_internal × engine_formula``.
  """
  df = _make_declining_ohlc(60)
  cfg = BacktestConfig(
    initial_capital=10_000.0, commission_pct=0.0,
    risk_pct=1.0,           # engine risk-sizing active
    martingale_enabled=False,  # NO engine martingale — isolate the replacement test
  )
  strategy = _SelfMartingaleStrategy()
  engine = BacktestEngine(cfg)
  engine.run(strategy, df)

  # Find all long entries
  entries = [t for t in strategy._trades if t.action == "entry"]
  assert len(entries) >= 3, "Not enough entries fired"

  # For each entry, its qty must equal engine's risk-sized qty
  # (risk_amt / sl_distance), NOT the strategy's self-martingaled qty.
  # SL is 1% below entry; risk is 1% of ~10k equity → ~$100 risk.
  # So expected engine qty ≈ $100 / ($entry × 0.01) ≈ 100/entry.
  for t in entries:
    expected_engine_qty = 100.0 / (t.price * 0.01)
    # The self-martingale would have fed qtys like 1, 2, 4, 8, 16, 32.
    # Engine replacement gives qty bounded by risk/SL ≈ 100/price.
    # If engine MULTIPLIED instead of REPLACED, we'd see 100×(1, 2, 4, 8, 16, 32).
    # Lock with a loose ceiling — qty should never exceed what the
    # engine's formula produces on its own, even by 5%.
    assert t.qty <= expected_engine_qty * 1.05 + 0.01, (
      f"CS-030: entry qty {t.qty} exceeds engine formula "
      f"{expected_engine_qty:.4f} — engine is multiplying on top "
      "of the strategy's internal martingale, not replacing it."
    )


def test_post_resize_with_engine_martingale():
  """When engine martingale IS enabled, the engine's own multiplier is
  applied — but only once. Qty grows with streak to ``multiplier^streak``
  off the risk base, NOT off the strategy's self-martingaled qty.
  """
  df = _make_declining_ohlc(60)
  cfg = BacktestConfig(
    initial_capital=10_000.0, commission_pct=0.0,
    risk_pct=1.0,
    martingale_enabled=True,
    martingale_multiplier=2.0,
    martingale_type="martingale",
    martingale_max_streak=3,
  )
  strategy = _SelfMartingaleStrategy()
  engine = BacktestEngine(cfg)
  engine.run(strategy, df)

  entries = [t for t in strategy._trades if t.action == "entry"]
  assert entries

  # Worst-case engine qty on a losing streak:
  #   base = (risk_pct * equity) / sl_distance
  #   capped streak = 3 → multiplier = 2^3 = 8
  #   peak qty ≈ 8 × base
  # If engine multiplied the strategy's internal martingale (also 2^streak)
  # on top, we'd see 2^n × 2^n = 4^n — diverges fast.
  # Cap the check at something far below 4^n but far above 8×base.
  for t in entries:
    base_qty = (0.01 * 10_000.0) / (t.price * 0.01)
    engine_peak_qty = base_qty * 8.0  # 2^3 cap
    # Allow generous headroom (10x) for equity growth from early wins
    # and rounding, but anything approaching 4^n compounding fails.
    assert t.qty <= engine_peak_qty * 10, (
      f"CS-030: qty {t.qty} blew past engine-only martingale ceiling "
      f"{engine_peak_qty * 10:.4f} — double-multiplication of strategy "
      "internal martingale × engine martingale has regressed."
    )
