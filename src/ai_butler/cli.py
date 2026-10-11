"""CLI: butler serve / status / check / service."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from . import __version__
from .config import default_home, load_gateway, load_gateway_optional, load_manifest


def _resolve(path_str: str | None, name: str) -> Path:
    if path_str:
        return Path(path_str)
    home = default_home()
    candidate = home / name
    if candidate.exists():
        return candidate
    dev = Path(__file__).resolve().parents[2] / "configs" / name
    return dev if dev.exists() else candidate


def _load_gateway_soft(path_str: str | None) -> "tuple[Any, Path]":
    path = _resolve(path_str, "gateway.yaml")
    return load_gateway_optional(path), path


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


def _resolve_models_root(args, gateway_cfg, manifest_cfg) -> Path | None:
    """--models-root > gateway.yaml paths.models_root > manifest paths.models_root."""
    if getattr(args, "models_root", None):
        return Path(args.models_root)
    for source in (gateway_cfg, manifest_cfg):
        value = (source.paths or {}).get("models_root")
        if value:
            return Path(value)
    return None


def _cmd_check(args) -> int:
    cfg, gateway_path = _load_gateway_soft(args.gateway)
    manifest = load_manifest(_resolve(args.manifest, "manifest.yaml"))
    models_root = _resolve_models_root(args, cfg, manifest)
    print(f"manifest v{manifest.version}: {len(manifest.models)} model entries")
    print(f"models_root: {models_root or '(未配置)'}")
    missing = 0
    for m in manifest.models:
        if m.runtime:
            print(f"  [RUNTIME ] {m.name}  -> runtime '{m.runtime}'")
            continue
        expected = models_root / m.dir / m.resolved_filename if models_root else None
        present = expected.exists() if expected else None
        state = "PRESENT" if present else ("UNKNOWN" if present is None else "MISSING")
        if not present:
            missing += 1
        loc = str(expected) if expected else "(未配置 models_root)"
        print(f"  [{state:8s}] {m.name:22s} {m.kind:16s} {loc}")
    print(f"\nsummary: {len(manifest.models) - missing}/{len(manifest.models)} present, {missing} missing")
    return 1 if missing and args.strict else 0


def _cmd_sync(args) -> int:
    from .sync import sync_missing

    cfg, gateway_path = _load_gateway_soft(args.gateway)
    manifest = load_manifest(_resolve(args.manifest, "manifest.yaml"))
    models_root = _resolve_models_root(args, cfg, manifest)
    if models_root is None:
        print("FAIL: models_root 未配置（--models-root 或 paths.models_root）")
        return 2

    def emit(kind: str, payload: dict) -> None:
        print(f"  [{kind}] {json.dumps(payload, ensure_ascii=False)[:160]}")

    print(f"sync target: {models_root}" + (f"  (only: {args.only})" if args.only else ""))
    report = sync_missing(manifest.models, models_root, emit=emit, only=args.only)
    print(f"\nsync summary: present={report['present']}  downloaded={report['downloaded']}  "
          f"failed={len(report['failed'])}  total={report['total']}")
    for failure in report["failed"]:
        print(f"  FAILED {failure['name']}: {failure.get('error', '')[:150]}")
    return 0 if not report["failed"] else 1


def _cmd_service(args) -> int:
    """Register/unregister a logon scheduled task (Windows)."""
    if sys.platform != "win32":
        print("service install currently supports Windows only (S1)")
        return 1
    import subprocess

    task = "ai-butler"
    enc = "gbk" if sys.platform == "win32" else "utf-8"
    if args.action == "install":
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        run = f'"{pythonw}"' if pythonw.exists() else f'"{sys.executable}"'
        cmd = f"{run} -m ai_butler serve --port {args.port}"
        result = subprocess.run(
            ["schtasks", "/Create", "/TN", task, "/TR", cmd, "/SC", "ONLOGON", "/F"],
            capture_output=True, text=True, encoding=enc, errors="replace")
        print(result.stdout or result.stderr)
        return result.returncode
    if args.action == "uninstall":
        result = subprocess.run(["schtasks", "/Delete", "/TN", task, "/F"],
                                capture_output=True, text=True, encoding=enc, errors="replace")
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

    sync = sub.add_parser("sync", help="download manifest entries missing from the local environment")
    sync.add_argument("--gateway", default=None)
    sync.add_argument("--manifest", default=None)
    sync.add_argument("--models-root", default=None, help="ComfyUI models dir override")
    sync.add_argument("--only", default=None, help="sync a single model by name")
    sync.set_defaults(func=_cmd_sync)

    service = sub.add_parser("service", help="manage logon autostart (Windows)")
    service.add_argument("action", choices=["install", "uninstall"])
    service.add_argument("--port", type=int, default=9000)
    service.set_defaults(func=_cmd_service)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
