"""Tests for state.json persistence: atomicity, recovery, dedupe."""

import json
import os

import pytest

from app.models import Recording
from app.state_manager import StateManager


def make_rec(rid="1", channel="teststreamer", date_str="2025-10-08"):
    return Recording(source_id=rid, channel=channel, title="T",
                     rec_date=date_str, duration_seconds=60,
                     discovered_at="2025-10-08T12:00:00+00:00")


class TestAtomicSave:
    def test_save_creates_file(self, tmp_path):
        path = str(tmp_path / "state.json")
        state = StateManager(path)
        state.add_recording(make_rec())
        state.save()
        assert os.path.exists(path)
        data = json.load(open(path, encoding="utf-8"))
        assert "1" in data["recordings"]

    def test_no_tmp_files_left(self, tmp_path):
        path = str(tmp_path / "state.json")
        StateManager(path).save()
        leftovers = [f for f in os.listdir(tmp_path) if f.startswith(".state-")]
        assert leftovers == []

    def test_roundtrip_preserves_fields(self, tmp_path):
        path = str(tmp_path / "state.json")
        s1 = StateManager(path)
        rec = make_rec()
        rec.filename = "BS_2025-10-08_T"
        rec.link = "https://savedly.net/f/x"
        rec.size_bytes = 123
        s1.add_recording(rec)
        s1.save()

        s2 = StateManager(path)
        loaded = s2.get_recording("1")
        assert loaded.filename == "BS_2025-10-08_T"
        assert loaded.link == "https://savedly.net/f/x"
        assert loaded.size_bytes == 123
        assert loaded.status == "discovered"


class TestRecovery:
    def test_missing_file_starts_fresh(self, tmp_path):
        state = StateManager(str(tmp_path / "nope.json"))
        assert state.data["recordings"] == {}

    def test_corrupt_file_recovered(self, tmp_path):
        path = str(tmp_path / "state.json")
        with open(path, "w") as fh:
            fh.write("{ not valid json !!!")
        state = StateManager(path)
        assert state.data["recordings"] == {}
        # the corrupt file is preserved for inspection
        assert os.path.exists(path + ".corrupt")

    def test_non_dict_root_recovered(self, tmp_path):
        path = str(tmp_path / "state.json")
        with open(path, "w") as fh:
            fh.write("[1, 2, 3]")
        state = StateManager(path)
        assert state.data["recordings"] == {}


class TestDedupe:
    def test_is_new_then_not(self, tmp_path):
        state = StateManager(str(tmp_path / "s.json"))
        assert state.is_new("42")
        state.add_recording(make_rec("42"))
        assert not state.is_new("42")

    def test_update_recording(self, tmp_path):
        state = StateManager(str(tmp_path / "s.json"))
        rec = make_rec("42")
        state.add_recording(rec)
        rec.status = "uploaded"
        rec.link = "https://savedly.net/f/z"
        state.update_recording(rec)
        state.save()
        s2 = StateManager(str(tmp_path / "s.json"))
        assert s2.get_recording("42").status == "uploaded"

    def test_add_same_id_twice_no_crash(self, tmp_path):
        state = StateManager(str(tmp_path / "s.json"))
        state.add_recording(make_rec("42"))
        state.add_recording(make_rec("42"))  # upsert
        assert len(state.data["recordings"]) == 1


class TestSessionHelpers:
    def test_session_key(self):
        assert StateManager.session_key("a", "2025-10-08") == "a|2025-10-08"

    def test_get_session_creates_default(self, tmp_path):
        state = StateManager(str(tmp_path / "s.json"))
        s = state.get_session("a|2025-10-08")
        assert s.channel == "a" and s.rec_date == "2025-10-08"
        assert s.recording_ids == []

    def test_recordings_in_session(self, tmp_path):
        state = StateManager(str(tmp_path / "s.json"))
        state.add_recording(make_rec("1"))
        state.add_recording(make_rec("2"))
        session = state.get_session("teststreamer|2025-10-08")
        session.recording_ids = ["1", "2"]
        state.update_session(session)
        assert len(state.recordings_in_session(session)) == 2

    def test_permanent_failures(self, tmp_path):
        state = StateManager(str(tmp_path / "s.json"))
        rec = make_rec("1")
        rec.attempts = 5
        rec.status = "failed:uploading"
        state.add_recording(rec)
        session = state.get_session("teststreamer|2025-10-08")
        session.recording_ids = ["1"]
        state.update_session(session)
        failed = state.permanent_failures(session, max_attempts=5)
        assert [r.source_id for r in failed] == ["1"]

    def test_uploaded_not_counted_as_failure(self, tmp_path):
        state = StateManager(str(tmp_path / "s.json"))
        rec = make_rec("1")
        rec.status = "uploaded"
        rec.link = "https://savedly.net/f/x"
        state.add_recording(rec)
        session = state.get_session("teststreamer|2025-10-08")
        session.recording_ids = ["1"]
        state.update_session(session)
        assert state.permanent_failures(session, 5) == []
