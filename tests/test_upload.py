"""Tests for the savedly.net upload client (HTTP fully mocked)."""

import json
import os
from types import SimpleNamespace

import pytest

import app.savedly as savedly_module
from app.savedly import SavedlyClient, UploadError


class FakeResponse:
    def __init__(self, status=200, body=None, text=None, headers=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.text = text if text is not None else json.dumps(self._body)
        self.headers = headers or {}

    def json(self):
        if isinstance(self._body, dict):
            return self._body
        raise ValueError("not json")


class FakeSession:
    """Scripted fake requests.Session: pops scripted responses per call."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def _record(self, method, url, **kw):
        self.calls.append(SimpleNamespace(method=method, url=url, **kw))
        if not self.script:
            raise AssertionError(f"unexpected call: {method} {url}")
        item = self.script.pop(0)
        status, body = item[0], item[1]
        headers = item[2] if len(item) > 2 else {}
        return FakeResponse(status, body, headers=headers)

    def post(self, url, data=None, headers=None, timeout=None, **kw):
        return self._record("POST", url, data=data, headers=headers)

    def put(self, url, data=None, timeout=None, **kw):
        return self._record("PUT", url, data=data)


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(savedly_module.time, "sleep", lambda s: None)


def make_client(script):
    client = SavedlyClient()
    client.session = FakeSession(script)
    return client


def small_file(tmp_path, size=1024):
    p = tmp_path / "BS_2025-10-08_Test.mp4"
    p.write_bytes(b"\0" * size)
    return str(p)


def big_sparse_file(tmp_path, mb=95):
    p = tmp_path / "BS_2025-10-08_Big.mp4"
    with open(p, "wb") as fh:
        fh.seek(mb * 1024 * 1024 - 1)
        fh.write(b"\0")
    return str(p)


class TestDirectUpload:
    def test_small_file_direct(self, tmp_path):
        path = small_file(tmp_path)
        client = make_client([
            (200, {"id": "abc123", "url": "https://savedly.net/f/abc123",
                   "name": "BS_2025-10-08_Test.mp4", "size": 1024,
                   "protected": False, "deleteToken": "tok"}),
        ])
        result = client.upload_file(path, "BS_2025-10-08_Test.mp4")
        assert result["url"] == "https://savedly.net/f/abc123"
        call = client.session.calls[0]
        assert call.url.endswith("/api/upload")
        assert call.headers.get("X-Filename") == "BS_2025-10-08_Test.mp4"

    def test_big_file_uses_chunked(self, tmp_path):
        path = big_sparse_file(tmp_path, mb=95)
        size = os.path.getsize(path)
        part_size = 50 * 1024 * 1024
        parts = (size + part_size - 1) // part_size
        script = [
            (200, {"id": "fid", "uploadId": "uid", "partSize": part_size, "parts": parts}),
        ] + [(200, {"partNumber": n, "etag": f"etag{n}"}) for n in range(1, parts + 1)] + [
            (200, {"id": "fid", "url": "https://savedly.net/f/fid", "name": "x",
                   "size": size, "protected": False, "deleteToken": "t"}),
        ]
        client = make_client(script)
        result = client.upload_file(path, "BS_2025-10-08_Big.mp4")
        assert result["url"] == "https://savedly.net/f/fid"
        methods = [c.method for c in client.session.calls]
        assert methods[0] == "POST" and client.session.calls[0].url.endswith("/api/upload/init")
        assert methods[1] == "PUT" and "n=1" in client.session.calls[1].url
        assert methods[-1] == "POST" and client.session.calls[-1].url.endswith("/api/upload/complete")
        # complete payload carries all etags
        complete_call = client.session.calls[-1]
        payload = json.loads(complete_call.data)
        assert payload["parts"] == [{"partNumber": n, "etag": f"etag{n}"} for n in range(1, parts + 1)]


class TestChunkedProtocol:
    def test_chunk_order_and_content(self, tmp_path):
        path = small_file(tmp_path, size=1024)
        client = make_client([
            (200, {"id": "fid", "uploadId": "uid", "partSize": 512, "parts": 2}),
            (200, {"partNumber": 1, "etag": "e1"}),
            (200, {"partNumber": 2, "etag": "e2"}),
            (200, {"id": "fid", "url": "https://savedly.net/f/fid", "size": 1024}),
        ])
        result = client.upload_file_chunked(path, "BS_2025-10-08_Test.mp4")
        assert result["url"].startswith("https://savedly.net/")
        put1, put2 = client.session.calls[1], client.session.calls[2]
        assert len(put1.data) == 512 and len(put2.data) == 512
        assert "uploadId=uid" in put1.url and "id=fid" in put1.url

    def test_part_retry_then_success(self, tmp_path, no_sleep):
        path = small_file(tmp_path, size=1024)
        client = make_client([
            (200, {"id": "fid", "uploadId": "uid", "partSize": 1024, "parts": 1}),
            (500, {"error": "boom"}),
            (200, {"partNumber": 1, "etag": "e1"}),
            (200, {"id": "fid", "url": "https://savedly.net/f/fid", "size": 1024}),
        ])
        result = client.upload_file_chunked(path, "x.mp4")
        assert result["url"] == "https://savedly.net/f/fid"
        assert len(client.session.calls) == 4

    def test_part_permanent_failure_raises(self, tmp_path, no_sleep):
        path = small_file(tmp_path, size=1024)
        client = make_client([
            (200, {"id": "fid", "uploadId": "uid", "partSize": 1024, "parts": 1}),
            (500, {"error": "boom"}),
            (500, {"error": "boom"}),
            (500, {"error": "boom"}),
        ])
        with pytest.raises(UploadError):
            client.upload_file_chunked(path, "x.mp4")

    def test_rate_limit_429_respected(self, tmp_path, no_sleep, monkeypatch):
        path = small_file(tmp_path, size=1024)
        client = make_client([
            (200, {"id": "fid", "uploadId": "uid", "partSize": 1024, "parts": 1}),
            (429, {"error": "slow down"}, {"Retry-After": "1"}),
            (200, {"partNumber": 1, "etag": "e1"}),
            (200, {"id": "fid", "url": "https://savedly.net/f/fid", "size": 1024}),
        ])
        slept = []
        monkeypatch.setattr(savedly_module.time, "sleep", lambda s: slept.append(s))
        result = client.upload_file_chunked(path, "x.mp4")
        assert result["url"] == "https://savedly.net/f/fid"
        assert 1 in slept

    def test_init_http_error(self, tmp_path, no_sleep):
        path = small_file(tmp_path, size=1024)
        client = make_client([(400, {"error": "empty"})])
        with pytest.raises(UploadError):
            client.upload_file_chunked(path, "x.mp4")

    def test_unexpected_response_shape(self, tmp_path, no_sleep):
        path = small_file(tmp_path, size=1024)
        client = make_client([(200, {"weird": True})])
        with pytest.raises(UploadError):
            client.upload_file_chunked(path, "x.mp4")


class TestDisplayGuard:
    def test_upload_uses_given_display_name(self, tmp_path):
        """The uploader must send the validated BS_ name, not the tmp path."""
        path = small_file(tmp_path)
        client = make_client([
            (200, {"id": "a", "url": "https://savedly.net/f/a", "size": 1024}),
        ])
        client.upload_file(path, "BS_2025-10-08_Renamed.mp4")
        assert client.session.calls[0].headers["X-Filename"] == "BS_2025-10-08_Renamed.mp4"
