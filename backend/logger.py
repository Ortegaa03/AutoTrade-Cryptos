from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional


class LogBus:
    """In-memory log bus with SSE fan-out."""

    def __init__(self, maxlen: int = 400) -> None:
        self._logs: Deque[Dict[str, Any]] = deque(maxlen=maxlen)
        self._subscribers: List[asyncio.Queue] = []
        self._lock = asyncio.Lock()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    async def emit(
        self,
        message: str,
        *,
        level: str = "info",
        kind: str = "system",
        data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        entry = {
            "id": f"{datetime.now(timezone.utc).timestamp():.6f}",
            "ts": self._now(),
            "level": level,
            "kind": kind,
            "message": message,
            "data": data or {},
        }
        async with self._lock:
            self._logs.append(entry)
            dead: List[asyncio.Queue] = []
            for q in self._subscribers:
                try:
                    q.put_nowait(entry)
                except asyncio.QueueFull:
                    dead.append(q)
            for q in dead:
                if q in self._subscribers:
                    self._subscribers.remove(q)
        return entry

    def history(self) -> List[Dict[str, Any]]:
        return list(self._logs)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._subscribers:
            self._subscribers.remove(q)


logs = LogBus()