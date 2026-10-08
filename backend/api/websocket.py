"""WebSocket connection manager for real-time UI updates."""
import logging

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
  def __init__(self):
    self.active: list[WebSocket] = []

  async def connect(self, websocket: WebSocket):
    await websocket.accept()
    self.active.append(websocket)

  def disconnect(self, websocket: WebSocket):
    try:
      self.active.remove(websocket)
    except ValueError:
      pass

  async def broadcast(self, message: dict):
    dead: list[WebSocket] = []
    for conn in list(self.active):
      try:
        await conn.send_json(message)
      except Exception:
        dead.append(conn)
    for conn in dead:
      self.disconnect(conn)

  async def send_personal(self, websocket: WebSocket, message: dict):
    await websocket.send_json(message)


ws_manager = ConnectionManager()
