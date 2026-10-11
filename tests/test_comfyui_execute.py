"""ComfyUI adapter execute() end-to-end against a mock ComfyUI HTTP server."""
import json
from pathlib import Path

import httpx
import pytest

from ai_butler.adapters.comfyui import ComfyUIAdapter

WORKFLOW = json.loads(
    (__import__("pathlib").Path(__file__).resolve().parents[1]
     / "configs" / "workflows" / "snow_cat_t2v.json").read_text(encoding="utf-8"))

PRESET = {
    "id": "snow-cat-t2v", "name": "x", "kind": "text-to-video", "adapter": "comfyui",
    "workflow": "workflows/snow_cat_t2v.json",
    "defaults": {"seed": 42, "length": 81},
    "inputs": [{"name": "prompt", "type": "string", "required": True},
               {"name": "seed", "type": "seed"}, {"name": "length", "type": "integer"}],
    "inject": [{"node": "15", "field": "text", "from": "prompt"},
               {"node": "30", "field": "noise_seed", "from": "seed"},
               {"node": "17", "field": "length", "from": "length"}],
}


def _mock_comfyui(handler_state):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            body = json.loads(request.content)
            handler_state["workflow"] = body["prompt"]
            return httpx.Response(200, json={"prompt_id": "mock-123"})
        if request.url.path.startswith("/history/"):
            if not handler_state.get("completed"):
                handler_state["completed"] = True   # first poll: still running
                return httpx.Response(200, json={})
            return httpx.Response(200, json={"mock-123": {
                "status": {"status_str": "success", "completed": True},
                "outputs": {"34": {"images": [{"filename": "butler_00001.mp4",
                                               "subfolder": "video", "type": "output"}]}}}})
        if request.url.path == "/view":
            return httpx.Response(200, content=b"MP4BYTES")
        return httpx.Response(404)
    return httpx.MockTransport(handler)


@pytest.mark.anyio
async def test_execute_end_to_end(tmp_path):
    state = {}
    adapter = ComfyUIAdapter({"url": "http://mock"}, transport=_mock_comfyui(state))
    result = await adapter.execute(PRESET, {"prompt": "a cat in snow"},
                                   workflows_dirs=[__import__("pathlib").Path(__file__).resolve().parents[1] / "configs"],
                                   media_dir=tmp_path, poll_interval=0.05, timeout=10)
    assert result["status"] == "done"
    assert result["prompt_id"] == "mock-123"
    assert len(result["files"]) == 1
    fetched = result["files"][0]
    fetched_path = Path(fetched)
    assert fetched_path.is_file() and fetched_path.suffix == ".mp4"
    assert fetched_path.parent.parent == tmp_path  # media/<task-hex>/file layout
    # injection actually reached the workflow posted to ComfyUI
    assert state["workflow"]["15"]["inputs"]["text"] == "a cat in snow"
    assert state["workflow"]["30"]["inputs"]["noise_seed"] == 42
    assert state["workflow"]["17"]["inputs"]["length"] == 81


@pytest.mark.anyio
async def test_execute_surfaces_comfyui_error(tmp_path):
    state = {"fail": True}

    def handler(request):
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "m2"})
        if request.url.path.startswith("/history/"):
            if not state.get("reported"):
                state["reported"] = True
                return httpx.Response(200, json={})
            return httpx.Response(200, json={"m2": {
                "status": {"status_str": "error", "completed": False,
                           "messages": [["execution_error", {"node_type": "KSampler"}]]}}})
        return httpx.Response(404)

    adapter = ComfyUIAdapter({"url": "http://mock"}, transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="comfyui execution failed"):
        await adapter.execute(PRESET, {"prompt": "x"},
                              workflows_dirs=[Path(__file__).resolve().parents[1] / "configs"],
                              media_dir=tmp_path, poll_interval=0.05, timeout=10)
