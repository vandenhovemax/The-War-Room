"""Quant Finance Scanner — trading-specific integrity checks."""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.analysis.oos_integrity.config import AuditConfig
from backend.analysis.oos_integrity.models import ScanResult
from backend.analysis.oos_integrity.scanners.base import AbstractScanner


class QuantFinanceScanner(AbstractScanner):
  name = "quant_finance"
  category = "Quant Finance"

  def scan(self, ctx: dict, config: AuditConfig) -> ScanResult:
    findings = []
    is_trades = ctx.get("is_trades", [])
    oos_trades = ctx.get("oos_trades", [])
    all_trades = is_trades + oos_trades

    if all_trades:
      findings.extend(self._check_point_in_time(all_trades))
      findings.extend(self._check_commission_consistency(is_trades, oos_trades))
      findings.extend(self._check_price_continuity(all_trades, config))
      findings.extend(self._check_stale_data(all_trades))
    if is_trades and oos_trades:
      findings.extend(self._check_survivorship_bias(is_trades, oos_trades))
      findings.extend(self._check_market_hours(all_trades))

    score = max(0, 100 - sum(
      30 if f.severity == "CRITICAL" else 15 if f.severity == "HIGH"
      else 5 for f in findings
    ))
    return ScanResult(self.name, findings, score)

  def _check_point_in_time(self, trades):
    """Entry signals should only use data available at entry time."""
    bad = []
    for t in trades:
      if t.entry_time and t.exit_time:
        entry = pd.Timestamp(t.entry_time)
        exit_t = pd.Timestamp(t.exit_time)
        # Entry and exit on exact same timestamp = suspicious
        if entry == exit_t:
          bad.append({"id": t.id, "symbol": t.symbol, "time": str(entry)})
    if not bad:
      return []
    return [self._finding(
      "QF-001", "MEDIUM",
      f"{len(bad)} trades with identical entry/exit timestamps",
      "Zero-duration trades may indicate look-ahead in exit logic",
      evidence={"count": len(bad), "samples": bad[:5]},
      affected_rows=len(bad),
      confidence=0.6,
    )]

  def _check_survivorship_bias(self, is_trades, oos_trades):
    """Symbols in IS that disappear from OOS may indicate delisting."""
    is_symbols = {t.symbol for t in is_trades}
    oos_symbols = {t.symbol for t in oos_trades}
    disappeared = is_symbols - oos_symbols
    if len(disappeared) == 0 or len(disappeared) < len(is_symbols) * 0.1:
      return []
    return [self._finding(
      "QF-002", "MEDIUM",
      f"{len(disappeared)} symbols in IS disappeared from OOS",
      "Missing symbols may indicate survivorship bias (delisted/dead symbols excluded)",
      evidence={"disappeared": sorted(disappeared)[:10], "pct": round(len(disappeared) / len(is_symbols) * 100, 1)},
      affected_columns=sorted(disappeared),
      confidence=0.5,
      remediation="Verify these symbols still trade in OOS period — if delisted, this is expected",
    )]

  def _check_commission_consistency(self, is_trades, oos_trades):
    """Commission rates should be consistent IS vs OOS."""
    def _avg_commission_rate(trades):
      rates = []
      for t in trades:
        comm = getattr(t, "commission", None)
        if comm and t.entry_price and t.quantity:
          notional = t.entry_price * t.quantity
          if notional > 0:
            rates.append(comm / notional)
      return np.mean(rates) if rates else 0

    is_rate = _avg_commission_rate(is_trades)
    oos_rate = _avg_commission_rate(oos_trades)
    if is_rate == 0 and oos_rate == 0:
      return []
    if is_rate == 0 or oos_rate == 0:
      return [self._finding(
        "QF-003", "MEDIUM",
        f"Commission rate mismatch: IS={is_rate:.6f}, OOS={oos_rate:.6f}",
        "One period has zero commission — may distort performance comparison",
        evidence={"is_rate": round(is_rate, 6), "oos_rate": round(oos_rate, 6)},
      )]
    ratio = max(is_rate, oos_rate) / max(min(is_rate, oos_rate), 1e-10)
    if ratio < 2.0:
      return []
    return [self._finding(
      "QF-004", "MEDIUM",
      f"Commission rate differs {ratio:.1f}x between IS and OOS",
      f"IS rate={is_rate:.6f}, OOS rate={oos_rate:.6f}",
      evidence={"ratio": round(ratio, 2), "is_rate": round(is_rate, 6), "oos_rate": round(oos_rate, 6)},
    )]

  def _check_price_continuity(self, trades, config):
    """Flag impossible price jumps between entry and exit."""
    jumps = []
    for t in trades:
      if t.entry_price and t.exit_price and t.entry_price > 0:
        change = abs(t.exit_price - t.entry_price) / t.entry_price
        if change > config.price_jump_threshold:
          jumps.append({
            "id": t.id, "symbol": t.symbol,
            "entry_price": t.entry_price, "exit_price": t.exit_price,
            "change_pct": round(change * 100, 1),
          })
    if not jumps:
      return []
    return [self._finding(
      "QF-005", "MEDIUM" if len(jumps) < 5 else "HIGH",
      f"{len(jumps)} trades with >{config.price_jump_threshold:.0%} price change",
      "Extreme price moves may indicate data gaps or corporate actions",
      evidence={"jumps": jumps[:10]},
      affected_rows=len(jumps),
      confidence=0.6,
    )]

  def _check_stale_data(self, trades):
    """Consecutive trades with identical prices = stale data feed."""
    by_symbol: dict[str, list] = {}
    for t in trades:
      if t.entry_price:
        by_symbol.setdefault(t.symbol, []).append(t.entry_price)
    stale_symbols = []
    for sym, prices in by_symbol.items():
      if len(prices) < 5:
        continue
      consecutive_same = 0
      max_consecutive = 0
      for i in range(1, len(prices)):
        if prices[i] == prices[i - 1]:
          consecutive_same += 1
          max_consecutive = max(max_consecutive, consecutive_same)
        else:
          consecutive_same = 0
      if max_consecutive >= 5:
        stale_symbols.append({"symbol": sym, "max_consecutive": max_consecutive})
    if not stale_symbols:
      return []
    return [self._finding(
      "QF-006", "MEDIUM",
      f"{len(stale_symbols)} symbols with 5+ consecutive identical entry prices",
      "Stale entry prices suggest data feed issues or very low liquidity",
      evidence={"stale_symbols": stale_symbols[:10]},
      affected_columns=[s["symbol"] for s in stale_symbols],
      confidence=0.5,
    )]

  def _check_market_hours(self, trades):
    """Flag trades on weekends (crypto trades 24/7, so this is INFO only)."""
    weekend = sum(
      1 for t in trades
      if t.entry_time and pd.Timestamp(t.entry_time).dayofweek >= 5
    )
    if weekend == 0 or len(trades) == 0:
      return []
    pct = weekend / len(trades) * 100
    # For crypto (Bybit) this is expected — just informational
    return [self._finding(
      "QF-007", "INFO",
      f"{weekend} trades ({pct:.1f}%) entered on weekends",
      "Expected for crypto markets (24/7 trading)",
      evidence={"weekend_count": weekend, "pct": round(pct, 1)},
      confidence=0.3,
    )]
