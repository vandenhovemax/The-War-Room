"""Engine control and position management commands (with confirmations)."""
from __future__ import annotations

import time

from telegram import Update
from telegram.ext import ContextTypes

from telegram_bot.api_client import WarRoomAPI
from telegram_bot.formatters import money, side_arrow

api = WarRoomAPI()

# Confirmation state: {chat_id: (action_name, confirm_word, callback, expires_at)}
_pending_confirmations: dict[int, tuple] = {}
CONFIRM_TIMEOUT = 60  # seconds — overridden by bot.py from settings


def set_confirmation(chat_id: int, action: str, word: str, callback) -> None:
  """Register a pending confirmation for a chat."""
  _pending_confirmations[chat_id] = (action, word, callback, time.time() + CONFIRM_TIMEOUT)


async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
  """Handle plain text messages — check for pending confirmations."""
  chat_id = update.effective_chat.id
  pending = _pending_confirmations.get(chat_id)
  if not pending:
    return  # No pending confirmation, ignore

  action, word, callback, expires = pending
  del _pending_confirmations[chat_id]

  if time.time() > expires:
    await update.message.reply_text(f"Confirmation expired. {action} cancelled.")
    return

  text = update.message.text.strip()
  if text == word:
    await callback(update)
  else:
    await update.message.reply_text(f"Cancelled. No action taken.")


# ── Engine Control ─────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
  # Check if live mode — require confirmation
  status = await api.get("/live/status")
  paper = status.get("paper_trading", True)
  if status.get("running"):
    await update.message.reply_text("Engine is already running.")
    return

  if not paper:
    # Live mode — require confirmation
    async def do_start(u: Update) -> None:
      result = await api.post("/live/start")
      if "error" in result:
        await u.message.reply_text(f"Failed: {result['error']}")
      else:
        await u.message.reply_text("Engine started (LIVE mode).")

    set_confirmation(update.effective_chat.id, "Start engine", "START", do_start)
    await update.message.reply_text(
      "Engine will start in LIVE mode (real money).\n\n"
      "Type START to confirm, anything else to cancel."
    )
  else:
    result = await api.post("/live/start")
    if "error" in result:
      await update.message.reply_text(f"Failed: {result['error']}")
    else:
      await update.message.reply_text("Engine started (PAPER mode).")


async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
  result = await api.post("/live/stop")
  if "error" in result:
    await update.message.reply_text(f"Failed: {result['error']}")
  else:
    await update.message.reply_text("Engine stopped gracefully.")


