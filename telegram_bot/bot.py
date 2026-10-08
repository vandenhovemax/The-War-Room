"""Bot application setup — wires everything together."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from telegram import Update
from telegram.ext import (
  Application, CommandHandler, MessageHandler, filters,
)

from telegram_bot import commands, commands_control, notifications

logger = logging.getLogger("telegram_bot")

HELP_TEXT = """THE WAR ROOM — Telegram Bot

MONITORING
/status    Engine overview
/positions Open positions
/orders    Pending orders
/pnl       P&L breakdown
/trades    Closed trades
/signals   Strategy signals
/events    Audit log
/health    System check
/balance   Exchange balance
/btc       BTC price
/kpis      Performance KPIs

PORTFOLIOS
/portfolios    List all
/portfolio     Detail view
/activate        Start trading
/activatepaper   Activate all paper
/activatelive    Activate all live
/deactivate      Stop trading
/monitor       Candle sync
/stopmon       Stop sync
/refresh       Sync exchange

ENGINE
/start       Start engine
/stop        Graceful stop
/estop       Emergency stop
/resetsafety Reset breakers

POSITIONS
/close     Close one
/closeall  Close all

SYSTEM
/ping      Bot alive check
/help      This list
"""


def _load_settings() -> dict:
  """Load all Telegram settings from War Room config."""
  # Add project root to path so backend.config is importable
  project_root = str(Path(__file__).resolve().parent.parent)
  if project_root not in sys.path:
    sys.path.insert(0, project_root)

  from backend.config.settings import get_settings
  s = get_settings()

  if not s.telegram_bot_token:
    logger.error("TELEGRAM_BOT_TOKEN not set in .env")
    sys.exit(1)
  if not s.telegram_chat_id:
    logger.error("TELEGRAM_CHAT_ID not set in .env")
    sys.exit(1)

  return {
    "token": s.telegram_bot_token,
    "chat_id": int(s.telegram_chat_id),
    "notifications_enabled": s.telegram_notifications_enabled,
    "event_poll_seconds": s.telegram_event_poll_seconds,
    "health_check_seconds": s.telegram_health_check_seconds,
    "confirm_timeout_seconds": s.telegram_confirm_timeout_seconds,
  }


def _auth_filter(chat_id: int) -> filters.BaseFilter:
  """Only allow messages from the configured chat ID."""
  return filters.Chat(chat_id=chat_id)


async def cmd_help(update: Update, ctx) -> None:
  await update.message.reply_text(HELP_TEXT)


def build_app() -> Application:
  """Build and return the Telegram Application (not yet running)."""
  logging.basicConfig(
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    level=logging.INFO,
  )

  cfg = _load_settings()
  token, chat_id = cfg["token"], cfg["chat_id"]
  auth = _auth_filter(chat_id)

  # Pass config to modules that need it
  commands_control.CONFIRM_TIMEOUT = cfg["confirm_timeout_seconds"]
  notifications.NOTIFICATIONS_ENABLED = cfg["notifications_enabled"]

  app = Application.builder().token(token).build()

  # Monitoring commands
  app.add_handler(CommandHandler("ping", commands.cmd_ping, filters=auth))
  app.add_handler(CommandHandler("health", commands.cmd_health, filters=auth))
  app.add_handler(CommandHandler("status", commands.cmd_status, filters=auth))
  app.add_handler(CommandHandler("positions", commands.cmd_positions, filters=auth))
  app.add_handler(CommandHandler("orders", commands.cmd_orders, filters=auth))
  app.add_handler(CommandHandler("pnl", commands.cmd_pnl, filters=auth))
  app.add_handler(CommandHandler("trades", commands.cmd_trades, filters=auth))
  app.add_handler(CommandHandler("signals", commands.cmd_signals, filters=auth))
  app.add_handler(CommandHandler("events", commands.cmd_events, filters=auth))
  app.add_handler(CommandHandler("balance", commands.cmd_balance, filters=auth))
  app.add_handler(CommandHandler("btc", commands.cmd_btc, filters=auth))
  app.add_handler(CommandHandler("kpis", commands.cmd_kpis, filters=auth))
  app.add_handler(CommandHandler("help", cmd_help, filters=auth))

  # Portfolio commands
  app.add_handler(CommandHandler("portfolios", commands.cmd_portfolios, filters=auth))
  app.add_handler(CommandHandler("portfolio", commands.cmd_portfolio_detail, filters=auth))

  # Engine control (with confirmations)
  app.add_handler(CommandHandler("start", commands_control.cmd_start, filters=auth))
  app.add_handler(CommandHandler("stop", commands_control.cmd_stop, filters=auth))
  app.add_handler(CommandHandler("estop", commands_control.cmd_estop, filters=auth))
  app.add_handler(CommandHandler("resetsafety", commands_control.cmd_resetsafety, filters=auth))

  # Position management
  app.add_handler(CommandHandler("close", commands_control.cmd_close, filters=auth))
  app.add_handler(CommandHandler("closeall", commands_control.cmd_closeall, filters=auth))

  # Portfolio control
  app.add_handler(CommandHandler("activate", commands_control.cmd_activate, filters=auth))
  app.add_handler(CommandHandler("activatepaper", commands_control.cmd_activatepaper, filters=auth))
  app.add_handler(CommandHandler("activatelive", commands_control.cmd_activatelive, filters=auth))
  app.add_handler(CommandHandler("deactivate", commands_control.cmd_deactivate, filters=auth))
  app.add_handler(CommandHandler("monitor", commands_control.cmd_monitor, filters=auth))
  app.add_handler(CommandHandler("stopmon", commands_control.cmd_stopmon, filters=auth))
  app.add_handler(CommandHandler("refresh", commands_control.cmd_refresh, filters=auth))

  # Text handler for confirmations (must be last)
  app.add_handler(MessageHandler(auth & filters.TEXT & ~filters.COMMAND, commands_control.handle_text))

  # Background jobs (intervals from settings)
  app.job_queue.run_repeating(notifications.poll_events, interval=cfg["event_poll_seconds"], first=5, data=chat_id)
  app.job_queue.run_repeating(notifications.health_check, interval=cfg["health_check_seconds"], first=10, data=chat_id)

  logger.info("Bot configured — chat_id=%d, %d commands registered", chat_id, 28)
  return app
