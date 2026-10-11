import pytest

from ai_butler.kernel.tasks import TaskStore
from ai_butler.presets import (
    Preset,
    PresetStore,
    apply_injection,
    load_preset_file,
    resolve_params,
)
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture()
def snow_cat_preset():
    return load_preset_file(REPO / "configs" / "presets" / "snow-cat-t2v.yaml")


# ---------- preset loading ----------

def test_load_builtin_preset(snow_cat_preset):
    assert snow_cat_preset.id == "snow-cat-t2v"
    assert snow_cat_preset.adapter == "comfyui"
    assert snow_cat_preset.workflow == "workflows/snow_cat_t2v.json"
    assert any(m["node"] == "15" and m["field"] == "text" for m in snow_cat_preset.inject)


def test_preset_store_finds_builtin():
    store = PresetStore([REPO / "configs" / "presets"])
    ids = {p.id for p in store.list()}
    assert "snow-cat-t2v" in ids
    assert store.get("snow-cat-t2v") is not None
    assert store.get("nope") is None


# ---------- resolve_params ----------

def test_resolve_params_merges_defaults(snow_cat_preset):
    values = resolve_params(snow_cat_preset, {"prompt": "test"})
    assert values["prompt"] == "test"
    assert values["seed"] == 42          # default
    assert values["length"] == 81        # default


def test_resolve_params_seed_coerced_to_int(snow_cat_preset):
    values = resolve_params(snow_cat_preset, {"prompt": "p", "seed": "777"})
    assert values["seed"] == 777 and isinstance(values["seed"], int)


def test_resolve_params_missing_required():
    # the .example variant declares prompt required with NO default
    preset = load_preset_file(REPO / "configs" / "presets" / "snow-cat-t2v.example.yaml")
    with pytest.raises(ValueError, match="missing required parameter: prompt"):
        resolve_params(preset, {})


def test_resolve_params_unknown_key(snow_cat_preset):
    with pytest.raises(ValueError, match="unknown parameter"):
        resolve_params(snow_cat_preset, {"prompt": "p", "nonsense": 1})


def test_resolve_params_max_length(snow_cat_preset):
    with pytest.raises(ValueError, match="exceeds max length"):
        resolve_params(snow_cat_preset, {"prompt": "x" * 5000})


# ---------- apply_injection ----------

def test_apply_injection_writes_workflow_inputs(snow_cat_preset):
    import json
    workflow = json.loads((REPO / "configs" / "workflows" / "snow_cat_t2v.json").read_text(encoding="utf-8"))
    values = resolve_params(snow_cat_preset, {"prompt": "hello snow", "seed": 1234, "length": 49})
    apply_injection(workflow, snow_cat_preset.inject, values)
    assert workflow["15"]["inputs"]["text"] == "hello snow"
    assert workflow["30"]["inputs"]["noise_seed"] == 1234
    assert workflow["17"]["inputs"]["length"] == 49


def test_apply_injection_unknown_node(snow_cat_preset):
    with pytest.raises(KeyError, match="not present in workflow"):
        apply_injection({"99": {"inputs": {}}}, snow_cat_preset.inject, {"prompt": "x", "seed": 1, "length": 1})


def test_apply_injection_missing_source(snow_cat_preset):
    with pytest.raises(KeyError, match="missing in resolved params"):
        apply_injection({"15": {"inputs": {}}, "30": {"inputs": {}}, "17": {"inputs": {}}},
                        snow_cat_preset.inject, {})


# ---------- TaskStore ----------

def test_task_store_lifecycle(tmp_path):
    store = TaskStore(tmp_path / "t.db")
    store.create("t1", kind="text-to-video", payload={"preset": "x"})
    assert store.get("t1")["status"] == "queued"
    store.set_status("t1", "running")
    store.set_status("t1", "done", result={"files": ["a.mp4"]})
    task = store.get("t1")
    assert task["status"] == "done"
    assert task["result"] == {"files": ["a.mp4"]}
    assert [t["id"] for t in store.recent_tasks(5)] == ["t1"]


def test_task_store_invalid_transition(tmp_path):
    store = TaskStore(tmp_path / "t.db")
    store.create("t2", kind="k")
    with pytest.raises(ValueError, match="invalid transition"):
        store.set_status("t2", "done")  # queued -> done is illegal


def test_task_store_unknown_task(tmp_path):
    store = TaskStore(tmp_path / "t.db")
    with pytest.raises(KeyError, match="unknown task"):
        store.set_status("nope", "running")
