"""Tests for host-side ops database URL resolution."""

import os

from scripts.ops_database_url import pick_ops_database_url


def test_prefers_localhost_database_when_working_is_compose_postgres(monkeypatch):
    monkeypatch.setenv(
        "JOBPILOT_WORKING_DATABASE_URL",
        "postgresql+asyncpg://u:p@postgres:5432/jobpilot_ai",
    )
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://u:p@127.0.0.1:5434/jobpilot_ai",
    )
    assert pick_ops_database_url() == os.environ["DATABASE_URL"]


def test_working_alone_when_no_localhost_override(monkeypatch):
    monkeypatch.setenv(
        "JOBPILOT_WORKING_DATABASE_URL",
        "postgresql+asyncpg://u:p@db.example.com:5432/jobpilot_ai",
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert pick_ops_database_url() == os.environ["JOBPILOT_WORKING_DATABASE_URL"]
