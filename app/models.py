"""Runtime data models (state.json stores plain dicts; these wrap them)."""

from __future__ import annotations

from dataclasses import dataclass, field


# Recording status pipeline (strict order enforced by main.py):
#   discovered -> downloading -> downloaded -> renamed -> uploading
#   -> uploaded -> announced
# A failing step records `failed:<step>` plus an attempt counter; the next
# run retries the whole pipeline from the failed step.
STATUSES = (
    "discovered", "downloading", "downloaded", "renamed",
    "uploading", "uploaded", "announced",
)


@dataclass
class Recording:
    source_id: str                 # stable id from streamrecorder (numeric recording id)
    channel: str
    title: str = ""                # stream title as shown on the source page
    rec_date: str = ""             # YYYY-MM-DD as displayed on the source page
    duration_seconds: int | None = None
    ended_at: str | None = None    # ISO UTC, only when the source provides real times
    discovered_at: str = ""        # ISO UTC
    preview_url: str = ""          # signed mp4 URL (latest recording only)
    page_url: str = ""
    status: str = "discovered"
    filename: str = ""             # final BS_... name (without extension)
    size_bytes: int | None = None
    sha256: str = ""
    link: str = ""                 # savedly.net/f/<id>
    delete_token: str = ""         # never logged
    attempts: int = 0
    last_error: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, data: dict) -> "Recording":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class Session:
    key: str                       # "<channel>|<YYYY-MM-DD>"
    channel: str
    rec_date: str                  # YYYY-MM-DD
    recording_ids: list[str] = field(default_factory=list)
    last_activity: str = ""        # ISO UTC: newest known end / discovery of any part
    announced: bool = False
    announced_at: str = ""
    message_url: str = ""          # Discord message link after posting (if returned)
    skipped: bool = False          # True = permanently not announced (too old)
    skip_reason: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, data: dict) -> "Session":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})
