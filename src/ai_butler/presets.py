"""Preset loading, parameter resolution, and data-driven workflow injection."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Preset:
    id: str
    name: str
    kind: str
    adapter: str
    workflow: str
    defaults: dict[str, Any] = field(default_factory=dict)
    inputs: list[dict[str, Any]] = field(default_factory=list)
    inject: list[dict[str, Any]] = field(default_factory=list)
    policy: dict[str, Any] = field(default_factory=dict)
    source_file: Path | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "kind": self.kind,
                "adapter": self.adapter, "workflow": self.workflow,
                "defaults": self.defaults, "inputs": self.inputs,
                "inject": self.inject, "policy": self.policy}


class PresetStore:
    """Scans one or more directories for *.yaml preset files."""

    def __init__(self, presets_dirs: list[Path]) -> None:
        self.presets_dirs = [Path(p) for p in presets_dirs]

    def list(self) -> list[Preset]:
        presets = []
        seen: set[str] = set()
        for directory in self.presets_dirs:
            if not Path(directory).exists():
                continue
            for path in sorted(Path(directory).glob("*.yaml")):
                preset = load_preset_file(path)
                if preset.id in seen:
                    continue
                seen.add(preset.id)
                presets.append(preset)
        return presets

    def get(self, preset_id: str) -> Preset | None:
        for preset in self.list():
            if preset.id == preset_id:
                return preset
        return None


def load_preset_file(path: Path) -> Preset:
    with Path(path).open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return Preset(
        id=Path(path).stem,
        name=str(raw.get("name") or Path(path).stem),
        kind=str(raw.get("kind", "")),
        adapter=str(raw.get("adapter", "")),
        workflow=str(raw.get("workflow", "")),
        defaults=dict(raw.get("defaults") or {}),
        inputs=list(raw.get("inputs") or []),
        inject=list(raw.get("inject") or []),
        policy=dict(raw.get("policy") or {}),
        source_file=Path(path),
    )


def resolve_params(preset: Preset, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Merge defaults + overrides, validate against the declared inputs."""
    values: dict[str, Any] = dict(preset.defaults)
    declared = {i["name"]: i for i in preset.inputs if isinstance(i, dict) and "name" in i}
    for key, value in (overrides or {}).items():
        if declared and key not in declared:
            raise ValueError(f"unknown parameter: {key}")
        values[key] = value
    for name, spec in declared.items():
        if spec.get("required") and name not in values:
            raise ValueError(f"missing required parameter: {name}")
        value = values.get(name)
        if value is None:
            continue
        if spec.get("type") == "seed":
            values[name] = int(value)
        if "max" in spec and isinstance(value, str) and len(value) > int(spec["max"]):
            raise ValueError(f"parameter '{name}' exceeds max length {spec['max']}")
    return values


def apply_injection(workflow: dict[str, Any], inject: list[dict[str, Any]],
                    values: dict[str, Any]) -> None:
    """Write values into workflow node inputs per the declared inject mappings.

    Data-driven by design: the kernel/engine never knows what a node means.
    """
    for mapping in inject:
        node = str(mapping["node"])
        field = mapping["field"]
        source = mapping.get("from", field)
        if node not in workflow:
            raise KeyError(f"injection target node '{node}' not present in workflow")
        if source not in values:
            raise KeyError(f"injection source '{source}' missing in resolved params")
        workflow[node].setdefault("inputs", {})[field] = values[source]
