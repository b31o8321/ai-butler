"""Curated channels: named subsets of the manifest offered as one-click packs.

Channels are pure data (yaml) referencing manifest entries by name 鈥?no new
sync machinery, just filtered views plus presence diffs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .config import Manifest, ModelEntry
from .sync import sync_missing


@dataclass
class Channel:
    id: str
    name: str
    description: str = ""
    model_names: list[str] = field(default_factory=list)
    workflows: list[str] = field(default_factory=list)   # relative to workflows dirs
    presets: list[str] = field(default_factory=list)     # preset ids
    source_file: Path | None = None


class ChannelStore:
    def __init__(self, channels_dirs: list[Path]) -> None:
        self.channels_dirs = [Path(p) for p in channels_dirs]

    def list(self) -> list[Channel]:
        channels: dict[str, Channel] = {}
        for directory in self.channels_dirs:
            if not Path(directory).exists():
                continue
            for path in sorted(Path(directory).glob("*.yaml")):
                channel = load_channel_file(path)
                channels.setdefault(channel.id, channel)
        return list(channels.values())

    def get(self, channel_id: str) -> Channel | None:
        for channel in self.list():
            if channel.id == channel_id:
                return channel
        return None


def load_channel_file(path: Path) -> Channel:
    with Path(path).open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return Channel(
        id=Path(path).stem,
        name=str(raw.get("name") or Path(path).stem),
        description=str(raw.get("description") or ""),
        model_names=[str(m) for m in (raw.get("models") or [])],
        workflows=[str(w) for w in (raw.get("workflows") or [])],
        presets=[str(p) for p in (raw.get("presets") or [])],
        source_file=Path(path),
    )


def _resolve_entries(channel: Channel, manifest: Manifest) -> list[ModelEntry]:
    by_name = {m.name: m for m in manifest.models}
    missing_names = [n for n in channel.model_names if n not in by_name]
    if missing_names:
        raise ValueError(f"channel references models absent from manifest: {missing_names}")
    return [by_name[n] for n in channel.model_names]


def channel_diff(channel: Channel, manifest: Manifest, models_root: Path | None) -> dict[str, Any]:
    """Presence diff for console display: every referenced item and its state."""
    models = []
    for entry in _resolve_entries(channel, manifest):
        if entry.runtime:
            state = "runtime"
            path = None
        elif models_root is None:
            state = "unknown"
            path = None
        else:
            path = models_root / entry.dir / entry.resolved_filename
            state = "present" if path.exists() else "missing"
        models.append({"name": entry.name, "kind": entry.kind, "state": state,
                       "path": str(path) if path else None})
    return {
        "id": channel.id, "name": channel.name, "description": channel.description,
        "models": models,
        "missing_count": sum(1 for m in models if m["state"] == "missing"),
        "workflows": channel.workflows,
        "presets": channel.presets,
    }


def install_channel(channel: Channel, manifest: Manifest, models_root: Path | None, *,
                    emit: Any = None) -> dict[str, Any]:
    """Sync every missing model of the channel. Raises on manifest mismatch."""
    entries = _resolve_entries(channel, manifest)
    if models_root is None:
        raise ValueError("models_root not configured (paths.models_root)")
    return sync_missing(entries, models_root, emit=emit)
