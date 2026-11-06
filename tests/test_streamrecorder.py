"""Tests for the streamrecorder.io page parser (fixture HTML, no network)."""

import pytest

from app.config import Config
from app.models import Recording
from app.sources import streamrecorder as sr
from app.sources.streamrecorder import StreamRecorderSource
from app.timeutil import parse_hhmmss, parse_site_date


FIXTURE = """
<div class="alt-preview-card" onclick="altOpenMonitorModal()">
  <div class="alt-preview-thumb">
    <img src="https://i.srcdn.net/media/235192867/thumb_small_270.jpg" alt="Timepass">
    <span class="alt-preview-duration">01:38:20</span>
  </div>
  <div class="alt-preview-info">
    <div class="alt-preview-title">Timepass Time</div>
    <div class="alt-preview-date">Nov 04, 2026</div>
  </div>
</div>

<div class="alt-preview-card" onclick="altOpenMonitorModal()">
  <div class="alt-preview-thumb">
    <img src="https://i.srcdn.net/media/235113230/thumb_small_270.jpg" alt="Timepass">
    <span class="alt-preview-duration">02:04:22</span>
  </div>
  <div class="alt-preview-info">
    <div class="alt-preview-title">Timepass Time</div>
    <div class="alt-preview-date">Nov 04, 2026</div>
  </div>
</div>

<script type="text/javascript">
const data_target = 'beaststatsofficial';
const data_recordingId = "235918109";
const data_title = 'Timepass Time \\U0001F441';
const data_sources = [{"size": 720, "src": "https://walterlike.eu.srcdn.net/ddlprev/uuid/v.mp4?iv=AAA\\u0026enc=BBB", "type": "video/mp4", "watchduration": 0}];
</script>
<span class="alt-teaser-meta"><span class="alt-teaser-duration">01:42:04</span> &middot; Nov 05, 2026</span>
"""


def make_source(monkeypatch, html=FIXTURE):
    cfg = Config(streamers=["beaststatsofficial"])
    src = StreamRecorderSource(cfg)

    class FakeResp:
        status_code = 200
        text = html

        def raise_for_status(self):
            pass

    monkeypatch.setattr(src.session, "get", lambda url, timeout=None: FakeResp())
    return src


class TestParsing:
    def test_parse_site_date(self):
        assert parse_site_date("Nov 05, 2026").isoformat() == "2026-11-05"
        assert parse_site_date("October 8, 2025").isoformat() == "2025-10-08"
        assert parse_site_date("garbage") is None

    def test_parse_hhmmss(self):
        assert parse_hhmmss("01:42:04") == 1 * 3600 + 42 * 60 + 4
        assert parse_hhmmss("42:04") == 42 * 60 + 4
        assert parse_hhmmss("nope") is None

    def test_list_recordings(self, monkeypatch):
        src = make_source(monkeypatch)
        recs = src.list_recordings("beaststatsofficial")
        ids = [r.source_id for r in recs]
        # newest (latest panel) plus two carousel cards
        assert "235918109" in ids
        assert "235192867" in ids
        assert "235113230" in ids
        # chronological: 2026-11-04 entries first, then 2026-11-05
        dates = [r.rec_date for r in recs]
        assert dates == sorted(dates)

    def test_latest_has_preview_url(self, monkeypatch):
        src = make_source(monkeypatch)
        recs = {r.source_id: r for r in src.list_recordings("x")}
        latest = recs["235918109"]
        assert latest.preview_url.startswith("https://walterlike.eu.srcdn.net/ddlprev/")
        assert "iv=AAA" in latest.preview_url and "enc=BBB" in latest.preview_url

    def test_carousel_durations_parsed(self, monkeypatch):
        src = make_source(monkeypatch)
        recs = {r.source_id: r for r in src.list_recordings("x")}
        assert recs["235192867"].duration_seconds == 1 * 3600 + 38 * 60 + 20
        assert recs["235113230"].duration_seconds == 2 * 3600 + 4 * 60 + 22

    def test_download_url_anonymous_uses_preview(self, monkeypatch):
        src = make_source(monkeypatch)
        rec = Recording(source_id="235918109", channel="x", rec_date="2026-11-05",
                        preview_url="https://walterlike.eu.srcdn.net/ddlprev/u/v.mp4?iv=A&enc=B")
        url, mode = src.get_download_url(rec)
        assert mode == "preview"
        assert url == rec.preview_url

    def test_no_url_raises(self, monkeypatch):
        src = make_source(monkeypatch)
        rec = Recording(source_id="999", channel="x", rec_date="2026-11-05")
        with pytest.raises(RuntimeError):
            src.get_download_url(rec)

    def test_cookie_mode_prefers_full(self, monkeypatch):
        html = FIXTURE.replace("ddlprev", "ddl")
        cfg = Config(streamers=["x"], streamrecorder_cookie="sessionid=abc")
        src = StreamRecorderSource(cfg)

        class FakeResp:
            status_code = 200
            text = html

            def raise_for_status(self):
                pass

        monkeypatch.setattr(src.session, "get", lambda url, timeout=None: FakeResp())
        rec = Recording(source_id="235918109", channel="x", rec_date="2026-11-05")
        url, mode = src.get_download_url(rec)
        assert mode == "full"
        assert "/ddl/" in url

    def test_diagnose(self, monkeypatch):
        src = make_source(monkeypatch)
        report = src.diagnose("beaststatsofficial")
        assert report["latest_id"] == "235918109"
        assert report["past_cards"] == 2
        assert report["has_signed_sources"] is True
