"""Generic ComfyUI-family HTTP runtime adapter (name: 'comfyui')."""
from __future__ import annotations

from typing import Any

from ..spi import BaseAdapter, http_probe_json


class ComfyUIAdapter(BaseAdapter):
    name = "comfyui"

    async def probe(self) -> dict[str, Any]:
        url = self.config.get("url", "http://127.0.0.1:8188").rstrip("/")
        result = await http_probe_json(url + "/system_stats", transport=self._transport)
        if result["ok"]:
            stats = result.pop("payload", {}).get("system") or {}
            result["detail"] = f"comfyui {stats.get('comfyui_version', '?')} @ {url}"
        return result
