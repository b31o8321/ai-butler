"""CLI: butler serve / status / check / service."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from . import __version__
from .config import default_home, load_gateway, load_manifest


def _resolve(path_str: str | None, name: str) -> Path:
    if path_str:
        return Path(path_str)
    home = default_home()
    candidate = home / name
    if candidate.exists():
        return candidate
    dev = Path(__file__).resolve().parents[2] / "configs" / name
    return dev if dev.exists() else candidate


async def _status_async(args) -> int:
    from .adapters import build_adapters
    from .config import load_gateway

    cfg = load_gateway(_resolve(args.gateway, "gateway.yaml"))
    lines = []
    for adapter in build_adapters(cfg):
        probe = await adapter.probe() if adapter.enabled else {"ok": False, "detail": "disabled"}
        mark = "OK  " if probe.get("ok") else "DOWN"
        models = ""
        if probe.get("models"):
            models = f"  models={len(probe['models'])}"
        lines.append(f"[{mark}] {adapter.name:14s} {probe.get('detail', '')}{models}")
    print(f"ai-butler v{__version__}  gateway={cfg.source_file}")
    print("\n".join(lines))
    return 0


def _cmd_serve(args) -> int:
    import uvicorn

    from .api import create_app
    from .config import load_gateway
    from .kernel.registry import Registry

    cfg = load_gateway(_resolve(args.gateway, "gateway.yaml"))
    home = Path(args.home or default_home())
    registry = Registry(home / "data" / "butler.db")
    app = create_app(cfg, registry)
    port = args.port or cfg.server.port
    registry.append("serve_started", {"port": port})
    uvicorn.run(app, host=cfg.server.host, port=port, log_level="info")
    return 0


def _cmd_check(args) -> int:
    cfg = load_gateway(_resolve(args.gateway, "gateway.yaml"))
    manifest = load_manifest(_resolve(args.manifest, "manifest.yaml"))
    comfy_root = None
    for rt in manifest.runtimes.values():
        if rt.get("models_root"):
            comfy_root = Path(rt["models_root"])
    if args.models_root:
        comfy_root = Path(args.models_root)
    print(f"manifest v{manifest.version}: {len(manifest.models)} model entries")
    missing = 0
    for m in manifest.models:
        if m.runtime:
            print(f"  [RUNTIME ] {m.name}  -> runtime '{m.runtime}'")
            continue
        expected = comfy_root / m.dir / m.filename if comfy_root else None
        present = expected.exists() if expected else None
        state = "PRESENT" if present else ("UNKNOWN" if present is None else "MISSING")
        if not present:
            missing += 1
        loc = str(expected) if expected else "(未配置 models_root)"
        print(f"  [{state:8s}] {m.name:22s} {m.kind:16s} {loc}")
    print(f"\nsummary: {len(manifest.models) - missing}/{len(manifest.models)} present, {missing} missing")
    return 1 if missing and args.strict else 0


def _cmd_service(args) -> int:
    """Register/unregister a logon scheduled task (Windows)."""
    if sys.platform != "win32":
        print("service install currently supports Windows only (S1)")
        return 1
    import subprocess

    task = "ai-butler"
    if args.action == "install":
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        cmd = f'"{pythonw}" -m ai_butler serve --port {args.port}'
        run = f'"{sys.executable}"' if not pythonw.exists() else f'"{pythonw}"'
        cmd = f"{run} -m ai_butler serve --port {args.port}"
        result = subprocess.run(
            ["schtasks", "/Create", "/TN", task, "/TR", cmd, "/SC", "ONLOGON", "/F"],
            capture_output=True, text=True)
        print(result.stdout or result.stderr)
        return result.returncode
    if args.action == "uninstall":
        result = subprocess.run(["schtasks", "/Delete", "/TN", task, "/F"],
                                capture_output=True, text=True)
        print(result.stdout or result.stderr)
        return result.returncode
    print("unknown action")
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="butler", description="personal AI gateway & runtime butler")
    parser.add_argument("--version", action="version", version=f"ai-butler {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="start the gateway + console")
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--gateway", default=None, help="path to gateway.yaml")
    serve.add_argument("--home", default=None, help="data home (db/state)")
    serve.set_defaults(func=_cmd_serve)

    status = sub.add_parser("status", help="probe all adapters")
    status.add_argument("--gateway", default=None)
    status.set_defaults(func=lambda a: asyncio.run(_status_async(a)))

    check = sub.add_parser("check", help="compare manifest against local environment")
    check.add_argument("--gateway", default=None)
    check.add_argument("--manifest", default=None)
    check.add_argument("--models-root", default=None, help="ComfyUI models dir override")
    check.add_argument("--strict", action="store_true", help="exit 1 when anything is missing")
    check.set_defaults(func=_cmd_check)

    service = sub.add_parser("service", help="manage logon autostart (Windows)")
    service.add_argument("action", choices=["install", "uninstall"])
    service.add_argument("--port", type=int, default=9000)
    service.set_defaults(func=_cmd_service)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
