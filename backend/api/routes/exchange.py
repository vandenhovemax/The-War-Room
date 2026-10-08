"""Exchange API routes — read-only Bybit integration."""
import asyncio

from fastapi import APIRouter

from backend.config import get_settings
from backend.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()


def _get_client():
  """Create Bybit client from settings. Returns None if keys not configured."""
  from backend.exchange.bybit_private import BybitPrivateClient
  s = get_settings()
  if not s.bybit_api_key or not s.bybit_api_secret:
    return None
  return BybitPrivateClient(s.bybit_api_key, s.bybit_api_secret, s.bybit_testnet)


@router.get("/status")
async def exchange_status():
  """Check exchange connection status."""
  s = get_settings()
  client = _get_client()
  if not client:
    return {"connected": False, "testnet": s.bybit_testnet, "error": "API keys not configured"}
  try:
    ok = await client.test_connection()
    return {"connected": ok, "testnet": s.bybit_testnet, "error": None if ok else "Connection failed"}
  except Exception as e:
    return {"connected": False, "testnet": s.bybit_testnet, "error": str(e)}


@router.get("/balance")
async def exchange_balance():
  """Get wallet balance."""
  client = _get_client()
  if not client:
    return {"total_equity": 0, "available": 0, "coins": [], "error": "API keys not configured"}
  try:
    return await client.get_wallet_balance()
  except Exception as e:
    logger.error("Failed to fetch balance: %s", e)
    return {"total_equity": 0, "available": 0, "coins": [], "error": str(e)}


@router.get("/positions")
async def exchange_positions():
  """Get open positions."""
  client = _get_client()
  if not client:
    return {"positions": [], "error": "API keys not configured"}
  try:
    positions = await client.get_positions()
    return {"positions": positions}
  except Exception as e:
    logger.error("Failed to fetch positions: %s", e)
    return {"positions": [], "error": str(e)}


@router.get("/trades")
async def exchange_trades():
  """Get recent trade history."""
  client = _get_client()
  if not client:
    return {"trades": [], "error": "API keys not configured"}
  try:
    trades = await client.get_trade_history()
    return {"trades": trades}
  except Exception as e:
    logger.error("Failed to fetch trades: %s", e)
    return {"trades": [], "error": str(e)}


@router.get("/balance-raw")
async def exchange_balance_raw():
  """Debug: return raw Bybit wallet-balance API response."""
  client = _get_client()
  if not client:
    return {"error": "API keys not configured"}
  try:
    result = await client._get("/v5/account/wallet-balance", {"accountType": "UNIFIED"})
    return {"raw": result}
  except Exception as e:
    return {"error": str(e)}
