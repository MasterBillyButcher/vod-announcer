"""streamrecorder.io integration (discovery + download URLs).

Verified against the live site (anonymous, browser-like requests):

* `GET https://streamrecorder.io/twitch/<channel>`
  - "Latest Twitch Stream" panel embeds JS vars:
      `data_recordingId`, `data_title`, `data_sources` (signed mp4 on
      i.srcdn.net / *.srcdn.net), teaser duration + date
  - "Past Twitch Streams" carousel contains `.alt-preview-card` blocks,
    each with:
      - thumbnail  https://i.srcdn.net/media/<RECORDING_ID>/thumb_small_270.jpg
      - .alt-preview-duration   HH:MM:SS
      - .alt-preview-title      stream title
      - .alt-preview-date       `Nov 05, 2026`

* The signed URL of the latest recording (`ddlprev`) serves video/mp4 but
  is capped (observed 200,000,000 bytes) for anonymous visitors - i.e. a
  preview.  Full-length downloads require a (free) account; when the
  STREAMRECORDER_COOKIE secret is set we re-fetch pages with that cookie
  and prefer full-download URLs (`/ddl/`) over previews (`/ddlprev/`).

Only *dates* (no clock times) are exposed publicly; recordings are
therefore grouped per day (see session_manager).
"""

from __future__ import annotations

import json
import re

import requests

from app.config import Config
from app.logsetup import get_logger
from app.models import Recording
from app.timeutil import iso, now_utc, parse_hhmmss, parse_site_date, within_days

log = get_logger()

_CARD_RE = re.compile(
    r'<div class="alt-preview-card".*?</div>\s*</div>\s*</div>', re.S
)
_THUMB_RE = re.compile(r"srcdn\.net/media/(\d+)/thumb")
_DURATION_RE = re.compile(r'class="alt-preview-duration">([^<]+)<')
_TITLE_RE = re.compile(r'class="alt-preview-title">([^<]+)<')
_DATE_RE = re.compile(r'class="alt-preview-date">([^<]+)<')

_LATEST_ID_RE = re.compile(r'data_recordingId\s*=\s*"(\d+)"')
_LATEST_TITLE_RE = re.compile(r"data_title\s*=\s*'([^']*)'")
_LATEST_SOURCES_RE = re.compile(r"const data_sources = (\[.*?\]);", re.S)
_TEASER_DUR_RE = re.compile(r'class="alt-teaser-duration">([^<]+)<')
_TEASER_DATE_RE = re.compile(r"alt-teaser-meta\">.*?&middot;\s*([A-Za-z]{3} \d{2}, \d{4})", re.S)


class NoDownloadURLError(RuntimeError):
    """Raised when the source exposes no download URL for a recording
    (only the latest recording has a public preview URL; older ones need
    the STREAMRECORDER_COOKIE secret)."""


