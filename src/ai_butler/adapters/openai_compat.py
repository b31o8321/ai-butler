"""Generic OpenAI-compatible runtime adapter (name: 'openai_compat').

Covers LM Studio (default) and any /v1 chat endpoint, including
SSE streaming passthrough (S6).
"""
from __future__ import annotations

from typing import Any, AsyncIterator

import httpx

from ..spi import BaseAdapter, http_probe_json


class OpenAICompatAdapter(BaseAdapter):
    name = "openai_compat"

    def __init__(self, config: dict[str, Any], settings: dict[str, Any] | None = None,
                 transport: Any = None) -> None:
        super().__init__(config, settings, transport=transport)
        self.alias = self.config.get("alias", "lmstudio")
        self.url = self.config.get("url", "http://127.0.0.1:1234/v1").rstrip("/")

    async def probe(self) -> dict[str, Any]:
        result = await http_probe_json(self.url + "/models", transport=self._transport)
        if result["ok"]:
            data = result.pop("payload", {}).get("data") or []
            result["models"] = [m.get("id") for m in data]
            result["detail"] = f"{len(data)} models @ {self.url}" if data else "reachable, no models"
        return result

    # ---- LLM chat (S6): straight passthrough, format already OpenAI ----

    async def chat(self, model: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
        body = {"model": model, "messages": messages, "stream": False}
        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0),
                                     trust_env=False, transport=self._transport) as client:
            resp = await client.post(self.url + "/chat/completions", json=body)
            resp.raise_for_status()
            return resp.json()

    async def chat_stream(self, model: str,
                          messages: list[dict[str, Any]]) -> AsyncIterator[str]:
        body = {"model": model, "messages": messages, "stream": True}
        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0, read=600.0),
                                     trust_env=False, transport=self._transport) as client:
            async with client.stream("POST", self.url + "/chat/completions", json=body) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        yield line + "\n\n"
                yield "data: [DONE]\n\n"
