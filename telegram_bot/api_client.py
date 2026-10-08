"""Async HTTP client for the War Room backend API."""
from __future__ import annotations

import httpx

API_BASE = "http://localhost:8055/api"
TIMEOUT = 10.0


class WarRoomAPI:
  """Thin async wrapper around the War Room REST API."""

  def __init__(self) -> None:
    self._client = httpx.AsyncClient(base_url=API_BASE, timeout=TIMEOUT)

  async def get(self, path: str, params: dict | None = None) -> dict:
    """GET request. Returns JSON dict or {"error": ...}."""
    try:
      r = await self._client.get(path, params=params)
      r.raise_for_status()
      return r.json()
    except httpx.ConnectError:
      return {"error": "Backend unreachable (is it running?)"}
    except httpx.HTTPStatusError as e:
      return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
      return {"error": str(e)[:200]}

  async def post(self, path: str, data: dict | None = None) -> dict:
    """POST request. Returns JSON dict or {"error": ...}."""
    try:
      r = await self._client.post(path, json=data or {})
      r.raise_for_status()
      return r.json()
    except httpx.ConnectError:
      return {"error": "Backend unreachable (is it running?)"}
    except httpx.HTTPStatusError as e:
      return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
      return {"error": str(e)[:200]}

  async def close(self) -> None:
    await self._client.aclose()
