import httpx
import pytest

from ai_butler.adapters.comfyui import ComfyUIAdapter
from ai_butler.adapters.ollama import OllamaAdapter
from ai_butler.adapters.openai_compat import OpenAICompatAdapter


def _transport(handler):
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_comfyui_probe_ok():
    def handler(request):
        assert request.url.path == "/system_stats"
        return httpx.Response(200, json={"system": {"comfyui_version": "0.39.0"}})
    adapter = ComfyUIAdapter({"url": "http://127.0.0.1:8188"},
                             transport=_transport(handler))
    result = await adapter.probe()
    assert result["ok"] is True
    assert "0.39.0" in result["detail"]


@pytest.mark.asyncio
async def test_ollama_probe_lists_models():
    def handler(request):
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": "qwen3:14b"}, {"name": "llama3"}]})
    adapter = OllamaAdapter({"url": "http://127.0.0.1:11434"},
                            transport=_transport(handler))
    result = await adapter.probe()
    assert result["ok"] is True
    assert result["models"] == ["qwen3:14b", "llama3"]


@pytest.mark.asyncio
async def test_openai_compat_probe_down():
    def handler(request):
        raise httpx.ConnectError("refused")
    adapter = OpenAICompatAdapter({"url": "http://127.0.0.1:1/v1"},
                                  transport=_transport(handler))
    result = await adapter.probe()
    assert result["ok"] is False
    assert "ConnectError" in result["detail"] or "refused" in result["detail"]
