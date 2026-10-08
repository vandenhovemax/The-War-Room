"""Telegram command handlers — all /commands the user can send."""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from telegram_bot.api_client import WarRoomAPI
from telegram_bot.formatters import (
  format_pnl, format_portfolio_detail, format_portfolios, format_positions,
  format_status, format_trades, money, num, pct, side_arrow,
)

api = WarRoomAPI()


# ── Monitoring Commands ────────────────────────────────────────────

async def cmd_ping(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
  await update.message.reply_text("Pong! Bot is alive.")


async def cmd_health(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
  health = await api.get("/health")
  exchange = await api.get("/exchange/status")
  if "error" in health:
    await update.message.reply_text(f"Backend: UNREACHABLE\n{health['error']}")
    return
  ex_ok = exchange.get("connected", False)
  ex_str = "Connected" if ex_ok else f"DOWN ({exchange.get('error', '?')})"
  await update.message.reply_text(f"Backend: OK\nExchange: {ex_str}")


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
  status = await api.get("/live/status")
  summary = await api.get("/live/account-summary")
  if "error" in status:
    await update.message.reply_text(f"Error: {status['error']}")
    return
  await update.message.reply_text(format_status(status, summary))


async def cmd_positions(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
  positions = await api.get("/live/positions")
  tsl = await api.get("/live/tsl-status")
  if "error" in positions:
    await update.message.reply_text(f"Error: {positions['error']}")
    return
  await update.message.reply_text(format_positions(positions, tsl))


async def cmd_orders(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
  orders = await api.get("/live/orders")
  if "error" in orders:
    await update.message.reply_text(f"Error: {orders['error']}")
    return
  if not orders:
    await update.message.reply_text("No active orders.")
    return
  lines = [f"{len(orders)} Orders\n"]
  for o in orders[:20]:
    lines.append(f"  {o.get('symbol')} {o.get('side')} {o.get('role')} — {o.get('status')}")
  await update.message.reply_text("\n".join(lines))


async def cmd_pnl(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
  data = await api.get("/live/pnl-summary")
  if "error" in data:
    await update.message.reply_text(f"Error: {data['error']}")
    return
  await update.message.reply_text(format_pnl(data))


async def cmd_trades(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
  args = ctx.args
  limit = min(int(args[0]), 50) if args and args[0].isdigit() else 10
  trades = await api.get("/live/trades", {"limit": limit})
  if "error" in trades:
    await update.message.reply_text(f"Error: {trades['error']}")
    return
  await update.message.reply_text(format_trades(trades))


async def cmd_signals(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
  signals = await api.get("/live/signals", {"limit": 20})
  if "error" in signals:
    await update.message.reply_text(f"Error: {signals['error']}")
    return
  if not signals:
    await update.message.reply_text("No signals.")
    return
  lines = ["Recent Signals\n"]
  for s in signals[:15]:
    status = s.get("status", "?")
    lines.append(f"  {s.get('symbol')} {s.get('action')} {s.get('direction', '')} [{status}]")
  await update.message.reply_text("\n".join(lines))


async def cmd_events(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
  args = ctx.args
  limit = min(int(args[0]), 100) if args and args[0].isdigit() else 20
  events = await api.get("/live/events", {"limit": limit})
  if "error" in events:
    await update.message.reply_text(f"Error: {events['error']}")
    return
  if not events:
    await update.message.reply_text("No events.")
    return
  lines = [f"Last {len(events)} Events\n"]
  for e in events[:20]:
    lines.append(f"  [{e.get('event_type')}] {e.get('symbol', '')} — {e.get('created_at', '')[:16]}")
  await update.message.reply_text("\n".join(lines))


async def cmd_balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
  data = await api.get("/exchange/balance")
  if "error" in data and data["error"]:
    await update.message.reply_text(f"Error: {data['error']}")
    return
  await update.message.reply_text(
    f"Exchange Balance\n"
    f"  Equity:    {money(data.get('total_equity'), sign=False)}\n"
    f"  Available: {money(data.get('available'), sign=False)}"
  )


async def cmd_btc(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
  data = await api.get("/dashboard/btc-change")
  if "error" in data:
    await update.message.reply_text(f"Error: {data['error']}")
    return
  price = data.get("price", 0)
  change = data.get("change_pct", 0)
  await update.message.reply_text(f"BTC: ${num(price)} ({'+' if change > 0 else ''}{change:.1f}%)")


async def cmd_kpis(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
  data = await api.get("/dashboard/account-kpis")
  if "error" in data:
    await update.message.reply_text(f"Error: {data['error']}")
    return
  lines = ["KPIs\n"]
  for key, val in data.items():
    if isinstance(val, float):
      lines.append(f"  {key}: {val:.2f}")
    else:
      lines.append(f"  {key}: {val}")
  await update.message.reply_text("\n".join(lines))


# ── Portfolio Commands ─────────────────────────────────────────────

async def cmd_portfolios(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
  data = await api.get("/portfolios")
  if "error" in data:
    await update.message.reply_text(f"Error: {data['error']}")
    return
  await update.message.reply_text(format_portfolios(data))


async def cmd_portfolio_detail(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
  if not ctx.args:
    await update.message.reply_text("Usage: /portfolio <ID>")
    return
  pid = ctx.args[0]
  info = await api.get(f"/portfolios/{pid}")
  if "error" in info:
    await update.message.reply_text(f"Error: {info['error']}")
    return
  # Fetch live trading KPIs for this portfolio
  kpis = await api.get("/dashboard/account-kpis", {"portfolio_id": pid})
  if "error" in kpis:
    kpis = {}
  await update.message.reply_text(format_portfolio_detail(info, kpis))
