"""
Application settings loaded from environment variables / .env file.
Uses Pydantic BaseSettings for validation and type coercion.
"""
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent


class Settings(BaseSettings):
  # --- App ---
  app_name: str = "The War Room"
  debug: bool = False
  host: str = "127.0.0.1"
  port: int = 8055
  frontend_url: str = "http://localhost:3033"
  secret_key: str = ""  # Set in .env if auth middleware is enabled

  # --- Database ---
  database_url: str = Field(
    default_factory=lambda: f"sqlite:///{PROJECT_ROOT / 'data' / 'antigravity.db'}"
  )

  # --- Bybit API ---
  bybit_api_key: str = ""
  bybit_api_secret: str = ""
  bybit_testnet: bool = True
  testnet_use_mainnet_prices: bool = True      # Use real mainnet prices when trading on testnet

  # --- TradingView Webhook ---
  webhook_secret: str = ""  # Set in .env if webhook route is enabled

  # --- Risk Management ---
  risk_mode: str = "pct"                       # "pct" = % of equity, "fixed" = fixed USD amount
  risk_per_trade_pct: float = 2.5              # % of equity risked per trade (when risk_mode="pct")
  risk_per_trade_usd: float = 50.0             # Fixed USD risk per trade (when risk_mode="fixed")
  # CS-024: DEPRECATED — the reserve_account_usd + wallet balance path is the
  # supported way to declare off-exchange equity. A loud warning is logged in
  # position_manager.calculate_position_size and in
  # engine_coordinator._get_total_equity_for_risk when this value is non-zero.
  # Scheduled for removal once confirmed no live account relies on it.
  total_equity_override: float = 0.0
  reserve_account_usd: float = 0.0             # Funds held off-exchange (added to Bybit balance for total equity)
  margin_breathing_room_pct: float = 10.0      # % buffer on margin budget per slot (prevents flush sizing)
  max_position_pct: float = 25.0               # Max margin (equity tied up) per position as % of total equity
  max_positions_long: int = 10
  max_positions_short: int = 10
  max_positions_total: int = 20
  daily_loss_limit_pct: float = 5.0
  default_leverage: int = 1

  # --- Trading Mode ---
  paper_trading: bool = True
  paper_initial_balance: float = 10_000.0      # Starting virtual balance for paper trading
  paper_slippage_pct: float = 0.01             # Simulated slippage % for paper fills

  # --- Dual-backend routing (additive, opt-in) ---
  # When the LIVE backend (paper_trading=False) receives an engine-state
  # request for a portfolio whose portfolio_type='paper', it forwards the
  # request to the paper backend at this URL instead of refusing it.
  # Empty string ('') disables forwarding — back to single-backend behavior
  # (paper portfolios refused on the live backend by the activation guard).
  # The paper backend (paper_trading=True) ignores this setting; it never
  # forwards. Only one direction: live → paper. No loops possible.
  paper_backend_url: str = ""

  # --- Order Types ---
  entry_order_type: str = "Market"             # "Market" or "Limit" for entry orders
  entry_limit_offset_pct: float = 0.05         # % offset from last price for limit entries
  tp_order_type: str = "Limit"                 # "Limit" (saves fees) or "Market" (guaranteed fill)

  # --- Live Execution Engine ---
  live_engine_enabled: bool = True             # Master kill switch
  entry_order_ttl_seconds: int = 120           # Limit entry timeout (cancel unfilled after N seconds)
  tp_order_ttl_seconds: int = 60               # TP limit timeout before switching to market close
  max_position_notional: float = 5000.0        # Max USD notional per position (legacy fallback)
  default_margin_mode: str = "isolated"        # "isolated" or "cross"
  reconciliation_interval_seconds: int = 5     # How often to sync local state vs exchange
  max_risk_per_trade_usd: float = 0.0           # Hard cap on per-trade risk (qty * SL_distance). 0 = disabled.
  max_risk_per_trade_pct: float = 0.0           # Hard cap as % of total equity. 0 = disabled. Used if USD cap is 0.
  skip_capped_entries: bool = True              # True = skip entries that can't fill full risk size. False = accept reduced size.
  max_daily_loss_usd: float = 500.0            # Hard daily loss cutoff (circuit breaker)
  ws_reconnect_delay_seconds: int = 5          # WebSocket reconnect delay

  # --- Data Collection ---
  bybit_base_url: str = "https://api.bybit.com"
  default_timeframe: str = "30m"

  # --- Telegram Bot ---
  telegram_bot_token: str = ""
  telegram_chat_id: str = ""
  telegram_notifications_enabled: bool = True    # Master toggle for push notifications
  telegram_event_poll_seconds: int = 10          # How often to poll for new events
  telegram_health_check_seconds: int = 60        # How often to check backend health
  telegram_confirm_timeout_seconds: int = 60     # How long confirmations stay valid

  # --- Paths ---
  log_dir: str = str(PROJECT_ROOT / "logs")
  upload_dir: str = str(PROJECT_ROOT / "data" / "uploads")

  model_config = {
    "env_file": str(BASE_DIR / "config" / ".env"),
    "env_file_encoding": "utf-8",
    "extra": "ignore",
  }


@lru_cache()
def get_settings() -> Settings:
  return Settings()