class StreamRecorderSource:
    name = "streamrecorder"

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": cfg.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })
        if cfg.streamrecorder_cookie:
            self.session.headers["Cookie"] = cfg.streamrecorder_cookie
            log.info("streamrecorder: using provided session cookie (value hidden)")

    # ------------------------------------------------------------ fetching
    def _get(self, url: str) -> str:
        resp = self.session.get(url, timeout=self.cfg.http_timeout)
        resp.raise_for_status()
        return resp.text

    def fetch_channel_page(self, channel: str) -> str:
        url = f"{self.cfg.sr_base}/twitch/{channel}"
        log.info("streamrecorder: fetching channel page for %s", channel)
        return self._get(url)

    # ----------------------------------------------------------- discovery
    def list_recordings(self, channel: str) -> list[Recording]:
        """Chronological list of recordings visible on the channel page,
        filtered to the backlog window."""
        html = self.fetch_channel_page(channel)
        today = parse_site_date(self._latest_teaser_date(html)) or now_utc().date()

        entries: dict[str, dict] = {}

        # 1) past-streams carousel
        for card in _CARD_RE.findall(html):
            thumb = _THUMB_RE.search(card)
            dur = _DURATION_RE.search(card)
            title = _TITLE_RE.search(card)
            date_raw = _DATE_RE.search(card)
            if not (thumb and dur and title and date_raw):
                continue
            rid = thumb.group(1)
            rec_date = parse_site_date(date_raw.group(1))
            if rec_date is None:
                continue
            entries[rid] = {
                "source_id": rid,
                "channel": channel,
                "title": title.group(1).strip(),
                "rec_date": rec_date.isoformat(),
                "duration_seconds": parse_hhmmss(dur.group(1)),
            }

        # 2) latest panel (richer: signed preview url)
        latest_id_m = _LATEST_ID_RE.search(html)
        if latest_id_m:
            rid = latest_id_m.group(1)
            entry = entries.get(rid, {})
            src_m = _LATEST_SOURCES_RE.search(html)
            preview_url = ""
            if src_m:
                try:
                    sources = json.loads(src_m.group(1).replace("\\u0026", "&"))
                    if sources and isinstance(sources, list):
                        preview_url = sources[0].get("src", "")
                except (json.JSONDecodeError, AttributeError):
                    log.warning("streamrecorder: could not parse data_sources JSON")
            title_m = _LATEST_TITLE_RE.search(html)
            teaser_dur = _TEASER_DUR_RE.search(html)
            teaser_date = parse_site_date(self._latest_teaser_date(html))
            entry.update({
                "source_id": rid,
                "channel": channel,
                "title": (title_m.group(1) if title_m else entry.get("title", "")).strip(),
                "rec_date": (teaser_date or parse_site_date(entry.get("rec_date", "")) or today).isoformat()
                if (teaser_date or entry.get("rec_date")) else "",
                "duration_seconds": parse_hhmmss(teaser_dur.group(1)) if teaser_dur else entry.get("duration_seconds"),
                "preview_url": preview_url,
            })
            entries[rid] = entry

        recordings: list[Recording] = []
        for rid, entry in entries.items():
            if not entry.get("rec_date"):
                continue
            rec = Recording(
                source_id=rid,
                channel=channel,
                title=entry.get("title", ""),
                rec_date=entry["rec_date"],
                duration_seconds=entry.get("duration_seconds"),
                preview_url=entry.get("preview_url", ""),
                page_url=f"{self.cfg.sr_base}/twitch/{channel}",
                discovered_at=iso(now_utc()),
            )
            recordings.append(rec)

        # keep only the backlog window, chronological (oldest first)
        cutoff_today = today
        recordings = [
            r for r in recordings
            if _iso_date(r.rec_date) and within_days(_iso_date(r.rec_date), cutoff_today, self.cfg.backlog_days)
        ]
        recordings.sort(key=lambda r: (r.rec_date, r.source_id))
        log.info("streamrecorder: %d recordings in backlog window for %s", len(recordings), channel)
        return recordings

    @staticmethod
    def _latest_teaser_date(html: str) -> str:
        m = _TEASER_DATE_RE.search(html)
        return m.group(1) if m else ""

    # ------------------------------------------------------- download urls
    def get_download_url(self, rec: Recording) -> tuple[str, str]:
        """Return (url, mode) for a recording.

        mode is `preview` (anonymous, size-capped) or `full` (cookie-based).
        """
        if self.cfg.streamrecorder_cookie:
            url = self._full_download_url(rec)
            if url:
                return url, "full"
            log.warning("streamrecorder: no full-download URL found for %s, falling back to preview", rec.source_id)
        if rec.preview_url:
            return rec.preview_url, "preview"
        raise NoDownloadURLError(
            f"no download URL available for recording {rec.source_id} "
            "(only the latest recording exposes a public preview URL; "
            "set the STREAMRECORDER_COOKIE secret for full downloads)"
        )

    def _full_download_url(self, rec: Recording) -> str | None:
        """Look for a full (`/ddl/`) signed URL on pages visible to the
        logged-in session.  Best-effort: the site is JS-heavy, so this
        scans for srcdn signed mp4 URLs and prefers /ddl/ over /ddlprev/."""
        try:
            html = self.fetch_channel_page(rec.channel)
        except requests.RequestException as exc:
            log.warning("streamrecorder: page fetch failed: %s", exc)
            return None
        full = [u for u in _signed_urls(html) if "/ddl/" in u]
        preview = [u for u in _signed_urls(html) if "/ddlprev/" in u]
        if full:
            log.info("streamrecorder: found %d full download URL(s)", len(full))
            return full[0]
        if preview:
            return preview[0]
        return None

    # ------------------------------------------------------------ diagnose
    def diagnose(self, channel: str) -> dict:
        html = self.fetch_channel_page(channel)
        cards = _CARD_RE.findall(html)
        return {
            "channel": channel,
            "cookie_set": bool(self.cfg.streamrecorder_cookie),
            "page_bytes": len(html),
            "latest_id": (_LATEST_ID_RE.search(html) or [None, None])[1],
            "latest_title": (_LATEST_TITLE_RE.search(html) or [None, ""])[1],
            "latest_teaser_date": self._latest_teaser_date(html),
            "past_cards": len(cards),
            "has_signed_sources": bool(_LATEST_SOURCES_RE.search(html)),
        }


def _signed_urls(html: str) -> list[str]:
    return re.findall(r'https://[a-z0-9.-]*srcdn\.net/(?:ddl|ddlprev)/[^"\'\s]+?\.mp4\?[^"\'\s]+', html)


def _iso_date(value: str):
    from datetime import date as _date

    try:
        return _date.fromisoformat(value)
    except (TypeError, ValueError):
        return None
