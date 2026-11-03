"""Redact secrets and sensitive payloads from log lines and structured fields."""

from __future__ import annotations

import re
from re import Pattern

_REDACTED = "[REDACTED]"

_PATTERNS: list[tuple[Pattern[str], str]] = [
    (re.compile(r"(?i)\bbearer\s+\S+"), "Bearer " + _REDACTED),
    (re.compile(r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?\S+"), r"\1Bearer " + _REDACTED),
    (re.compile(r"(?i)(api[_-]?auth[_-]?token\s*[:=]\s*)\S+"), r"\1" + _REDACTED),
    (re.compile(r"(?i)(telegram[_-]?bot[_-]?token\s*[:=]\s*)\S+"), r"\1" + _REDACTED),
    (re.compile(r"(?i)(openai[_-]?api[_-]?key\s*[:=]\s*)\S+"), r"\1" + _REDACTED),
    (re.compile(r"(?i)(openrouter[_-]?api[_-]?key\s*[:=]\s*)\S+"), r"\1" + _REDACTED),
    (re.compile(r"(?i)(cursor[_-]?api[_-]?key\s*[:=]\s*)\S+"), r"\1" + _REDACTED),
    (re.compile(r"(?i)(kwork[_-]?password\s*[:=]\s*)\S+"), r"\1" + _REDACTED),
    (re.compile(r"(?i)(password\s*[:=]\s*)\S+"), r"\1" + _REDACTED),
    (re.compile(r"(?i)(postgres[_-]?password\s*[:=]\s*)\S+"), r"\1" + _REDACTED),
    (re.compile(r"(?i)(__secure-session=)[^;\s]+"), r"\1" + _REDACTED),
    (re.compile(r"(?i)(cf_clearance=)[^;\s]+"), r"\1" + _REDACTED),
    (re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{20,}\b"), _REDACTED),
    (
        re.compile(r"(postgresql(?:\+asyncpg)?://[^:]+:)([^@/\s]+)(@)"),
        r"\1" + _REDACTED + r"\3",
    ),
    (re.compile(r"\bsk-(?:proj-)?[a-zA-Z0-9_-]{24,}\b"), _REDACTED),
    (re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"), _REDACTED),
]

_PLACEHOLDER_ALLOW = (
    "sk-your-openai-key",
    "generate-a-long-random-secret",
    "jobpilot_secret",
    "jobpilot_ci",
    "secret-test-token",
    "reconcile-secret",
    "test-secret",
    "test-key",
    "secret-token",
)


def scrub_sensitive_text(text: str) -> str:
    if not text:
        return text
    lowered = text.lower()
    if any(marker in lowered for marker in _PLACEHOLDER_ALLOW):
        return text
    out = text
    for pattern, repl in _PATTERNS:
        out = pattern.sub(repl, out)
    return out
