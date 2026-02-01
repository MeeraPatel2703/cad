import json
import uuid
from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        self._connections: dict[str, list[WebSocket]] = {}

    async def connect(self, drawing_id: uuid.UUID, websocket: WebSocket):
        await websocket.accept()
        key = str(drawing_id)
        if key not in self._connections:
            self._connections[key] = []
        self._connections[key].append(websocket)

    def disconnect(self, drawing_id: uuid.UUID, websocket: WebSocket):
        key = str(drawing_id)
        if key in self._connections:
            self._connections[key] = [ws for ws in self._connections[key] if ws is not websocket]
            if not self._connections[key]:
                del self._connections[key]

    async def send_event(self, drawing_id: uuid.UUID, agent: str, event_type: str, data: dict):
        key = str(drawing_id)
        message = json.dumps({"agent": agent, "type": event_type, "data": data})
        for ws in self._connections.get(key, []):
            try:
                await ws.send_text(message)
            except Exception:
                pass

    async def broadcast(self, drawing_id: uuid.UUID, message: str):
        key = str(drawing_id)
        for ws in self._connections.get(key, []):
            try:
                await ws.send_text(message)
            except Exception:
                pass

    # ── Session-scoped WebSocket support ──

    async def connect_session(self, session_id: uuid.UUID, websocket: WebSocket):
        await websocket.accept()
        key = f"session_{session_id}"
        if key not in self._connections:
            self._connections[key] = []
        self._connections[key].append(websocket)

    def disconnect_session(self, session_id: uuid.UUID, websocket: WebSocket):
        key = f"session_{session_id}"
        if key in self._connections:
            self._connections[key] = [ws for ws in self._connections[key] if ws is not websocket]
            if not self._connections[key]:
                del self._connections[key]

    async def send_session_event(self, session_id: uuid.UUID, agent: str, event_type: str, data: dict):
        key = f"session_{session_id}"
        message = json.dumps({"agent": agent, "type": event_type, "data": data})
        for ws in self._connections.get(key, []):
            try:
                await ws.send_text(message)
            except Exception:
                pass


manager = ConnectionManager()
