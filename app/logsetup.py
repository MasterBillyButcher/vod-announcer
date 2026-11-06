"""Logging setup with secret redaction.

Secrets (webhook URLs, cookies, delete tokens, signed URLs) must never end
up in GitHub Actions logs.
"""

from __future__ import annotations

import logging
import re
import sys

_REDACT = "[REDACTED]"

_PATTERNS = [
    # discord webhooks: keep the id, hide the token
    (re.compile(r"(https://(?:discord|discordapp)\.com/api/webhooks/\d+/)[\w-]+"), r"\1" + _REDACT),
    # savedly signed API parts and delete tokens
    (re.compile(r"(uploadId=)[\w-]+"), r"\1" + _REDACT),
    (re.compile(r'("deleteToken"\s*:\s*")[^"]+(")'), r"\1" + _REDACT + r"\2"),
    # streamrecorder signed mp4 query strings
    (re.compile(r"(srcdn\.net/[^\s\"']+ \?)([^\"'\s]+)"), r"\1" + _REDACT),
    (re.compile(r"(\?iv=)[^&\s\"']+"), r"\1" + _REDACT),
    (re.compile(r"(&enc=)[^&\s\"']+"), r"\1" + _REDACT),
]

_COOKIES = re.compile(
    r"(?i)(cookie\s*[:=]\s*)([^\s;]+(?:;\s*[^\s;]+)*)"
)


def redact(message: str) -> str:
    out = message
    for pattern, repl in _PATTERNS:
        out = pattern.sub(repl, out)
    out = _COOKIES.sub(lambda m: m.group(1) + _REDACT, out)
    return out


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(str(record.msg))
            if record.args:
                record.args = tuple(
                    redact(str(a)) if isinstance(a, str) else a for a in record.args
                )
        except Exception:
            pass
        return True


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("vod-announcer")
    if logger.handlers:
        return logger
    logger.setLevel(level)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S"))
    handler.addFilter(RedactingFilter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger("vod-announcer")
