"""Time helpers.

All *display* times use the project timezone (IST / Asia/Kolkata) while all
internal timestamps are stored as ISO-8601 strings in UTC with offset info.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc


def project_tz(tz_name: str = "Asia/Kolkata") -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("Asia/Kolkata")


def now_utc() -> datetime:
    return datetime.now(UTC)


def to_tz(dt: datetime, tz_name: str = "Asia/Kolkata") -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(project_tz(tz_name))


def iso(dt: datetime) -> str:
    """ISO-8601 with UTC offset, second precision."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def from_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


# --- formatting rules fixed by the project spec ---------------------------

def format_date_long(d: date) -> str:
    """`2025-10-08` -> `October 8` (full month name, day without leading zero)."""
    months = [
        "January", "February", "March", "April", "May", "June", "July",
        "August", "September", "October", "November", "December",
    ]
    return f"{months[d.month - 1]} {d.day}"


def format_duration(total_seconds: int | float | None) -> str:
    """`8524` -> `2h 22m 4s` - always all three units, zero included."""
    if total_seconds is None:
        return "unknown"
    total = int(total_seconds)
    if total < 0:
        total = 0
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m {s}s"


# --- parsing helpers for streamrecorder.io ---------------------------------

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def parse_site_date(text: str) -> date | None:
    """Parse `Nov 05, 2026` / `October 8, 2025` into a date."""
    import re

    m = re.search(r"([A-Za-z]{3,9})\s+(\d{1,2}),\s*(\d{4})", text or "")
    if not m:
        return None
    month = _MONTHS.get(m.group(1)[:3].lower())
    if month is None:
        return None
    try:
        return date(int(m.group(3)), month, int(m.group(2)))
    except ValueError:
        return None


def parse_hhmmss(text: str) -> int | None:
    """Parse `01:42:04` (or `42:04`) into seconds."""
    import re

    m = re.match(r"^(?:(\d+):)?(\d{1,2}):(\d{2})$", (text or "").strip())
    if not m:
        return None
    h = int(m.group(1) or 0)
    minutes = int(m.group(2))
    seconds = int(m.group(3))
    if minutes > 59 or seconds > 59:
        return None
    return h * 3600 + minutes * 60 + seconds


def within_days(d: date, ref: date, days: int) -> bool:
    """True when `d` falls within the last `days` days before `ref`."""
    return (ref - d).days <= max(0, days - 1) and d <= ref
