"""Safety Monitor — circuit breakers, pre-trade checks, daily loss limits.

Protects against excessive losses and enforces trading limits.
Resets daily counters at midnight New York time.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from backend.config.settings import get_settings
from backend.db.models import DailyPnL, LiveEvent, Order, Position
from backend.utils.logger import get_logger

logger = get_logger(__name__)

NYT = ZoneInfo("America/New_York")


class SafetyMonitor:
  """Enforces trading safety limits and circuit breakers.

  Checks:
  - Daily loss limit (circuit breaker)
  - Max positions (long/short/total)
  - Max position notional
  - Exchange connectivity
  - Position count limits
  """

  def __init__(
    self,
    get_equity_fn: "Callable[[], float] | None" = None,
    db_factory: "Callable[[], Session] | None" = None,
  ):
    self._settings = get_settings()
    self._get_equity = get_equity_fn  # Returns total equity (reserve + exchange)
    self._db_factory = db_factory
    self._circuit_breaker_active = False
    self._circuit_breaker_reason = ""
    self._daily_realized_pnl = 0.0
    self._daily_trade_count = 0
    self._daily_wins = 0
    self._daily_losses = 0
    self._last_reset_date: date | None = None
    # Load persisted daily PnL from DB on startup (Bug 43)
    self._load_daily_from_db()

  def _load_daily_from_db(self) -> None:
    """Load today's daily PnL from DB so it persists across restarts (Bug 43)."""
    if not self._db_factory:
      return
    try:
      db = self._db_factory()
      today = datetime.now(NYT).date()
      rows = db.query(DailyPnL).filter(DailyPnL.date == today).all()
      if rows:
        self._daily_realized_pnl = sum(r.realized_pnl or 0 for r in rows)
        self._daily_trade_count = sum(r.trade_count or 0 for r in rows)
        self._daily_wins = sum(r.win_count or 0 for r in rows)
        self._daily_losses = sum(r.loss_count or 0 for r in rows)
        self._last_reset_date = today
        logger.info(
          "Loaded daily PnL from DB: $%.2f (%d trades, %dW/%dL)",
          self._daily_realized_pnl, self._daily_trade_count,
          self._daily_wins, self._daily_losses,
        )
        # Re-check circuit breaker on startup (both USD and % limits)
        if self._daily_realized_pnl < 0:
          daily_loss = abs(self._daily_realized_pnl)
          if self._settings.max_daily_loss_usd > 0 and daily_loss >= self._settings.max_daily_loss_usd:
            self.activate_circuit_breaker("daily_loss_limit_reached")
          elif self._settings.daily_loss_limit_pct > 0 and self._get_equity:
            total_equity = self._get_equity()
            if total_equity > 0:
              pct_limit_usd = total_equity * (self._settings.daily_loss_limit_pct / 100)
              if daily_loss >= pct_limit_usd:
                self.activate_circuit_breaker("daily_loss_pct_limit_reached")
      db.close()
    except Exception as e:
      logger.warning("Failed to load daily PnL from DB: %s", e)

  # ── Pre-Trade Checks ──────────────────────────────────────────────

  def check_pre_trade(
    self,
    db: Session,
    symbol: str,
    side: str,
    notional: float,
    portfolio_id: int | None = None,
  ) -> tuple[bool, str]:
    """Run all pre-trade safety checks.

    Returns: (allowed, reason)
    """
    # Check circuit breaker
    if self._circuit_breaker_active:
      return False, f"Circuit breaker active: {self._circuit_breaker_reason}"

    # Check daily loss limit (both USD and percentage)
    self._ensure_daily_reset()
    if self._daily_realized_pnl < 0:
      daily_loss = abs(self._daily_realized_pnl)

      # Fixed USD limit
      if self._settings.max_daily_loss_usd > 0 and daily_loss >= self._settings.max_daily_loss_usd:
        self.activate_circuit_breaker("daily_loss_limit_reached")
        return False, f"Daily loss limit reached: ${daily_loss:.2f} >= ${self._settings.max_daily_loss_usd:.2f}"

      # Percentage-of-equity limit
      if self._settings.daily_loss_limit_pct > 0 and self._get_equity:
        total_equity = self._get_equity()
        if total_equity > 0:
          pct_limit_usd = total_equity * (self._settings.daily_loss_limit_pct / 100)
          if daily_loss >= pct_limit_usd:
            self.activate_circuit_breaker("daily_loss_pct_limit_reached")
            return False, (
              f"Daily loss {self._settings.daily_loss_limit_pct}% limit reached: "
              f"${daily_loss:.2f} >= ${pct_limit_usd:.2f} ({self._settings.daily_loss_limit_pct}% of ${total_equity:.2f})"
            )

    # Check max notional (skip when notional=0, i.e. pre-sizing call)
    if notional > 0 and self._settings.max_position_notional > 0:
      if notional > self._settings.max_position_notional:
        return False, f"Notional ${notional:.2f} exceeds max ${self._settings.max_position_notional:.2f}"

    # Check position count limits (risk-free positions don't count against slots)
    open_positions = db.query(Position).filter(
      Position.is_open == True,
      Position.risk_removed == False,
    ).all()

    long_count = sum(1 for p in open_positions if p.side == "Buy")
    short_count = sum(1 for p in open_positions if p.side == "Sell")
    total_count = len(open_positions)

    if side == "Buy" and long_count >= self._settings.max_positions_long:
      return False, f"Max long positions reached ({long_count}/{self._settings.max_positions_long})"

    if side == "Sell" and short_count >= self._settings.max_positions_short:
      return False, f"Max short positions reached ({short_count}/{self._settings.max_positions_short})"

    if total_count >= self._settings.max_positions_total:
      return False, f"Max total positions reached ({total_count}/{self._settings.max_positions_total})"

    # Check for duplicate position (same symbol + side)
    for p in open_positions:
      if p.symbol == symbol and p.side == side:
        if portfolio_id and p.portfolio_id == portfolio_id:
          return False, f"Already have {side} position in {symbol} for this portfolio"

    return True, "ok"

  # ── Trade Recording ───────────────────────────────────────────────

  def record_trade_result(
    self,
    db: Session,
    pnl: float,
    commission: float,
    portfolio_id: int | None = None,
  ) -> None:
    """Record a completed trade result for daily tracking.

    pnl: Net realized PnL — already includes commission & funding deductions
         (from trade.realized_pnl).  Do NOT subtract commission again.
    commission: Total commission for this trade (tracked separately for reporting).
    """
    self._ensure_daily_reset()

    # pnl is already net of commission+funding (Bug 36 fix — was double-counting)
    self._daily_realized_pnl += pnl
    self._daily_trade_count += 1
    if pnl >= 0:
      self._daily_wins += 1
    else:
      self._daily_losses += 1

    # Update DailyPnL in DB
    today = datetime.now(NYT).date()
    daily = db.query(DailyPnL).filter(
      DailyPnL.date == today,
      DailyPnL.portfolio_id == portfolio_id,
    ).first()

    if daily:
      daily.realized_pnl = (daily.realized_pnl or 0) + pnl
      daily.total_commission = (daily.total_commission or 0) + commission
      daily.trade_count = (daily.trade_count or 0) + 1
      if pnl >= 0:
        daily.win_count = (daily.win_count or 0) + 1
      else:
        daily.loss_count = (daily.loss_count or 0) + 1
    else:
      daily = DailyPnL(
        date=today,
        portfolio_id=portfolio_id,
        realized_pnl=pnl,
        total_commission=commission,
        trade_count=1,
        win_count=1 if pnl >= 0 else 0,
        loss_count=0 if pnl >= 0 else 1,
      )
      db.add(daily)

    db.commit()

    # Check circuit breaker after recording (both USD and percentage)
    if self._daily_realized_pnl < 0:
      daily_loss = abs(self._daily_realized_pnl)
      if self._settings.max_daily_loss_usd > 0 and daily_loss >= self._settings.max_daily_loss_usd:
        self.activate_circuit_breaker("daily_loss_limit_reached")
      elif self._settings.daily_loss_limit_pct > 0 and self._get_equity:
        total_equity = self._get_equity()
        if total_equity > 0:
          pct_limit_usd = total_equity * (self._settings.daily_loss_limit_pct / 100)
          if daily_loss >= pct_limit_usd:
            self.activate_circuit_breaker("daily_loss_pct_limit_reached")

    logger.info(
      "Trade recorded: PnL=$%.2f, daily total=$%.2f (%d trades)",
      pnl, self._daily_realized_pnl, self._daily_trade_count,
    )

  # ── Circuit Breaker ───────────────────────────────────────────────

  def activate_circuit_breaker(self, reason: str) -> None:
    """Activate the circuit breaker — blocks all new entries."""
    if self._circuit_breaker_active:
      return

    self._circuit_breaker_active = True
    self._circuit_breaker_reason = reason
    logger.warning("CIRCUIT BREAKER ACTIVATED: %s", reason)

  def deactivate_circuit_breaker(self) -> None:
    """Manually deactivate the circuit breaker."""
    self._circuit_breaker_active = False
    self._circuit_breaker_reason = ""
    logger.info("Circuit breaker deactivated")

  def reset_counters(self) -> None:
    """Reset all daily counters and deactivate circuit breaker.

    Used for testing — allows multiple test runs in one session
    without restarting the backend.
    """
    self._circuit_breaker_active = False
    self._circuit_breaker_reason = ""
    self._daily_realized_pnl = 0.0
    self._daily_trade_count = 0
    self._daily_wins = 0
    self._daily_losses = 0
    self._last_reset_date = None
    logger.info(
      "Safety monitor counters RESET: pnl=0, trades=0, circuit_breaker=OFF"
    )

  def is_circuit_breaker_active(self) -> bool:
    """Check if circuit breaker is currently active."""
    return self._circuit_breaker_active

  # ── Health Check ──────────────────────────────────────────────────

  def health_check(self, ws_status: dict, db: Session) -> dict:
    """Run a health check on the trading system."""
    open_positions = db.query(Position).filter(Position.is_open == True).count()
    open_orders = db.query(Order).filter(
      Order.status.in_(["open", "partial", "submitting"])
    ).count()

    return {
      "circuit_breaker": self._circuit_breaker_active,
      "circuit_breaker_reason": self._circuit_breaker_reason,
      "daily_pnl": self._daily_realized_pnl,
      "daily_loss_limit_usd": self._settings.max_daily_loss_usd,
      "daily_loss_limit_pct": self._settings.daily_loss_limit_pct,
      "daily_loss_used_pct": (
        abs(self._daily_realized_pnl) / self._settings.max_daily_loss_usd * 100
        if self._settings.max_daily_loss_usd > 0 else 0
      ),
      "daily_trade_count": self._daily_trade_count,
      "daily_wins": self._daily_wins,
      "daily_losses": self._daily_losses,
      "open_positions": open_positions,
      "max_positions": self._settings.max_positions_total,
      "open_orders": open_orders,
      "ws_public": ws_status.get("public", False),
      "ws_private": ws_status.get("private", False),
      "max_position_notional": self._settings.max_position_notional,
    }

  # ── Daily Reset ───────────────────────────────────────────────────

  def _ensure_daily_reset(self) -> None:
    """Reset daily counters at midnight New York time."""
    today = datetime.now(NYT).date()
    if self._last_reset_date != today:
      self._daily_realized_pnl = 0.0
      self._daily_trade_count = 0
      self._daily_wins = 0
      self._daily_losses = 0
      self._last_reset_date = today

      # Auto-deactivate circuit breaker on new day
      if self._circuit_breaker_active and "daily_loss" in self._circuit_breaker_reason:
        self.deactivate_circuit_breaker()
        logger.info("Circuit breaker auto-reset on new trading day")

  # ── Status ────────────────────────────────────────────────────────

  def get_status(self) -> dict:
    """Get safety monitor status for frontend."""
    self._ensure_daily_reset()
    return {
      "circuit_breaker_active": self._circuit_breaker_active,
      "circuit_breaker_reason": self._circuit_breaker_reason,
      "daily_realized_pnl": self._daily_realized_pnl,
      "daily_trade_count": self._daily_trade_count,
      "daily_wins": self._daily_wins,
      "daily_losses": self._daily_losses,
      "daily_loss_limit_usd": self._settings.max_daily_loss_usd,
      "daily_loss_limit_pct": self._settings.daily_loss_limit_pct,
      "max_positions_long": self._settings.max_positions_long,
      "max_positions_short": self._settings.max_positions_short,
      "max_positions_total": self._settings.max_positions_total,
      "max_position_notional": self._settings.max_position_notional,
    }
