"""Adapter registry: the kernel's single doorway to concrete adapters."""
from __future__ import annotations

from ..config import AdapterConfig, GatewayConfig
from ..spi import BaseAdapter
from .comfyui import ComfyUIAdapter
from .ollama import OllamaAdapter
from .openai_compat import OpenAICompatAdapter

REGISTRY: dict[str, type[BaseAdapter]] = {
    ComfyUIAdapter.name: ComfyUIAdapter,
    OllamaAdapter.name: OllamaAdapter,
    OpenAICompatAdapter.name: OpenAICompatAdapter,
}


def build_adapters(config: GatewayConfig) -> list[BaseAdapter]:
    """Instantiate configured adapters. Unknown names in gateway.yaml are ignored."""
    adapters: list[BaseAdapter] = []
    for name, adapter_cls in REGISTRY.items():
        runtime_cfg = dict(config.runtimes.get(name, {}))
        adapter_cfg = config.adapters.get(name) or AdapterConfig()
        runtime_cfg["enabled"] = adapter_cfg.enabled
        adapters.append(adapter_cls(runtime_cfg, settings={"policy": {"max_concurrent_tasks": config.policy.max_concurrent_tasks}}))
    return adapters
