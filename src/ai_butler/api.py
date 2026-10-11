"""FastAPI application: the gateway's HTTP face."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .adapters import build_adapters
from .config import GatewayConfig
from .kernel.registry import Registry
from .runtime_manager import RuntimeManager, detect_hardware
from .spi import BaseAdapter


def create_app(config: GatewayConfig, registry: Registry,
               runtime_manager: RuntimeManager | None = None) -> FastAPI:
    app = FastAPI(title="ai-butler", version=__version__, docs_url="/docs")
    adapters: list[BaseAdapter] = build_adapters(config)
    app.state.config = config
    app.state.registry = registry
    app.state.adapters = adapters
    manager = runtime_manager or RuntimeManager(config, registry, adapters=adapters)
    app.state.runtime_manager = manager

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

    @app.get("/v1/models")
    async def list_models():
        """OpenAI-compatible discovery. Real alias routing lands in S6."""
        data = [{"id": f"butler/{a.name}", "object": "model", "owned_by": "ai-butler"}
                for a in adapters if a.enabled]
        return {"object": "list", "data": data, "note": "stub until S6 alias routing"}

    app.mount("/console", StaticFiles(directory=str(console_dir), html=True), name="console")
    return app
