"""Tests for the streamrecorder.io page parser + fresh-link resolver."""

import pytest

from app.config import Config
from app.models import Recording
from app.sources import streamrecorder as sr
from app.sources.streamrecorder import StreamRecorderSource
from app.timeutil import parse_hhmmss, parse_site_date


class FakeResp:
    def __init__(self, text="", status_code=200, headers=None):
        self.text = text
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"HTTP {self.status_code}")


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
    monkeypatch.setattr(src.session, "get",
                        lambda url, timeout=None, **kw: FakeResp(text=html))
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
        monkeypatch.setattr(src.session, "get",
                            lambda url, timeout=None, **kw: FakeResp(text=html))
        rec = Recording(source_id="235918109", channel="x", rec_date="2026-11-05")
        url, mode = src.get_download_url(rec)
        assert mode == "full"
        assert "/ddl/" in url

    # ------------------------------------------------ resolver unit tests
    def test_ensure_dl1(self):
        u = "https://kattahduck.eu.srcdn.net/ddl/uuid/file.mp4?iv=A&enc=B"
        assert sr._ensure_dl1(u) == u + "&dl=1"
        assert sr._ensure_dl1(u + "&dl=1") == u + "&dl=1"
        assert sr._ensure_dl1("https://host/file.mp4").endswith("?dl=1")

    SIGNED = ("https://kattahduck.eu.srcdn.net/ddl/76391bba-15a4-404e-a906-88ab8fe44b99/"
              "twitch_beaststatsofficial_2026-11-04%2019-56-39_720p.mp4?iv=A&enc=B")

    def _resolver(self, monkeypatch, page_html, mphp_resp):
        cfg = Config(streamers=["beaststatsofficial"],
                     streamrecorder_cookie="Authorization=a; bkndsrv=b")
        src = StreamRecorderSource(cfg)

        def fake_get(url, timeout=None, **kw):
            if "m.php" in url:
                assert kw.get("allow_redirects") is False
                return mphp_resp
            return FakeResp(text=page_html)

        monkeypatch.setattr(src.session, "get", fake_get)
        return src

    def test_resolve_via_redirect(self, monkeypatch):
        page = '<a href="/m.php?download=76391bba">Download</a>'
        src = self._resolver(monkeypatch, page, FakeResp(
            status_code=302, headers={"Location": self.SIGNED}))
        rec = Recording(source_id="1", channel="beaststatsofficial",
                        rec_date="2026-11-04")
        url = src.resolve_fresh_url(rec)
        assert url == self.SIGNED + "&dl=1"

    def test_resolve_via_json_body(self, monkeypatch):
        page = '<button onclick="fetch(\'/m.php?download=99\')">DL</button>'
        body = '{"ok":true,"url":"' + self.SIGNED.replace("iv=A&enc=B", "iv=C&enc=D") + '"}'
        src = self._resolver(monkeypatch, page, FakeResp(text=body))
        rec = Recording(source_id="2", channel="beaststatsofficial",
                        rec_date="2026-11-04")
        url = src.resolve_fresh_url(rec)
        assert "/ddl/" in url and "iv=C" in url and url.endswith("&dl=1")

    def test_resolve_prefers_matching_date(self, monkeypatch):
        other = self.SIGNED.replace("2026-11-04", "2026-11-05")
        page = f'<a href="{other}">one</a><a href="{self.SIGNED}">two</a>'
        cfg = Config(streamers=["beaststatsofficial"],
                     streamrecorder_cookie="c=1")
        src = StreamRecorderSource(cfg)
        monkeypatch.setattr(src.session, "get",
                            lambda url, timeout=None, **kw: FakeResp(text=page))
        rec = Recording(source_id="3", channel="beaststatsofficial",
                        rec_date="2026-11-04")
        url = src.resolve_fresh_url(rec)
        assert "2026-11-04" in url

    def test_gateway_rejects_wrong_recording(self, monkeypatch):
        """A gateway answer for a DIFFERENT recording must never be used -
        it would download the wrong VOD."""
        other = self.SIGNED.replace("2026-11-04", "2026-11-03")
        page = '<a href="/m.php?download=abc">Download</a>'
        src = self._resolver(monkeypatch, page, FakeResp(
            status_code=302, headers={"Location": other}))
        rec = Recording(source_id="9", channel="beaststatsofficial",
                        rec_date="2026-11-04")
        assert src.resolve_fresh_url(rec) is None

    def test_gateway_prefers_link_naming_this_recording(self, monkeypatch):
        """m.php links containing the recording id are tried first."""
        seen = []
        other_link = "/m.php?download=777"
        my_link = "/m.php?download=235113230"
        page = f'<a href="{other_link}">a</a><a href="{my_link}">b</a>'
        cfg = Config(streamers=["beaststatsofficial"],
                     streamrecorder_cookie="Authorization=a; bkndsrv=b")
        src = StreamRecorderSource(cfg)

        def fake_get(url, timeout=None, **kw):
            seen.append(url)
            if "m.php" in url:
                if "235113230" in url:
                    return FakeResp(status_code=302,
                                    headers={"Location": self.SIGNED})
                return FakeResp(status_code=302, headers={"Location":
                                    self.SIGNED.replace("2026-11-04", "2026-11-01")})
            return FakeResp(text=page)

        monkeypatch.setattr(src.session, "get", fake_get)
        rec = Recording(source_id="235113230", channel="beaststatsofficial",
                        rec_date="2026-11-04")
        url = src.resolve_fresh_url(rec)
        assert url is not None and "2026-11-04" in url
        mphp_hits = [u for u in seen if "m.php" in u]
        assert mphp_hits, "gateway must have been called"
        assert mphp_hits[0].endswith("download=235113230")

    def test_resolve_returns_none_when_nothing_found(self, monkeypatch):
        src = self._resolver(monkeypatch, "<html>empty</html>",
                             FakeResp(text="nope"))
        rec = Recording(source_id="4", channel="beaststatsofficial",
                        rec_date="2026-11-04")
        assert src.resolve_fresh_url(rec) is None

    def test_dashboard_page_is_scanned_when_configured(self, monkeypatch):
        seen = []
        cfg = Config(streamers=["beaststatsofficial"],
                     streamrecorder_cookie="c=1",
                     sr_dashboard_url="https://streamrecorder.io/dashboard/recordings")
        src = StreamRecorderSource(cfg)

        def fake_get(url, timeout=None, **kw):
            seen.append(url)
            if "/ddl/" in url:
                return FakeResp(text="")
            return FakeResp(text="no links here")

        monkeypatch.setattr(src.session, "get", fake_get)
        rec = Recording(source_id="5", channel="beaststatsofficial",
                        rec_date="2026-11-04")
        assert src.resolve_fresh_url(rec) is None
        assert any("dashboard/recordings" in u for u in seen)

    def test_diagnose(self, monkeypatch):
        src = make_source(monkeypatch)
        report = src.diagnose("beaststatsofficial")
        assert report["latest_id"] == "235918109"
        assert report["past_cards"] == 2
        assert report["has_signed_sources"] is True
        assert report["channel_ddlprev_urls"] == 1
        assert report["channel_ddl_urls"] == 0
        assert report["channel_mphp_links"] == 0
        assert report["cookie_has_auth"] is False
        assert "not configured" in report["dashboard"]

    def test_diagnose_with_dashboard(self, monkeypatch):
        html = FIXTURE + ('<a href="/twitch/m.php?download=235113230&token=x">DL</a>'
                          '<a href="/twitch/m.php?download=235192867&token=y">DL</a>')
        cfg = Config(streamers=["beaststatsofficial"],
                     streamrecorder_cookie="Authorization=a",
                     sr_dashboard_url="https://streamrecorder.io/dashboard/rec")
        src = StreamRecorderSource(cfg)

        def fake_get(url, timeout=None, **kw):
            if "dashboard" in url:
                return FakeResp(text=html)
            return FakeResp(text=FIXTURE)

        monkeypatch.setattr(src.session, "get", fake_get)
        report = src.diagnose("beaststatsofficial")
        assert report["dashboard"]["ok"] is True
        assert report["dashboard"]["mphp_links"] == 2
        assert report["dashboard"]["mphp_shape"] == [
            "/twitch/m.php?download&token",
            "/twitch/m.php?download&token",
        ]
        assert "token=x" not in str(report)  # token values never leaked
