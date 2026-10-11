import sys
from pathlib import Path

import yaml

from ai_butler.config import load_gateway, load_manifest

REPO = Path(__file__).resolve().parents[1]


def test_load_gateway_example():
    cfg = load_gateway(REPO / "configs" / "gateway.example.yaml")
    assert cfg.server.port == 9000
    assert cfg.policy.max_concurrent_tasks == 1
    assert cfg.routes.llm_prefer == ["ollama", "lmstudio"]
    assert cfg.adapters["plan"].enabled is False


def test_load_manifest_example():
    m = load_manifest(REPO / "configs" / "manifest.example.yaml")
    names = {entry.name for entry in m.models}
    assert {"noobaiXL_v1.1", "wan22_i2v_14B_high", "qwen3_14b_gm"} <= names
    noobai = next(e for e in m.models if e.name == "noobaiXL_v1.1")
    assert noobai.filename == "NoobAI-XL-v1.1.safetensors"
    assert noobai.sources[0].type == "modelscope"
    # fallback present
    assert len(noobai.sources) >= 2


def test_gateway_missing_file_raises(tmp_path):
    import pytest
    with pytest.raises(FileNotFoundError):
        load_gateway(tmp_path / "nope.yaml")


def test_manifest_tolerates_null_sections(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text(yaml.dump({"version": 1}), encoding="utf-8")
    m = load_manifest(p)
    assert m.models == []
    assert m.version == 1
