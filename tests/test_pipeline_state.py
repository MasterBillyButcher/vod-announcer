"""Pipeline integration tests with fully mocked external services.

Guarantees the two most expensive failure modes can never happen:
1. a successful upload that is NOT persisted (would re-upload next run)
2. a second announcement/upload for an already-announced session
"""

import os

import pytest

import app.main as main_module
from app.config import Config
from app.main import run
from app.models import Recording
from app.timeutil import iso, now_utc


class FakeSource:
    def __init__(self, cfg):
        self.cfg = cfg
        self.list_calls = 0

    def list_recordings(self, channel):
        self.list_calls += 1
        return [Recording(
            source_id="111",
            channel=channel,
            title="Test Stream",
            rec_date="2025-10-08",
            duration_seconds=10,
            preview_url="https://signed.example/x.mp4?iv=a&enc=b",
            page_url="https://example.com",
            discovered_at=iso(now_utc()),
        )]

    def get_download_url(self, rec):
        return rec.preview_url, "preview"


class FakeUploader:
    calls = []  # class-level so both run() invocations share it

    def __init__(self, *args, **kwargs):
        pass

    def upload_file(self, path, display_name):
        FakeUploader.calls.append(display_name)
        return {"url": f"https://savedly.net/f/{len(FakeUploader.calls)}",
                "deleteToken": "tok"}


def fake_download_file(url, dest_dir, basename, **kwargs):
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, basename)
    with open(path, "wb") as fh:
        fh.write(b"0" * 100)
    return path, 100, "a" * 64


@pytest.fixture(autouse=True)
def reset_uploader():
    FakeUploader.calls = []
    yield


def make_cfg(tmp_path):
    return Config(
        streamers=["teststreamer"],
        wait_minutes=0,
        backlog_days=7,
        discord_webhook_url="https://discord.com/api/webhooks/1/tok",
        state_path=str(tmp_path / "state.json"),
        work_dir=str(tmp_path / "work"),
    )


@pytest.fixture
def mocks(monkeypatch):
    sent = []
    monkeypatch.setattr(main_module, "StreamRecorderSource", FakeSource)
    monkeypatch.setattr(main_module, "SavedlyClient", FakeUploader)
    monkeypatch.setattr(main_module, "download_file", fake_download_file)
    monkeypatch.setattr(main_module, "send",
                        lambda url, messages, **kw: sent.append(messages[0]) or "https://discord.com/msg/1")
    return sent


class TestPipelinePersistence:
    def test_upload_is_persisted_and_not_duplicated(self, tmp_path, mocks):
        cfg = make_cfg(tmp_path)

        # run 1: discovers, downloads, renames, uploads, announces
        assert run(cfg) == 0
        assert len(FakeUploader.calls) == 1
        assert FakeUploader.calls[0] == "BS_2025-10-08_Test_Stream.mp4"

        import json
        state = json.load(open(cfg.state_path, encoding="utf-8"))
        rec = state["recordings"]["111"]
        assert rec["status"] == "uploaded"
        assert rec["link"] == "https://savedly.net/f/1"
        assert rec["filename"] == "BS_2025-10-08_Test_Stream"
        session = state["sessions"]["teststreamer|2025-10-08"]
        assert session["announced"] is True
        assert len(mocks) == 1
        assert "https://savedly.net/f/1" in mocks[0]

        # run 2 (new process simulation: same state file): nothing new
        assert run(cfg) == 0
        assert len(FakeUploader.calls) == 1, "must not re-upload!"
        assert len(mocks) == 1, "must not re-announce!"

    def test_download_then_upload_uses_renamed_file(self, tmp_path, mocks):
        cfg = make_cfg(tmp_path)
        assert run(cfg) == 0
        # the display name given to the uploader IS the validated BS_ name
        assert FakeUploader.calls[0].startswith("BS_2025-10-08_")
