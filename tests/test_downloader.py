"""Tests for the streaming downloader (HTTP mocked)."""

import hashlib

import pytest

import app.downloader as dl
from app.downloader import DownloadError, download_file


class FakeStreamResp:
    def __init__(self, chunks, content_type="video/mp4", status=200):
        self._chunks = chunks
        self.headers = {"Content-Type": content_type}
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=1024 * 1024):
        yield from self._chunks

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestDownload:
    def test_success_and_sha256(self, tmp_path, monkeypatch):
        data = [b"a" * 1024, b"b" * 1024]
        monkeypatch.setattr(dl.requests, "get",
                            lambda *a, **kw: FakeStreamResp(data))
        path, size, sha = download_file("http://x/v.mp4", str(tmp_path), "rec_1.mp4")
        assert size == 2048
        assert path.endswith("rec_1.mp4")
        expected = hashlib.sha256(b"a" * 1024 + b"b" * 1024).hexdigest()
        assert sha == expected
        assert open(path, "rb").read() == b"a" * 1024 + b"b" * 1024
        assert not path.endswith(".part")

    def test_html_response_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dl.requests, "get",
                            lambda *a, **kw: FakeStreamResp([b"<html>403</html>"], content_type="text/html"))
        with pytest.raises(DownloadError):
            download_file("http://x/v.mp4", str(tmp_path), "rec_1.mp4")

    def test_html_sniffed_even_without_content_type(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dl.requests, "get",
                            lambda *a, **kw: FakeStreamResp([b"<!DOCTYPE html><html></html>"], content_type=""))
        with pytest.raises(DownloadError):
            download_file("http://x/v.mp4", str(tmp_path), "rec_1.mp4")

    def test_empty_response_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dl.requests, "get", lambda *a, **kw: FakeStreamResp([]))
        with pytest.raises(DownloadError):
            download_file("http://x/v.mp4", str(tmp_path), "rec_1.mp4")

    def test_retry_then_success(self, tmp_path, monkeypatch):
        calls = {"n": 0}

        def flaky(*a, **kw):
            calls["n"] += 1
            if calls["n"] < 3:
                raise ConnectionError("boom")
            return FakeStreamResp([b"data"])

        monkeypatch.setattr(dl.requests, "get", flaky)
        monkeypatch.setattr(dl.time, "sleep", lambda s: None)
        path, size, _ = download_file("http://x/v.mp4", str(tmp_path), "rec_1.mp4")
        assert size == 4
        assert calls["n"] == 3

    def test_no_part_file_left_after_failure(self, tmp_path, monkeypatch):
        def always_fail(*a, **kw):
            raise ConnectionError("boom")

        monkeypatch.setattr(dl.requests, "get", always_fail)
        monkeypatch.setattr(dl.time, "sleep", lambda s: None)
        with pytest.raises(DownloadError):
            download_file("http://x/v.mp4", str(tmp_path), "rec_1.mp4", retries=2)
        assert list(tmp_path.glob("*.part")) == []

    def test_max_bytes_guard(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dl.requests, "get",
                            lambda *a, **kw: FakeStreamResp([b"x" * (2 * 1024 * 1024)]))
        with pytest.raises(DownloadError):
            download_file("http://x/v.mp4", str(tmp_path), "rec_1.mp4",
                          max_bytes=1024 * 1024)
