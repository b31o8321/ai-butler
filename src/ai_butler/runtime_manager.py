"""Runtime manager: hardware detection + lifecycle of local runtimes.

Ops-layer module (domain words allowed here). The kernel never imports it.
All process operations are PID-targeted; global kills are forbidden by the
project goal constraints.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

Runner = Callable[[list[str]], tuple[int, str]]


def _run(cmd: list[str], timeout: int = 20) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              creationflags=CREATE_NO_WINDOW)
        return proc.returncode, proc.stdout or ""
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, f"{type(exc).__name__}: {exc}"


# ---------- hardware detection (pure parse functions are unit-testable) ----------

def parse_gpu_json(text: str) -> list[dict[str, Any]]:
    """Parse `Win32_VideoController | Select Name,DriverVersion,AdapterRAM | ConvertTo-Json`."""
    if not text.strip():
        return []
    data = json.loads(text)
    if isinstance(data, dict):
        data = [data]
    gpus = []
    for item in data:
        gpus.append({
            "name": item.get("Name"),
            "driver_version": item.get("DriverVersion"),
            "adapter_ram_bytes": item.get("AdapterRAM"),  # uint32, may cap at 4GB
        })
    return gpus


def parse_vram_registry_json(text: str) -> list[int | None]:
    """Parse HardwareInformation.qwMemorySize entries (uncapped VRAM, per adapter key)."""
    if not text.strip():
        return []
    data = json.loads(text)
    if isinstance(data, dict):
        data = [data]
    out: list[int | None] = []
    for item in data:
        value = None
        if isinstance(item, dict):
            raw = item.get("HardwareInformation.qwMemorySize")
            if isinstance(raw, list) and raw:
                raw = raw[0]
            if isinstance(raw, int) and raw > 0:
                value = raw
        out.append(value)
    return out


def parse_os_json(text: str) -> dict[str, int]:
    data = json.loads(text)
    if isinstance(data, list):
        data = data[0]
    return {
        "total_bytes": int(data.get("TotalVisibleMemorySize", 0)) * 1024,
        "free_bytes": int(data.get("FreePhysicalMemory", 0)) * 1024,
    }


def detect_hardware(runner: Runner, data_home: Path | None = None) -> dict[str, Any]:
    """Snapshot of GPU / RAM / disk. Best-effort: failures degrade to null fields."""
    rc, out = runner(["powershell", "-NoProfile", "-Command",
                      "Get-CimInstance Win32_VideoController | "
                      "Select-Object Name,DriverVersion,AdapterRAM | ConvertTo-Json -Compress"])
    gpus = parse_gpu_json(out) if rc == 0 else []

    rc, out = runner(["powershell", "-NoProfile", "-Command",
                      "$x = Get-ItemProperty 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Class\\"
                      "{4d36e968-e325-11ce-bfc1-08002be10318}\\0*' -ErrorAction SilentlyContinue | "
                      "Select-Object HardwareInformation.qwMemorySize | ConvertTo-Json -Compress; $x"])
    if rc == 0 and gpus:
        vram = parse_vram_registry_json(out)
        for gpu, value in zip(gpus, vram):
            if value:
                gpu["vram_bytes"] = value  # uncapped, more trustworthy than AdapterRAM

    rc, out = runner(["powershell", "-NoProfile", "-Command",
                      "Get-CimInstance Win32_OperatingSystem | "
                      "Select-Object TotalVisibleMemorySize,FreePhysicalMemory | ConvertTo-Json -Compress"])
    ram = parse_os_json(out) if rc == 0 else {"total_bytes": None, "free_bytes": None}

    disks = []
    seen: set[str] = set()
    for path in {Path(data_home or Path.cwd()).anchor or "C:\\", "C:\\"}:
        if path in seen:
            continue
        seen.add(path)
        try:
            usage = shutil.disk_usage(path)
            disks.append({"path": path, "total_bytes": usage.total, "free_bytes": usage.free})
        except OSError:
            pass
    return {"gpus": gpus, "ram": ram, "disks": disks}


# ---------- runtime lifecycle ----------

def _default_spawn(cmd: list[str], log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = open(log_path, "ab")
    proc = subprocess.Popen(cmd, stdout=log, stderr=log, creationflags=CREATE_NO_WINDOW)
    return proc.pid


def _default_kill(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
    else:
        subprocess.run(["kill", "-9", str(pid)], capture_output=True, text=True)


def _default_pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        rc, out = _run(["tasklist", "/FI", f"PID eq {pid}", "/NH"])
        return rc == 0 and str(pid) in out
    rc, out = _run(["kill", "-0", str(pid)])
    return rc == 0


class RuntimeManager:
    """Start/stop local runtimes by PID. ComfyUI instances the user is actively
    using should stay read-only: simply omit `start_command` in the config."""

    def __init__(self, config: Any, registry: Any,
                 adapters: list[Any] | None = None,
                 spawner: Callable[[list[str], Path], int] | None = None,
                 killer: Callable[[int], None] | None = None,
                 pid_checker: Callable[[int], bool] | None = None,
                 data_home: Path | None = None) -> None:
        self.config = config
        self.registry = registry
        self.adapters = {a.name: a for a in (adapters or [])}
        self._spawn = spawner or _default_spawn
        self._kill = killer or _default_kill
        self._pid_alive = pid_checker or _default_pid_alive
        self.data_home = Path(data_home or Path.cwd() / ".data")
        self.managed: dict[str, dict[str, Any]] = {}

    def detect_hardware(self) -> dict[str, Any]:
        return detect_hardware(_run, data_home=self.data_home)

    def _runtime_cfg(self, name: str) -> dict[str, Any]:
        cfg = self.config.runtimes.get(name)
        if cfg is None:
            raise KeyError(f"unknown runtime: {name}")
        return cfg

    def _adapter_for(self, name: str) -> Any | None:
        direct = self.adapters.get(name)
        if direct is not None:
            return direct
        for adapter in self.adapters.values():  # openai_compat alias (e.g. lmstudio)
            if getattr(adapter, "alias", None) == name:
                return adapter
        return None

    async def status_all(self) -> list[dict[str, Any]]:
        return [await self.status(name) for name in self.config.runtimes]

    async def status(self, name: str) -> dict[str, Any]:
        cfg = self._runtime_cfg(name)
        adapter = self._adapter_for(name)
        if adapter is not None:
            probe = await adapter.probe()
            alive = bool(probe.get("ok"))
            detail = probe.get("detail", "")
        else:
            alive = None
            detail = "no probe adapter"
        managed = self.managed.get(name)
        managed_pid = managed["pid"] if managed and self._pid_alive(managed["pid"]) else None
        return {
            "name": name,
            "url": cfg.get("url"),
            "alive": alive,
            "detail": detail,
            "start_configured": isinstance(cfg.get("start_command"), list) and bool(cfg["start_command"]),
            "managed_pid": managed_pid,
        }

    def start(self, name: str) -> dict[str, Any]:
        cfg = self._runtime_cfg(name)
        cmd = cfg.get("start_command")
        if not isinstance(cmd, list) or not cmd:
            raise ValueError(f"runtime '{name}' has no start_command configured (read-only)")
        existing = self.managed.get(name)
        if existing and self._pid_alive(existing["pid"]):
            return {"ok": True, "already_running": True, "pid": existing["pid"]}
        log_path = self.data_home / "logs" / f"runtime-{name}.log"
        pid = self._spawn([str(part) for part in cmd], log_path)
        self.managed[name] = {"pid": pid, "started_at": time.time()}
        self.registry.append("runtime_started", {"name": name, "pid": pid})
        return {"ok": True, "pid": pid, "command": cmd}

    def stop(self, name: str) -> dict[str, Any]:
        if name not in self.managed:
            raise ValueError(f"runtime '{name}' was not started by this session; "
                             "refusing to kill unknown processes")
        pid = self.managed[name]["pid"]
        alive_before = self._pid_alive(pid)
        if alive_before:
            self._kill(pid)
            time.sleep(0.5)
        still = self._pid_alive(pid)
        self.managed.pop(name, None)
        self.registry.append("runtime_stopped", {"name": name, "pid": pid, "still_alive": still})
        return {"ok": not still, "pid": pid, "was_alive": alive_before, "still_alive": still}
