"""Tests for the dual-backend paper-portfolio forwarding helper in
backend/api/routes/live.py.

The live backend (paper_trading=False) forwards engine-state requests for
paper-type portfolios to a separately-running paper backend at
settings.paper_backend_url. The paper backend itself never forwards.

These tests exercise the helper `_should_forward_to_paper` directly with
each combination of (engine mode, portfolio type, paper_backend_url).
They do NOT spin up a real paper backend or engine — that's an
integration concern handled at run time.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.api.routes import live as live_routes
from backend.config.settings import Settings


def _patch_settings(**overrides):
  """Build a Settings instance with the given overrides and patch
  get_settings() to return it. Returns the patcher (start/stop)."""
  base = Settings(
    paper_trading=False,
    paper_backend_url="",
  )
  data = base.model_dump()
  data.update(overrides)
  fake = Settings(**data)
  return patch.object(live_routes, "get_settings", return_value=fake)


def test_live_backend_forwards_paper_portfolio_when_url_set():
  with _patch_settings(paper_trading=False, paper_backend_url="http://localhost:8056"):
    assert live_routes._should_forward_to_paper("paper") is True


def test_live_backend_does_not_forward_real_portfolio():
  with _patch_settings(paper_trading=False, paper_backend_url="http://localhost:8056"):
    assert live_routes._should_forward_to_paper("real") is False


def test_paper_backend_never_forwards_paper_portfolio():
  # On the paper backend (paper_trading=True), even paper portfolios stay local.
  # This is the loop-prevention guard.
  with _patch_settings(paper_trading=True, paper_backend_url="http://localhost:8056"):
    assert live_routes._should_forward_to_paper("paper") is False


def test_paper_backend_never_forwards_real_portfolio():
  with _patch_settings(paper_trading=True, paper_backend_url="http://localhost:8056"):
    assert live_routes._should_forward_to_paper("real") is False


def test_live_backend_with_url_unset_does_not_forward():
  # When forwarding isn't configured (default), behavior matches pre-change:
  # paper portfolios fall through to the engine's activation guard which
  # refuses them with the original error message.
  with _patch_settings(paper_trading=False, paper_backend_url=""):
    assert live_routes._should_forward_to_paper("paper") is False


def test_unknown_portfolio_type_does_not_forward():
  # 'saved' or other non-(paper|real) types should not be forwarded.
  with _patch_settings(paper_trading=False, paper_backend_url="http://localhost:8056"):
    assert live_routes._should_forward_to_paper("saved") is False
    assert live_routes._should_forward_to_paper(None) is False


def test_forward_raises_503_when_paper_backend_unreachable():
  """The forward helper must surface a clean 503 when the paper backend
  isn't running, NOT a raw ConnectError."""
  import asyncio
  import httpx
  from fastapi import HTTPException

  class _DeadClient:
    async def __aenter__(self): return self
    async def __aexit__(self, *_): return False
    async def post(self, *args, **kwargs):
      raise httpx.ConnectError("paper backend not running")

  with _patch_settings(paper_trading=False, paper_backend_url="http://localhost:8056"):
    with patch("backend.api.routes.live.httpx.AsyncClient", return_value=_DeadClient()):
      with pytest.raises(HTTPException) as exc_info:
        asyncio.run(live_routes._forward_to_paper(
          "/api/live/activate", {"portfolio_id": 1},
        ))
      assert exc_info.value.status_code == 503
      assert "Paper backend not reachable" in exc_info.value.detail


def test_forward_raises_504_when_paper_backend_times_out():
  """If the paper backend exists but is too slow (engine warmup), surface
  504 Gateway Timeout with a helpful message rather than the misleading
  503 'not reachable'."""
  import asyncio
  import httpx
  from fastapi import HTTPException

  class _SlowClient:
    async def __aenter__(self): return self
    async def __aexit__(self, *_): return False
    async def post(self, *args, **kwargs):
      raise httpx.ReadTimeout("engine still warming")

  with _patch_settings(paper_trading=False, paper_backend_url="http://localhost:8056"):
    with patch("backend.api.routes.live.httpx.AsyncClient", return_value=_SlowClient()):
      with pytest.raises(HTTPException) as exc_info:
        asyncio.run(live_routes._forward_to_paper(
          "/api/live/start", timeout=1.0,
        ))
      assert exc_info.value.status_code == 504
      assert "did not respond" in exc_info.value.detail
      assert "warming up" in exc_info.value.detail


