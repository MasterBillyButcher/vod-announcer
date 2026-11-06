"""Configuration loaded from environment variables.

Every value has a sensible default so the only REQUIRED secret is
DISCORD_WEBHOOK_URL.  Everything else can optionally be overridden from
GitHub repository secrets (or a local .env when running locally).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


# Cookie names the streamrecorder.io site itself sets (verified from a real
# logged-in browser).  Used to tell "a bare token paste" apart from "a paste
# of some other cookie".
_KNOWN_COOKIE_NAMES = {
    "authorization",
    "bkndsrv",
    "sr_player_theater",
    "srp2_panel",
    "srp2_tour",
}


def _normalize_cookie(raw: str) -> str:
    """Turn any reasonable paste of the session cookie into a Cookie header.

    Accepted forms (each covered by a test):

    * ``PASTE_THE_TOKEN_VALUE_ALONE``       -> wrapped as Authorization=...
      (the most common paste; base64 padding containing '=' works too)
    * ``Bearer eyJ...``                     -> same, prefix kept exactly
    * ``Authorization=...``                 -> fine on its own (bkndsrv optional)
    * ``Authorization=A; bkndsrv=B``        -> the documented full form
    * the whole 7-cookie line from devtools -> extra cookies are harmless
    """
    value = re.sub(r"[\r\n\t]+", "", raw).strip()
    if not value:
        return ""
    pairs = [p.strip() for p in value.split(";") if p.strip()]
    names = [p.split("=", 1)[0].strip().lower() for p in pairs]

    if "authorization" in names:
        fixed = []
        for p in pairs:
            if "=" in p:
                name, _, val = p.partition("=")
                fixed.append(f"{name.strip()}={val.strip()}")
            else:
                fixed.append(p)
        return "; ".join(fixed)

    # No Authorization pair anywhere.  A single chunk that is not one of the
    # site's own cookie names is almost certainly the bare VALUE of the
    # Authorization cookie - wrap it (a 'Bearer ' prefix and internal spaces
    # are kept exactly as the browser stores them).
    if len(pairs) == 1 and names[0] not in _KNOWN_COOKIE_NAMES:
        return f"Authorization={value}"

    return "; ".join(pairs)


@dataclass
class Config:
    # --- what to monitor -------------------------------------------------
    streamers: list[str] = field(default_factory=lambda: ["beaststatsofficial"])

    # --- business rules (defaults fixed by the project spec) -------------
    wait_minutes: int = 60          # session wait timer (minutes)
    backlog_days: int = 2           # today + yesterday ONLY - never announce older
    contact: str = "@bobmasterbillie"

    # --- credentials / endpoints ----------------------------------------
    discord_webhook_url: str = ""
    streamrecorder_cookie: str = ""  # optional: free-account cookie for full downloads
    sr_base: str = "https://streamrecorder.io"
    # optional: your logged-in RECORDINGS page (helps the bot find fresh
    # download links exactly where your browser finds them)
    sr_dashboard_url: str = ""
    savedly_base: str = "https://savedly.net"
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )

    # --- behaviour -------------------------------------------------------
    state_path: str = "state/state.json"
    work_dir: str = "/tmp/vod-announcer"   # ephemeral download dir (runner is wiped)
    timezone: str = "Asia/Kolkata"         # logging + display timezone (IST)
    http_timeout: int = 60                 # seconds for API/page calls
    download_timeout: int = 3600           # seconds for one file download
    download_retries: int = 3
    part_retries: int = 3                  # retries per savedly upload part
    max_recording_attempts: int = 5        # give up a recording after N failed runs
    max_upload_mb: int = 8192              # safety stop (8 GB)
    direct_upload_max_mb: int = 90         # savedly single-request limit
    dry_run: bool = False                  # discover only: no download/upload/announce
    diagnose: bool = False                 # print a source report and exit

    def validate(self) -> list[str]:
        """Return a list of human-readable configuration problems."""
        problems: list[str] = []
        if not self.streamers:
            problems.append("STREAMERS is empty - nothing to monitor")
        if self.wait_minutes < 0:
            problems.append("WAIT_MINUTES must be >= 0")
        if self.backlog_days < 1:
            problems.append("BACKLOG_DAYS must be >= 1")
        if self.discord_webhook_url and not self.discord_webhook_url.startswith(
            ("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/")
        ):
            problems.append("DISCORD_WEBHOOK_URL does not look like a Discord webhook URL")
        # NOTE: STREAMRECORDER_COOKIE problems are deliberately NOT fatal
        # (never exit code 2).  Any reasonable paste is normalized in
        # _normalize_cookie(); a broken cookie just means the bot falls
        # back to capped preview downloads and logs a warning.
        return problems


def load_config() -> Config:
    streamers_raw = _env_str("STREAMERS", "beaststatsofficial")
    streamers = [s.strip().lstrip("@") for s in streamers_raw.split(",") if s.strip()]

    # Secrets are easy to paste wrong (stray newlines, only the token value,
    # with/without the 'Authorization=' name, the whole devtools line ...).
    # _normalize_cookie() cleans all of that up into one usable header.
    raw_cookie = _normalize_cookie(_env_str("STREAMRECORDER_COOKIE", ""))

    return Config(
        streamers=streamers,
        wait_minutes=_env_int("WAIT_MINUTES", 60),
        backlog_days=_env_int("BACKLOG_DAYS", 2),
        contact=_env_str("CONTACT", "@bobmasterbillie"),
        discord_webhook_url=_env_str("DISCORD_WEBHOOK_URL", ""),
        streamrecorder_cookie=raw_cookie,
        sr_dashboard_url=_env_str("SR_DASHBOARD_URL", ""),
        sr_base=_env_str("SR_BASE", "https://streamrecorder.io").rstrip("/"),
        savedly_base=_env_str("SAVEDLY_BASE", "https://savedly.net").rstrip("/"),
        state_path=_env_str("STATE_PATH", "state/state.json"),
        work_dir=_env_str("WORK_DIR", "/tmp/vod-announcer"),
        timezone=_env_str("APP_TZ", "Asia/Kolkata"),
        http_timeout=_env_int("HTTP_TIMEOUT", 60),
        download_timeout=_env_int("DOWNLOAD_TIMEOUT", 3600),
        download_retries=_env_int("DOWNLOAD_RETRIES", 3),
        part_retries=_env_int("PART_RETRIES", 3),
        max_recording_attempts=_env_int("MAX_RECORDING_ATTEMPTS", 5),
        max_upload_mb=_env_int("MAX_UPLOAD_MB", 8192),
        direct_upload_max_mb=_env_int("DIRECT_UPLOAD_MAX_MB", 90),
        dry_run=_env_bool("DRY_RUN"),
        diagnose=_env_bool("DIAGNOSE"),
    )
