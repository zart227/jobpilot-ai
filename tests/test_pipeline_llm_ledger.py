from app.db.models import Job
from app.services.pipeline_llm_ledger import append_stage_log, persist_stage_log


def test_append_stage_log_accumulates() -> None:
    state = {"llm_stage_log": append_stage_log({}, "filter", "simple")}
    log = append_stage_log(state, "scoring", "scoring")
    assert len(log) == 2
    assert log[0]["stage"] == "filter"
    assert log[1]["stage"] == "scoring"


def test_persist_stage_log_writes_raw_data() -> None:
    job = Job(platform="kwork", external_id="1", title="T", description="d", raw_data={})
    persist_stage_log(job, {"llm_stage_log": append_stage_log({}, "proposal", "main")})
    assert job.raw_data["llm_stage_count"] == 1
    assert job.raw_data["llm_stage_totals"]["proposal"] == 1