def test_forward_returns_paper_backend_response_on_success():
  """When the paper backend responds 200, the helper returns the JSON."""
  import asyncio

  class _OkResp:
    status_code = 200
    def json(self): return {"status": "activated", "portfolio_id": 28}

  class _OkClient:
    async def __aenter__(self): return self
    async def __aexit__(self, *_): return False
    async def post(self, *args, **kwargs): return _OkResp()

  with _patch_settings(paper_trading=False, paper_backend_url="http://localhost:8056"):
    with patch("backend.api.routes.live.httpx.AsyncClient", return_value=_OkClient()):
      out = asyncio.run(live_routes._forward_to_paper(
        "/api/live/activate", {"portfolio_id": 28},
      ))
      assert out == {"status": "activated", "portfolio_id": 28}


def test_forward_surfaces_paper_backend_error_verbatim():
  """A 400 from the paper backend should bubble up as a 400 with the
  original detail, not be swallowed."""
  import asyncio
  from fastapi import HTTPException

  class _ErrResp:
    status_code = 400
    text = '{"detail": "engine refused"}'
    def json(self): return {"detail": "engine refused"}

  class _ErrClient:
    async def __aenter__(self): return self
    async def __aexit__(self, *_): return False
    async def post(self, *args, **kwargs): return _ErrResp()

  with _patch_settings(paper_trading=False, paper_backend_url="http://localhost:8056"):
    with patch("backend.api.routes.live.httpx.AsyncClient", return_value=_ErrClient()):
      with pytest.raises(HTTPException) as exc_info:
        asyncio.run(live_routes._forward_to_paper(
          "/api/live/activate", {"portfolio_id": 28},
        ))
      assert exc_info.value.status_code == 400
      assert "engine refused" in exc_info.value.detail


# ── Paper-engine remote-control routes (start/stop/status) ───────────

def test_refuse_if_on_paper_backend_raises_400_when_paper():
  """The /api/paper/* routes are invalid on the paper backend itself.
  The guard must raise 400 (not silently forward, which would loop)."""
  from fastapi import HTTPException
  with _patch_settings(paper_trading=True, paper_backend_url="http://localhost:8056"):
    with pytest.raises(HTTPException) as exc_info:
      live_routes._refuse_if_on_paper_backend()
    assert exc_info.value.status_code == 400
    assert "only valid on the LIVE backend" in exc_info.value.detail


def test_refuse_if_on_paper_backend_passes_on_live():
  """On the live backend, the guard returns silently — no error."""
  with _patch_settings(paper_trading=False, paper_backend_url="http://localhost:8056"):
    # Should not raise
    live_routes._refuse_if_on_paper_backend()


def test_forward_get_method_supported():
  """The /api/paper/status route is GET, so the helper must support it."""
  import asyncio

  class _OkResp:
    status_code = 200
    def json(self): return {"running": False, "enabled": True, "paper_trading": True}

  class _OkClient:
    async def __aenter__(self): return self
    async def __aexit__(self, *_): return False
    async def get(self, *_args, **_kwargs): return _OkResp()
    async def post(self, *_a, **_k):
      raise AssertionError("POST should not be called when method='GET'")

  with _patch_settings(paper_trading=False, paper_backend_url="http://localhost:8056"):
    with patch("backend.api.routes.live.httpx.AsyncClient", return_value=_OkClient()):
      out = asyncio.run(live_routes._forward_to_paper(
        "/api/live/status", method="GET",
      ))
      assert out["paper_trading"] is True
      assert out["running"] is False


def test_forward_raises_503_when_paper_backend_url_empty():
  """If forwarding is configured (paper_backend_url) is empty, the helper
  refuses with a clean 503 instead of crashing."""
  import asyncio
  from fastapi import HTTPException

  with _patch_settings(paper_trading=False, paper_backend_url=""):
    with pytest.raises(HTTPException) as exc_info:
      asyncio.run(live_routes._forward_to_paper("/api/live/start"))
    assert exc_info.value.status_code == 503
    assert "Paper backend not configured" in exc_info.value.detail
