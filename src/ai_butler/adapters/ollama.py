"""Generic Ollama HTTP runtime adapter (name: 'ollama')."""
from __future__ import annotations

from typing import Any

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
