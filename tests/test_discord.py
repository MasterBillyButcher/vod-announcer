"""Tests for Discord announcements (templates, splitting, HTTP mocked)."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import app.discord_notify as dn
from app.discord_notify import (
    build_announcement,
    mask_webhook,
    send,
)


class Resp:
    def __init__(self, status=204, body=None, headers=None):
        self.status_code = status
        self.headers = headers or {}
        self._body = body or {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


def parts(*links, duration=3724):
    return [
        {"filename": f"BS_2025-10-08_Test_P{i:02d}", "duration_seconds": duration, "link": link}
        for i, link in enumerate(links, 1)
    ]


def one_part(duration=1, link="https://savedly.net/f/z"):
    return [{"filename": "x", "duration_seconds": duration, "link": link}]


class TestTemplates:
    def test_single_part_format(self):
        msgs = build_announcement(
            channel="beaststatsofficial",
            rec_date="2025-10-08",
            title="BS_2025-10-08_Timepass_Time",
            parts=parts("https://savedly.net/f/abc"),
        )
        text = msgs[0]
        assert "October 8" in text                      # exact date format
        assert "1h 2m 4s" in text                        # 3724s, all three units
        assert "beaststatsofficial" in text
        assert "https://savedly.net/f/abc" in text
        assert "Parts:" not in text                      # single part -> no part list
        assert "7 days" in text                          # retention warning

    def test_multi_part_format(self):
        msgs = build_announcement(
            channel="beaststatsofficial",
            rec_date="2025-10-08",
            title="BS_2025-10-08_Timepass_Time",
            parts=parts("https://savedly.net/f/a", "https://savedly.net/f/b",
                        "https://savedly.net/f/c", duration=3600),
        )
        text = msgs[0]
        assert "**Parts:** 3" in text
        assert "BS_2025-10-08_Test_P01" in text
        assert "BS_2025-10-08_Test_P02" in text
        assert "BS_2025-10-08_Test_P03" in text
        assert "**Total duration:** 3h 0m 0s" in text    # zero units still shown
        assert "October 8" in text

    def test_duration_three_units_always(self):
        msgs = build_announcement("c", "2025-10-08", "t", one_part(duration=0))
        assert "0h 0m 0s" in msgs[0]

    def test_contact_included(self):
        msgs = build_announcement("c", "2025-10-08", "t", one_part(),
                                  contact="@bobmasterbillie")
        assert "@bobmasterbillie" in msgs[0]

    def test_warnings_included(self):
        msgs = build_announcement("c", "2025-10-08", "t", one_part(),
                                  warnings=["\u26A0\uFE0F Part 2 failed permanently: nope"])
        assert "failed permanently" in msgs[0]

    def test_bad_date_falls_back(self):
        msgs = build_announcement("c", "not-a-date", "t", one_part())
        assert "not-a-date" in msgs[0]

    def test_long_messages_split(self):
        big = parts(*[f"https://savedly.net/f/id{i:04d}" for i in range(120)])
        msgs = build_announcement("c", "2025-10-08", "t", big)
        assert len(msgs) > 1
        for m in msgs:
            assert len(m) <= 2000                        # discord hard limit
        assert all(m.strip() for m in msgs)


class TestMaskWebhook:
    def test_mask(self):
        url = "https://discord.com/api/webhooks/123456789/AbCdEfGh-IjK"
        assert mask_webhook(url) == "https://discord.com/api/webhooks/123456789/[REDACTED]"

    def test_mask_garbage(self):
        assert "secret" not in mask_webhook("whatever-secret")


class TestSend:
    def test_send_posts_each_message(self):
        with patch.object(dn.requests, "post", return_value=Resp(204, {}, {"Location": "https://discord.com/x"})) as p:
            out = send("https://discord.com/api/webhooks/1/tok", ["m1", "m2"])
        assert p.call_count == 2
        assert out == "https://discord.com/x"
        payload = json.loads(p.call_args_list[0].kwargs["data"])
        assert payload["content"] == "m1"

    def test_rejects_invalid_webhook(self):
        with pytest.raises(Exception):
            send("https://evil.example/hook", ["m"])

    def test_rejects_empty_webhook(self):
        with pytest.raises(Exception):
            send("", ["m"])

    def test_429_retries_then_posts(self):
        responses = [Resp(429, {"retry_after": 0.01}), Resp(204)]
        with patch.object(dn.requests, "post", side_effect=responses), \
             patch.object(dn.time, "sleep"):
            send("https://discord.com/api/webhooks/1/tok", ["m"])
        assert responses[1].status_code == 204

    def test_permanent_429_raises(self):
        with patch.object(dn.requests, "post", return_value=Resp(429, {"retry_after": 0.01})), \
             patch.object(dn.time, "sleep"):
            with pytest.raises(Exception):
                send("https://discord.com/api/webhooks/1/tok", ["m"])

    def test_5xx_retries_then_raises(self):
        with patch.object(dn.requests, "post", return_value=Resp(503, {"message": "oops"})), \
             patch.object(dn.time, "sleep"):
            with pytest.raises(Exception):
                send("https://discord.com/api/webhooks/1/tok", ["m"])
