"""Generic Ollama HTTP runtime adapter (name: 'ollama')."""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from ..spi import BaseAdapter, http_probe_json


class OllamaAdapter(BaseAdapter):
    name = "ollama"

    async def probe(self) -> dict[str, Any]:
        url = self.config.get("url", "http://127.0.0.1:11434").rstrip("/")
        result = await http_probe_json(url + "/api/tags", transport=self._transport)
        if result["ok"]:
            models = result.pop("payload", {}).get("models") or []
            result["models"] = [m.get("name") for m in models]
            result["detail"] = f"{len(models)} models @ {url}" if models else "reachable, no models"
        return result

    # ---- LLM chat (S6): native /api/chat converted to OpenAI format ----

    def _chat_url(self) -> str:
        return self.config.get("url", "http://127.0.0.1:11434").rstrip("/") + "/api/chat"

    @staticmethod
    def _to_openai_completion(payload: dict[str, Any], model: str) -> dict[str, Any]:
        message = payload.get("message") or {}
        return {
            "id": f"chatcmpl-butler-{payload.get('created_at', '')}",
            "object": "chat.completion",
            "model": model,
            "choices": [{
                "index": 0,
                "message": {"role": message.get("role", "assistant"),
                            "content": message.get("content", "")},
                "finish_reason": "stop" if payload.get("done") else None,
            }],
            "usage": {"prompt_tokens": payload.get("prompt_eval_count"),
                      "completion_tokens": payload.get("eval_count")},
        }

    @staticmethod
    def _to_openai_chunk(payload: dict[str, Any], model: str) -> str:
        message = payload.get("message") or {}
        chunk = {"id": "chatcmpl-butler-stream", "object": "chat.completion.chunk",
                 "model": model,
                 "choices": [{"index": 0,
                              "delta": {"content": message.get("content", "")},
                              "finish_reason": "stop" if payload.get("done") else None}]}
        return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

    async def chat(self, model: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
        body = {"model": model, "messages": messages, "stream": False}
        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0),
                                     trust_env=False, transport=self._transport) as client:
            resp = await client.post(self._chat_url(), json=body)
            resp.raise_for_status()
            return self._to_openai_completion(resp.json(), model)

    async def chat_stream(self, model: str,
                          messages: list[dict[str, Any]]) -> AsyncIterator[str]:
        body = {"model": model, "messages": messages, "stream": True}
        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0, read=600.0),
                                     trust_env=False, transport=self._transport) as client:
            async with client.stream("POST", self._chat_url(), json=body) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    payload = json.loads(line)
                    yield self._to_openai_chunk(payload, model)
                yield "data: [DONE]\n\n"
