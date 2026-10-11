"""S5 channels tests: loading, diff, install (via local HTTP server fixture)."""
import threading
from pathlib import Path

import pytest
import yaml

from ai_butler.channels import ChannelStore, channel_diff, install_channel, load_channel_file
from ai_butler.config import Manifest, load_manifest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture()
def channel_store():
    return ChannelStore([REPO / "configs" / "channels"])


def test_load_builtin_channels(channel_store):
    channels = {c.id: c for c in channel_store.list()}
    assert "dzmm-pack" in channels
    pack = channels["dzmm-pack"]
    assert "noobaiXL_v1.1" in pack.model_names
    assert "snow-cat-t2v" in pack.presets


def test_channel_diff_states(tmp_path, monkeypatch):
    # manifest with two models: one present, one missing
    manifest_yaml = tmp_path / "manifest.yaml"
    manifest_yaml.write_text(yaml.dump({
        "version": 1,
        "models": [
            {"name": "m_present", "kind": "checkpoint", "dir": "dir",
             "source": {"primary": {"type": "url", "url": "http://127.0.0.1:1/a"}}},
            {"name": "m_missing", "kind": "checkpoint", "dir": "dir",
             "source": {"primary": {"type": "url", "url": "http://127.0.0.1:1/b"}}},
        ]}), encoding="utf-8")
    manifest = load_manifest(manifest_yaml)
    (tmp_path / "models" / "dir").mkdir(parents=True)
    (tmp_path / "models" / "dir" / "m_present.safetensors").write_bytes(b"x")

    channel_yaml = tmp_path / "chan.yaml"
    channel_yaml.write_text(yaml.dump({
        "name": "测试频道", "models": ["m_present", "m_missing"]}), encoding="utf-8")
    channel = load_channel_file(channel_yaml)

    diff = channel_diff(channel, manifest, tmp_path / "models")
    states = {m["name"]: m["state"] for m in diff["models"]}
    assert states == {"m_present": "present", "m_missing": "missing"}
    assert diff["missing_count"] == 1


def test_channel_diff_unknown_model_raises(tmp_path):
    manifest_yaml = tmp_path / "manifest.yaml"
    manifest_yaml.write_text(yaml.dump({"version": 1, "models": []}), encoding="utf-8")
    manifest = Manifest(version=1)
    channel_yaml = tmp_path / "c.yaml"
    channel_yaml.write_text(yaml.dump({"name": "x", "models": ["ghost"]}), encoding="utf-8")
    channel = load_channel_file(channel_yaml)
    with pytest.raises(ValueError, match="absent from manifest"):
        channel_diff(channel, manifest, tmp_path)


def test_install_channel_downloads_missing(tmp_path):
    base, store = _mini_server(tmp_path)
    (store / "missing.bin").write_bytes(b"payload-123")

    manifest_yaml = tmp_path / "manifest.yaml"
    manifest_yaml.write_text(yaml.dump({
        "version": 1,
        "models": [
            {"name": "already_ok", "kind": "checkpoint", "dir": "dir",
             "source": {"primary": {"type": "url", "url": f"{base}/missing.bin"}}},
            {"name": "to_fetch", "kind": "checkpoint", "dir": "dir",
             "source": {"primary": {"type": "url", "url": f"{base}/missing.bin"}}},
        ]}), encoding="utf-8")
    manifest = load_manifest(manifest_yaml)
    (tmp_path / "models" / "dir").mkdir(parents=True)
    (tmp_path / "models" / "dir" / "already_ok.safetensors").write_bytes(b"payload-123")

    channel_yaml = tmp_path / "c.yaml"
    channel_yaml.write_text(yaml.dump({"name": "x", "models": ["already_ok", "to_fetch"]}),
                            encoding="utf-8")
    channel = load_channel_file(channel_yaml)

    events = []
    report = install_channel(channel, manifest, tmp_path / "models",
                             emit=lambda k, p: events.append(k))
    assert report["present"] == 1      # already_ok skipped
    assert report["downloaded"] == 1   # to_fetch fetched
    assert (tmp_path / "models" / "dir" / "to_fetch.safetensors").read_bytes() == b"payload-123"
    assert "download_done" in events


def _mini_server(tmp_path):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    store = tmp_path / "srv_store"
    store.mkdir(exist_ok=True)

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            f = store / self.path.lstrip("/")
            if not f.is_file():
                self.send_error(404)
                return
            data = f.read_bytes()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}", store
