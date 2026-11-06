"""savedly.net upload client.

Verified live against the real API (2026-11):

* single-request upload (files <= ~90 MB):
    POST {base}/api/upload           raw body, header `X-Filename: name`
    -> 200 {"id","url","name","size","protected","deleteToken"}
  (with `?plain` the response is just the link)

* chunked upload (any size; the website itself uses this for > 90 MB):
    POST {base}/api/upload/init      JSON {"name","size","type","password"?}
    -> {"id","uploadId","partSize","parts"}      (observed partSize = 50 MiB)
    PUT  {base}/api/upload/part?id=..&uploadId=..&n=<1-based>
      body = raw chunk bytes
    -> {"partNumber","etag"}
    POST {base}/api/upload/complete  JSON {"id","uploadId","parts":[{"partNumber","etag"}..]}
    -> {"id","url","name","size","protected","deleteToken"}

* limits: 90 MB single request, 500 files/h, 3000 files/day, 200 GB/day
* retention: files with no views for 7 days are deleted automatically
"""

from __future__ import annotations

import json
import os
import time

import requests

from app.logsetup import get_logger

log = get_logger()


class UploadError(RuntimeError):
    pass


class SavedlyClient:
    def __init__(self, base: str = "https://savedly.net", user_agent: str | None = None,
                 http_timeout: int = 120, part_retries: int = 3):
        self.base = base.rstrip("/")
        self.http_timeout = http_timeout
        self.part_retries = part_retries
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent or (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Origin": self.base,
            "Referer": f"{self.base}/",
        })

    # ----------------------------------------------------------- public API
    def upload_file(self, path: str, display_name: str) -> dict:
        """Upload `path`; returns the API response dict (contains `url`)."""
        size = os.path.getsize(path)
        if size <= 90 * 1024 * 1024:
            try:
                return self._direct_upload(path, display_name)
            except UploadError as exc:
                log.warning("direct upload failed (%s) - retrying via chunked path", exc)
        return self._chunked_upload(path, display_name)

    def upload_file_chunked(self, path: str, display_name: str) -> dict:
        return self._chunked_upload(path, display_name)

    # ------------------------------------------------------------ internals
    def _direct_upload(self, path: str, display_name: str) -> dict:
        size = os.path.getsize(path)
        log.info("savedly: direct upload %s (%.1f MiB)", display_name, size / 1024 / 1024)
        with open(path, "rb") as fh:
            resp = self.session.post(
                f"{self.base}/api/upload",
                data=fh,
                headers={"X-Filename": display_name},
                timeout=max(self.http_timeout, 900),
            )
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "30"))
            raise UploadError(f"rate limited (429), retry after {retry_after}s")
        if resp.status_code >= 400:
            raise UploadError(f"direct upload HTTP {resp.status_code}: {resp.text[:200]}")
        data = self._json(resp)
        self._validate_response(data, display_name, size)
        log.info("savedly: uploaded -> %s", data["url"])
        return data

    def _chunked_upload(self, path: str, display_name: str) -> dict:
        size = os.path.getsize(path)
        log.info("savedly: chunked upload %s (%.1f MiB)", display_name, size / 1024 / 1024)

        init_resp = self.session.post(
            f"{self.base}/api/upload/init",
            data=json.dumps({"name": display_name, "size": size,
                             "type": "video/mp4"}).encode(),
            headers={"content-type": "application/json"},
            timeout=self.http_timeout,
        )
        if init_resp.status_code == 429:
            raise UploadError("rate limited (429) on init")
        if init_resp.status_code >= 400:
            raise UploadError(f"init failed HTTP {init_resp.status_code}: {init_resp.text[:200]}")
        init = SavedlyClient._json_any(init_resp)
        for key in ("id", "uploadId", "partSize", "parts"):
            if key not in init:
                raise UploadError(f"init response missing {key!r}: {str(init)[:200]}")
        fid, upload_id = init["id"], init["uploadId"]
        part_size = int(init["partSize"])
        expected_parts = int(init["parts"])
        log.info("savedly: init ok id=%s partSize=%.0f MiB parts=%d",
                 fid, part_size / 1024 / 1024, expected_parts)

        parts: list[dict] = []
        with open(path, "rb") as fh:
            for n in range(1, expected_parts + 1):
                fh.seek((n - 1) * part_size)
                chunk = fh.read(part_size)
                if len(chunk) != min(part_size, size - (n - 1) * part_size):
                    raise UploadError("file changed size during upload - aborting")
                etag = self._put_part(fid, upload_id, n, chunk)
                parts.append({"partNumber": etag["partNumber"], "etag": etag["etag"]})
                log.info("savedly: part %d/%d done (%.0f MiB)",
                         n, expected_parts, len(chunk) / 1024 / 1024)

        comp_resp = self.session.post(
            f"{self.base}/api/upload/complete",
            data=json.dumps({"id": fid, "uploadId": upload_id, "parts": parts}).encode(),
            headers={"content-type": "application/json"},
            timeout=self.http_timeout,
        )
        if comp_resp.status_code >= 400:
            raise UploadError(f"complete failed HTTP {comp_resp.status_code}: {comp_resp.text[:200]}")
        data = self._json(comp_resp)
        self._validate_response(data, display_name, size)
        log.info("savedly: uploaded -> %s", data["url"])
        return data

    def _put_part(self, fid: str, upload_id: str, number: int, chunk: bytes) -> dict:
        url = (f"{self.base}/api/upload/part"
               f"?id={fid}&uploadId={upload_id}&n={number}")
        delay = 2
        for attempt in range(1, self.part_retries + 1):
            try:
                resp = self.session.put(url, data=chunk, timeout=max(self.http_timeout, 600))
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", "30"))
                    log.warning("savedly: 429 on part %d, sleeping %ss", number, retry_after)
                    time.sleep(retry_after)
                    continue
                if resp.status_code >= 400:
                    raise UploadError(f"part {number} HTTP {resp.status_code}: {resp.text[:200]}")
                return SavedlyClient._json_any(resp)
            except (requests.RequestException, UploadError, ValueError) as exc:
                log.warning("savedly: part %d attempt %d/%d failed: %s",
                            number, attempt, self.part_retries, exc)
                if attempt >= self.part_retries:
                    raise UploadError(f"part {number} failed after {attempt} attempts") from exc
                time.sleep(delay)
                delay *= 2
        raise UploadError(f"part {number} failed after {self.part_retries} attempts")

    @staticmethod
    def _json_any(resp: requests.Response) -> dict:
        try:
            data = resp.json()
        except ValueError as exc:
            raise UploadError(f"unexpected non-JSON response: {resp.text[:200]}") from exc
        if not isinstance(data, dict):
            raise UploadError(f"unexpected response shape: {str(data)[:200]}")
        return data

    @staticmethod
    def _json(resp: requests.Response) -> dict:
        data = SavedlyClient._json_any(resp)
        if "url" not in data:
            raise UploadError(f"unexpected response shape: {str(data)[:200]}")
        return data

    @staticmethod
    def _validate_response(data: dict, display_name: str, size: int) -> None:
        if data.get("size") not in (None, size):
            log.warning("savedly: server size %s != local %s", data.get("size"), size)
        if not str(data.get("url", "")).startswith("http"):
            raise UploadError("server did not return a usable url")