async def cmd_estop(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
  async def do_estop(u: Update) -> None:
    result = await api.post("/live/emergency-stop")
    if "error" in result:
      await u.message.reply_text(f"Failed: {result['error']}")
    else:
      await u.message.reply_text("EMERGENCY STOP executed. All positions closing. Engine shutting down.")

  set_confirmation(update.effective_chat.id, "Emergency stop", "EMERGENCY", do_estop)
  await update.message.reply_text(
    "EMERGENCY STOP will:\n"
    "  - Close ALL open positions at market\n"
    "  - Shut down the trading engine\n"
    "  - This cannot be undone.\n\n"
    "Type EMERGENCY to confirm, anything else to cancel."
  )


async def cmd_resetsafety(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
  async def do_reset(u: Update) -> None:
    result = await api.post("/live/reset-safety")
    if "error" in result:
      await u.message.reply_text(f"Failed: {result['error']}")
    else:
      await u.message.reply_text("Circuit breakers reset. Safety limits refreshed.")

  set_confirmation(update.effective_chat.id, "Reset safety", "RESET", do_reset)
  await update.message.reply_text(
    "This will reset ALL circuit breakers.\n"
    "Trading will resume with fresh safety limits.\n\n"
    "Type RESET to confirm, anything else to cancel."
  )


# ── Position Management ───────────────────────────────────────────

async def cmd_close(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
  if not ctx.args:
    await update.message.reply_text("Usage: /close <position_id>")
    return
  try:
    pid = int(ctx.args[0])
  except ValueError:
    await update.message.reply_text("Position ID must be a number.")
    return

  result = await api.post("/live/close-position", {"position_id": pid})
  if "error" in result:
    await update.message.reply_text(f"Failed: {result['error']}")
  else:
    await update.message.reply_text(f"Closing position {pid}.")


async def cmd_closeall(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
  # Show current positions first
  positions = await api.get("/live/positions")
  if isinstance(positions, list) and not positions:
    await update.message.reply_text("No open positions to close.")
    return

  pos_lines = []
  if isinstance(positions, list):
    for p in positions:
      sym = p.get("symbol", "?")
      side = side_arrow(p.get("side", ""))
      pnl = money(p.get("unrealized_pnl"))
      pos_lines.append(f"  {sym} {side} | P&L: {pnl}")

  async def do_closeall(u: Update) -> None:
    result = await api.post("/live/close-all")
    if "error" in result:
      await u.message.reply_text(f"Failed: {result['error']}")
    else:
      count = result.get("positions", "?")
      await u.message.reply_text(f"All {count} positions closing.")

  count = len(positions) if isinstance(positions, list) else "?"
  msg = f"CLOSE ALL will close {count} positions at market:\n"
  msg += "\n".join(pos_lines) if pos_lines else ""
  msg += "\n\nType CLOSEALL to confirm, anything else to cancel."

  set_confirmation(update.effective_chat.id, "Close all", "CLOSEALL", do_closeall)
  await update.message.reply_text(msg)


# ── Portfolio Control ─────────────────────────────────────────────

async def cmd_activate(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
  if not ctx.args:
    await update.message.reply_text("Usage: /activate <portfolio_id>")
    return
  pid = int(ctx.args[0])
  result = await api.post("/live/activate", {"portfolio_id": pid})
  if "error" in result:
    await update.message.reply_text(f"Failed: {result['error']}")
  else:
    await update.message.reply_text(f"Portfolio {pid} activated for live trading.")


async def _activate_by_type(update: Update, ptype: str, confirm_word: str) -> None:
  """Shared logic: activate all monitoring portfolios of a given type."""
  label = "paper" if ptype == "paper" else "live"
  portfolios = await api.get("/portfolios")
  if "error" in portfolios:
    await update.message.reply_text(f"Failed: {portfolios['error']}")
    return
  if not isinstance(portfolios, list):
    await update.message.reply_text("Unexpected response from portfolios API.")
    return

  # Find monitoring portfolios of the right type
  eligible = [
    p for p in portfolios
    if p.get("is_active") and p.get("portfolio_type") == ptype
  ]
  if not eligible:
    await update.message.reply_text(f"No monitoring {label} portfolios to activate.")
    return

  lines = [f"Activate {len(eligible)} {label} portfolios for trading:\n"]
  for p in eligible:
    lines.append(f"  {p['id']}. {p.get('name', '?')}")
  lines.append(f"\nType {confirm_word} to confirm, anything else to cancel.")

  async def do_activate(u: Update) -> None:
    ok, fail = [], []
    for p in eligible:
      result = await api.post("/live/activate", {"portfolio_id": p["id"]})
      if "error" in result:
        fail.append(f"  {p['id']}. {p.get('name', '?')}: {result['error']}")
      else:
        ok.append(p.get("name", str(p["id"])))
    msg = ""
    if ok:
      msg += f"Activated: {', '.join(ok)}\n"
    if fail:
      msg += f"Failed:\n" + "\n".join(fail)
    await u.message.reply_text(msg.strip() or "No portfolios activated.")

  set_confirmation(update.effective_chat.id, f"Activate {label}", confirm_word, do_activate)
  await update.message.reply_text("\n".join(lines))


async def cmd_activatepaper(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
  """Activate all monitoring paper portfolios for trading."""
  await _activate_by_type(update, "paper", "PAPER")


async def cmd_activatelive(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
  """Activate all monitoring real portfolios for live trading."""
  await _activate_by_type(update, "real", "LIVE")


async def cmd_deactivate(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
  if not ctx.args:
    await update.message.reply_text("Usage: /deactivate <portfolio_id>")
    return
  pid = int(ctx.args[0])
  result = await api.post("/live/deactivate", {"portfolio_id": pid, "close_positions": False})
  if "error" in result:
    await update.message.reply_text(f"Failed: {result['error']}")
  else:
    await update.message.reply_text(f"Portfolio {pid} deactivated.")


async def cmd_monitor(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
  if not ctx.args:
    await update.message.reply_text("Usage: /monitor <portfolio_id>")
    return
  pid = int(ctx.args[0])
  result = await api.post("/live/monitor", {"portfolio_id": pid})
  if "error" in result:
    await update.message.reply_text(f"Failed: {result['error']}")
  else:
    await update.message.reply_text(f"Portfolio {pid} monitoring started (candle sync).")


async def cmd_stopmon(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
  if not ctx.args:
    await update.message.reply_text("Usage: /stopmon <portfolio_id>")
    return
  pid = int(ctx.args[0])
  result = await api.post("/live/stop-monitoring", {"portfolio_id": pid})
  if "error" in result:
    await update.message.reply_text(f"Failed: {result['error']}")
  else:
    await update.message.reply_text(f"Portfolio {pid} monitoring stopped.")


async def cmd_refresh(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
  if not ctx.args:
    await update.message.reply_text("Usage: /refresh <portfolio_id>")
    return
  pid = int(ctx.args[0])
  result = await api.post("/live/refresh-portfolio", {"portfolio_id": pid})
  if "error" in result:
    await update.message.reply_text(f"Failed: {result['error']}")
  else:
    added = result.get("added", [])
    removed = result.get("removed", [])
    await update.message.reply_text(
      f"Portfolio {pid} refreshed.\n"
      f"  Added: {', '.join(added) if added else 'none'}\n"
      f"  Removed: {', '.join(removed) if removed else 'none'}"
    )
