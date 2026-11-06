"""Blocked-recording retry + freshness-window session skipping.

1. A recording with no download URL yet (status `blocked`) must be RETRIED
   on the next run - adding a cookie / SR_DASHBOARD_URL later must rescue
   it, and NoDownloadURLError must not count toward the attempt limit.
2. A session older than the freshness window (today + yesterday) must be
   marked skipped once and never announced or processed again.
"""

import json
import os
from datetime import timedelta

import pytest

import app.main as main_module
from app.config import Config
from app.main import run
from app.models import Recording
from app.sources.streamrecorder import NoDownloadURLError
from app.timeutil import iso, now_utc, to_tz


class FlakySource:
    """get_download_url fails on the FIRST call ever, succeeds afterwards.

    The call counter is class-level because main.run() builds a fresh
    source instance per run, while the flow spans two runs.
    """

    url_calls = 0

    def __init__(self, cfg):
        self.cfg = cfg

    def list_recordings(self, channel):
        return [Recording(
            source_id="222",
            channel=channel,
            title="Retry Stream",
            rec_date=to_tz(now_utc()).date().isoformat(),  # today: inside window
            duration_seconds=10,
            preview_url="https://signed.example/x.mp4?iv=a&enc=b",
            page_url="https://example.com",
            discovered_at=iso(now_utc()),
        )]

    def get_download_url(self, rec):
        FlakySource.url_calls += 1
        if FlakySource.url_calls == 1:
            raise NoDownloadURLError("no url yet")
        return rec.preview_url, "preview"


class OldSource:
    """A recording way outside the freshness window."""

    def __init__(self, cfg):
        self.cfg = cfg

    def list_recordings(self, channel):
        return [Recording(
            source_id="333",
            channel=channel,
            title="Ancient Stream",
            rec_date="2020-01-01",
            duration_seconds=10,
            preview_url="https://signed.example/x.mp4?iv=a&enc=b",
            page_url="https://example.com",
            discovered_at=iso(now_utc()),
        )]

    def get_download_url(self, rec):  # pragma: no cover - must never be reached
        raise AssertionError("skipped sessions must not be processed")


class FixedDateSource:
    """Successful source whose recording date is set per test."""

    rec_date_val = ""

    def __init__(self, cfg):
        self.cfg = cfg

    def list_recordings(self, channel):
        return [Recording(
            source_id="444",
            channel=channel,
            title="Grace Stream",
            rec_date=FixedDateSource.rec_date_val,
            duration_seconds=10,
            preview_url="https://signed.example/x.mp4?iv=a&enc=b",
            page_url="https://example.com",
            discovered_at=iso(now_utc()),
        )]

    def get_download_url(self, rec):
        return rec.preview_url, "preview"


class FakeUploader:
    calls = []

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
def reset_fakes():
    FakeUploader.calls = []
    FlakySource.url_calls = 0
    yield


def make_cfg(tmp_path):
    return Config(
        streamers=["teststreamer"],
        wait_minutes=0,
        backlog_days=2,
        discord_webhook_url="https://discord.com/api/webhooks/1/tok",
        state_path=str(tmp_path / "state.json"),
        work_dir=str(tmp_path / "work"),
    )


@pytest.fixture
def mocks(monkeypatch):
    sent = []
    monkeypatch.setattr(main_module, "SavedlyClient", FakeUploader)
    monkeypatch.setattr(main_module, "download_file", fake_download_file)
    monkeypatch.setattr(main_module, "send",
                        lambda url, messages, **kw: sent.append(messages[0]) or "https://discord.com/msg/1")
    return sent


class TestBlockedRetry:
    def test_blocked_recording_is_retried_next_run(self, tmp_path, mocks, monkeypatch):
        monkeypatch.setattr(main_module, "StreamRecorderSource", FlakySource)
        cfg = make_cfg(tmp_path)

        # run 1: discovery OK, but no download URL -> blocked, NOT announced
        assert run(cfg) == 0
        assert FakeUploader.calls == []
        assert mocks == []
        state = json.load(open(cfg.state_path, encoding="utf-8"))
        rec = state["recordings"]["222"]
        assert rec["status"] == "blocked"
        assert rec["attempts"] == 0, "NoDownloadURLError must not count as an attempt"

        # run 2 ('cookie/dashboard now fixed'): the SAME recording succeeds
        assert run(cfg) == 0
        assert len(FakeUploader.calls) == 1
        assert len(mocks) == 1
        state = json.load(open(cfg.state_path, encoding="utf-8"))
        assert state["recordings"]["222"]["status"] == "uploaded"
        assert state["sessions"]["teststreamer|" + state["recordings"]["222"]["rec_date"]]["announced"] is True


class TestFreshnessWindow:
    def test_session_outside_window_is_skipped_and_never_announced(self, tmp_path, mocks, monkeypatch):
        monkeypatch.setattr(main_module, "StreamRecorderSource", OldSource)
        cfg = make_cfg(tmp_path)

        assert run(cfg) == 0
        assert FakeUploader.calls == []
        assert mocks == []
        state = json.load(open(cfg.state_path, encoding="utf-8"))
        session = state["sessions"]["teststreamer|2020-01-01"]
        assert session["skipped"] is True
        assert "freshness" in session["skip_reason"]

        # a later run must not even consider it due (no processing, no spam)
        assert run(cfg) == 0
        assert FakeUploader.calls == []
        assert mocks == []


class TestGraceWindow:
    def test_skipped_session_is_reopened_within_grace(self, tmp_path, mocks, monkeypatch):
        """A session retired while downloads were broken must be rescued by
        the grace window: discovery re-opens it and it announces normally."""
        from app.state_manager import StateManager

        monkeypatch.setattr(main_module, "StreamRecorderSource", FixedDateSource)
        FixedDateSource.rec_date_val = (
            to_tz(now_utc()).date() - timedelta(days=2)).isoformat()  # inside grace
        cfg = make_cfg(tmp_path)

        # pre-seed state: recording + session exist and were retired earlier
        state = StateManager(cfg.state_path)
        rec = Recording(source_id="444", channel="teststreamer", title="Grace Stream",
                        rec_date=FixedDateSource.rec_date_val, discovered_at=iso(now_utc()))
        state.add_recording(rec)
        sess = state.get_session(state.session_key("teststreamer", FixedDateSource.rec_date_val))
        sess.recording_ids.append("444")
        sess.last_activity = iso(now_utc())  # wait timer already expired
        sess.skipped = True
        sess.skip_reason = "older than the 2-day freshness window"
        state.update_session(sess)
        state.save()

        assert run(cfg) == 0
        data = json.load(open(cfg.state_path, encoding="utf-8"))
        key = f"teststreamer|{FixedDateSource.rec_date_val}"
        assert data["sessions"][key]["skipped"] is False
        assert data["sessions"][key]["skip_reason"] == ""
        assert data["sessions"][key]["announced"] is True
        assert len(mocks) == 1
        assert len(FakeUploader.calls) == 1
