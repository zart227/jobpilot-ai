#!/usr/bin/env python3
"""Scan git-tracked files for committed secrets (C015 acceptance helper)."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SKIP_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".gz",
    ".zip",
    ".woff",
    ".woff2",
    ".ttf",
    ".sqlite",
    ".lock",
}

LINE_ALLOW_SUBSTRINGS = (
    "sk-your-openai-key",
    "generate-a-long-random-secret",
    "jobpilot_secret",
    "jobpilot_ci",
    "secret-test-token",
    "reconcile-secret",
    "test-secret",
    "secret-token",
    "CURSOR_BRIDGE_AUTH_TOKEN=secret-token",
    "kwork_password=\"secret\"",
    "PASSWORD=secret)",
    "Bearer test-secret",
    "Bearer reconcile-secret",
)

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("telegram_bot_token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[a-zA-Z0-9_-]{32,}\b")),
    ("private_key_block", re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----")),
    (
        "anthropic_key",
        re.compile(r"\bsk-ant-[a-zA-Z0-9_-]{20,}\b"),
    ),
]


def tracked_files() -> list[Path]:
    proc = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        capture_output=True,
        text=False,
        check=True,
    )
    paths: list[Path] = []
    for raw in proc.stdout.split(b"\0"):
        if not raw:
            continue
        rel = raw.decode("utf-8", errors="replace")
        path = ROOT / rel
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        paths.append(path)
    return paths


def line_allowed(line: str) -> bool:
    lowered = line.lower()
    return any(marker.lower() in lowered for marker in LINE_ALLOW_SUBSTRINGS)


def scan_text(path: Path, text: str) -> list[tuple[int, str, str]]:
    findings: list[tuple[int, str, str]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if line_allowed(line):
            continue
        for label, pattern in PATTERNS:
            if pattern.search(line):
                findings.append((line_no, label, path.as_posix()))
                break
    return findings


def main() -> int:
    findings: list[tuple[int, str, str]] = []
    for path in tracked_files():
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        findings.extend(scan_text(path, text))

    if not findings:
        print("secret_scan: OK (no forbidden patterns in tracked files)")
        return 0

    print("secret_scan: FAIL", file=sys.stderr)
    for line_no, label, relpath in findings[:50]:
        print(f"  {relpath}:{line_no} [{label}]", file=sys.stderr)
    if len(findings) > 50:
        print(f"  ... and {len(findings) - 50} more", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
