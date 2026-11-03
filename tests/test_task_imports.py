"""Importing either task module first should register both tasks without a cycle."""

import subprocess
import sys

import pytest


@pytest.mark.parametrize("first", ["app.tasks.scrape_tasks", "app.tasks.embedding_tasks"])
def test_task_modules_import_in_either_order(first):
    source = (
        f"import {first}\n"
        "from app.celery_app import celery_app\n"
        "assert 'app.tasks.scrape_tasks.run_all_scrapers' in celery_app.tasks\n"
        "assert 'app.tasks.embedding_tasks.reindex_pending_embeddings' in celery_app.tasks\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
