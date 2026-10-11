"""FastAPI application: the gateway's HTTP face."""
from __future__ import annotations

import asyncio
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .adapters import build_adapters
from .config import GatewayConfig
from .kernel.registry import Registry
from .kernel.tasks import TaskStore
from .presets import PresetStore, resolve_params
from .runtime_manager import RuntimeManager, detect_hardware
from .spi import BaseAdapter


def create_app(config: GatewayConfig, registry: Registry,
               runtime_manager: RuntimeManager | None = None,
               preset_store: PresetStore | None = None,
               task_store: TaskStore | None = None,
               executor: Any = None) -> FastAPI:
    app = FastAPI(title="ai-butler", version=__version__, docs_url="/docs")
    adapters: list[BaseAdapter] = build_adapters(config)
    app.state.config = config
    app.state.registry = registry
    app.state.adapters = adapters
    manager = runtime_manager or RuntimeManager(config, registry, adapters=adapters)
    app.state.runtime_manager = manager
    app.state.task_store = task_store or TaskStore(registry.db_path)

    repo_configs = Path(__file__).resolve().parents[2] / "configs"
    data_home = registry.db_path.parent.parent
    preset_dirs = [data_home / "presets", repo_configs / "presets"]
    workflow_dirs = [data_home, repo_configs]
    media_dir = data_home / "media"
    app.state.preset_store = preset_store or PresetStore(preset_dirs)

    def default_executor(preset_dict: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
        adapter = next((a for a in adapters if a.name == preset_dict.get("adapter")), None)
        if adapter is None:
            raise RuntimeError(f"no adapter for preset kind: {preset_dict.get('adapter')}")
        return asyncio.run(adapter.execute(
            preset_dict, params, workflows_dirs=workflow_dirs, media_dir=media_dir))

    run_executor = executor or default_executor
    app.state.executor = run_executor

    console_dir = Path(__file__).parent / "console"

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse("/console/")

    @app.get("/health")
    async def health():
        return {"app": "ai-butler", "version": __version__, "adapters": len(adapters)}

    @app.get("/admin/status")
    async def admin_status() -> dict[str, Any]:
        results = []
        for adapter in adapters:
            probe = await adapter.probe() if adapter.enabled else {
                "ok": False, "detail": "disabled in gateway.yaml"}
            results.append({"name": adapter.name, "enabled": adapter.enabled, **probe})
        status = {
            "app": "ai-butler", "version": __version__,
            "server": {"host": config.server.host, "port": config.server.port},
            "policy": {"max_concurrent_tasks": config.policy.max_concurrent_tasks,
                       "gpu_exclusive_window_minutes": config.policy.gpu_exclusive_window_minutes},
            "routes": {"llm_prefer": config.routes.llm_prefer, "llm_fallback": config.routes.llm_fallback},
            "adapters": results,
            "events": registry.recent(10),
        }
        registry.append("admin_status", {"adapters_ok": sum(1 for a in results if a.get("ok"))})
        return status

    @app.post("/admin/probe/{name}")
    async def probe_one(name: str) -> dict[str, Any]:
        for adapter in adapters:
            if adapter.name == name:
                return await adapter.probe()
        raise HTTPException(status_code=404, detail=f"unknown adapter: {name}")

    @app.get("/admin/hardware")
    async def admin_hardware() -> dict[str, Any]:
        return manager.detect_hardware()

    @app.get("/admin/runtimes")
    async def admin_runtimes() -> dict[str, Any]:
        return {"runtimes": await manager.status_all()}

    @app.post("/admin/runtimes/{name}/start")
    async def runtime_start(name: str) -> dict[str, Any]:
        try:
            return manager.start(name)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/admin/runtimes/{name}/stop")
    async def runtime_stop(name: str) -> dict[str, Any]:
        try:
            return manager.stop(name)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # ---- presets & tasks (S4) ----

    @app.get("/presets")
    async def list_presets() -> dict[str, Any]:
        return {"presets": [p.as_dict() for p in app.state.preset_store.list()]}

    @app.get("/presets/{preset_id}")
    async def get_preset(preset_id: str) -> dict[str, Any]:
        preset = app.state.preset_store.get(preset_id)
        if preset is None:
            raise HTTPException(status_code=404, detail=f"unknown preset: {preset_id}")
        return preset.as_dict()

    @app.post("/presets/{preset_id}/run")
    async def run_preset(preset_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        preset = app.state.preset_store.get(preset_id)
        if preset is None:
            raise HTTPException(status_code=404, detail=f"unknown preset: {preset_id}")
        params = (body or {}).get("params") or {}
        try:
            values = resolve_params(preset, params)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        task_store: TaskStore = app.state.task_store
        task_id = uuid.uuid4().hex[:12]
        task_store.create(task_id, kind=preset.kind, payload={"preset": preset.id, "params": values})

        def _worker() -> None:
            try:
                task_store.set_status(task_id, "running")
                result = run_executor(preset.as_dict(), values)
                task_store.set_status(task_id, "done", result=result)
            except Exception as exc:
                try:
                    task_store.set_status(task_id, "failed", error=str(exc)[:800])
                except ValueError:
                    pass

        threading.Thread(target=_worker, daemon=True, name=f"task-{task_id}").start()
        return {"task_id": task_id, "status": "queued"}

    @app.get("/tasks")
    async def list_tasks() -> dict[str, Any]:
        store: TaskStore = app.state.task_store
        return {"tasks": [store.get(t["id"]) for t in store.recent_tasks(50)]}

    @app.get("/tasks/{task_id}")
    async def get_task(task_id: str) -> dict[str, Any]:
        task = app.state.task_store.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"unknown task: {task_id}")
        return task

    @app.get("/v1/models")
    async def list_models():
        """OpenAI-compatible discovery. Real alias routing lands in S6."""
        data = [{"id": f"butler/{a.name}", "object": "model", "owned_by": "ai-butler"}
                for a in adapters if a.enabled]
        return {"object": "list", "data": data, "note": "stub until S6 alias routing"}

    app.mount("/console", StaticFiles(directory=str(console_dir), html=True), name="console")
    return app
