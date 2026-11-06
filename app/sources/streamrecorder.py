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

from urllib.parse import unquote

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

# download-gateway links used by the site's Download button (seen as
# `m.php?download=...` in the browser network panel)
_MPHP_RE = re.compile(r'["\']([^"\']*?m\.php\?download=[^"\']*)["\']', re.I)


def _ensure_dl1(url: str) -> str:
    """The site forces the save dialog with a dl=1 query param - keep it."""
    if "dl=1" in url:
        return url
    return url + ("&" if "?" in url else "?") + "dl=1"


def _recording_in_url(url: str, rec: Recording) -> bool:
    """Signed file URLs embed twitch_<channel>_<YYYY-MM-DD HH-MM-SS>_..."""
    try:
        path = unquote(url)
    except Exception:  # noqa: BLE001 - decoding oddities must never crash us
        return False
    return f"twitch_{rec.channel}_{rec.rec_date}" in path


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
        Full links are SIGNED and differ on every request, so a fresh one
        is resolved at download time (see resolve_fresh_url).
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
        """Fresh full (`/ddl/`) URL for the logged-in session."""
        return self.resolve_fresh_url(rec)

    # ------------------------------------------------- fresh link resolver
    def _gateway_pages(self, channel: str) -> list[str]:
        pages = [f"{self.cfg.sr_base}/twitch/{channel}"]
        if self.cfg.sr_dashboard_url:
            pages.append(self.cfg.sr_dashboard_url)
        return pages

    def resolve_fresh_url(self, rec: Recording) -> str | None:
        """Get a FRESH signed download URL, mimicking the browser's flow.

        Full-download links on srcdn.net are signed (`iv=` + `enc=` query
        params) and DIFFER ON EVERY REQUEST - that is by design (they are
        per-request tokens, not stable links; they also expire).  So we
        never cache them: at download time we (1) scan the pages our
        session can see for a ready signed /ddl/ URL of this recording,
        or (2) hit the site's download-gateway links (`m.php?download=...`,
        exactly what the Download button calls) and read the fresh signed
        URL from the redirect target or the response body.
        """
        mphp_links: list[str] = []
        for page in self._gateway_pages(rec.channel):
            try:
                html = self._get(page)
            except requests.RequestException as exc:
                log.warning("streamrecorder: gateway page %s failed: %s", page, exc)
                continue

            full = [u for u in _signed_urls(html) if "/ddl/" in u]
            if len(full) == 1:
                log.info("streamrecorder: found a fresh /ddl/ url on %s", page)
                return _ensure_dl1(full[0])
            matching = [u for u in full if _recording_in_url(u, rec)]
            if matching:
                log.info("streamrecorder: found a fresh /ddl/ url matching %s on %s",
                         rec.rec_date, page)
                return _ensure_dl1(matching[0])

            for link in _MPHP_RE.findall(html):
                url = link.replace("&amp;", "&")
                if url.startswith("/"):
                    url = self.cfg.sr_base + url
                elif not url.startswith("http"):
                    url = f"{self.cfg.sr_base}/{url}"
                if url not in mphp_links:
                    mphp_links.append(url)

        for link in mphp_links[:5]:  # be polite to the site
            fresh = self._request_mphp(link)
            if fresh:
                return fresh
        return None

    def _request_mphp(self, url: str) -> str | None:
        """Hit one download-gateway link and extract the fresh signed URL
        from either the redirect target or the response body (JSON/text)."""
        shown = url if len(url) <= 120 else url[:120] + "..."
        try:
            resp = self.session.get(url, timeout=self.cfg.http_timeout,
                                    allow_redirects=False)
        except requests.RequestException as exc:
            log.warning("streamrecorder: gateway %s failed: %s", shown, exc)
            return None
        if 300 <= resp.status_code < 400:
            loc = resp.headers.get("Location", "")
            if "srcdn.net" in loc:
                log.info("streamrecorder: gateway redirected to a fresh signed url")
                return _ensure_dl1(loc)
        body = getattr(resp, "text", "") or ""
        if body:
            m = re.search(r'https://[a-z0-9.-]*srcdn\.net/ddl/[^"\'\s\\]+', body)
            if m:
                log.info("streamrecorder: gateway returned a fresh signed url in the body")
                return _ensure_dl1(m.group(0).replace("\\u0026", "&"))
        log.warning("streamrecorder: gateway %s -> HTTP %s, no signed url inside",
                    shown, resp.status_code)
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
