"""Operational metrics must consume each SQLAlchemy result only once."""

from unittest.mock import AsyncMock

from sqlalchemy import create_engine, text

from app.services.ops_metrics import collect_ops_metrics


async def test_counts_can_be_reused_in_job_and_funnel_metrics() -> None:
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as connection:
            values = [2, 1, None, 0, 1, 3, 4, 42, 5, 7, 9]
            results = [connection.execute(text("SELECT :value"), {"value": v}) for v in values]
            session = AsyncMock()
            session.execute.side_effect = results
            metrics = await collect_ops_metrics(session)
        assert metrics["jobs"]["total"] == metrics["funnel"]["unique_jobs"] == 42
        assert metrics["jobs"]["with_confirmed_send"] == 9
        assert metrics["funnel"]["jobs_with_confirmed_external_send"] == 9
    finally:
        engine.dispose()
