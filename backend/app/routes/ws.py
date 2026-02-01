import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.ws_manager import manager

router = APIRouter()


@router.websocket("/ws/audit/{drawing_id}")
async def audit_websocket(websocket: WebSocket, drawing_id: uuid.UUID):
    await manager.connect(drawing_id, websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect(drawing_id, websocket)


@router.websocket("/ws/inspection/{session_id}")
async def inspection_websocket(websocket: WebSocket, session_id: uuid.UUID):
    await manager.connect_session(session_id, websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect_session(session_id, websocket)
