from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import redis.asyncio as redis  # type: ignore


class TaskStatusTracker:
    """Utility for recording and streaming per-task status events via Redis Streams."""

    def __init__(self, url: str, *, prefix: str = "pipeline") -> None:
        self._prefix = prefix.rstrip(":")
        self._client = redis.from_url(url, decode_responses=True)

    def stream_name(self, task_id: str) -> str:
        return f"{self._prefix}:status:{task_id}"

    async def append(
        self,
        task_id: str,
        status: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> str:
        data: Dict[str, Any] = {
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if payload is not None:
            data["payload"] = json.dumps(payload, default=str)
        return await self._client.xadd(self.stream_name(task_id), data)

    async def stream(self, task_id: str, last_id: str = "0-0"):
        stream_name = self.stream_name(task_id)
        while True:
            entries = await self._client.xread(
                {stream_name: last_id},
                block=5000,
                count=1,
            )
            if not entries:
                yield {"event": "ping", "data": "{}"}
                continue
            _, messages = entries[0]
            for message_id, fields in messages:
                last_id = message_id
                payload = fields.get("payload")
                if payload is None:
                    payload_json = "{}"
                else:
                    payload_json = payload
                data = {
                    "status": fields.get("status"),
                    "timestamp": fields.get("timestamp"),
                    "payload": json.loads(payload_json),
                    "id": message_id,
                }
                yield {"event": "status", "data": json.dumps(data)}

    async def close(self) -> None:
        await self._client.aclose()
