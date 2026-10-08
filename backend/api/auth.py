"""Optional API key authentication middleware.

When `secret_key` is set in settings (non-empty), all API requests must include
the key in the `Authorization: Bearer <key>` or `X-API-Key: <key>` header.

When `secret_key` is empty (default), authentication is disabled — all requests
pass through. This keeps the dev/local workflow frictionless.

WebSocket connections and the health-check root endpoint are always exempt.
"""
from __future__ import annotations

import hmac
from collections.abc import Callable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from backend.config import get_settings
from backend.utils.logger import get_logger

logger = get_logger(__name__)

# Paths that never require auth (exact match)
_EXEMPT_PATHS = {"/", "/health", "/docs", "/openapi.json", "/redoc"}


class APIKeyMiddleware(BaseHTTPMiddleware):
  """Middleware that enforces API key authentication when configured.

  If settings.secret_key is empty, all requests pass through (no auth).
  If settings.secret_key is set, requests must include it as:
    - Authorization: Bearer <key>
    - X-API-Key: <key>
  """

  async def dispatch(
    self, request: Request, call_next: Callable,
  ) -> Response:
    settings = get_settings()

    # Auth disabled when no secret_key is configured
    if not settings.secret_key:
      return await call_next(request)

    # Exempt paths (health check, docs)
    if request.url.path in _EXEMPT_PATHS:
      return await call_next(request)

    # WebSocket connections are exempt (handled separately if needed)
    if request.url.path.startswith("/ws"):
      return await call_next(request)

    # Extract key from headers
    auth_header = request.headers.get("authorization", "")
    api_key_header = request.headers.get("x-api-key", "")

    provided_key = ""
    if auth_header.lower().startswith("bearer "):
      provided_key = auth_header[7:].strip()
    elif api_key_header:
      provided_key = api_key_header.strip()

    # Return the 401 directly: an HTTPException raised inside a
    # BaseHTTPMiddleware bypasses FastAPI's handlers and reaches the
    # client as a 500 (fixed 2026-10-08).
    if not hmac.compare_digest(provided_key.encode(), settings.secret_key.encode()):
      return JSONResponse(
        status_code=401,
        content={"detail": "Invalid or missing API key. Set Authorization: Bearer <key> or X-API-Key: <key>."},
      )

    return await call_next(request)
