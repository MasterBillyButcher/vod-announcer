"""Tests for config loading hardening (cookie normalization, defaults).

The STREAMRECORDER_COOKIE secret is pasted by a human, so ANY reasonable
paste must work and a weird cookie must NEVER kill the run (no exit
code 2) - worst case the bot falls back to capped preview downloads.
"""

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


def test_bare_token_value_is_wrapped(monkeypatch):
    """The most common paste: only the Authorization VALUE, no name."""
    monkeypatch.setenv("STREAMRECORDER_COOKIE", "eyJhbGciOiJIUzI1NiJ9.sig.part")
    cfg = load_config()
    assert cfg.streamrecorder_cookie == "Authorization=eyJhbGciOiJIUzI1NiJ9.sig.part"
    assert cfg.validate() == []


def test_bare_token_with_bearer_prefix_is_wrapped(monkeypatch):
    """The browser stores the value as 'Bearer eyJ...' - prefix must survive."""
    monkeypatch.setenv("STREAMRECORDER_COOKIE", "Bearer eyJhbGciOiJIUzI1NiJ9.abc.sig")
    cfg = load_config()
    assert cfg.streamrecorder_cookie == "Authorization=Bearer eyJhbGciOiJIUzI1NiJ9.abc.sig"
    assert cfg.validate() == []


def test_bare_token_with_base64_padding_is_wrapped_whole(monkeypatch):
    """A '=' inside the token must not confuse the wrapper."""
    monkeypatch.setenv("STREAMRECORDER_COOKIE", "dJhcQx9z+Abc=")
    cfg = load_config()
    assert cfg.streamrecorder_cookie == "Authorization=dJhcQx9z+Abc="
    assert cfg.validate() == []


def test_authorization_alone_is_enough(monkeypatch):
    """bkndsrv is recommended but optional - Auth-only must pass."""
    monkeypatch.setenv("STREAMRECORDER_COOKIE", "Authorization=xyz")
    cfg = load_config()
    assert cfg.streamrecorder_cookie == "Authorization=xyz"
    assert cfg.validate() == []


def test_cookie_pairs_with_spaces_are_normalized(monkeypatch):
    monkeypatch.setenv("STREAMRECORDER_COOKIE",
                       "Authorization = abc ; bkndsrv = eu1")
    cfg = load_config()
    assert cfg.streamrecorder_cookie == "Authorization=abc; bkndsrv=eu1"
    assert cfg.validate() == []


def test_full_devtools_line_is_kept(monkeypatch):
    """Pasting ALL cookies from the browser is harmless (extras ignored)."""
    line = ("Authorization=tok123; _pk_id.1.6f93=v1; _pk_ses.1.6f93=1; "
            "bkndsrv=sr-web04; sr_player_theater=1; srp2_panel=0; srp2_tour=1")
    monkeypatch.setenv("STREAMRECORDER_COOKIE", line)
    cfg = load_config()
    assert "Authorization=tok123" in cfg.streamrecorder_cookie
    assert "bkndsrv=sr-web04" in cfg.streamrecorder_cookie
    assert cfg.validate() == []


def test_other_cookie_only_never_fatal(monkeypatch):
    """A paste without any Authorization pair must not exit-2 the run."""
    monkeypatch.setenv("STREAMRECORDER_COOKIE", "bkndsrv=sr-web04")
    cfg = load_config()
    assert cfg.streamrecorder_cookie == "bkndsrv=sr-web04"
    assert cfg.validate() == []


def test_good_cookie_passes_validation(monkeypatch):
    monkeypatch.setenv("STREAMRECORDER_COOKIE",
                       "Authorization=xyz; bkndsrv=eu1")
    cfg = load_config()
    assert cfg.validate() == []


def test_broken_webhook_still_fails_validation(monkeypatch):
    """Exit code 2 stays reserved for REAL config problems (webhook etc.)."""
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://example.com/hook")
    cfg = load_config()
    assert any("DISCORD_WEBHOOK_URL" in p for p in cfg.validate())
