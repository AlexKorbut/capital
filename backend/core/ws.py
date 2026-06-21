"""Process-local WebSocket connection manager (per-user fan-out).

Dev runs a single process, so an in-memory registry is enough. In prod (multiple
workers) this is swapped for a Redis pub/sub broadcast — same `broadcast` API.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger("kapital.ws")


class ConnectionManager:
    def __init__(self) -> None:
        self._conns: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, user_id: str, ws: WebSocket) -> None:
        await ws.accept()
        await self.register(user_id, ws)

    async def register(self, user_id: str, ws: WebSocket) -> None:
        """Track an already-accepted socket (auth handshake done by the caller)."""
        async with self._lock:
            self._conns[user_id].add(ws)
        logger.info("ws connected: user=%s (%d total)", user_id, len(self._conns[user_id]))

    async def disconnect(self, user_id: str, ws: WebSocket) -> None:
        async with self._lock:
            self._conns.get(user_id, set()).discard(ws)
            if not self._conns.get(user_id):
                self._conns.pop(user_id, None)

    async def broadcast(self, user_id: str, message: dict[str, Any]) -> None:
        """Send a JSON message to every live socket for a user (best-effort)."""
        targets = list(self._conns.get(user_id, set()))
        dead: list[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_json(message)
            except Exception:  # noqa: BLE001 — drop broken sockets silently
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._conns.get(user_id, set()).discard(ws)


_manager: ConnectionManager | None = None


def get_ws_manager() -> ConnectionManager:
    global _manager
    if _manager is None:
        _manager = ConnectionManager()
    return _manager
