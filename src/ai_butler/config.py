"""Configuration loading: gateway.yaml + manifest.yaml.

Schema mirrors configs/*.example.yaml. The kernel is domain-agnostic:
it only knows servers, adapters, policies, and resource entries.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def default_home() -> Path:
    """Data home: AI_BUTLER_HOME env, else ~/.ai-butler, else ./configs (dev)."""
    env = os.environ.get("AI_BUTLER_HOME")
    if env:
        return Path(env)
    home = Path(os.environ.get("USERPROFILE", Path.home())) / ".ai-butler"
    if home.exists():
        return home
    return Path.cwd() / "configs"


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 9000


@dataclass
class PolicyConfig:
    gpu_exclusive_window_minutes: int = 30
    max_concurrent_tasks: int = 1


@dataclass
class AdapterConfig:
    enabled: bool = True


@dataclass
class RoutesConfig:
    llm_prefer: list[str] = field(default_factory=list)
    llm_fallback: list[str] = field(default_factory=list)


@dataclass
class GatewayConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    routes: RoutesConfig = field(default_factory=RoutesConfig)
    adapters: dict[str, AdapterConfig] = field(default_factory=dict)
    runtimes: dict[str, dict[str, Any]] = field(default_factory=dict)
    paths: dict[str, str] = field(default_factory=dict)
    source_file: Path | None = None


@dataclass
class ModelSource:
    type: str
    repo: str | None = None
    file: str | None = None
    path: str | None = None
    url: str | None = None


@dataclass
class ModelEntry:
    name: str
    kind: str
    dir: str
    sources: list[ModelSource] = field(default_factory=list)
    runtime: str | None = None
    sha256: str | None = None

    @property
    def filename(self) -> str:
        """Best-known on-disk filename for presence checks."""
        for src in self.sources:
            if src.file:
                return Path(src.file).name
            if src.path:
                return Path(src.path).name
        return self.name + ".safetensors"


@dataclass
class Manifest:
    version: int = 1
    runtimes: dict[str, dict[str, Any]] = field(default_factory=dict)
    models: list[ModelEntry] = field(default_factory=list)
    custom_nodes: dict[str, Any] = field(default_factory=dict)
    paths: dict[str, str] = field(default_factory=dict)
    source_file: Path | None = None


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"config not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_gateway_optional(path: Path) -> GatewayConfig:
    """Load gateway config; fall back to defaults when the file is absent
    (manifest-only workflows like check/sync must run standalone)."""
    if not Path(path).exists():
        return GatewayConfig()
    return load_gateway(path)


def load_gateway(path: Path) -> GatewayConfig:
    raw = _load_yaml(path)
    server_raw = raw.get("server") or {}
    policy_raw = raw.get("policy") or {}
    routes_raw = raw.get("routes") or {}
    llm_raw = routes_raw.get("llm") or {}
    adapters_raw = raw.get("adapters") or {}
    cfg = GatewayConfig(
        server=ServerConfig(host=server_raw.get("host", "127.0.0.1"),
                            port=int(server_raw.get("port", 9000))),
        policy=PolicyConfig(
            gpu_exclusive_window_minutes=int(policy_raw.get("gpu_exclusive_window", {}).get("minutes", 30)),
            max_concurrent_tasks=int(policy_raw.get("max_concurrent_tasks", 1)),
        ),
        routes=RoutesConfig(llm_prefer=list(llm_raw.get("prefer") or []),
                            llm_fallback=list(llm_raw.get("fallback") or [])),
        adapters={name: AdapterConfig(enabled=bool(spec.get("enabled", True)))
                  for name, spec in adapters_raw.items()},
        runtimes={name: dict(spec) for name, spec in (raw.get("runtimes") or {}).items()},
        paths={k: str(v) for k, v in (raw.get("paths") or {}).items()},
        source_file=path,
    )
    return cfg


def load_manifest(path: Path) -> Manifest:
    raw = _load_yaml(path)
    models = []
    for entry in raw.get("models") or []:
        sources: list[ModelSource] = []
        src = entry.get("source") or {}
        primary = src.get("primary")
        if primary:
            sources.append(ModelSource(type=primary.get("type", ""), repo=primary.get("repo"),
                                       file=primary.get("file"), path=primary.get("path"),
                                       url=primary.get("url")))
        for fb in src.get("fallback") or []:
            sources.append(ModelSource(type=fb.get("type", ""), repo=fb.get("repo"),
                                       file=fb.get("file"), path=fb.get("path"),
                                       url=fb.get("url")))
        models.append(ModelEntry(name=str(entry.get("name")), kind=str(entry.get("kind", "")),
                                 dir=str(entry.get("dir", "")), sources=sources,
                                 runtime=entry.get("runtime"), sha256=entry.get("sha256")))
    cfg = Manifest(version=int(raw.get("version", 1)),
                   runtimes={k: dict(v) for k, v in (raw.get("runtimes") or {}).items()},
                   models=models,
                   custom_nodes=dict(raw.get("custom_nodes") or {}),
                   paths={k: str(v) for k, v in (raw.get("paths") or {}).items()},
                   source_file=path)
    return cfg
