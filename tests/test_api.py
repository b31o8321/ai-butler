import pytest
from fastapi.testclient import TestClient

from ai_butler.api import create_app
from ai_butler.config import load_gateway
from ai_butler.kernel.registry import Registry

REPO = __import__("pathlib").Path(__file__).resolve().parents[1]


@pytest.fixture()
def client(tmp_path):
    cfg = load_gateway(REPO / "configs" / "gateway.example.yaml")
    cfg.server.port = 9111
    reg = Registry(tmp_path / "test.db")
    app = create_app(cfg, reg)
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["app"] == "ai-butler"
    assert body["adapters"] == 3


def test_admin_status_shape(client):
    r = client.get("/admin/status")
    assert r.status_code == 200
    body = r.json()
    names = {a["name"] for a in body["adapters"]}
    assert {"comfyui", "ollama", "openai_compat"} <= names
    # probes will mostly fail in CI (no runtimes) — shape matters, not ok flags
    for a in body["adapters"]:
        assert "ok" in a and "detail" in a
    assert isinstance(body["events"], list)


def test_admin_status_records_event(client):
    client.get("/admin/status")
    r2 = client.get("/admin/status")
    assert len(r2.json()["events"]) >= 1


def test_v1_models_stub(client):
    r = client.get("/v1/models")
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "list"
    ids = {m["id"] for m in body["data"]}
    assert "butler/comfyui" in ids


def test_probe_unknown_adapter_404(client):
    assert client.post("/admin/probe/nope").status_code == 404


def test_console_served(client):
    r = client.get("/console/")
    assert r.status_code == 200
    assert "ai-butler" in r.text
