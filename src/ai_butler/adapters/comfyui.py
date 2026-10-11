"""Generic ComfyUI HTTP runtime adapter (name: 'comfyui')."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

from ..spi import BaseAdapter, http_probe_json
from ..presets import apply_injection, resolve_params


class ComfyUIAdapter(BaseAdapter):
    name = "comfyui"

    async def probe(self) -> dict[str, Any]:
        url = self.config.get("url", "http://127.0.0.1:8188").rstrip("/")
        result = await http_probe_json(url + "/system_stats")
        if result["ok"]:
            stats = result.pop("payload", {}).get("system") or {}
            result["detail"] = f"comfyui {stats.get('comfyui_version', '?')} @ {url}"
        return result

    # ---- preset execution (S4) ----

    def _resolve_workflow_path(self, preset: dict[str, Any],
                               workflows_dirs: list[Path]) -> Path:
        raw = Path(preset["workflow"])
        if raw.is_absolute():
            if not raw.exists():
                raise FileNotFoundError(f"workflow not found: {raw}")
            return raw
        for directory in workflows_dirs:
            candidate = Path(directory) / raw
            if candidate.exists():
                return candidate
        raise FileNotFoundError(
            f"workflow '{preset['workflow']}' not found in {workflows_dirs}")

    def _fetch_output(self, base_url: str, item: dict[str, Any],
                      dest_dir: Path, client) -> str:
        params = {"filename": item["filename"], "subfolder": item.get("subfolder", ""),
                  "type": item.get("type", "output")}
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / item["filename"]
        with client.stream("GET", base_url + "/view", params=params, timeout=120) as resp:
            resp.raise_for_status()
            with dest.open("wb") as f:
                for chunk in resp.iter_bytes(1024 * 1024):
                    f.write(chunk)
        return str(dest)

    async def execute(self, preset: dict[str, Any], params: dict[str, Any],
                      *, workflows_dirs: list[Path] | None = None,
                      media_dir: Path | None = None,
                      poll_interval: float = 5.0, timeout: float = 1200.0) -> dict[str, Any]:
        """Run one preset end-to-end: inject → /prompt → poll → fetch products."""
        import httpx

        from ..presets import Preset
        if isinstance(preset, Preset):
            preset = preset.as_dict()

        base_url = self.config.get("url", "http://127.0.0.1:8188").rstrip("/")
        workflows_dirs = workflows_dirs or []
        workflow = json.loads(self._resolve_workflow_path(preset, workflows_dirs).read_text(encoding="utf-8"))
        values = resolve_params(_dict_to_preset(preset), params)
        apply_injection(workflow, preset.get("inject") or [], values)

        client_id = uuid.uuid4().hex[:12]
        media_target = Path(media_dir or ".") / str(uuid.uuid4().hex[:8])
        files: list[str] = []
        prompt_id = None
        error_detail = None

        def popen_results() -> dict[str, Any]:
            return {"prompt_id": prompt_id, "files": files, "params": values}

        with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0), trust_env=False,
                          transport=self._transport) as client:
            resp = client.post(base_url + "/prompt",
                               json={"prompt": workflow, "client_id": client_id})
            if resp.status_code != 200:
                detail = resp.text[:500]
                raise RuntimeError(f"comfyui rejected the workflow (HTTP {resp.status_code}): {detail}")
            prompt_id = resp.json().get("prompt_id")

            deadline = time.monotonic() + timeout
            status = "running"
            while time.monotonic() < deadline:
                await asyncio.sleep(poll_interval)
                history = client.get(base_url + f"/history/{prompt_id}").json()
                if prompt_id not in history:
                    continue
                entry = history[prompt_id]
                state = entry.get("status", {})
                if state.get("status_str") == "error":
                    status = "failed"
                    error_detail = json.dumps(
                        [m for m in state.get("messages", []) if m[0] == "execution_error"],
                        ensure_ascii=False)[:800]
                    break
                if state.get("completed"):
                    status = "done"
                    for node_output in entry.get("outputs", {}).values():
                        for key, items in node_output.items():
                            if isinstance(items, list):
                                for item in items:
                                    if isinstance(item, dict) and item.get("filename"):
                                        files.append(self._fetch_output(
                                            base_url, item, media_target, client))
                    break
            else:
                status = "failed"
                error_detail = f"timeout after {timeout}s waiting for {prompt_id}"

        result = popen_results() | {"status": status}
        if error_detail:
            result["error"] = error_detail
        if status == "failed":
            raise RuntimeError(f"comfyui execution failed: {error_detail}")
        return result


def _dict_to_preset(preset: dict[str, Any]) -> Any:
    """resolve_params expects attribute access; wrap a plain dict minimally."""
    from ..presets import Preset
    return Preset(id=preset.get("id", ""), name=preset.get("name", ""),
                  kind=preset.get("kind", ""), adapter=preset.get("adapter", ""),
                  workflow=preset.get("workflow", ""), defaults=preset.get("defaults") or {},
                  inputs=preset.get("inputs") or [], inject=preset.get("inject") or [],
                  policy=preset.get("policy") or {})
