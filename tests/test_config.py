"""Tests for config loading hardening (cookie sanitation, defaults)."""

import pytest

from app.config import load_config


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("STREAMERS", "WAIT_MINUTES", "BACKLOG_DAYS", "CONTACT",
                 "DISCORD_WEBHOOK_URL", "STREAMRECORDER_COOKIE",
                 "SR_DASHBOARD_URL", "DRY_RUN", "DIAGNOSE"):
        monkeypatch.delenv(name, raising=False)


def test_default_backlog_is_today_plus_yesterday():
    cfg = load_config()
    assert cfg.backlog_days == 2


def test_cookie_newlines_are_stripped(monkeypatch):
    monkeypatch.setenv(
        "STREAMRECORDER_COOKIE",
        "Authorization=abc123\r\n; bkndsrv=srv7\n",
    )
    cfg = load_config()
    assert "\r" not in cfg.streamrecorder_cookie
    assert "\n" not in cfg.streamrecorder_cookie
    assert cfg.streamrecorder_cookie == "Authorization=abc123; bkndsrv=srv7"


def test_cookie_without_equals_fails_validation(monkeypatch):
    monkeypatch.setenv("STREAMRECORDER_COOKIE", "garbage-value-no-pairs")
    cfg = load_config()
    problems = cfg.validate()
    assert any("STREAMRECORDER_COOKIE" in p for p in problems)


def test_good_cookie_passes_validation(monkeypatch):
    monkeypatch.setenv("STREAMRECORDER_COOKIE",
                       "Authorization=xyz; bkndsrv=eu1")
    cfg = load_config()
    assert cfg.validate() == []
