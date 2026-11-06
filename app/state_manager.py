"""state.json persistence - atomic writes, corrupt-file recovery, dedupe.

The whole application state lives in a single JSON file that is committed
back to the repository after every run.  That is what makes GitHub Actions
stateless-safe: every run starts from the last committed state.
"""

from __future__ import annotations

import json
import os
import tempfile

from app.logsetup import get_logger
from app.models import Recording, Session
from app.timeutil import iso, now_utc

log = get_logger()

SCHEMA_VERSION = 1
STATUS_ACTIVE = ("discovered", "downloading", "downloaded", "renamed", "uploading")


class StateManager:
    def __init__(self, path: str):
        self.path = path
        self.data: dict = self._load()

    # ------------------------------------------------------------- loading
    def _load(self) -> dict:
        if not os.path.exists(self.path):
            log.info("state file %s not found - starting fresh", self.path)
            return self._empty()
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                raise ValueError("state root is not an object")
            data.setdefault("schema_version", SCHEMA_VERSION)
            data.setdefault("recordings", {})
            data.setdefault("sessions", {})
            data.setdefault("meta", {})
            return data
        except (OSError, ValueError) as exc:
            backup = self.path + ".corrupt"
            try:
                os.replace(self.path, backup)
                log.error("state file was unreadable (%s) - moved to %s and starting fresh", exc, backup)
            except OSError:
                log.error("state file was unreadable (%s) - starting fresh", exc)
            return self._empty()

    @staticmethod
    def _empty() -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "recordings": {},
            "sessions": {},
            "meta": {},
        }

    # ------------------------------------------------------------- saving
    def save(self) -> None:
        """Atomic write: temp file in the same directory, fsync, os.replace."""
        directory = os.path.dirname(os.path.abspath(self.path)) or "."
        os.makedirs(directory, exist_ok=True)
        self.data["meta"]["last_saved"] = iso(now_utc())
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".state-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, ensure_ascii=False, indent=1, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, self.path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        log.info("state saved (%d recordings, %d sessions)", len(self.data["recordings"]), len(self.data["sessions"]))

    # --------------------------------------------------------- recordings
    def is_new(self, source_id: str) -> bool:
        return source_id not in self.data["recordings"]

    def add_recording(self, rec: Recording) -> Recording:
        rec.discovered_at = rec.discovered_at or iso(now_utc())
        if rec.attempts == 0:
            rec.attempts = 0
        self.data["recordings"][rec.source_id] = rec.to_dict()
        return rec

    def get_recording(self, source_id: str) -> Recording | None:
        raw = self.data["recordings"].get(source_id)
        return Recording.from_dict(raw) if raw else None

    def update_recording(self, rec: Recording) -> None:
        self.data["recordings"][rec.source_id] = rec.to_dict()

    def recordings_in_session(self, session: Session) -> list[Recording]:
        out = []
        for rid in session.recording_ids:
            rec = self.get_recording(rid)
            if rec:
                out.append(rec)
        return out

    def permanent_failures(self, session: Session, max_attempts: int) -> list[Recording]:
        failed = []
        for rec in self.recordings_in_session(session):
            if rec.status == "announced" or rec.status == "uploaded":
                continue
            if rec.status == "blocked" or (rec.attempts >= max_attempts and not rec.link):
                failed.append(rec)
        return failed

    # ----------------------------------------------------------- sessions
    @staticmethod
    def session_key(channel: str, rec_date: str) -> str:
        return f"{channel}|{rec_date}"

    def get_session(self, key: str) -> Session:
        raw = self.data["sessions"].get(key)
        if raw:
            return Session.from_dict(raw)
        channel, rec_date = key.split("|", 1)
        return Session(key=key, channel=channel, rec_date=rec_date)

    def update_session(self, session: Session) -> None:
        self.data["sessions"][session.key] = session.to_dict()

    def sessions_for(self, channel: str) -> list[Session]:
        return [
            Session.from_dict(v)
            for k, v in self.data["sessions"].items()
            if v.get("channel") == channel
        ]
