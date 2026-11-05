"""C098: gzip restore into drill database preserves row counts and FK links."""

from __future__ import annotations

import asyncio
import gzip
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1",
    reason="isolated *_test database required",
)

DRILL_DB = "jobpilot_c098_drill"
ALLOWED_DRILL_DB_SUFFIXES = ("_test", "_drill")


def _pg_bin(name: str) -> str | None:
    return shutil.which(name)


def _assert_drill_db_allowed(db_name: str) -> None:
    if db_name.endswith(("_test", "_drill")) or db_name == DRILL_DB:
        return
    raise RuntimeError(f"refusing C098 drill against database: {db_name!r}")


def _pg_env_and_connect_args(async_url: str) -> tuple[dict[str, str], list[str]]:
    url = make_url(async_url)
    env = os.environ.copy()
    if url.password:
        env["PGPASSWORD"] = url.password
    args = [
        "-h",
        url.host or "localhost",
        "-p",
        str(url.port or 5432),
        "-U",
        url.username or "jobpilot",
    ]
    return env, args


def _drop_drill_db(async_url: str) -> None:
    source_db = make_url(async_url).database or ""
    _assert_drill_db_allowed(source_db)
    env, args = _pg_env_and_connect_args(async_url)
    subprocess.run(
        [_pg_bin("dropdb"), *args, "--if-exists", DRILL_DB],
        env=env,
        capture_output=True,
        check=False,
    )


def _create_drill_db(async_url: str) -> None:
    env, args = _pg_env_and_connect_args(async_url)
    proc = subprocess.run([_pg_bin("createdb"), *args, "-O", "jobpilot", DRILL_DB],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def _psql_query(connect: list[str], env: dict[str, str], db: str, query: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_pg_bin("psql"), *connect, "-d", db, "-tAc", query],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _dump_and_restore(async_url: str, source_db: str, drill_db: str) -> None:
    env, connect = _pg_env_and_connect_args(async_url)
    with tempfile.TemporaryDirectory() as tmp:
        dump_path = Path(tmp) / "c098.sql.gz"
        pg_dump = subprocess.run([_pg_bin("pg_dump"), *connect, "--no-owner", "--no-acl", source_db],
            env=env,
            capture_output=True,
            check=False,
        )
        assert pg_dump.returncode == 0, pg_dump.stderr.decode()
        with gzip.open(dump_path, "wb") as output:
            output.write(pg_dump.stdout)
        with gzip.open(dump_path, "rb") as dump_file:
            restore = subprocess.run([_pg_bin("psql"), *connect, "-v", "ON_ERROR_STOP=1", "-d", drill_db],
                env=env,
                input=dump_file.read(),
                check=False,
            )
        assert restore.returncode == 0, restore.stderr.decode() if restore.stderr else restore.stdout.decode()


@pytest.fixture
def pg_tools():
    for name in ("pg_dump", "psql", "createdb", "dropdb"):
        if not _pg_bin(name):
            pytest.skip("postgresql-client tools not installed")
    yield


@pytest.fixture
def cleanup_drill(pg_tools):
    source = os.environ["DATABASE_URL"]
    _drop_drill_db(source)
    yield
    _drop_drill_db(source)


def test_gzip_restore_to_drill_db_preserves_counts_and_fk(pg_tools, cleanup_drill) -> None:
    from app.db.models import Client, Job, Proposal
    from app.db.session import AsyncSessionLocal

    async def run() -> None:
        source_url = os.environ["DATABASE_URL"]
        source_db = make_url(source_url).database or ""

        job_id = uuid.uuid4()
        client_id = uuid.uuid4()
        proposal_id = uuid.uuid4()
        marker = f"c098-{job_id.int}"

        async with AsyncSessionLocal() as session:
            before_jobs = await session.scalar(text("SELECT count(*) FROM jobs"))
            before_proposals = await session.scalar(text("SELECT count(*) FROM proposals"))
            session.add(
                Client(
                    id=client_id,
                    platform="kwork",
                    external_id=f"{marker}-client",
                    name="C098",
                )
            )
            session.add(
                Job(
                    id=job_id,
                    platform="kwork",
                    external_id=f"{marker}-job",
                    title="C098 restore",
                    description="C098",
                    status="pending",
                    client_id=client_id,
                )
            )
            session.add(
                Proposal(
                    id=proposal_id,
                    job_id=job_id,
                    status="draft",
                    content="C098 proposal",
                )
            )
            await session.commit()

        _drop_drill_db(source_url)
        _create_drill_db(source_url)
        _dump_and_restore(source_url, source_db, DRILL_DB)

        env, connect = _pg_env_and_connect_args(source_url)
        jobs_out = await asyncio.to_thread(
            _psql_query, connect, env, DRILL_DB, "SELECT count(*) FROM jobs"
        )
        proposals_out = await asyncio.to_thread(
            _psql_query, connect, env, DRILL_DB, "SELECT count(*) FROM proposals"
        )
        orphan_out = await asyncio.to_thread(
            _psql_query,
            connect,
            env,
            DRILL_DB,
            "SELECT count(*) FROM proposals p LEFT JOIN jobs j ON j.id = p.job_id WHERE j.id IS NULL",
        )
        marker_out = await asyncio.to_thread(
            _psql_query,
            connect,
            env,
            DRILL_DB,
            f"SELECT count(*) FROM jobs WHERE external_id = '{marker}-job'",
        )
        assert jobs_out.returncode == 0
        assert proposals_out.returncode == 0
        assert orphan_out.returncode == 0
        assert marker_out.returncode == 0
        jobs = int(jobs_out.stdout.strip())
        proposals = int(proposals_out.stdout.strip())
        orphan = int(orphan_out.stdout.strip())
        marker_row = int(marker_out.stdout.strip())

        assert marker_row == 1
        assert jobs == int(before_jobs) + 1
        assert proposals == int(before_proposals) + 1
        assert orphan == 0

    asyncio.run(run())
