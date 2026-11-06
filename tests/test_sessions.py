"""Tests for session grouping and the 60-minute announcement timer."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from app.config import Config
from app.models import Recording
from app.session_manager import SessionManager
from app.state_manager import StateManager

UTC = timezone.utc


def make_cfg(wait_minutes=60):
    return Config(streamers=["teststreamer"], wait_minutes=wait_minutes)


def make_state(tmp_path):
    return StateManager(str(tmp_path / "state.json"))


def rec(rid, date_str, discovered_offset_min=0, channel="teststreamer"):
    disc = datetime(2025, 10, 8, 12, 0, tzinfo=UTC) + timedelta(minutes=discovered_offset_min)
    return Recording(
        source_id=rid,
        channel=channel,
        title="Test Stream",
        rec_date=date_str,
        duration_seconds=3600,
        discovered_at=disc.isoformat(),
        page_url="https://example.com",
    )


class TestGrouping:
    def test_same_date_same_session(self, tmp_path):
        state, sm = make_state(tmp_path), SessionManager(make_state(tmp_path), make_cfg())
        state = sm.state
        s1 = sm.register(rec("1", "2025-10-08", 0))
        s2 = sm.register(rec("2", "2025-10-08", 10))
        assert s1.key == s2.key
        assert s1.recording_ids == ["1", "2"]

    def test_different_date_different_session(self, tmp_path):
        state = make_state(tmp_path)
        sm = SessionManager(state, make_cfg())
        a = sm.register(rec("1", "2025-10-08", 0))
        b = sm.register(rec("2", "2025-10-09", 500))
        assert a.key != b.key

    def test_no_duplicate_registration(self, tmp_path):
        state = make_state(tmp_path)
        sm = SessionManager(state, make_cfg())
        s = sm.register(rec("1", "2025-10-08", 0))
        s2 = sm.register(rec("1", "2025-10-08", 0))
        assert s2.recording_ids == ["1"]


class TestTimer:
    def test_timer_resets_with_new_recording(self, tmp_path):
        state = make_state(tmp_path)
        sm = SessionManager(state, make_cfg(wait_minutes=60))
        # first recording discovered at T; second at T+30min
        sm.register(rec("1", "2025-10-08", 0))
        session = sm.register(rec("2", "2025-10-08", 30))
        # last_activity must be T+30min -> ready at T+90min
        base = datetime(2025, 10, 8, 12, 30, tzinfo=UTC)
        ready = sm._ready_at(session)
        assert ready == base + timedelta(minutes=60)

    def test_not_due_before_wait(self, tmp_path):
        state = make_state(tmp_path)
        sm = SessionManager(state, make_cfg(wait_minutes=60))
        sm.register(rec("1", "2025-10-08", 0))  # discovered 12:00
        # simulate "now" by monkeypatching now_utc used inside module
        import app.session_manager as module

        fixed_now = datetime(2025, 10, 8, 12, 30, tzinfo=UTC)
        orig = module.now_utc
        module.now_utc = lambda: fixed_now
        try:
            assert sm.due_sessions() == []
        finally:
            module.now_utc = orig

    def test_due_after_wait(self, tmp_path):
        state = make_state(tmp_path)
        sm = SessionManager(state, make_cfg(wait_minutes=60))
        sm.register(rec("1", "2025-10-08", 0))  # discovered 12:00
        import app.session_manager as module

        fixed_now = datetime(2025, 10, 8, 13, 1, tzinfo=UTC)
        orig = module.now_utc
        module.now_utc = lambda: fixed_now
        try:
            due = sm.due_sessions()
            assert [s.key for s in due] == ["teststreamer|2025-10-08"]
        finally:
            module.now_utc = orig

    def test_due_when_newer_date_exists_even_if_timer_young(self, tmp_path):
        state = make_state(tmp_path)
        sm = SessionManager(state, make_cfg(wait_minutes=60))
        sm.register(rec("1", "2025-10-08", 0))       # older day
        sm.register(rec("2", "2025-10-09", 5))       # newer day -> old day must close
        import app.session_manager as module

        fixed_now = datetime(2025, 10, 9, 12, 6, tzinfo=UTC)
        orig = module.now_utc
        module.now_utc = lambda: fixed_now
        try:
            due = [s.key for s in sm.due_sessions()]
            assert "teststreamer|2025-10-08" in due
        finally:
            module.now_utc = orig

    def test_announced_session_not_due_again(self, tmp_path):
        state = make_state(tmp_path)
        sm = SessionManager(state, make_cfg(wait_minutes=0))
        s = sm.register(rec("1", "2025-10-08", 0))
        sm.mark_announced(s)
        import app.session_manager as module

        fixed_now = datetime(2025, 10, 8, 20, 0, tzinfo=UTC)
        orig = module.now_utc
        module.now_utc = lambda: fixed_now
        try:
            assert sm.due_sessions() == []
        finally:
            module.now_utc = orig

    def test_late_discovery_announces_quickly(self, tmp_path):
        """A recording discovered long after it ended (late discovery) must
        be due immediately - the day is definitely over."""
        state = make_state(tmp_path)
        sm = SessionManager(state, make_cfg(wait_minutes=60))
        # recording discovered 12:00 but we run at 23:00
        sm.register(rec("1", "2025-10-08", 0))
        import app.session_manager as module

        fixed_now = datetime(2025, 10, 8, 23, 0, tzinfo=UTC)
        orig = module.now_utc
        module.now_utc = lambda: fixed_now
        try:
            assert len(sm.due_sessions()) == 1
        finally:
            module.now_utc = orig


class TestStatePersistence:
    def test_sessions_survive_reload(self, tmp_path):
        state = make_state(tmp_path)
        sm = SessionManager(state, make_cfg())
        sm.register(rec("1", "2025-10-08", 0))
        state.save()

        state2 = make_state(tmp_path)
        s2 = state2.get_session("teststreamer|2025-10-08")
        assert s2.recording_ids == ["1"]
        assert s2.announced is False

    def test_mark_announced_persists(self, tmp_path):
        state = make_state(tmp_path)
        sm = SessionManager(state, make_cfg())
        s = sm.register(rec("1", "2025-10-08", 0))
        sm.mark_announced(s, "https://discord.com/msg")
        state.save()
        state2 = make_state(tmp_path)
        assert state2.get_session("teststreamer|2025-10-08").announced is True
