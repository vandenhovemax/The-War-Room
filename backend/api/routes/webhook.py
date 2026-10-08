"""TradingView webhook receiver (Phase 8 placeholder)."""
from fastapi import APIRouter

router = APIRouter()


@router.post("/tv")
async def receive_tv_webhook(payload: dict):
  # Phase 8: parse signal, run risk checks, place order
  return {"status": "received", "message": "Webhook processing not yet implemented"}
