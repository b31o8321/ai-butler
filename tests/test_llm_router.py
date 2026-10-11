"""S6 LLM routing tests: format conversion, fallback chain, streaming, API."""
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from ai_butler.adapters.ollama import OllamaAdapter
from ai_butler.adapters.openai_compat import OpenAICompatAdapter
from ai_butler.api import create_app
from ai_butler.config import load_gateway
from ai_butler.kernel.registry import Registry
from ai_butler.llm_router import LLMRouter

REPO = Path(__file__).resolve().parents[1]


def _ollama_adapter(handler):
    return OllamaAdapter({"url": "http://ollama-mock"}, transport=httpx.MockTransport(handler))


def _lmstudio_adapter(handler):
    return OpenAICompatAdapter({"url": "http://lms-mock/v1"}, transport=httpx.MockTransport(handler))


# ---------- format conversion ----------

@pytest.mark.anyio
async def test_ollama_chat_converted_to_openai_format():
    def handler(request):
        assert request.url.path == "/api/chat"
        return httpx.Response(200, json={
            "model": "qwen3:14b", "message": {"role": "assistant", "content": "浣犲ソ"},
            "done": True, "eval_count": 42})
    adapter = _ollama_adapter(handler)
    result = await adapter.chat("qwen3:14b", [{"role": "user", "content": "hi"}])
    assert result["object"] == "chat.completion"
    assert result["choices"][0]["message"]["content"] == "浣犲ソ"
    assert result["usage"]["completion_tokens"] == 42


@pytest.mark.anyio
async def test_openai_compat_chat_passthrough():
    def handler(request):
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(200, json={"object": "chat.completion",
                                         "choices": [{"message": {"content": "ok"}}]})
    adapter = _lmstudio_adapter(handler)
    result = await adapter.chat("m", [{"role": "user", "content": "hi"}])
    assert result["choices"][0]["message"]["content"] == "ok"


# ---------- router ----------

def _router_with(ollama_handler=None, lms_handler=None, aliases=None, prefer=None, fallback=None):
    adapters = []
    if ollama_handler is not None:
        adapters.append(_ollama_adapter(ollama_handler))
    if lms_handler is not None:
        adapters.append(_lmstudio_adapter(lms_handler))
    return LLMRouter(adapters, aliases or {}, prefer or [], fallback or [])


@pytest.mark.anyio
async def test_router_alias_resolves_to_declared_backend():
    def handler(request):
        return httpx.Response(200, json={"message": {"content": "b"}, "done": True})
    router = _router_with(ollama_handler=handler,
                          aliases={"qwen3-14b": {"backend": "ollama", "model": "qwen3:14b"}})
    result = await router.chat("qwen3-14b", [{"role": "user", "content": "hi"}])
    assert result["choices"][0]["message"]["content"] == "b"


@pytest.mark.anyio
async def test_router_falls_back_when_preferred_down():
    def dead(request):
        raise httpx.ConnectError("refused")
    def alive(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "fallback"}}]})
    router = _router_with(ollama_handler=dead, lms_handler=alive,
                          aliases={"m": {"backend": "ollama", "model": "m-name"}},
                          fallback=["openai_compat"])
    result = await router.chat("m", [{"role": "user", "content": "hi"}])
    assert result["choices"][0]["message"]["content"] == "fallback"


@pytest.mark.anyio
async def test_router_all_backends_fail():
    def dead(request):
        raise httpx.ConnectError("refused")
    router = _router_with(ollama_handler=dead, lms_handler=dead,
                          prefer=["ollama"], fallback=["openai_compat"])
    with pytest.raises(RuntimeError, match="all LLM backends failed"):
        await router.chat("unknown-alias", [{"role": "user", "content": "hi"}])


@pytest.mark.anyio
async def test_router_stream_yields_chunks():
    def handler(request):
        assert request.url.path == "/api/chat"
        return httpx.Response(200, text='\n'.join([
            json.dumps({"message": {"content": "你"}, "done": False}),
            json.dumps({"message": {"content": "好"}, "done": True})]))
    router = _router_with(ollama_handler=handler,
                          aliases={"qwen3-14b": {"backend": "ollama", "model": "qwen3:14b"}})
    chunks = [c async for c in router.chat_stream("qwen3-14b", [{"role": "user", "content": "hi"}])]
    assert len(chunks) == 3  # two content chunks + [DONE]
    assert chunks[-1] == "data: [DONE]\n\n"
    assert '"delta"' in chunks[0]


# ---------- API endpoints ----------

@pytest.fixture()
def llm_client(tmp_path):
    def ollama_dead(request):
        raise httpx.ConnectError("refused")
    def lms_ok(request):
        if request.url.path == "/v1/chat/completions":
            return httpx.Response(200, json={"object": "chat.completion",
                                             "model": request.headers.get("host"),
                                             "choices": [{"index": 0,
                                                          "message": {"role": "assistant",
                                                                      "content": " routed"},
                                                          "finish_reason": "stop"}]})
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(404)
    cfg = load_gateway(REPO / "configs" / "gateway.example.yaml")
    adapters = [_lmstudio_adapter(lms_ok), _ollama_adapter(ollama_dead)]
    with TestClient(create_app(cfg, Registry(tmp_path / "db.db"),
                               runtime_manager=None, executor=lambda p, x: {},
                               llm_router=LLMRouter(adapters, cfg.llm_aliases,
                                                    cfg.routes.llm_prefer,
                                                    cfg.routes.llm_fallback))) as c:
        yield c


def test_v1_models_lists_aliases(llm_client):
    r = llm_client.get("/v1/models")
    ids = {m["id"] for m in r.json()["data"]}
    assert "qwen3-14b" in ids


def test_chat_completions_via_alias(llm_client):
    r = llm_client.post("/v1/chat/completions", json={
        "model": "qwen3-14b", "messages": [{"role": "user", "content": "浣犲ソ"}]})
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == " routed"


def test_chat_completions_requires_model_and_messages(llm_client):
    assert llm_client.post("/v1/chat/completions", json={"model": "qwen3-14b"}).status_code == 400
    assert llm_client.post("/v1/chat/completions", json={"messages": []}).status_code == 400


def test_chat_completions_stream_sse(llm_client):
    class FakeStreamRouter:
        async def chat_stream(self, model, messages):
            for piece in ["data: {\"a\":1}\n\n", "data: [DONE]\n\n"]:
                yield piece
    llm_client.app.state.llm_router = FakeStreamRouter()
    with llm_client.stream("POST", "/v1/chat/completions",
                           json={"model": "qwen3-14b", "stream": True,
                                 "messages": [{"role": "user", "content": "hi"}]}) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        body = b"".join(resp.iter_bytes()).decode()
        assert body.endswith("data: [DONE]\n\n")
