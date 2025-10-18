from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Iterable

from events.bus import EventBus

logger = logging.getLogger(__name__)


class StreamWorker:
    """Reusable worker skeleton for Redis Stream consumers."""

    def __init__(
        self,
        *,
        bus: EventBus,
        stream: str,
        group: str,
        consumer_name: str,
        poll_interval: float = 0.1,
        batch_size: int = 10,
    ) -> None:
        self.bus = bus
        self.stream = stream
        self.group = group
        self.consumer_name = consumer_name
        self.poll_interval = poll_interval
        self.batch_size = batch_size
        self._running = False

    async def start(self) -> None:
        await self.bus.ensure_consumer_group(self.stream, self.group)
        self._running = True
        logger.info(
            "Worker %s subscribing to stream=%s group=%s",
            self.consumer_name,
            self.stream,
            self.group,
        )
        while self._running:
            messages = await self.bus.consume(
                self.stream,
                self.group,
                self.consumer_name,
                count=self.batch_size,
                block_ms=int(self.poll_interval * 1000),
            )
            if not messages:
                continue
            ack_ids: list[str] = []
            for message_id, envelope in messages:
                try:
                    await self.handle(envelope["payload"], envelope.get("metadata") or {}, envelope.get("idempotency_key"))
                    ack_ids.append(message_id)
                except Exception as exc:  # pragma: no cover - worker logging
                    logger.exception("Worker %s failed to handle message %s: %s", self.consumer_name, message_id, exc)
            if ack_ids:
                await self.bus.acknowledge(self.stream, self.group, ack_ids)

    async def handle(
        self,
        payload: Dict[str, Any],
        metadata: Dict[str, Any],
        idempotency_key: str | None,
    ) -> None:  # pragma: no cover - to be implemented by subclasses
        raise NotImplementedError

    async def stop(self) -> None:
        self._running = False
