"""Discord webhook announcements.

Message formats (fixed by the project spec):

* date rendered as `October 8` (full month, no year, no leading zero)
* duration rendered as `Xh Xm Xs` - always all three units
* single-part sessions post one download link,
  multi-part sessions post a numbered part list
* messages are split so no single POST exceeds Discord's 2000-char limit
"""

from __future__ import annotations

import json
import time
from datetime import date

import requests

from app.logsetup import get_logger
from app.timeutil import format_date_long, format_duration

log = get_logger()

DISCORD_LIMIT = 1900  # safety margin below the 2000-char hard limit


class DiscordError(RuntimeError):
    pass


def mask_webhook(url: str) -> str:
    """`https://discord.com/api/webhooks/123456/AbC-xYz` -> `.../123456/[REDACTED]`"""
    if "/api/webhooks/" in (url or ""):
        head, _, token = url.rpartition("/")
        return f"{head}/[REDACTED]"
    return "[REDACTED]"


def build_announcement(channel: str, rec_date: str, title: str, parts: list[dict],
                       contact: str = "", warnings: list[str] | None = None) -> list[str]:
    """Build the announcement text (possibly multiple messages).

    `parts` items: {"filename": ..., "duration_seconds": ..., "link": ...}
    """
    try:
        d = date.fromisoformat(rec_date)
        date_str = format_date_long(d)
    except (TypeError, ValueError):
        date_str = rec_date or "unknown date"

    total = sum(p.get("duration_seconds") or 0 for p in parts)
    lines: list[str] = []
    head = [
        "\U0001F3AC **New VOD Posted!**",
        "",
        f"**Stream:** {channel}",
        f"**Date:** {date_str}",
        f"**Title:** {title}",
    ]

    if len(parts) == 1:
        p = parts[0]
        head += [
            f"**Duration:** {format_duration(p.get('duration_seconds'))}",
            "",
            f"\U0001F4E5 **Download:** {p['link']}",
        ]
    else:
        head += [f"**Parts:** {len(parts)}", ""]
        for i, p in enumerate(parts, 1):
            head.append(
                f"**{i}.** {p['filename']} - {format_duration(p.get('duration_seconds'))} - {p['link']}"
            )
        head += ["", f"**Total duration:** {format_duration(total)}"]

    if warnings:
        head += ["", *warnings]
    head += [
        "",
        "_Note: links auto-delete after 7 days without views - download soon._",
    ]
    if contact:
        head.append(f"_Contact: {contact}_")

    text = "\n".join(head)
    return _split_message(text)


def _split_message(text: str) -> list[str]:
    if len(text) <= DISCORD_LIMIT:
        return [text]
    messages: list[str] = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > DISCORD_LIMIT:
            messages.append(current.rstrip())
            current = ""
        current += line + "\n"
    if current.strip():
        messages.append(current.rstrip())
    return messages


def send(webhook_url: str, messages: list[str], http_timeout: int = 30,
         retries: int = 4) -> str:
    """POST each message; returns the last message URL (or '')."""
    if not webhook_url:
        raise DiscordError("DISCORD_WEBHOOK_URL is not configured")
    if not webhook_url.startswith(("https://discord.com/api/webhooks/",
                                   "https://discordapp.com/api/webhooks/")):
        raise DiscordError(f"webhook URL does not look valid: {mask_webhook(webhook_url)}")

    last_url = ""
    for msg in messages:
        payload = json.dumps({"content": msg, "allowed_mentions": {"parse": []}}).encode()
        delay = 2
        posted = False
        for attempt in range(1, retries + 1):
            try:
                resp = requests.post(
                    webhook_url, data=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=http_timeout,
                )
                if resp.status_code == 429:
                    retry_after = float(resp.json().get("retry_after", delay)) \
                        if resp.headers.get("content-type", "").startswith("application/json") else delay
                    log.warning("discord: rate limited, sleeping %.1fs", retry_after)
                    time.sleep(retry_after)
                    continue
                if resp.status_code in (500, 502, 503, 504):
                    raise DiscordError(f"server error {resp.status_code}")
                if resp.status_code >= 400:
                    raise DiscordError(f"webhook rejected: HTTP {resp.status_code} {resp.text[:200]}")
                last_url = resp.headers.get("Location", "") or ""
                posted = True
                break
            except (requests.RequestException, DiscordError, ValueError) as exc:
                if attempt >= retries:
                    raise DiscordError(f"discord send failed after {retries} attempts: {exc}") from exc
                log.warning("discord: attempt %d/%d failed (%s), retrying", attempt, retries, exc)
                time.sleep(delay)
                delay *= 2
        if not posted:
            raise DiscordError(f"discord send failed after {retries} attempts (rate limited)")
        log.info("discord: message posted (%d chars)", len(msg))
    return last_url
