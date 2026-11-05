"""C058: bounded retries/concurrency and stage logs tied to processing attempts."""

from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

from app.config import Settings
from app.services import kwork_inbox
from app.services.memory_index_queue import process_memory_index_queue
from app.services.pipeline_llm_ledger import persist_stage_log

ROOT = Path(__file__).resolve().parents[1]

GRAPH_LLM_AGENTS = [
    "app/agents/filter_agent.py",
    "app/agents/scoring_agent.py",
    "app/agents/proposal_agent.py",
    "app/agents/chat_agent.py",
    "app/agents/learning_agent.py",
]


def test_job_graph_llm_agents_append_stage_log_on_success() -> None:
    missing = []
    for rel in GRAPH_LLM_AGENTS:
        text = (ROOT / rel).read_text(encoding="utf-8")
        if "append_stage_log" not in text:
            missing.append(rel)
    assert not missing, missing


def test_persist_stage_log_attaches_attempt_id_and_count() -> None:
    job = SimpleNamespace(
        processing_attempt_count=3,
        raw_data={"current_processing_attempt_id": "attempt-uuid"},
    )
    persist_stage_log(job, {"llm_stage_log": [{"stage": "filter", "bucket": "simple"}]})
    row = job.raw_data["llm_stage_log"][0]
    assert row["processing_attempt"] == 3
    assert row["processing_attempt_id"] == "attempt-uuid"


def test_settings_define_bounded_retry_and_lease_limits() -> None:
    fields = Settings.model_fields
    for name in (
        "job_processing_max_attempts",
        "send_attempt_max_attempts",
        "notification_outbox_max_attempts",
        "memory_index_lease_seconds",
    ):
        assert name in fields
        assert fields[name].metadata  # ge= constraints present on several


def test_compose_celery_worker_concurrency_capped() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    worker = compose.split("    celery-worker:", 1)[1].split("\n    celery-beat:", 1)[0]
    assert "--concurrency=" in worker


def test_memory_index_queue_default_batch_limit() -> None:
    assert inspect.signature(process_memory_index_queue).parameters["limit"].default == 25


def test_inbound_generation_attempt_cap_is_bounded() -> None:
    assert 1 <= kwork_inbox.INBOUND_GENERATION_MAX_ATTEMPTS <= 20
