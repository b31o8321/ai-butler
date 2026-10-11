"""Environment sync: manifest-driven model download with mirror selection.

Ops-layer module. Sources are declared data (ModelSource), never kernel code.
Downloads are streaming with .part resume support and optional sha256 verify.
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any, Callable

import httpx

from .config import ModelEntry, ModelSource

DOWNLOAD_CHUNK = 1024 * 1024
SPEED_SAMPLE_BYTES = 262144  # 256KB per candidate mirror
SPEED_TIMEOUT = 12.0

Emit = Callable[[str, dict[str, Any]], None]


def build_source_url(source: ModelSource) -> str:
    """Translate a declared source into a concrete download URL (per-segment quoting)."""
    from urllib.parse import quote
    if source.type not in ("url", "modelscope", "hf", "hf-mirror"):
        raise ValueError(f"unknown source type: {source.type}")
    if source.type == "url":
        if not source.url:
            raise ValueError("url source needs url")
        return source.url
    target = source.path or source.file
    if not target:
        raise ValueError("source needs file or path")
    target = quote(target.replace("\\", "/"), safe="/")
    if source.type == "modelscope":
        if not source.repo:
            raise ValueError("modelscope source needs repo")
        return f"https://www.modelscope.cn/models/{source.repo}/resolve/master/{target}"
    if source.type == "hf":
        if not source.repo:
            raise ValueError("hf source needs repo")
        return f"https://huggingface.co/{source.repo}/resolve/main/{target}"
    return f"https://hf-mirror.com/{source.repo}/resolve/main/{target}"


def measure_speed(url: str, sample_bytes: int = SPEED_SAMPLE_BYTES,
                  timeout: float = SPEED_TIMEOUT) -> float:
    """Bytes/sec over a ranged GET sample; 0.0 when unreachable. Honors env proxy
    (remote mirrors, unlike local runtimes, may legitimately need one)."""
    received = 0
    headers_seen = False
    start = time.monotonic()
    try:
        with httpx.stream("GET", url, headers={"Range": f"bytes=0-{sample_bytes - 1}"},
                          timeout=timeout, follow_redirects=True) as resp:
            resp.raise_for_status()
            headers_seen = True
            try:
                for chunk in resp.iter_bytes(chunk_size=65536):
                    received += len(chunk)
                    if received >= sample_bytes:
                        break
            except (httpx.RemoteProtocolError, httpx.StreamError):
                # Some CDNs declare a longer content-length than the ranged body
                # they actually send for small files; the connection closes early.
                # Bytes yielded before the exception are already counted above.
                pass
        elapsed = time.monotonic() - start
        if received > 0:
            return received / elapsed
        if headers_seen:
            # reachable but nothing yielded: minimal positive score so this
            # source still outranks unreachable ones in mirror selection
            return 1.0 / max(elapsed, 0.001)
        return 0.0
    except Exception:
        return 0.0


def pick_source(sources: list[ModelSource], speeds: dict[str, float] | None = None) -> tuple[ModelSource, str, dict[str, float]]:
    """Pick the fastest reachable source. speeds maps url->bytes/sec when pre-measured
    (tests); otherwise measured live. Returns (source, url, all_speeds)."""
    candidates = []
    all_speeds: dict[str, float] = dict(speeds or {})
    for source in sources:
        url = build_source_url(source)
        if url not in all_speeds:
            all_speeds[url] = measure_speed(url)
        candidates.append((all_speeds[url], source, url))
    candidates.sort(key=lambda item: item[0], reverse=True)
    if not candidates or candidates[0][0] <= 0:
        raise RuntimeError("no reachable source for this entry")
    best_speed, best_source, best_url = candidates[0]
    return best_source, best_url, all_speeds


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(DOWNLOAD_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_file(url: str, dest: Path, *, sha256: str | None = None,
                  resume: bool = True, emit: Emit | None = None) -> dict[str, Any]:
    """Stream url -> dest with .part resume. Returns a report dict; raises on failure."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    start = part.stat().st_size if (resume and part.exists()) else 0
    headers = {"Range": f"bytes={start}-"} if start else {}
    resumed = start > 0
    if emit:
        emit("download_started", {"url": url, "dest": str(dest), "resume_from": start})
    downloaded = 0
    try:
        with httpx.stream("GET", url, headers=headers,
                          timeout=httpx.Timeout(300.0, connect=15.0, read=120.0),
                          follow_redirects=True) as resp:
            if start and resp.status_code == 200:
                # server ignored Range — restart from scratch
                start = 0
                resumed = False
            resp.raise_for_status()
            mode = "ab" if (start and resp.status_code == 206) else "wb"
            with part.open(mode) as f:
                for chunk in resp.iter_bytes(chunk_size=DOWNLOAD_CHUNK):
                    f.write(chunk)
                    downloaded += len(chunk)
    except Exception as exc:
        if emit:
            emit("download_failed", {"url": url, "error": str(exc)[:200]})
        raise RuntimeError(f"download failed ({url}): {exc}") from exc

    size = part.stat().st_size
    if sha256:
        actual = _sha256(part)
        if actual != sha256:
            part.unlink(missing_ok=True)
            if emit:
                emit("download_failed", {"url": url, "error": "sha256 mismatch"})
            raise RuntimeError(f"sha256 mismatch for {url}: expected {sha256}, got {actual}")
    part.replace(dest)
    report = {"ok": True, "url": url, "dest": str(dest), "size": size,
              "downloaded_this_run": downloaded, "resumed": resumed, "verified": bool(sha256)}
    if emit:
        emit("download_done", {k: report[k] for k in ("url", "dest", "size")})
    return report


def sync_entry(entry: ModelEntry, models_root: Path, *,
               emit: Emit | None = None, force: bool = False) -> dict[str, Any]:
    """Ensure one manifest entry: PRESENT (skip), download+verify, or report no-source."""
    dest = models_root / entry.dir / entry.filename
    if dest.exists() and not force:
        if emit:
            emit("sync_present", {"name": entry.name, "dest": str(dest)})
        return {"name": entry.name, "status": "present", "dest": str(dest)}
    if not entry.sources:
        if emit:
            emit("sync_no_source", {"name": entry.name})
        return {"name": entry.name, "status": "missing", "reason": "no source declared",
                "dest": str(dest)}
    source, url, speeds = pick_source(entry.sources)
    if emit:
        emit("sync_source_selected", {"name": entry.name, "url": url,
                                      "speed_bytes_per_s": round(speeds[url], 1)})
    result = download_file(url, dest, sha256=entry.sha256, emit=emit)
    return {"name": entry.name, "status": "downloaded", **result}


def sync_missing(entries: list[ModelEntry], models_root: Path, *,
                 emit: Emit | None = None, only: str | None = None) -> dict[str, Any]:
    """Sync all entries that are not on disk. Returns an aggregate report."""
    results = []
    for entry in entries:
        if only and entry.name != only:
            continue
        try:
            results.append(sync_entry(entry, models_root, emit=emit))
        except Exception as exc:
            results.append({"name": entry.name, "status": "failed", "error": str(exc)[:300]})
    return {
        "total": len(results),
        "present": sum(1 for r in results if r["status"] == "present"),
        "downloaded": sum(1 for r in results if r["status"] == "downloaded"),
        "failed": [r for r in results if r["status"] == "failed"],
        "results": results,
    }
