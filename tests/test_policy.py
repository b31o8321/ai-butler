"""S8 policy engine tests: GPU exclusive window + concurrency limit."""
import pytest

from ai_butler.kernel.policy import PolicyEngine
from ai_butler.kernel.tasks import TaskStore
from ai_butler.llm_router import LLMRouter


class FakeTaskStore:
    def __init__(self, tasks):
        self._tasks = tasks

    def recent_tasks(self, limit=200):
        return self._tasks[:limit]

    def running_tasks(self):
        return [t for t in self._tasks if t["status"] == "running"]


def _policy(running_kinds, max_concurrent=1):
    tasks = [{"id": f"t{i}", "kind": k, "status": "running"}
             for i, k in enumerate(running_kinds)]
    return PolicyEngine(FakeTaskStore(tasks), {"max_concurrent_tasks": max_concurrent,
                                               "gpu_task_kinds": ["text-to-video", "text-to-image"]})


def test_llm_should_fallback_when_gpu_busy():
    engine = _policy(["text-to-video"])
    blocked, reason = engine.llm_should_fallback()
    assert blocked is True
    assert "text-to-video" in reason


def test_llm_should_fallback_false_when_idle():
    engine = _policy([])
    blocked, reason = engine.llm_should_fallback()
    assert blocked is False


def test_llm_should_fallback_ignores_non_gpu_kinds():
    engine = _policy(["channel-install"])  # network task, not GPU
    blocked, _ = engine.llm_should_fallback()
    assert blocked is False


def test_can_start_gpu_task_respects_max_concurrent():
    engine = _policy([], max_concurrent=1)
    allowed, _ = engine.can_start_gpu_task()
    assert allowed is True
    engine = _policy(["text-to-video"], max_concurrent=1)
    allowed, reason = engine.can_start_gpu_task()
    assert allowed is False
    assert "max_concurrent_tasks=1" in reason


def test_can_start_gpu_task_allows_within_limit():
    engine = _policy(["text-to-video"], max_concurrent=2)
    allowed, _ = engine.can_start_gpu_task()
    assert allowed is True


# ---------- router integration: GPU busy defers primary to fallback ----------

class RecordingAdapter:
    alias = None

    def __init__(self, tag, reply):
        self.name = tag
        self.reply = reply
        self.chat_calls = []

    async def chat(self, model, messages):
        self.chat_calls.append(model)
        return {"choices": [{"message": {"content": self.reply}}]}


def test_router_defers_primary_when_policy_blocks():
    primary = RecordingAdapter("ollama", "primary-reply")
    fallback = RecordingAdapter("openai_compat", "fallback-reply")
    fallback.alias = "lmstudio"

    blocked = {"v": True}
    router = LLMRouter(
        [primary, fallback],
        {"qwen3-14b": {"backend": "ollama", "model": "m"}},
        prefer=[], fallback=["openai_compat"],
        should_skip_primary=lambda: (blocked["v"], "gpu busy"))

    # GPU busy: primary deferred, fallback answers (fallback uses the raw alias
    # as model name — documented semantics for cross-backend fallback)
    import asyncio
    result = asyncio.run(router.chat("qwen3-14b", [{"role": "user", "content": "hi"}]))
    assert result["choices"][0]["message"]["content"] == "fallback-reply"
    assert primary.chat_calls == []
    assert fallback.chat_calls == ["qwen3-14b"]

    # GPU idle: primary answers again
    blocked["v"] = False
    result = asyncio.run(router.chat("qwen3-14b", [{"role": "user", "content": "hi"}]))
    assert result["choices"][0]["message"]["content"] == "primary-reply"
    assert primary.chat_calls == ["m"]


def test_router_policy_blocks_but_no_fallback_uses_primary_degraded():
    primary = RecordingAdapter("ollama", "degraded-primary")
    blocked = {"v": True}
    router = LLMRouter(
        [primary],
        {"qwen3-14b": {"backend": "ollama", "model": "m"}},
        prefer=[], fallback=[],
        should_skip_primary=lambda: (blocked["v"], "gpu busy"))
    import asyncio
    result = asyncio.run(router.chat("qwen3-14b", [{"role": "user", "content": "hi"}]))
    assert result["choices"][0]["message"]["content"] == "degraded-primary"
