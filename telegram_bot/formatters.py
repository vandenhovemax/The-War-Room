"""Message formatting helpers for Telegram bot responses."""
from __future__ import annotations


def money(value: float | None, sign: bool = True) -> str:
  """Format a dollar amount: +$1,234.56 or -$42.10."""
  if value is None:
    return "$0.00"
  prefix = "+" if sign and value > 0 else ""
  return f"{prefix}${value:,.2f}"


def pct(value: float | None) -> str:
  """Format a percentage: 62.5%."""
  if value is None:
    return "0.0%"
  return f"{value:.1f}%"


def num(value: float | int | None, decimals: int = 2) -> str:
  """Format a number with thousands separators."""
  if value is None:
    return "0"
  if isinstance(value, int) or decimals == 0:
    return f"{int(value):,}"
  return f"{value:,.{decimals}f}"


def status_emoji(running: bool) -> str:
  return "RUNNING" if running else "STOPPED"


def side_arrow(side: str) -> str:
  return "Long" if side == "Buy" else "Short"


def format_status(status: dict, summary: dict) -> str:
  """Format /status response."""
  engine = status
  s = summary
  running = engine.get("running", False)
  mode = "PAPER" if engine.get("paper_trading") else "LIVE"
  state = status_emoji(running)

  lines = [
    f"THE WAR ROOM\n",
    f"Engine: {state} ({mode})",
  ]
  if engine.get("testnet"):
    lines.append("Network: TESTNET")

  lines.append(f"\nAccount")
  lines.append(f"  Equity:    {money(s.get('equity'), sign=False)}")
  lines.append(f"  Margin:    {money(s.get('margin_used'), sign=False)}")
  lines.append(f"  Available: {money(s.get('available_balance'), sign=False)}")

  pos_count = s.get("position_count", 0)
  long_c = s.get("long_count", 0)
  short_c = s.get("short_count", 0)
  lines.append(f"\nPositions: {pos_count} open ({long_c}L / {short_c}S)")
  lines.append(f"  Unrealized P&L: {money(s.get('unrealized_pnl'))}")

  lines.append(f"\nToday: {money(s.get('daily_pnl'))}  ({s.get('daily_trades', 0)} trades)")

  cb = "TRIPPED" if s.get("circuit_breaker") else "OK"
  lines.append(f"\nCircuit Breaker: {cb}")

  return "\n".join(lines)


def format_positions(positions: list, tsl_status: list) -> str:
  """Format /positions response."""
  if not positions:
    return "No open positions."

  # Build TSL lookup
  tsl_map = {}
  for t in (tsl_status or []):
    tsl_map[t.get("position_db_id")] = t

  lines = [f"{len(positions)} Open Positions\n"]
  for i, p in enumerate(positions, 1):
    side = side_arrow(p.get("side", ""))
    sym = p.get("symbol", "?")
    entry = num(p.get("entry_price"), 2)
    pnl = money(p.get("unrealized_pnl"))
    sl = num(p.get("sl_price"), 2) if p.get("sl_price") else "-"
    tp = num(p.get("tp_price"), 2) if p.get("tp_price") else "-"
    pid = p.get("id", "?")

    lines.append(f"{i}. {sym} {side}")
    lines.append(f"   Entry: {entry} | P&L: {pnl}")
    lines.append(f"   SL: {sl} | TP: {tp}")

    tsl = tsl_map.get(pid)
    if tsl:
      lines.append(f"   TSL: active (trailing)")

    lines.append(f"   ID: {pid}")
    lines.append("")

  lines.append("Close with: /close <ID>")
  return "\n".join(lines)


def format_pnl(data: dict) -> str:
  """Format /pnl response."""
  lines = [
    "P&L Summary\n",
    f"  Today:    {money(data.get('today'))}",
    f"  Week:     {money(data.get('week'))}",
    f"  Month:    {money(data.get('month'))}",
    f"  All-time: {money(data.get('all_time'))}",
    f"\n  Trades: {data.get('total_trades', 0)} total",
    f"  Win rate: {pct(data.get('win_rate'))} ({data.get('winning_trades', 0)}/{data.get('total_trades', 0)})",
    f"  Avg win:  {money(data.get('avg_win'))}",
    f"  Avg loss: {money(data.get('avg_loss'))}",
  ]
  return "\n".join(lines)


def format_trades(trades: list) -> str:
  """Format /trades response."""
  if not trades:
    return "No closed trades yet."

  lines = [f"Last {len(trades)} Trades\n"]
  for t in trades:
    sym = t.get("symbol", "?")
    side = side_arrow(t.get("side", ""))
    pnl = money(t.get("realized_pnl"))
    reason = t.get("close_reason", "?")
    lines.append(f"  {sym} {side} {pnl} ({reason})")
  return "\n".join(lines)


