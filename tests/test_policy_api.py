"""S8 API tests: 429 concurrency rejection on preset run."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ai_butler.api import create_app
from ai_butler.config import load_gateway
from ai_butler.kernel.registry import Registry
from ai_butler.kernel.tasks import TaskStore

REPO = Path(__file__).resolve().parents[1]


class SlowExecutor:
    def __call__(self, preset_dict, params):
        import time
        time.sleep(30)  # simulate long generation; test kills server before this ends
        return {"status": "done", "files": []}


def test_preset_run_429_when_gpu_busy(tmp_path):
    cfg = load_gateway(REPO / "configs" / "gateway.example.yaml")
    cfg.policy.max_concurrent_tasks = 1
    registry = Registry(tmp_path / "db.db")
    app = create_app(cfg, registry, executor=SlowExecutor())
    # pre-seed one running GPU task
    app.state.task_store.create("running-1", kind="text-to-video", payload={})
    app.state.task_store.set_status("running-1", "running")
    with TestClient(app) as c:
        r = c.post("/presets/snow-cat-t2v/run", json={"params": {"prompt": "x"}})
        assert r.status_code == 429
        assert "max_concurrent_tasks=1" in r.json()["detail"]


def test_preset_run_200_when_free(tmp_path):
    cfg = load_gateway(REPO / "configs" / "gateway.example.yaml")
    cfg.policy.max_concurrent_tasks = 1
    app = create_app(cfg, Registry(tmp_path / "db2.db"), executor=lambda p, x: {"status": "done", "files": []})
    with TestClient(app) as c:
        r = c.post("/presets/snow-cat-t2v/run", json={"params": {"prompt": "x"}})
        assert r.status_code == 200
