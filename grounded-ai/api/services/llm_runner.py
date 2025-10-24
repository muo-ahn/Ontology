"""
Helper for invoking local LLMs (Ollama) for text-only reasoning.
Falls back to mock echoes when httpx/host is unavailable so the API remains callable.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class LLMRunner:
    base_url: str
    model: str
    timeout: float
    _client: Optional[object] = None

    def __post_init__(self) -> None:
        try:
            import httpx  # type: ignore

            self._client = httpx.AsyncClient(timeout=self.timeout)
        except Exception:  # pragma: no cover - fallback path
            self._client = None

    @classmethod
    def from_env(cls) -> "LLMRunner":
        base_url = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        model = os.getenv("LLM_MODEL", "qwen2.5:7b-instruct-q4_K_M")
        timeout = float(os.getenv("LLM_TIMEOUT", "120"))
        return cls(base_url=base_url, model=model, timeout=timeout)

    async def generate(
        self,
        prompt: str,
        *,
        temperature: float = 0.2,
        context: Optional[str] = None,
    ) -> Dict[str, Any]:
        start = time.perf_counter()
        if self._client is None:
            # Offline mock useful during development without Ollama
            message = f"[mock-llm] {prompt[:200]}"
            return {
                "output": message,
                "model": self.model,
                "latency_ms": int((time.perf_counter() - start) * 1000),
            }

        payload: Dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if context:
            payload["context"] = context

        async def _post() -> Dict[str, Any]:
            client = self._client
            assert client is not None
            response = await client.post(f"{self.base_url}/api/generate", json=payload)
            response.raise_for_status()
            return response.json()

        try:
            data = await _post()
        except Exception as exc:  # pragma: no cover - network fallback
            latency_ms = int((time.perf_counter() - start) * 1000)
            message = f"[mock-llm] {prompt[:200]}"
            return {
                "output": message,
                "model": self.model,
                "latency_ms": latency_ms,
                "warning": f"LLM call failed: {exc}",
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
        """Lightweight readiness probe for the underlying Ollama endpoint."""

        client = self._client
        if client is None:  # Offline/dev fallback still counts as healthy.
            return True
        try:
            response = await client.get(f"{self.base_url}/api/tags")
            response.raise_for_status()
        except Exception:
            return False
        return True
