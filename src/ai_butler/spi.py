"""Adapter SPI: the kernel's only contract with the outside world.

Kernel code must never import concrete adapters; it talks to these
interfaces and to the adapter registry only.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import httpx

PROBE_TIMEOUT = 5.0


class BaseAdapter(ABC):
    """One adapter = one family of runtimes/providers.

    probe(): is it reachable / healthy right now?
    sync():  make the environment match the manifest (S3+).
    execute(): run one preset/turn against it (S4+).
    """

    name: str = "base"

    def __init__(self, config: dict[str, Any], settings: dict[str, Any] | None = None,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.config = dict(config or {})
        self.settings = dict(settings or {})
        self._transport = transport  # injectable for tests

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", True))

    @abstractmethod
    async def probe(self) -> dict[str, Any]:
        """Return {ok: bool, detail: str, **extra}."""

    async def sync(self, manifest: Any) -> dict[str, Any]:  # pragma: no cover - S3
        raise NotImplementedError(f"{self.name} sync lands in S3")

    async def execute(self, preset: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError(f"{self.name} execute lands in S4+")


async def http_probe_json(url: str, client: httpx.AsyncClient | None = None,
                          transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    """GET a JSON endpoint and normalize the probe result."""
    owned = client is None
    # trust_env=False: local runtimes must never be routed through a system
    # proxy (Clash TUN turns unreachable loopback targets into HTTP 502s).
    client = client or httpx.AsyncClient(timeout=PROBE_TIMEOUT, transport=transport,
                                         trust_env=False)
    try:
        resp = await client.get(url)
        resp.raise_for_status()
        payload = resp.json()
        return {"ok": True, "detail": "reachable", "payload": payload}
    except httpx.HTTPStatusError as exc:
        return {"ok": False, "detail": f"HTTP {exc.response.status_code}"}
    except Exception as exc:  # connection refused/timeout/DNS
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"[:200]}
    finally:
        if owned:
            await client.aclose()
