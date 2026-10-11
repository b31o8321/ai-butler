"""S3 sync engine tests: URL builders, download with resume, sha256, source picking.

Uses a real local HTTP server (Range-capable) instead of mocks so the
resume path is exercised end-to-end.
"""
import hashlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ai_butler.config import ModelSource
from ai_butler.sync import (
    build_source_url,
    download_file,
    pick_source,
    sync_entry,
    sync_missing,
)


# ---------- local Range-capable HTTP server fixture ----------

class RangeHandler(BaseHTTPRequestHandler):
    store_dir = None          # set by fixture
    requests_log = None

    def do_GET(self):
        RangeHandler.requests_log.append(dict(self.headers))
        file_path = RangeHandler.store_dir / self.path.lstrip("/")
        if not file_path.is_file():
            self.send_error(404)
            return
        data = file_path.read_bytes()
        range_header = self.headers.get("Range")
        if range_header and range_header.startswith("bytes="):
            start = int(range_header.split("=")[1].split("-")[0])
            chunk = data[start:]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(data)-1}/{len(data)}")
        else:
            chunk = data
            self.send_response(200)
        self.send_header("Content-Length", str(len(chunk)))
        self.end_headers()
        self.wfile.write(chunk)

    def log_message(self, *args):
        pass


@pytest.fixture()
def http_server(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    RangeHandler.store_dir = store
    RangeHandler.requests_log = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), RangeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", store
    server.shutdown()


# ---------- URL builders ----------

def test_build_source_url_modelscope():
    src = ModelSource(type="modelscope", repo="Laxhar/noobai-XL-1.1",
                      file="NoobAI-XL-v1.1.safetensors")
    assert build_source_url(src) == \
        "https://www.modelscope.cn/models/Laxhar/noobai-XL-1.1/resolve/master/NoobAI-XL-v1.1.safetensors"


def test_build_source_url_hf_path_quoted():
    src = ModelSource(type="hf", repo="jaysowen/wan2.2-nsfw-loras",
                      path="wan2.2 high\\NSFW_HN.safetensors")
    url = build_source_url(src)
    assert url == ("https://huggingface.co/jaysowen/wan2.2-nsfw-loras"
                   "/resolve/main/wan2.2%20high/NSFW_HN.safetensors")


def test_build_source_url_unknown_type():
    import pytest
    with pytest.raises(ValueError, match="unknown source type"):
        build_source_url(ModelSource(type="ftp"))


# ---------- download + resume + sha256 ----------

def test_download_full_and_sha256(http_server, tmp_path):
    base, store = http_server
    payload = b"x" * 300000
    (store / "model.bin").write_bytes(payload)
    dest = tmp_path / "out" / "model.bin"
    sha = hashlib.sha256(payload).hexdigest()
    report = download_file(f"{base}/model.bin", dest, sha256=sha)
    assert report["ok"] and report["size"] == len(payload)
    assert dest.read_bytes() == payload
    assert not dest.with_suffix(".bin.part").exists()


def test_download_resume_from_part(http_server, tmp_path):
    base, store = http_server
    payload = bytes(range(256)) * 2000  # 512KB
    (store / "model.bin").write_bytes(payload)
    dest = tmp_path / "model.bin"
    part = dest.with_suffix(".bin.part")
    part.write_bytes(payload[:200000])  # simulate a partial download

    report = download_file(f"{base}/model.bin", dest)
    assert report["resumed"] is True
    assert dest.read_bytes() == payload
    # a Range request must have been made
    assert any(h.get("Range", "").startswith("bytes=200000") for h in RangeHandler.requests_log)


def test_download_sha_mismatch_removes_part(http_server, tmp_path):
    base, store = http_server
    (store / "bad.bin").write_bytes(b"actual")
    dest = tmp_path / "bad.bin"
    with pytest.raises(RuntimeError, match="sha256 mismatch"):
        download_file(f"{base}/bad.bin", dest, sha256="0" * 64)
    assert not dest.exists()
    assert not dest.with_suffix(".bin.part").exists()


# ---------- source picking ----------

def test_pick_source_prefers_measured_speed(http_server):
    base, _ = http_server
    slow = ModelSource(type="url", url=f"{base}/nonexistent.bin")   # measure -> 0
    fast = ModelSource(type="url", url=f"{base}/model.bin")         # measure -> >0
    (http_server[1] / "model.bin").write_bytes(b"data" * 100000)
    source, url, speeds = pick_source([slow, fast])
    assert url == f"{base}/model.bin"
    assert speeds[url] > 0


def test_pick_source_all_unreachable():
    import pytest
    dead = ModelSource(type="url", url="http://127.0.0.1:9/nothing.bin")
    with pytest.raises(RuntimeError, match="no reachable source"):
        pick_source([dead])


# ---------- sync_entry / sync_missing ----------

def test_sync_entry_present_skips(http_server, tmp_path):
    base, store = http_server
    (store / "m.bin").write_bytes(b"data")
    dest = tmp_path / "models" / "dir" / "m.bin"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"data")
    entry = ModelEntry_for_test("m.bin", sources=[ModelSource(type="url", url=f"{base}/m.bin")])
    result = sync_entry(entry, tmp_path / "models")
    assert result["status"] == "present"


def test_sync_missing_downloads_missing_only(http_server, tmp_path):
    base, store = http_server
    (store / "have.bin").write_bytes(b"a")
    (store / "missing.bin").write_bytes(b"b" * 5000)
    (tmp_path / "models" / "dir").mkdir(parents=True)
    (tmp_path / "models" / "dir" / "have.bin").write_bytes(b"a")

    def entry(name, size_marker):
        return ModelEntry_for_test(name, sources=[ModelSource(type="url", url=f"{base}/{name}")])

    have = ModelEntry_for_test("have.bin", sources=[ModelSource(type="url", url=f"{base}/have.bin")])
    missing = ModelEntry_for_test("missing.bin", sources=[ModelSource(type="url", url=f"{base}/missing.bin")])
    report = sync_missing([have, missing], tmp_path / "models")
    assert report["present"] == 1
    assert report["downloaded"] == 1
    assert (tmp_path / "models" / "dir" / "missing.bin").read_bytes() == b"b" * 5000


def ModelEntry_for_test(filename, sources=None, sha256=None):
    """Build a minimal manifest-like entry; sources default to a url source
    declaring `file` so filename resolution matches the test layout."""
    from ai_butler.config import ModelEntry, ModelSource
    if sources is None:
        sources = []
    for src in sources:
        if src.file is None and src.path is None:
            src.file = filename
    return ModelEntry(name=filename, kind="checkpoint", dir="dir",
                      sources=sources, sha256=sha256)
