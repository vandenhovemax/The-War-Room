"""Settings route — read and update platform configuration.

Reads from and writes to the .env file. After updating, clears the
settings cache so new values take effect immediately. Sensitive fields
(API keys) are masked in GET responses.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from backend.config import get_settings
from backend.config.settings import BASE_DIR
from backend.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter()

ENV_PATH = BASE_DIR / "config" / ".env"

# Fields that can be updated via the API
EDITABLE_FIELDS = {
  # Bybit API
  "bybit_api_key", "bybit_api_secret", "bybit_testnet",
  "testnet_use_mainnet_prices",
  # Risk Management
  "risk_mode", "risk_per_trade_pct", "risk_per_trade_usd",
  "total_equity_override", "reserve_account_usd", "margin_breathing_room_pct",
  "max_position_pct",
  "max_risk_per_trade_usd", "max_risk_per_trade_pct",
  "max_positions_long", "max_positions_short",
  "max_positions_total", "daily_loss_limit_pct", "default_leverage",
  # Order Types
  "entry_order_type", "entry_limit_offset_pct", "tp_order_type",
  # Trading Mode
  "paper_trading", "paper_initial_balance", "paper_slippage_pct",
  # Live Engine
  "live_engine_enabled", "entry_order_ttl_seconds", "tp_order_ttl_seconds",
  "max_position_notional", "default_margin_mode", "max_daily_loss_usd",
  # Data
  "default_timeframe",
  # Telegram Bot
  "telegram_bot_token", "telegram_chat_id",
  "telegram_notifications_enabled", "telegram_event_poll_seconds",
  "telegram_health_check_seconds", "telegram_confirm_timeout_seconds",
}


def _mask_secret(value: str) -> str:
  """Mask a secret value, showing only last 4 chars."""
  if not value or len(value) <= 4:
    return "****" if value else ""
  return "*" * (len(value) - 4) + value[-4:]


@router.get("")
def get_app_settings():
  """Return all platform settings. API keys are masked."""
  s = get_settings()
  return {
    # Bybit API
    "bybit_api_key": _mask_secret(s.bybit_api_key),
    "bybit_api_secret": _mask_secret(s.bybit_api_secret),
    "bybit_testnet": s.bybit_testnet,
    "testnet_use_mainnet_prices": s.testnet_use_mainnet_prices,
    "has_api_keys": bool(s.bybit_api_key and s.bybit_api_secret),
    # Risk Management
    "risk_mode": s.risk_mode,
    "risk_per_trade_pct": s.risk_per_trade_pct,
    "risk_per_trade_usd": s.risk_per_trade_usd,
    "total_equity_override": s.total_equity_override,
    "reserve_account_usd": s.reserve_account_usd,
    "margin_breathing_room_pct": s.margin_breathing_room_pct,
    "max_position_pct": s.max_position_pct,
    "max_positions_long": s.max_positions_long,
    "max_positions_short": s.max_positions_short,
    "max_positions_total": s.max_positions_total,
    "max_risk_per_trade_usd": s.max_risk_per_trade_usd,
    "max_risk_per_trade_pct": s.max_risk_per_trade_pct,
    "daily_loss_limit_pct": s.daily_loss_limit_pct,
    "default_leverage": s.default_leverage,
    # Order Types
    "entry_order_type": s.entry_order_type,
    "entry_limit_offset_pct": s.entry_limit_offset_pct,
    "tp_order_type": s.tp_order_type,
    # Trading Mode
    "paper_trading": s.paper_trading,
    "paper_initial_balance": s.paper_initial_balance,
    "paper_slippage_pct": s.paper_slippage_pct,
    # Live Engine
    "live_engine_enabled": s.live_engine_enabled,
    "entry_order_ttl_seconds": s.entry_order_ttl_seconds,
    "tp_order_ttl_seconds": s.tp_order_ttl_seconds,
    "max_position_notional": s.max_position_notional,
    "default_margin_mode": s.default_margin_mode,
    "max_daily_loss_usd": s.max_daily_loss_usd,
    # Data
    "default_timeframe": s.default_timeframe,
    # Telegram Bot
    "telegram_bot_token": _mask_secret(s.telegram_bot_token),
    "telegram_chat_id": _mask_secret(s.telegram_chat_id),
    "has_telegram": bool(s.telegram_bot_token and s.telegram_chat_id),
    "telegram_notifications_enabled": s.telegram_notifications_enabled,
    "telegram_event_poll_seconds": s.telegram_event_poll_seconds,
    "telegram_health_check_seconds": s.telegram_health_check_seconds,
    "telegram_confirm_timeout_seconds": s.telegram_confirm_timeout_seconds,
  }


class SettingsUpdate(BaseModel):
  """Partial settings update — only include fields to change."""
  bybit_api_key: str | None = None
  bybit_api_secret: str | None = None
  bybit_testnet: bool | None = None
  testnet_use_mainnet_prices: bool | None = None
  risk_mode: str | None = None
  risk_per_trade_pct: float | None = None
  risk_per_trade_usd: float | None = None
  total_equity_override: float | None = None
  reserve_account_usd: float | None = None
  margin_breathing_room_pct: float | None = None
  max_position_pct: float | None = None
  max_positions_long: int | None = None
  max_positions_short: int | None = None
  max_positions_total: int | None = None
  max_risk_per_trade_usd: float | None = None
  max_risk_per_trade_pct: float | None = None
  daily_loss_limit_pct: float | None = None
  default_leverage: int | None = None
  entry_order_type: str | None = None
  entry_limit_offset_pct: float | None = None
  tp_order_type: str | None = None
  paper_trading: bool | None = None
  paper_initial_balance: float | None = None
  paper_slippage_pct: float | None = None
  live_engine_enabled: bool | None = None
  entry_order_ttl_seconds: int | None = None
  tp_order_ttl_seconds: int | None = None
  max_position_notional: float | None = None
  default_margin_mode: str | None = None
  max_daily_loss_usd: float | None = None
  default_timeframe: str | None = None
  # Telegram Bot
  telegram_bot_token: str | None = None
  telegram_chat_id: str | None = None
  telegram_notifications_enabled: bool | None = None
  telegram_event_poll_seconds: int | None = None
  telegram_health_check_seconds: int | None = None
  telegram_confirm_timeout_seconds: int | None = None


@router.post("")
def update_settings(update: SettingsUpdate):
  """Update settings in .env file and reload.

  Only provided (non-None) fields are updated. Masked API key values
  (containing '****') are ignored to prevent overwriting real keys
  with masked placeholders.
  """
  changes = update.model_dump(exclude_none=True)

  if not changes:
    return {"status": "no_changes", "updated": []}

  # Filter out masked values (user didn't actually change the key/token)
  for key in ("bybit_api_key", "bybit_api_secret", "telegram_bot_token", "telegram_chat_id"):
    if key in changes and "****" in str(changes[key]):
      del changes[key]

  # Filter to only editable fields
  changes = {k: v for k, v in changes.items() if k in EDITABLE_FIELDS}

  if not changes:
    return {"status": "no_changes", "updated": []}

  # Read current .env content
  env_lines = _read_env_file()

  # Apply changes
  updated_keys = []
  for field, value in changes.items():
    env_key = field.upper()
    env_value = _format_env_value(value)
    env_lines = _set_env_line(env_lines, env_key, env_value)
    updated_keys.append(field)
    logger.info("Settings updated: %s = %s", env_key,
                "****" if "secret" in field or "key" in field or "token" in field or "chat_id" in field else env_value)

  # Write back to .env
  _write_env_file(env_lines)

  # Clear the settings cache so new values take effect
  get_settings.cache_clear()

  return {
    "status": "updated",
    "updated": updated_keys,
    "restart_required": _needs_restart(updated_keys),
  }


def _needs_restart(updated_keys: list[str]) -> bool:
  """Check if any updated fields require an engine restart."""
  restart_fields = {
    "bybit_api_key", "bybit_api_secret", "bybit_testnet",
    "paper_trading", "paper_initial_balance",
    "live_engine_enabled",
    "telegram_bot_token", "telegram_chat_id",
  }
  return bool(set(updated_keys) & restart_fields)


def _read_env_file() -> list[str]:
  """Read .env file lines."""
  if not ENV_PATH.exists():
    return []
  return ENV_PATH.read_text(encoding="utf-8").splitlines()


def _write_env_file(lines: list[str]) -> None:
  """Write lines back to .env file."""
  content = "\n".join(lines)
  if not content.endswith("\n"):
    content += "\n"
  ENV_PATH.write_text(content, encoding="utf-8")


def _set_env_line(lines: list[str], key: str, value: str) -> list[str]:
  """Set or add a KEY=VALUE line in the env file."""
  target = f"{key}="
  found = False

  for i, line in enumerate(lines):
    stripped = line.strip()
    if stripped.startswith(target):
      lines[i] = f"{key}={value}"
      found = True
      break

  if not found:
    lines.append(f"{key}={value}")

  return lines


def _format_env_value(value) -> str:
  """Format a Python value for .env file."""
  if isinstance(value, bool):
    return "true" if value else "false"
  return str(value)
