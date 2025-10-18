from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

import redis.asyncio as redis  # type: ignore


class EventBus:
    """Thin wrapper over Redis Streams for publishing and consuming events."""

    def __init__(self, url: str, *, prefix: str = "pipeline") -> None:
        self._url = url
        self._prefix = prefix.rstrip(":")
        self._client = redis.from_url(url, decode_responses=True)

    def stream_name(self, name: str) -> str:
        return f"{self._prefix}:{name}"

    async def publish(
        self,
        stream: str,
        payload: Dict[str, Any],
        *,
        idempotency_key: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        data: Dict[str, Any] = {
            "payload": json.dumps(payload, default=str),
        }
        if idempotency_key:
            data["idempotency_key"] = idempotency_key
        if metadata:
            data["metadata"] = json.dumps(metadata, default=str)
        stream_name = self.stream_name(stream)
        return await self._client.xadd(stream_name, data)

    async def ensure_consumer_group(
        self,
        stream: str,
        group: str,
        *,
        mkstream: bool = True,
    ) -> None:
        stream_name = self.stream_name(stream)
        try:
            await self._client.xgroup_create(
                stream_name,
                group,
                id="0-0",
                mkstream=mkstream,
            )
        except redis.ResponseError as exc:  # already exists
            if "BUSYGROUP" not in str(exc):
                raise

    async def consume(
        self,
        stream: str,
        group: str,
        consumer: str,
        *,
        count: int = 10,
        block_ms: int = 1000,
    ) -> List[Tuple[str, Dict[str, Any]]]:
        stream_name = self.stream_name(stream)
        entries = await self._client.xreadgroup(
            group,
            consumer,
            streams={stream_name: ">"},
            count=count,
            block=block_ms,
        )
        results: List[Tuple[str, Dict[str, Any]]] = []
        for _, messages in entries:
            for message_id, fields in messages:
                payload = json.loads(fields.get("payload", "{}"))
                metadata = json.loads(fields.get("metadata", "{}"))
                results.append(
                    (
                        message_id,
                        {
                            "payload": payload,
                            "metadata": metadata,
                            "idempotency_key": fields.get("idempotency_key"),
                        },
                    )
                )
        return results

    async def acknowledge(self, stream: str, group: str, message_ids: Iterable[str]) -> None:
        stream_name = self.stream_name(stream)
        ids = list(message_ids)
        if ids:
            await self._client.xack(stream_name, group, *ids)

    async def close(self) -> None:
        await self._client.aclose()
