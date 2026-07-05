"""In-memory WebSocket connection registry, grouped by session.

Provides per-user and per-session (broadcast) delivery so the backend can push
chat responses, context updates and file changes to all participants in real
time.
"""

import asyncio

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        # session_id (str) -> { user_id (str) -> WebSocket }
        self._connections: dict[str, dict[str, WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, session_id: str, user_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.setdefault(session_id, {})[user_id] = websocket

    async def disconnect(self, session_id: str, user_id: str) -> None:
        async with self._lock:
            room = self._connections.get(session_id)
            if room and user_id in room:
                del room[user_id]
                if not room:
                    del self._connections[session_id]

    async def send_personal(self, session_id: str, user_id: str, message: dict) -> None:
        websocket = self._connections.get(session_id, {}).get(user_id)
        if websocket is not None:
            await self._safe_send(websocket, message)

    async def broadcast(
        self, session_id: str, message: dict, exclude_user: str | None = None
    ) -> None:
        for user_id, websocket in list(self._connections.get(session_id, {}).items()):
            if user_id == exclude_user:
                continue
            await self._safe_send(websocket, message)

    def peers(self, session_id: str) -> list[str]:
        return list(self._connections.get(session_id, {}).keys())

    @staticmethod
    async def _safe_send(websocket: WebSocket, message: dict) -> None:
        try:
            await websocket.send_json(message)
        except Exception:
            # A broken socket must never take down the broadcast loop.
            pass


manager = ConnectionManager()
