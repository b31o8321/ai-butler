"""Generic OpenAI-compatible runtime adapter (name: 'openai_compat').

Covers LM Studio (default) and any /v1 chat endpoint. LLM routing
through it lands in S6; today it only probes.
"""
from __future__ import annotations

from typing import Any

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
