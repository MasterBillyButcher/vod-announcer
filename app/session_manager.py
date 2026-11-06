"""Session grouping (the 60-minute rule) and announcement scheduling.

Business rules implemented here
--------------------------------
1.  A session groups the recordings of one stream day.  The public
    streamrecorder.io page only shows *dates* (no clock times), so the
    practical grouping key is `<channel>|<date>`.  When real timestamps
    become available (`ended_at` set from a logged-in source), the wait
    timer uses the true end time.
2.  The timer resets whenever a new recording joins the session: the
    session is announced only after `wait_minutes` of quiet.
3.  A session is announced immediately when a recording with a newer
    date appears (the previous day is definitely over) - late discovery
    and overlapping/segmented recordings therefore cannot stall it.
4.  Sessions already announced are never announced twice.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.config import Config
from app.logsetup import get_logger
from app.models import Recording, Session
from app.state_manager import StateManager
from app.timeutil import from_iso, iso, now_utc

log = get_logger()


class SessionManager:
    def __init__(self, state: StateManager, cfg: Config):
        self.state = state
        self.cfg = cfg

    # ------------------------------------------------------- registering
    def register(self, rec: Recording) -> Session:
        """Add a recording to its session (creating/extending as needed)."""
        key = self.state.session_key(rec.channel, rec.rec_date)
        session = self.state.get_session(key)
        if rec.source_id not in session.recording_ids:
            session.recording_ids.append(rec.source_id)
            log.info("recording %s joined session %s (parts: %d)",
                     rec.source_id, rec.rec_date, len(session.recording_ids))

        # Timer: newest known end time, else newest discovery time.
        candidates = [rec.ended_at or rec.discovered_at]
        for other in self.state.recordings_in_session(session):
            candidates.append(other.ended_at or other.discovered_at)
        session.last_activity = max(candidates)

        self.state.update_recording(rec)
        self.state.update_session(session)
        return session

    # ----------------------------------------------------------- timing
    def _ready_at(self, session: Session) -> datetime | None:
        base = from_iso(session.last_activity)
        if base is None:
            return None
        return base + timedelta(minutes=self.cfg.wait_minutes)

    def _newer_date_exists(self, session: Session) -> bool:
        for other in self.state.sessions_for(session.channel):
            if other.rec_date > session.rec_date:
                return True
        return False

    # ------------------------------------------------------------- due
    def due_sessions(self) -> list[Session]:
        """Sessions ready to be announced (oldest first)."""
        now = now_utc()
        due: list[Session] = []
        for raw in self.state.data["sessions"].values():
            session = Session.from_dict(raw)
            if session.announced or session.skipped or not session.recording_ids:
                continue
            ready = self._ready_at(session)
            if (ready and now >= ready) or self._newer_date_exists(session):
                due.append(session)
        due.sort(key=lambda s: (s.rec_date, s.channel))
        if due:
            log.info("sessions due for announcement: %s", [s.key for s in due])
        return due

    # ------------------------------------------------------------ marking
    def mark_announced(self, session: Session, message_url: str = "") -> None:
        session.announced = True
        session.announced_at = iso(now_utc())
        session.message_url = message_url
        self.state.update_session(session)

    def mark_skipped(self, session: Session, reason: str = "") -> None:
        """Permanently retire a session without announcing it (e.g. it fell
        outside the freshness window while its parts were blocked)."""
        session.skipped = True
        session.skip_reason = reason
        self.state.update_session(session)

    # -------------------------------------------------------- completeness
    def session_summary(self, session: Session) -> dict:
        recordings = self.state.recordings_in_session(session)
        recordings.sort(key=lambda r: r.source_id)
        uploaded = [r for r in recordings if r.link]
        return {
            "session": session,
            "recordings": recordings,
            "uploaded": uploaded,
        }