def format_portfolios(portfolios: list) -> str:
  """Format /portfolios response — only live/paper trading portfolios."""
  # Filter to only live or paper trading portfolios
  trading = [
    p for p in portfolios
    if p.get("portfolio_type") in ("real", "paper")
  ]

  if not trading:
    return "No live or paper trading portfolios."

  lines = ["Trading Portfolios\n"]
  for p in trading:
    ptype = p.get("portfolio_type", "?").upper()
    status = "ACTIVE" if p.get("is_active") else "STOPPED"
    name = p.get("name", "?")
    asset_count = p.get("asset_count", "?")
    lines.append(f"  {p['id']}. {name}")
    lines.append(f"     {ptype} | {status} | {asset_count} assets")
    lines.append("")

  lines.append("Details: /portfolio <ID>")
  return "\n".join(lines)


def format_portfolio_detail(info: dict, kpis: dict) -> str:
  """Format /portfolio <ID> response with status and live KPIs."""
  name = info.get("name", "?")
  pid = info.get("id", "?")
  ptype = info.get("portfolio_type", "?").upper()
  active = "ACTIVE" if info.get("is_active") else "STOPPED"
  assets = info.get("assets", [])
  asset_count = len(assets) if isinstance(assets, list) else info.get("asset_count", "?")

  lines = [
    f"Portfolio: {name}\n",
    f"  ID:     {pid}",
    f"  Type:   {ptype}",
    f"  Status: {active}",
    f"  Assets: {asset_count}",
  ]

  # Activated/deactivated timestamps
  if info.get("activated_at"):
    lines.append(f"  Active since: {info['activated_at'][:16]}")
  if info.get("deactivated_at") and not info.get("is_active"):
    lines.append(f"  Stopped at:   {info['deactivated_at'][:16]}")

  # Live trading KPIs
  total = kpis.get("total_trades", 0)
  if total > 0:
    lines.append(f"\nLive Trading KPIs")
    lines.append(f"  Total P&L:      {money(kpis.get('total_pnl'))}")
    lines.append(f"  Trades:         {total}")
    lines.append(f"  Win rate:       {pct(kpis.get('win_rate'))}")
    lines.append(f"  Avg trade:      {money(kpis.get('avg_trade'))}")
    pf = kpis.get("profit_factor")
    lines.append(f"  Profit factor:  {num(pf) if pf else 'N/A'}")
    lines.append(f"  Best trade:     {money(kpis.get('best_trade'))}")
    lines.append(f"  Worst trade:    {money(kpis.get('worst_trade'))}")
    lines.append(f"  Max drawdown:   {money(kpis.get('max_drawdown'))}")
    lines.append(f"  Open positions: {kpis.get('open_positions', 0)}")
  else:
    lines.append(f"\nNo live trades yet.")

  return "\n".join(lines)


def format_event_notification(event: dict) -> str | None:
  """Format an event into a push notification. Returns None to skip."""
  etype = event.get("event_type", "")
  symbol = event.get("symbol", "")
  details = event.get("details", "")

  # Parse details if it's a string
  if isinstance(details, str):
    try:
      import json
      details = json.loads(details)
    except (json.JSONDecodeError, TypeError):
      details = {}

  # --- Position opened ---
  if etype == "position_opened":
    side = details.get("side", "?")
    entry = details.get("entry_price", 0)
    qty = details.get("qty", "?")
    return f"OPENED {symbol} {side}\n  Entry: {num(entry)} | Qty: {qty}"

  if etype == "position_opened_partial":
    filled = details.get("filled_qty", "?")
    intended = details.get("intended_qty", "?")
    entry = details.get("entry_price", 0)
    return f"PARTIAL FILL {symbol}\n  Filled: {filled}/{intended} @ {num(entry)}"

  # --- Position closed ---
  if etype == "position_closed":
    pnl = details.get("pnl", 0)
    reason = details.get("close_reason", "?")
    exit_p = details.get("exit_price", 0)
    return f"CLOSED {symbol} — {money(pnl)}\n  Exit: {num(exit_p)} | Reason: {reason}"

  # --- Order failures ---
  if etype == "order_failed":
    error = details.get("error", "unknown")
    return f"ORDER FAILED {symbol}\n  {error}"

  if etype == "order_rejected":
    reason = details.get("reason", "unknown")
    return f"ORDER REJECTED {symbol}\n  {reason}"

  # --- SL placement failure ---
  if etype == "sl_placement_failed":
    sl_price = details.get("sl_price", "?")
    return f"SL PLACEMENT FAILED {symbol}\n  Attempted SL: {sl_price}"

  # --- Original engine-level events ---
  messages = {
    "engine_started": f"Engine started ({details.get('paper', 'unknown')} mode)",
    "engine_stopped": "Engine stopped",
    "emergency_stop": "EMERGENCY STOP executed",
    "portfolio_deactivated": f"Portfolio deactivated",
    "manual_close": f"Manual close: {symbol}",
    "emergency_close_all": f"All positions closing ({details.get('positions_closed', '?')})",
    "entry_blocked": f"Entry blocked: {symbol} — {details.get('reason', '?')}",
  }

  return messages.get(etype)
