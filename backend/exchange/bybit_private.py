"""Bybit V5 private API client — read-only.

HMAC-signed requests for account balance, positions, and trade history.
No order placement — read-only for monitoring.
"""
import hashlib
import hmac
import time

import httpx

from backend.utils.logger import get_logger

logger = get_logger(__name__)

TESTNET_URL = "https://api-testnet.bybit.com"
MAINNET_URL = "https://api.bybit.com"


class BybitPrivateClient:
  """Read-only Bybit V5 private API client."""

  def __init__(self, api_key: str, api_secret: str, testnet: bool = True):
    self.api_key = api_key
    self.api_secret = api_secret
    self.base_url = TESTNET_URL if testnet else MAINNET_URL
    self.recv_window = "60000"  # 60s tolerance for system clock drift

  @staticmethod
  def _safe_float(val, default: float = 0.0) -> float:
    """Convert to float, handling empty strings from Bybit API."""
    if val is None or val == "":
      return default
    try:
      return float(val)
    except (ValueError, TypeError):
      return default

  def _sign(self, timestamp: str, params: str) -> str:
    """Generate HMAC-SHA256 signature for Bybit V5 API."""
    payload = f"{timestamp}{self.api_key}{self.recv_window}{params}"
    return hmac.new(
      self.api_secret.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()

  def _headers(self, params: str = "") -> dict:
    """Build authenticated headers."""
    ts = str(int(time.time() * 1000))
    return {
      "X-BAPI-API-KEY": self.api_key,
      "X-BAPI-SIGN": self._sign(ts, params),
      "X-BAPI-SIGN-TYPE": "2",
      "X-BAPI-TIMESTAMP": ts,
      "X-BAPI-RECV-WINDOW": self.recv_window,
      "Content-Type": "application/json",
    }

  async def _get(self, path: str, params: dict | None = None) -> dict:
    """Authenticated GET request."""
    query = "&".join(f"{k}={v}" for k, v in (params or {}).items())
    url = f"{self.base_url}{path}"
    if query:
      url += f"?{query}"
    async with httpx.AsyncClient(timeout=10) as client:
      resp = await client.get(url, headers=self._headers(query))
      resp.raise_for_status()
      data = resp.json()
    if data.get("retCode") != 0:
      raise ValueError(f"Bybit API error: {data.get('retMsg', 'Unknown')}")
    return data.get("result", {})

  async def test_connection(self) -> bool:
    """Test if API keys are valid by fetching account info."""
    try:
      await self._get("/v5/account/info")
      return True
    except Exception as e:
      logger.warning("Bybit connection test failed: %s", e)
      return False

  async def get_wallet_balance(self, account_type: str = "UNIFIED") -> dict:
    """Get wallet balance. Returns {total_equity, available, coins}."""
    result = await self._get("/v5/account/wallet-balance", {"accountType": account_type})
    accounts = result.get("list", [])
    if not accounts:
      return {"total_equity": 0, "available": 0, "coins": []}
    acct = accounts[0]
    coins = []
    for c in acct.get("coin", []):
      eq = self._safe_float(c.get("equity"))
      if eq == 0 and self._safe_float(c.get("walletBalance")) == 0:
        continue  # Skip zero-balance coins
      coins.append({
        "coin": c.get("coin", ""),
        "equity": eq,
        "available": self._safe_float(c.get("availableToWithdraw")),
        "unrealized_pnl": self._safe_float(c.get("unrealisedPnl")),
      })
    total_equity = self._safe_float(acct.get("totalEquity"))
    available = self._safe_float(acct.get("totalAvailableBalance"))

    # Fallback: if totalAvailableBalance is 0 but equity exists,
    # sum per-coin available balances (testnet often returns empty string)
    if available == 0 and total_equity > 0:
      coin_available = sum(c["available"] for c in coins)
      if coin_available > 0:
        available = coin_available
        logger.info(
          "totalAvailableBalance was 0, using per-coin sum: $%.2f",
          available,
        )
      else:
        # Last resort: use totalWalletBalance
        available = self._safe_float(acct.get("totalWalletBalance"))
        if available > 0:
          logger.info(
            "Using totalWalletBalance as available: $%.2f", available,
          )

    return {
      "total_equity": total_equity,
      "wallet_balance": self._safe_float(acct.get("totalWalletBalance")),
      "available": available,
      "coins": coins,
    }

  async def get_positions(self, category: str = "linear") -> list[dict]:
    """Get open positions (USDT perpetuals)."""
    result = await self._get("/v5/position/list", {
      "category": category, "settleCoin": "USDT",
    })
    positions = []
    for p in result.get("list", []):
      size = self._safe_float(p.get("size"))
      # CS-020: Bybit V5 returns size as a string that _safe_float parses to
      # a binary float. An exact == 0 check is fragile — a dust position
      # like 1e-12 would NOT equal 0 and would be reported as open, but
      # reduceOnly orders for that size fail with lot-size errors on the
      # next tick. Use an epsilon well below any Bybit min_qty (~1e-5 for
      # USDT perps) so real positions keep flowing, but subnormal residues
      # from precision issues are dropped.
      if abs(size) < 1e-9:
        continue
      positions.append({
        "symbol": p.get("symbol", ""),
        "side": p.get("side", ""),
        "size": size,
        "entry_price": self._safe_float(p.get("avgPrice")),
        "mark_price": self._safe_float(p.get("markPrice")),
        "unrealized_pnl": self._safe_float(p.get("unrealisedPnl")),
        "leverage": p.get("leverage", "1"),
        "liq_price": self._safe_float(p.get("liqPrice")) or None,
      })
    return positions

  async def get_trade_history(
    self, category: str = "linear", limit: int = 50,
  ) -> list[dict]:
    """Get recent trade (execution) history."""
    result = await self._get("/v5/execution/list", {
      "category": category, "limit": str(limit),
    })
    trades = []
    for t in result.get("list", []):
      trades.append({
        "symbol": t.get("symbol", ""),
        "side": t.get("side", ""),
        "qty": self._safe_float(t.get("execQty")),
        "price": self._safe_float(t.get("execPrice")),
        "fee": self._safe_float(t.get("execFee")),
        "exec_time": t.get("execTime", ""),
        "exec_type": t.get("execType", ""),
        "order_id": t.get("orderId", ""),
      })
    return trades
