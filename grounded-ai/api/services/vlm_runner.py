"""
Callable helper to interact with a local vision-language model endpoint (e.g. Ollama).
Falls back to mock responses when httpx is unavailable.
"""

from __future__ import annotations

import asyncio
import base64
import os
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional


class Task(str, Enum):
    CAPTION = "caption"
    VQA = "vqa"


@dataclass
class VLMRunner:
    base_url: str
    model: str
    timeout: float
    _client: Optional[object] = None

    Task = Task

    def __post_init__(self) -> None:
        try:
            import httpx  # type: ignore

            self._client = httpx.AsyncClient(timeout=self.timeout)
        except Exception:  # pragma: no cover - fallback
            self._client = None

    @classmethod
    def from_env(cls) -> "VLMRunner":
        base_url = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        model = os.getenv("VLM_MODEL", "qwen2-vl:2b-instruct-q4_0")
        timeout = float(os.getenv("VLM_TIMEOUT", "60"))
        return cls(base_url=base_url, model=model, timeout=timeout)

    async def generate(
        self,
        image_bytes: bytes,
        prompt: str,
        task: Task = Task.CAPTION,
        temperature: float = 0.2,
    ) -> Dict[str, Any]:
        start = time.perf_counter()
        if self._client is None:
            message = f"[mock-{task}] {prompt}"
            return {
                "output": message,
                "model": self.model,
                "latency_ms": int((time.perf_counter() - start) * 1000),
            }

        payload = {
            "model": self.model,
            "prompt": prompt,
            "options": {"temperature": temperature},
            "images": [base64.b64encode(image_bytes).decode("utf-8")],
            "stream": False,
        }

        async def _post() -> Dict[str, Any]:
            client = self._client
            assert client is not None
            response = await client.post(f"{self.base_url}/api/generate", json=payload)
            response.raise_for_status()
            return response.json()

        try:
            data = await _post()
        except Exception as exc:  # pragma: no cover - network failures fallback
            latency_ms = int((time.perf_counter() - start) * 1000)
            message = f"[mock-{task}] {prompt}"
            return {
                "output": message,
                "model": self.model,
                "latency_ms": latency_ms,
                "warning": f"VLM call failed: {exc}",
            }

        latency_ms = int((time.perf_counter() - start) * 1000)
        output = data.get("response") or data.get("result") or ""
        return {
            "output": output,
            "model": data.get("model", self.model),
            "latency_ms": latency_ms,
        }

    def close(self) -> None:
        if self._client is None:
            return
        client = self._client
        if not hasattr(client, "aclose"):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(client.aclose())  # type: ignore[union-attr]
        else:
            loop.create_task(client.aclose())  # type: ignore[arg-type]

    async def health(self) -> bool:
        """Return True when the backing vision-language endpoint is reachable."""

        client = self._client
        if client is None:
            return True
        try:
            response = await client.get(f"{self.base_url}/api/tags")
            response.raise_for_status()
        except Exception:
            return False
        return True
