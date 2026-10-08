"""Position Manager — risk-based sizing, leverage adaptation, reconciliation.

Handles: position size calculation based on risk amount and SL distance,
leverage adaptation to match actual equity, instrument precision rounding,
symbol preparation (isolated margin + leverage), and position reconciliation
between local DB and exchange state.
"""
from __future__ import annotations

import math
import time

from sqlalchemy.orm import Session

from backend.config.settings import get_settings
from backend.db.models import LiveEvent, Order, Position
from backend.exchange.bybit_trading import BybitTradingClient
from backend.utils.logger import get_logger

logger = get_logger(__name__)


class PositionManager:
  """Manages position sizing, leverage, and reconciliation.

  Core algorithm:
    1. risk_amount = configured risk (fixed $ or % of equity)
    2. sl_distance = |entry_price - sl_price|
    3. qty = risk_amount / sl_distance
    4. notional = qty * entry_price
    5. Cap margin: max_position_pct % of equity = max margin per position
    6. leverage = ceil(notional / margin_budget)
    7. Cap leverage at exchange max for the symbol, reduce qty if needed
  """

  def __init__(self, trading_client: BybitTradingClient):
    self._client = trading_client
    self._settings = get_settings()
    # Symbols already prepared (margin mode + leverage set)
    self._prepared_symbols: set[str] = set()
    # Wallet cache — refreshed every 5s by engine coordinator, avoids
    # hitting Bybit REST API on every frontend poll
    self._wallet_cache: dict = {"total_equity": 0, "available": 0}
    self._wallet_cache_time: float = 0.0

  # ── Core Sizing ───────────────────────────────────────────────────

  def calculate_position_size(
    self,
    symbol: str,
    side: str,
    entry_price: float,
    sl_price: float,
    risk_amount: float,
    current_open_count: int = 0,
    max_margin_override: float | None = None,
    max_position_pct_override: float | None = None,
  ) -> dict:
    """Calculate position size with auto-leverage.

    The flow:
      1. qty = risk_amount / sl_distance  (position size from risk)
      2. notional = qty * entry_price     (dollar value of position)
      3. Get wallet_balance (realized only, stable across open positions)
      4. total_slots = max_positions_total (fixed)
      5. margin_budget = wallet_balance / total_slots * breathing
      6. Safety: cap margin_budget to available_balance if insufficient
      7. Cap margin_budget by max_position_pct % of equity (guardrail)
      8. leverage = ceil(notional / margin_budget)  (auto-calculated)
      9. Cap leverage at exchange max, reduce qty if needed

    Args:
      symbol: e.g. "BTCUSDT"
      side: "Buy" (long) or "Sell" (short)
      entry_price: Expected entry price
      sl_price: Stop loss price
      risk_amount: Dollar amount at risk for this trade
      current_open_count: (Legacy, unused) Number of currently open positions

    Returns:
      {
        "qty": float,              # Rounded to lot_size
        "notional": float,         # qty * entry_price
        "leverage": int,           # Auto-calculated leverage
        "risk_amount": float,      # Actual risk after rounding
        "sl_distance": float,      # Price distance to SL
        "capped": bool,            # True if qty was reduced
        "max_leverage": float,     # Exchange max for this symbol
        "margin_budget": float,    # Fixed margin per slot
        "total_slots": int,        # max_positions_total (fixed)
        "wallet_balance": float,   # Realized balance (no unrealized PnL)
        "available_balance": float,# Actual free margin on Bybit
      }
    """
    info = self._client.get_instrument_info(symbol)
    lot_size = info["lot_size"]
    max_leverage = info["max_leverage"]
    min_qty = info["min_qty"]

    # SL distance
    sl_distance = abs(entry_price - sl_price)
    if sl_distance == 0:
      raise ValueError(f"SL price equals entry price for {symbol}")

    # Raw qty from risk
    raw_qty = risk_amount / sl_distance

    # Round to lot size
    qty = self._floor_to_step(raw_qty, lot_size)

    if qty < min_qty:
      logger.warning(
        "Calculated qty %.6f < min_qty %.6f for %s — using min_qty",
        qty, min_qty, symbol,
      )
      qty = min_qty

    # Get wallet balance (realized only) for stable calculations
    wallet_balance = self.get_wallet_balance()

    # Cap equity = reserve (off-exchange) + wallet balance (realized only)
    reserve = self._settings.reserve_account_usd
    cap_equity = reserve + wallet_balance if reserve > 0 else wallet_balance
    # CS-024: total_equity_override is a deprecated legacy fallback — the
    # reserve_account_usd + wallet_balance path replaces it. Keep honoring
    # a non-zero value for safety (don't surprise an existing live account)
    # but log a DEPRECATION warning so the user is nudged to migrate. Once
    # confirmed no account reads this non-zero, a future cleanup can remove
    # the setting entirely.
    if cap_equity <= 0 and self._settings.total_equity_override > 0:
      logger.warning(
        "CS-024 DEPRECATED: total_equity_override=$%.2f is in use as a "
        "cap-equity fallback for %s. This setting will be removed — migrate "
        "to reserve_account_usd + live wallet balance.",
        self._settings.total_equity_override, symbol,
      )
      cap_equity = self._settings.total_equity_override
    if cap_equity <= 0:
      cap_equity = wallet_balance

    notional = qty * entry_price
    desired_qty = qty          # snapshot BEFORE any caps reduce it
    capped = False

    # Auto-leverage: fixed allocation per slot from stable wallet balance
    # wallet_balance / total_slots gives each position an equal, fixed budget
    # that doesn't shrink as positions open (unlike available_balance)
    total_slots = max(1, self._settings.max_positions_total)
    breathing = 1.0 - (self._settings.margin_breathing_room_pct / 100)
    margin_budget = (wallet_balance / total_slots) * breathing

    # Safety check: verify Bybit has enough free margin to cover this budget
    available_balance = self.get_available_balance()
    if available_balance <= 0:
      raise ValueError("Available balance is zero or negative — cannot open position")
    if margin_budget > available_balance:
      logger.warning(
        "Fixed margin budget $%.2f exceeds available balance $%.2f — capping to available",
        margin_budget, available_balance,
      )
      margin_budget = available_balance

    if margin_budget <= 0:
      raise ValueError("No margin budget available for new positions")

    # Margin cap: max_position_pct caps the MARGIN (equity tied up), not the notional.
    # This prevents any single position from using more than X% of your total equity.
    # Portfolio-level override takes precedence over the global .env default when provided.
    effective_max_pct = (
      max_position_pct_override
      if max_position_pct_override is not None
      else self._settings.max_position_pct
    )
    max_margin = None
    if effective_max_pct > 0 and cap_equity > 0:
      max_margin = cap_equity * (effective_max_pct / 100)
      if margin_budget > max_margin:
        src = "portfolio override" if max_position_pct_override is not None else "max_position_pct"
        logger.info(
          "Margin budget capped by %s (%.1f%%): $%.2f -> $%.2f for %s",
          src, effective_max_pct, margin_budget, max_margin, symbol,
        )
        margin_budget = max_margin

    # Per-group margin override: tighter cap from portfolio per-batch config
    if max_margin_override is not None and max_margin_override > 0:
      if margin_budget > max_margin_override:
        logger.info(
          "Margin budget capped by per-group override: $%.2f -> $%.2f for %s",
          margin_budget, max_margin_override, symbol,
        )
        margin_budget = max_margin_override

    # Hard notional cap: legacy fallback (independent of margin/leverage)
    max_notional_hard = self._settings.max_position_notional
    if max_notional_hard > 0 and notional > max_notional_hard:
      qty = self._floor_to_step(max_notional_hard / entry_price, lot_size)
      notional = qty * entry_price
      capped = True
      logger.info(
        "Position capped by max_notional ($%.0f): %s qty=%.6f notional=$%.2f",
        max_notional_hard, symbol, qty, notional,
      )

    required_leverage = math.ceil(notional / margin_budget)
    # Floor: at least default_leverage (user-configurable minimum)
    lev_floor = max(1, self._settings.default_leverage)
    required_leverage = max(lev_floor, required_leverage)

    if required_leverage > max_leverage:
      # Reduce qty to fit within max leverage + margin budget
      max_notional_for_lev = margin_budget * max_leverage
      qty = self._floor_to_step(max_notional_for_lev / entry_price, lot_size)
      notional = qty * entry_price
      required_leverage = int(max_leverage)
      capped = True
      # CS-032: include the $ delta so the caller/UI can surface the shortfall.
      _capped_actual = qty * sl_distance
      logger.warning(
        "Leverage capped at %dx for %s — requested risk $%.2f, actual $%.2f "
        "(qty=%.6f, margin_budget=$%.2f)",
        required_leverage, symbol, risk_amount, _capped_actual, qty, margin_budget,
      )

    # Actual risk after rounding
    actual_risk = qty * sl_distance

    # ── Max Risk Per Trade cap (safety net) ──────────────────────────
    # Hard ceiling on dollar risk. Catches bad SL calculations, bugs,
    # or edge cases where risk_amount is correct but qty rounding
    # or other adjustments pushed actual_risk beyond intended limits.
    max_risk_cap = self._settings.max_risk_per_trade_usd
    if max_risk_cap <= 0 and self._settings.max_risk_per_trade_pct > 0 and cap_equity > 0:
      max_risk_cap = cap_equity * (self._settings.max_risk_per_trade_pct / 100)
    if max_risk_cap > 0 and actual_risk > max_risk_cap:
      old_qty = qty
      qty = self._floor_to_step(max_risk_cap / sl_distance, lot_size)
      if qty < min_qty:
        raise ValueError(
          f"Max risk cap ${max_risk_cap:.2f} requires qty below min_qty "
          f"({qty:.6f} < {min_qty:.6f}) for {symbol} — trade blocked"
        )
      notional = qty * entry_price
      actual_risk = qty * sl_distance
      capped = True
      logger.warning(
        "RISK CAP: %s actual_risk $%.2f exceeded max $%.2f — "
        "qty reduced %.6f -> %.6f (new risk=$%.2f)",
        symbol, old_qty * sl_distance, max_risk_cap, old_qty, qty, actual_risk,
      )

    # CS-032: expose the originally-requested risk alongside the capped
    # effective risk so the caller (and eventual UI) can show the shortfall
    # when any of the caps above reduced qty (leverage cap, notional cap,
    # risk-per-trade cap). effective_risk == actual_risk; the alias makes
    # callers treating `risk_amount` as "requested" unambiguous — they can
    # compare `requested_risk` vs `effective_risk` to detect shortfall.
    risk_shortfall_usd = max(0.0, risk_amount - actual_risk)
    result = {
      "qty": qty,
      "desired_qty": desired_qty,
      "notional": notional,
      "leverage": required_leverage,
      "risk_amount": actual_risk,
      "requested_risk": risk_amount,
      "effective_risk": actual_risk,
      "risk_shortfall_usd": risk_shortfall_usd,
      "sl_distance": sl_distance,
      "capped": capped,
      "max_leverage": max_leverage,
      "margin_budget": margin_budget,
      "total_slots": total_slots,
      "wallet_balance": wallet_balance,
      "available_balance": available_balance,
    }

    logger.info(
      "Position size: %s %s qty=%.6f notional=$%.2f lev=%dx risk=$%.2f "
      "sl_dist=%.4f wallet=$%.2f avail=$%.2f slots=%d margin_budget=$%.2f",
      symbol, side, qty, notional, required_leverage, actual_risk,
      sl_distance, wallet_balance, available_balance, total_slots, margin_budget,
    )
    return result

  # ── Symbol Preparation ────────────────────────────────────────────

  def prepare_symbol(self, symbol: str, leverage: int) -> None:
    """Set isolated margin mode and leverage for a symbol.

    Called before the first trade on a symbol. Idempotent — skips
    if already prepared this session.
    """
    if symbol in self._prepared_symbols:
      return

    lev_str = str(leverage)

    # Switch to isolated margin (skip for Unified Trading Accounts — error 100028)
    try:
      self._client.switch_to_isolated(symbol, lev_str, lev_str)
      logger.info("Set isolated margin for %s", symbol)
    except Exception as e:
      err_str = str(e)
      if "100028" in err_str:
        logger.info("UTA account — isolated margin switch not needed for %s", symbol)
      else:
        logger.warning("switch_to_isolated for %s failed: %s (may already be set)", symbol, err_str[:100])

    # Set leverage (110043 = already set — handled inside set_leverage)
    try:
      self._client.set_leverage(symbol, lev_str, lev_str)
      logger.info("Set leverage %sx for %s", lev_str, symbol)
    except Exception as e:
      err_str = str(e)
      if "110043" in err_str:
        logger.info("Leverage already %sx for %s", lev_str, symbol)
      else:
        logger.warning("set_leverage for %s failed: %s", symbol, err_str[:100])

    self._prepared_symbols.add(symbol)

  def update_leverage(self, symbol: str, leverage: int) -> None:
    """Update leverage for a symbol (e.g. when equity changes significantly)."""
    lev_str = str(leverage)
    try:
      self._client.set_leverage(symbol, lev_str, lev_str)
      logger.info("Updated leverage to %sx for %s", lev_str, symbol)
    except Exception as e:
      logger.warning("update_leverage for %s: %s", symbol, e)

  # ── Rounding ──────────────────────────────────────────────────────

  def round_qty(self, symbol: str, qty: float) -> str:
    """Round quantity to exchange lot size and return as string."""
    info = self._client.get_instrument_info(symbol)
    lot_size = info["lot_size"]
    rounded = self._floor_to_step(qty, lot_size)
    if lot_size >= 1:
      return str(int(rounded))
    decimals = len(str(lot_size).rstrip("0").split(".")[-1])
    return f"{rounded:.{decimals}f}"

  def round_price(self, symbol: str, price: float) -> str:
    """Round price to exchange tick size and return as string."""
    info = self._client.get_instrument_info(symbol)
    tick_size = info["tick_size"]
    rounded = self._floor_to_step(price, tick_size)
    if tick_size >= 1:
      return str(int(rounded))
    decimals = len(str(tick_size).rstrip("0").split(".")[-1])
    return f"{rounded:.{decimals}f}"

  # ── Reconciliation ────────────────────────────────────────────────

  def reconcile_positions(self, db: Session, portfolio_id: int | None = None) -> dict:
    """Compare local DB positions vs exchange positions.

    Returns:
      {
        "matched": [{"symbol": ..., "db_qty": ..., "exchange_qty": ...}],
        "db_only": [{"symbol": ..., "db_id": ..., "qty": ...}],
        "exchange_only": [{"symbol": ..., "side": ..., "size": ...}],
        "qty_mismatch": [{"symbol": ..., "db_qty": ..., "exchange_qty": ...}],
      }
    """
    # Get exchange positions
    exchange_positions = self._client.get_positions()
    exchange_by_symbol_side = {}
    for ep in exchange_positions:
      key = (ep["symbol"], ep["side"])
      exchange_by_symbol_side[key] = ep

    # Get local open positions
    query = db.query(Position).filter(Position.is_open == True)
    if portfolio_id is not None:
      query = query.filter(Position.portfolio_id == portfolio_id)
    local_positions = query.all()

    local_by_symbol_side = {}
    for lp in local_positions:
      key = (lp.symbol, lp.side)
      local_by_symbol_side[key] = lp

    matched = []
    db_only = []
    exchange_only = []
    qty_mismatch = []

    # Check local vs exchange
    for key, lp in local_by_symbol_side.items():
      ep = exchange_by_symbol_side.get(key)
      if ep:
        if abs(lp.quantity - ep["size"]) < 1e-8:
          matched.append({"symbol": key[0], "side": key[1], "qty": lp.quantity})
        else:
          qty_mismatch.append({
            "symbol": key[0], "side": key[1],
            "db_qty": lp.quantity, "exchange_qty": ep["size"],
            "db_id": lp.id,
          })
      else:
        db_only.append({
          "symbol": key[0], "side": key[1],
          "qty": lp.quantity, "db_id": lp.id,
        })

    # Check exchange positions not in local DB
    for key, ep in exchange_by_symbol_side.items():
      if key not in local_by_symbol_side:
        exchange_only.append({
          "symbol": key[0], "side": key[1], "size": ep["size"],
          "entry_price": ep["entry_price"],
        })

    result = {
      "matched": matched,
      "db_only": db_only,
      "exchange_only": exchange_only,
      "qty_mismatch": qty_mismatch,
    }

    if db_only or exchange_only or qty_mismatch:
      logger.warning("Position reconciliation discrepancies: %s", result)

    return result

  def reconcile_and_fix(self, db: Session, portfolio_id: int | None = None) -> dict:
    """Reconcile positions and fix DB from exchange truth.

    - DB-only positions: mark as closed (exchange doesn't have them)
    - Qty mismatches: update DB qty to match exchange
    - Exchange-only: log warning (won't auto-create — could be from another source)
    """
    recon = self.reconcile_positions(db, portfolio_id)
    fixes = {"closed_db_only": 0, "qty_fixed": 0, "exchange_only_warned": 0}

    # Close DB positions that don't exist on exchange
    for item in recon["db_only"]:
      pos = db.get(Position, item["db_id"])
      if pos:
        pos.is_open = False
        logger.warning(
          "Closing DB-only position (not on exchange): %s %s id=%d",
          item["symbol"], item["side"], item["db_id"],
        )
        # Cancel associated exit orders on exchange to prevent orphan conditionals
        exit_orders = db.query(Order).filter(
          Order.position_id_fk == pos.id,
          Order.role.in_(["sl", "tp", "tsl"]),
          Order.status == "open",
        ).all()
        for ex_order in exit_orders:
          try:
            self._client.cancel_order(
              symbol=ex_order.symbol,
              order_link_id=ex_order.order_link_id,
            )
            logger.info(
              "Cancelled orphan %s order on exchange: %s linkId=%s",
              ex_order.role, ex_order.symbol, ex_order.order_link_id,
            )
          except Exception as cancel_err:
            logger.warning("Failed to cancel orphan %s order: %s", ex_order.role, cancel_err)
          ex_order.status = "cancelled"
          ex_order.cancel_reason = "position_reconciled"
        fixes["closed_db_only"] += 1

    # Fix qty mismatches
    for item in recon["qty_mismatch"]:
      pos = db.get(Position, item["db_id"])
      if pos:
        pos.quantity = item["exchange_qty"]
        logger.warning(
          "Fixed qty mismatch: %s %s db=%.6f -> exchange=%.6f",
          item["symbol"], item["side"], item["db_qty"], item["exchange_qty"],
        )
        fixes["qty_fixed"] += 1

    # Warn about exchange-only positions
    for item in recon["exchange_only"]:
      logger.warning(
        "Exchange position not in DB: %s %s size=%.6f — not auto-creating",
        item["symbol"], item["side"], item["size"],
      )
      fixes["exchange_only_warned"] += 1

    db.commit()
    return fixes

  # ── Funding Fees ──────────────────────────────────────────────────

  def update_funding_fees(self, db: Session, portfolio_id: int | None = None) -> int:
    """Query recent executions for funding-type fills and update positions.

    Returns number of positions updated.
    """
    executions = self._client.get_executions(limit=100)
    funding_execs = [e for e in executions if e.get("exec_type") == "Funding"]

    if not funding_execs:
      return 0

    query = db.query(Position).filter(Position.is_open == True)
    if portfolio_id is not None:
      query = query.filter(Position.portfolio_id == portfolio_id)
    open_positions = {p.symbol: p for p in query.all()}

    updated = 0
    for fe in funding_execs:
      symbol = fe.get("symbol", "")
      fee = fe.get("fee", 0)
      if symbol in open_positions:
        pos = open_positions[symbol]
        pos.funding_accumulated = (pos.funding_accumulated or 0) + abs(fee)
        updated += 1

    if updated:
      db.commit()
      logger.info("Updated funding fees for %d positions", updated)

    return updated

  # ── Equity Query (cached) ─────────────────────────────────────────

  def refresh_wallet_cache(self) -> None:
    """Fetch wallet balance from Bybit and store in cache.

    Called every 5s by EngineCoordinator's background loop.
    Frontend endpoints read the cache instead of hitting Bybit directly.
    """
    try:
      wallet = self._client.get_wallet_balance()
      self._wallet_cache = wallet
      self._wallet_cache_time = time.monotonic()
    except Exception as e:
      logger.warning("Wallet cache refresh failed: %s", e)

  def get_exchange_equity(self) -> float:
    """Get current exchange equity (from cache, refreshed every 5s)."""
    # Fallback: if cache is stale (>10s), fetch live
    if time.monotonic() - self._wallet_cache_time > 10:
      self.refresh_wallet_cache()
    return self._wallet_cache.get("total_equity", 0)

  def get_available_balance(self) -> float:
    """Get available balance for new positions (from cache).

    Falls back to total_equity if available is zero (some Bybit account
    types return 0 for totalAvailableBalance).
    """
    # Fallback: if cache is stale (>10s), fetch live
    if time.monotonic() - self._wallet_cache_time > 10:
      self.refresh_wallet_cache()

    available = self._wallet_cache.get("available", 0)
    equity = self._wallet_cache.get("total_equity", 0)

    if available <= 0 and equity > 0:
      if not getattr(self, '_avail_zero_warned', False):
        logger.warning(
          "Bybit returned available=%.2f but equity=%.2f — using equity as fallback (suppressing future repeats)",
          available, equity,
        )
        self._avail_zero_warned = True
      return equity

    if available <= 0:
      logger.warning("Bybit wallet returned available=%.2f equity=%.2f", available, equity)

    return available

  def get_wallet_balance(self) -> float:
    """Get Bybit wallet balance — realized only (no unrealized PnL).

    This is Bybit's totalWalletBalance: only changes on realized events
    (trade closes, funding fees, deposits/withdrawals). Used for stable
    risk sizing that doesn't fluctuate with open position PnL.
    Falls back to total_equity if wallet_balance field is missing.
    """
    if time.monotonic() - self._wallet_cache_time > 10:
      self.refresh_wallet_cache()

    wb = self._wallet_cache.get("wallet_balance", 0)
    if wb > 0:
      return wb

    # Fallback for older cache entries that don't have wallet_balance
    return self._wallet_cache.get("total_equity", 0)

  # ── Internal ──────────────────────────────────────────────────────

  @staticmethod
  def _floor_to_step(value: float, step: float) -> float:
    """Floor a value to the nearest step (lot_size or tick_size).

    CS-037: float division drops a full lot on common cases —
    ``math.floor(0.3 / 0.1) == 2.0`` because ``0.3 / 0.1`` evaluates
    to ``2.9999...``. The result is one lot short of what risk
    actually authorised, so every live size is silently under-
    provisioned. Use ``Decimal`` with ``str(x)`` conversion so the
    shortest-repr round-trip avoids the artefact.
    """
    if step <= 0:
      return value
    from decimal import Decimal
    v = Decimal(str(value))
    s = Decimal(str(step))
    return float((v // s) * s)
