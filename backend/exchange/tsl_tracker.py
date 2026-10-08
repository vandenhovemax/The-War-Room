"""Trailing Stop Loss Tracker — real-time TSL via live price feeds.

Mirrors the backtester's trailing stop logic: tracks extreme price per
position, triggers market close when price retraces by trail_points.
Uses live ticker prices from BybitWSManager.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from backend.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class TrackedPosition:
  """A position being tracked for trailing stop."""
  position_db_id: int
  portfolio_id: int
  symbol: str
  side: str          # "Buy" (long) or "Sell" (short)
  entry_price: float
  trail_points: float
  trail_offset: float
  extreme_price: float  # Best price seen (highest for long, lowest for short)
  active: bool = True


# Callback type: (position_db_id, symbol, side, qty, trigger_price) → None
TSLTriggerCallback = Callable[[int, str, str, float, float], None]


class TrailingStopTracker:
  """Tracks trailing stops for live positions using real-time prices.

  Algorithm (mirrors backtester engine.py lines 581-608):
  - Long position:
    1. Track highest price seen (extreme_price)
    2. Activation: extreme_price >= entry_price + trail_offset
    3. Stop level: extreme_price - trail_points
    4. Trigger: current_price <= stop_level
  - Short position:
    1. Track lowest price seen (extreme_price)
    2. Activation: extreme_price <= entry_price - trail_offset
    3. Stop level: extreme_price + trail_points
    4. Trigger: current_price >= stop_level
  """

  def __init__(self, on_trigger: TSLTriggerCallback | None = None):
    self._on_trigger = on_trigger
    # symbol → list of TrackedPositions (multiple positions per symbol possible)
    self._tracked: dict[str, list[TrackedPosition]] = {}

  # ── Registration ──────────────────────────────────────────────────

  def register(
    self,
    position_db_id: int,
    portfolio_id: int,
    symbol: str,
    side: str,
    entry_price: float,
    trail_points: float,
    trail_offset: float = 0,
    initial_extreme: float | None = None,
  ) -> None:
    """Register a position for trailing stop tracking.

    Args:
      position_db_id: Database Position.id
      portfolio_id: Portfolio this belongs to
      symbol: e.g. "BTCUSDT"
      side: "Buy" (long) or "Sell" (short)
      entry_price: Position entry price
      trail_points: Price distance for the trailing stop
      trail_offset: Price distance from entry before TSL activates (0 = immediate)
      initial_extreme: Saved extreme price from DB (for restart recovery).
                       If None, defaults to entry_price (normal new-position flow).
    """
    if trail_points <= 0:
      return

    # Dedup: skip if already tracked (prevents double-registration from
    # both _on_entry_filled and subsequent register_exit signals)
    if self.is_tracking(position_db_id):
      logger.debug("TSL already tracked for pos=%d — skipping duplicate register", position_db_id)
      return

    tracked = TrackedPosition(
      position_db_id=position_db_id,
      portfolio_id=portfolio_id,
      symbol=symbol,
      side=side,
      entry_price=entry_price,
      trail_points=trail_points,
      trail_offset=trail_offset,
      extreme_price=initial_extreme if initial_extreme is not None else entry_price,
    )

    if symbol not in self._tracked:
      self._tracked[symbol] = []
    self._tracked[symbol].append(tracked)

    logger.info(
      "TSL registered: pos=%d %s %s entry=%.4f trail=%.4f offset=%.4f",
      position_db_id, symbol, side, entry_price, trail_points, trail_offset,
    )

  def unregister(self, position_db_id: int) -> None:
    """Remove a position from tracking (closed or TSL cancelled)."""
    for symbol, positions in list(self._tracked.items()):
      self._tracked[symbol] = [p for p in positions if p.position_db_id != position_db_id]
      if not self._tracked[symbol]:
        del self._tracked[symbol]

  def is_tracking(self, position_db_id: int) -> bool:
    """Check if a position is already being tracked for TSL."""
    for positions in self._tracked.values():
      for pos in positions:
        if pos.position_db_id == position_db_id and pos.active:
          return True
    return False

  def get_tracked_count(self) -> int:
    """Get total number of positions being tracked."""
    return sum(len(positions) for positions in self._tracked.values())

  def get_tracked_symbols(self) -> set[str]:
    """Get set of symbols with active TSL tracking."""
    return set(self._tracked.keys())

  def update_extreme(self, position_db_id: int, new_extreme: float) -> None:
    """Sync extreme price from an external source (e.g. bar-based DB update).

    Only moves the extreme in the favorable direction:
    - Long (Buy): extreme can only increase
    - Short (Sell): extreme can only decrease
    """
    for positions in self._tracked.values():
      for pos in positions:
        if pos.position_db_id == position_db_id and pos.active:
          if pos.side == "Buy":
            pos.extreme_price = max(pos.extreme_price, new_extreme)
          else:
            pos.extreme_price = min(pos.extreme_price, new_extreme)
          return

  # ── Price Updates ─────────────────────────────────────────────────

  def on_price_update(self, symbol: str, last_price: float) -> None:
    """Process a live price update for a symbol.

    Called on every ticker update from BybitWSManager. Checks all
    tracked positions for this symbol against the current price.
    """
    if last_price <= 0:
      logger.warning("TSL ignoring invalid price %.4f for %s", last_price, symbol)
      return

    positions = self._tracked.get(symbol)
    if not positions:
      return

    triggered = []

    for pos in positions:
      if not pos.active:
        continue

      if pos.side == "Buy":
        # Long position — track highest, trigger on retrace
        pos.extreme_price = max(pos.extreme_price, last_price)

        # Check activation (must move trail_offset above entry first)
        if pos.trail_offset > 0:
          activation_level = pos.entry_price + pos.trail_offset
          if pos.extreme_price < activation_level:
            continue  # Not activated yet

        # Check trigger
        stop_level = pos.extreme_price - pos.trail_points
        if last_price <= stop_level:
          logger.info(
            "TSL TRIGGERED (long): pos=%d %s price=%.4f extreme=%.4f stop=%.4f",
            pos.position_db_id, symbol, last_price, pos.extreme_price, stop_level,
          )
          triggered.append(pos)

      else:
        # Short position — track lowest, trigger on bounce
        pos.extreme_price = min(pos.extreme_price, last_price)

        # Check activation
        if pos.trail_offset > 0:
          activation_level = pos.entry_price - pos.trail_offset
          if pos.extreme_price > activation_level:
            continue  # Not activated yet

        # Check trigger
        stop_level = pos.extreme_price + pos.trail_points
        if last_price >= stop_level:
          logger.info(
            "TSL TRIGGERED (short): pos=%d %s price=%.4f extreme=%.4f stop=%.4f",
            pos.position_db_id, symbol, last_price, pos.extreme_price, stop_level,
          )
          triggered.append(pos)

    # Fire callbacks for triggered positions
    for pos in triggered:
      pos.active = False
      if self._on_trigger:
        try:
          self._on_trigger(
            pos.position_db_id, pos.symbol, pos.side,
            0,  # qty=0 means close full position
            last_price,
          )
        except Exception as e:
          logger.error("TSL trigger callback error: %s", e)

    # Remove triggered positions from tracking
    if triggered:
      triggered_ids = {p.position_db_id for p in triggered}
      self._tracked[symbol] = [
        p for p in self._tracked[symbol]
        if p.position_db_id not in triggered_ids
      ]
      if not self._tracked[symbol]:
        del self._tracked[symbol]

  # ── Status ────────────────────────────────────────────────────────

  def get_status(self) -> list[dict]:
    """Get current TSL tracking status for all positions."""
    result = []
    for symbol, positions in self._tracked.items():
      for pos in positions:
        if pos.side == "Buy":
          stop_level = pos.extreme_price - pos.trail_points
        else:
          stop_level = pos.extreme_price + pos.trail_points

        result.append({
          "position_id": pos.position_db_id,
          "portfolio_id": pos.portfolio_id,
          "symbol": symbol,
          "side": pos.side,
          "entry_price": pos.entry_price,
          "extreme_price": pos.extreme_price,
          "trail_points": pos.trail_points,
          "trail_offset": pos.trail_offset,
          "stop_level": stop_level,
          "active": pos.active,
        })
    return result
