import pytest

from ai_butler.runtime_manager import (
    RuntimeManager,
    detect_hardware,
    parse_gpu_json,
    parse_os_json,
    parse_vram_registry_json,
)


# ---------- parse functions ----------

def test_parse_gpu_json_single():
    text = '{"Name":"AMD Radeon RX 9070","DriverVersion":"32.0.31041.1004","AdapterRAM":4095}'
    gpus = parse_gpu_json(text)
    assert len(gpus) == 1
    assert gpus[0]["name"] == "AMD Radeon RX 9070"
    assert gpus[0]["adapter_ram_bytes"] == 4095  # uint32 cap visible, qwMemorySize compensates


def test_parse_gpu_json_array_and_empty():
    arr = parse_gpu_json('[{"Name":"A"},{"Name":"B"}]')
    assert [g["name"] for g in arr] == ["A", "B"]
    assert parse_gpu_json("") == []


def test_parse_vram_registry_uncapped():
    text = '[{"HardwareInformation.qwMemorySize": [17163034624]}, {"HardwareInformation.qwMemorySize": null}]'
    values = parse_vram_registry_json(text)
    assert values == [17163034624, None]


def test_parse_os_json():
    ram = parse_os_json('{"TotalVisibleMemorySize": 33554432, "FreePhysicalMemory": 16777216}')
    assert ram["total_bytes"] == 33554432 * 1024
    assert ram["free_bytes"] == 16777216 * 1024


# ---------- detect_hardware with fake runner ----------

def test_detect_hardware_with_fake_runner(tmp_path):
    outputs = {
        0: '{"Name":"AMD Radeon RX 9070","DriverVersion":"32.0","AdapterRAM":4095}',
        1: '[{"HardwareInformation.qwMemorySize": 17163034624}]',
        2: '{"TotalVisibleMemorySize": 33554432, "FreePhysicalMemory": 16777216}',
    }

    def runner(cmd):
        # identify which query by a marker in the command text
        joined = " ".join(cmd)
        if "Win32_VideoController" in joined:
            return 0, outputs[0]
        if "qwMemorySize" in joined:
            return 0, outputs[1]
        if "Win32_OperatingSystem" in joined:
            return 0, outputs[2]
        return 1, ""

    hw = detect_hardware(runner, data_home=tmp_path)
    assert hw["gpus"][0]["name"] == "AMD Radeon RX 9070"
    assert hw["gpus"][0]["vram_bytes"] == 17163034624  # uncapped 16GB
    assert hw["ram"]["total_bytes"] == 33554432 * 1024
    assert len(hw["disks"]) >= 1


def test_detect_hardware_degrades_on_runner_failure(tmp_path):
    def runner(cmd):
        return 1, "boom"
    hw = detect_hardware(runner, data_home=tmp_path)
    assert hw["gpus"] == []
    assert hw["ram"]["total_bytes"] is None


# ---------- RuntimeManager lifecycle (injected spawner/killer/checker) ----------

class FakeConfig:
    def __init__(self, runtimes):
        self.runtimes = runtimes


class FakeRegistry:
    def __init__(self):
        self.events = []
    def append(self, kind, payload=None):
        self.events.append((kind, payload))


@pytest.fixture()
def manager():
    cfg = FakeConfig({
        "ollama": {"url": "http://127.0.0.1:11434",
                   "start_command": ["ollama", "serve"]},
        "comfyui": {"url": "http://127.0.0.1:8188"},  # read-only: no start_command
    })
    spawned, killed = [], []
    alive = set()

    def spawn(cmd, log_path):
        spawned.append(cmd)
        pid = 4000 + len(spawned)
        alive.add(pid)
        return pid

    def kill(pid):
        killed.append(pid)
        alive.discard(pid)

    mgr = RuntimeManager(cfg, FakeRegistry(),
                         spawner=spawn, killer=kill,
                         pid_checker=lambda pid: pid in alive)
    mgr._spawned, mgr._killed = spawned, killed
    return mgr


def test_start_requires_start_command(manager):
    with pytest.raises(ValueError, match="read-only"):
        manager.start("comfyui")


def test_start_and_stop_roundtrip(manager):
    result = manager.start("ollama")
    assert result["ok"] is True
    pid = result["pid"]
    assert manager._spawned == [["ollama", "serve"]]
    assert manager._pid_alive(pid)
    stop = manager.stop("ollama")
    assert stop["ok"] is True and stop["was_alive"] is True
    assert manager._killed == [pid]
    assert "ollama" not in manager.managed


def test_stop_unmanaged_refuses(manager):
    with pytest.raises(ValueError, match="not started by this session"):
        manager.stop("ollama")


def test_double_start_reports_existing(manager):
    first = manager.start("ollama")
    second = manager.start("ollama")
    assert second["already_running"] is True
    assert second["pid"] == first["pid"]
    assert len(manager._spawned) == 1  # no second spawn


def test_stop_after_process_died_is_clean(manager):
    result = manager.start("ollama")
    pid = result["pid"]
    manager._pid_alive = lambda p: False  # process died on its own
    stop = manager.stop("ollama")
    assert stop["was_alive"] is False
    assert manager._killed == []  # nothing to kill
