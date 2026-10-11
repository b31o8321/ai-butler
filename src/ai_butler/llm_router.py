"""LLM alias routing: stable alias -> backend+model, with fallback chain.

The router is transport-agnostic: it talks to adapters that implement
chat()/chat_stream(). Alias map and fallback chain are pure configuration.
"""
from __future__ import annotations

from typing import Any, AsyncIterator


class LLMRouter:
    def __init__(self, adapters: list[Any], aliases: dict[str, dict[str, str]],
                 prefer: list[str], fallback: list[str]) -> None:
        # index by adapter name AND its alias (openai_compat also answers to
        # its runtime key, e.g. 'lmstudio')
        self.adapters: dict[str, Any] = {}
        for a in adapters:
            self.adapters[a.name] = a
            if getattr(a, "alias", None):
                self.adapters.setdefault(a.alias, a)
        self.aliases = aliases
        self.prefer = list(prefer)
        self.fallback = list(fallback)

    def resolve(self, model_alias: str) -> list[tuple[Any, str]]:
        """Return the ordered (adapter, model_name) attempt chain for an alias.

        Explicit alias -> its declared backend first, then configured fallback
        adapters (same model name — works when backends share naming).
        Unknown alias -> prefer list then fallback list with the alias as model.
        """
        attempts: list[tuple[str, str]] = []
        spec = self.aliases.get(model_alias)
        if spec:
            attempts.append((spec["backend"], spec["model"]))
        else:
            attempts.extend((b, model_alias) for b in self.prefer)
        attempts.extend((b, model_alias) for b in self.fallback
                        if b not in [a[0] for a in attempts])
        chain = []
        for backend, model_name in attempts:
            adapter = self.adapters.get(backend)
            if adapter is not None and getattr(adapter, "enabled", True):
                chain.append((adapter, model_name))
        return chain

    async def chat(self, model_alias: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
        """Non-streaming chat with fallback chain. Raises RuntimeError when all fail."""
        errors: list[str] = []
        for adapter, model_name in self.resolve(model_alias):
            try:
                return await adapter.chat(model_name, messages)
            except Exception as exc:
                errors.append(f"{adapter.name}: {type(exc).__name__}: {exc}"[:160])
        raise RuntimeError("all LLM backends failed -> " + " | ".join(errors))

    async def chat_stream(self, model_alias: str,
                          messages: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
        """Streaming chat yielding OpenAI chunk payloads; commits to the first
        backend that starts streaming, falls back only on connection errors."""
        errors: list[str] = []
        for adapter, model_name in self.resolve(model_alias):
            stream = adapter.chat_stream(model_name, messages)
            try:
                first = await stream.__anext__()
            except StopAsyncIteration:
                continue
            except Exception as exc:
                errors.append(f"{adapter.name}: {type(exc).__name__}: {exc}"[:160])
                continue
            async for chunk in _chain_first(stream, first):
                yield chunk
            return
        raise RuntimeError("all LLM backends failed -> " + " | ".join(errors))


async def _chain_first(rest: AsyncIterator[Any], first: Any) -> AsyncIterator[Any]:
    yield first
    async for item in rest:
        yield item
