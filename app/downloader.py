"""Streaming file download with integrity checks and retries."""

from __future__ import annotations

import hashlib
import os
import time

import requests

from app.logsetup import get_logger

log = get_logger()

_CHUNK = 1024 * 1024  # 1 MiB
_LOG_EVERY = 50 * _CHUNK  # log every 50 MiB


class DownloadError(RuntimeError):
    pass


def _looks_like_html(first_bytes: bytes, content_type: str) -> bool:
    if "text/html" in content_type.lower():
        return True
    head = first_bytes[:512].lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html")


def download_file(
    url: str,
    dest_dir: str,
    basename: str,
    *,
    timeout: int = 3600,
    retries: int = 3,
    max_bytes: int = 0,
    headers: dict | None = None,
) -> tuple[str, int, str]:
    """Stream `url` to `dest_dir/basename`.  Returns (path, size, sha256).

    Raises DownloadError on failure.  Partial files are removed.
    """
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, basename)
    last_exc: Exception | None = None

    for attempt in range(1, retries + 1):
        tmp = dest + ".part"
        try:
            sha = hashlib.sha256()
            size = 0
            started = time.time()
            with requests.get(url, stream=True, timeout=timeout, headers=headers or {}) as resp:
                resp.raise_for_status()
                ctype = resp.headers.get("Content-Type", "")
                it = resp.iter_content(chunk_size=_CHUNK)
                first = next(it, b"")
                if first and _looks_like_html(first, ctype):
                    raise DownloadError(
                        f"got HTML instead of a file (content-type={ctype!r}) - "
                        "the signed URL likely expired or requires a session cookie"
                    )
                with open(tmp, "wb") as fh:
                    if first:
                        fh.write(first)
                        sha.update(first)
                        size += len(first)
                        if max_bytes and size > max_bytes:
                            raise DownloadError(f"file exceeds MAX_UPLOAD_MB ({max_bytes} MB) - aborting")
                    for chunk in it:
                        if not chunk:
                            continue
                        fh.write(chunk)
                        sha.update(chunk)
                        size += len(chunk)
                        if size % _LOG_EVERY < _CHUNK:
                            log.info("downloading %s: %.0f MiB done", basename, size / _CHUNK)
                        if max_bytes and size > max_bytes:
                            raise DownloadError(f"file exceeds MAX_UPLOAD_MB ({max_bytes} MB) - aborting")
            if size == 0:
                raise DownloadError("empty response (0 bytes)")
            elapsed = time.time() - started
            log.info("downloaded %s: %.1f MiB in %.0fs (sha256 %.12s...)",
                     basename, size / _CHUNK, elapsed, sha.hexdigest())
            os.replace(tmp, dest)
            return dest, size, sha.hexdigest()
        except (requests.RequestException, DownloadError, OSError) as exc:
            last_exc = exc
            log.warning("download attempt %d/%d failed: %s", attempt, retries, exc)
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            if attempt < retries:
                time.sleep(min(60, 5 * attempt))
    raise DownloadError(f"download failed after {retries} attempts: {last_exc}")
