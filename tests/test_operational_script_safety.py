"""Script guards tested with fake docker/curl; never run a real restore or rollout."""

import gzip
import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def fake_commands(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "docker.log"
    docker = bindir / "docker"
    docker.write_text("""#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_DOCKER_LOG"
case "$*" in
  *createdb*) [ "${FAKE_CREATEDB_FAIL:-0}" != 1 ] ;;
  *psql*) cat >/dev/null ;;
esac
""")
    docker.chmod(0o755)
    curl = bindir / "curl"
    curl.write_text("""#!/bin/sh
case "$*" in
  */health*) [ "${FAKE_HEALTH_FAIL:-0}" != 1 ] || exit 22 ;;
  *) while [ "$#" -gt 0 ]; do
       if [ "$1" = -o ]; then shift; printf '%s' "$FAKE_READY_BODY" > "$1"; fi
       shift
     done
     printf '%s' "$FAKE_READY_CODE" ;;
esac
""")
    curl.chmod(0o755)
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "FAKE_DOCKER_LOG": str(log)}
    return env, log


@pytest.fixture
def dump(tmp_path):
    path = tmp_path / "safe.sql.gz"
    with gzip.open(path, "wt") as output:
        output.write("SELECT 1;\n")
    return path


def run_restore(env, dump, name):
    return subprocess.run(["sh", str(ROOT / "scripts/restore_postgres_drill.sh"), str(dump)],
        env={**env, "JOBPILOT_DRILL_DB": name}, capture_output=True, text=True, check=False,
    )


@pytest.mark.parametrize("name", ["jobpilot_ai", "postgres", "jobpilot_bad;DROP_drill", "x_drill", "jobpilot_x_drill;--", "jobpilot_X_drill"])
def test_restore_rejects_unsafe_target_before_docker(fake_commands, dump, name):
    env, log = fake_commands
    assert run_restore(env, dump, name).returncode != 0
    assert not log.exists()


def test_restore_rejects_corrupt_dump_before_database_creation(fake_commands, tmp_path):
    env, log = fake_commands
    path = tmp_path / "broken.gz"
    path.write_text("not gzip")
    assert run_restore(env, path, "jobpilot_safety_drill").returncode != 0
    assert not log.exists()


def test_restore_uses_only_new_database_without_drop_or_terminate(fake_commands, dump):
    env, log = fake_commands
    assert run_restore(env, dump, "jobpilot_safety_drill").returncode == 0
    calls = log.read_text()
    assert "createdb" in calls and "jobpilot_safety_drill" in calls
    assert "DROP DATABASE" not in calls and "pg_terminate_backend" not in calls


def test_restore_existing_database_fails_without_restoring(fake_commands, dump):
    env, log = fake_commands
    result = run_restore({**env, "FAKE_CREATEDB_FAIL": "1"}, dump, "jobpilot_safety_drill")
    assert result.returncode != 0
    assert "psql" not in log.read_text()


@pytest.mark.parametrize("code,body,ok", [
    ("200", '{"ready":true,"status":"ready"}', True),
    ("200", '{"ready":true,"status":"degraded"}', True),
    ("503", '{"ready":false,"status":"not_ready"}', False),
    ("200", '{"ready":false,"status":"not_ready"}', False),
    ("200", '{"ready":"true","status":"ready"}', False),
    ("200", "not json", False),
])
def test_readiness_smoke_requires_real_ready_result(fake_commands, code, body, ok):
    env, _ = fake_commands
    result = subprocess.run(["sh", str(ROOT / "scripts/readiness_smoke.sh")],
        env={**env, "FAKE_READY_CODE": code, "FAKE_READY_BODY": body},
        capture_output=True, text=True, check=False,
    )
    assert (result.returncode == 0) is ok


def test_readiness_smoke_fails_when_liveness_fails(fake_commands):
    env, _ = fake_commands
    result = subprocess.run(["sh", str(ROOT / "scripts/readiness_smoke.sh")],
        env={**env, "FAKE_HEALTH_FAIL": "1", "FAKE_READY_CODE": "200",
             "FAKE_READY_BODY": '{"ready":true,"status":"ready"}'},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0


@pytest.fixture
def fake_rollout(tmp_path):
    root = tmp_path / "project"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy(ROOT / "scripts/production_rollout.sh", scripts)
    python = root / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.symlink_to("/usr/bin/python3")
    (scripts / "migration_preflight.py").write_text(
        "import os\nopen(os.environ['FAKE_ROLLOUT_LOG'],'a').write('preflight\\n')\n"
    )
    alembic = root / "alembic"
    alembic.mkdir()
    (alembic / "__init__.py").write_text("")
    (alembic / "__main__.py").write_text(
        "import os\nopen(os.environ['FAKE_ROLLOUT_LOG'],'a').write('migration\\n')\n"
    )
    smoke = scripts / "readiness_smoke.sh"
    smoke.write_text('#!/bin/sh\nprintf "smoke\\n" >> "$FAKE_ROLLOUT_LOG"\nexit "${FAKE_SMOKE_EXIT:-0}"\n')
    smoke.chmod(0o755)
    backup = root / "backup.sql.gz"
    with gzip.open(backup, "wt") as output:
        output.write("--\n-- PostgreSQL database dump\n--\nSELECT 1;\n")
    log = root / "calls.log"
    env = {"PATH": "/usr/bin:/bin", "FAKE_ROLLOUT_LOG": str(log),
           "JOBPILOT_ROLLOUT_BACKUP": str(backup),
           "JOBPILOT_ROLLOUT_BACKUP_SHA256": hashlib.sha256(backup.read_bytes()).hexdigest()}
    return root, env, log, backup


@pytest.mark.parametrize("case", ["missing_backup", "wrong_digest", "invalid_gzip", "wrong_format", "missing_smoke"])
def test_rollout_refuses_before_migrations_without_backup_and_smoke(fake_rollout, case):
    root, env, log, backup = fake_rollout
    if case == "missing_backup":
        env.pop("JOBPILOT_ROLLOUT_BACKUP")
    elif case == "wrong_digest":
        env["JOBPILOT_ROLLOUT_BACKUP_SHA256"] = "0" * 64
    elif case == "invalid_gzip":
        backup.write_bytes(b"broken")
        env["JOBPILOT_ROLLOUT_BACKUP_SHA256"] = hashlib.sha256(backup.read_bytes()).hexdigest()
    elif case == "wrong_format":
        backup.write_bytes(gzip.compress(b"not a PostgreSQL dump"))
        env["JOBPILOT_ROLLOUT_BACKUP_SHA256"] = hashlib.sha256(backup.read_bytes()).hexdigest()
    else:
        (root / "scripts/readiness_smoke.sh").unlink()
    result = subprocess.run(["sh", str(root / "scripts/production_rollout.sh")],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert not log.exists()


def test_rollout_stops_when_ops_readiness_enabled_without_working_url(fake_rollout):
    root, env, log, _ = fake_rollout
    result = subprocess.run(["sh", str(root / "scripts/production_rollout.sh")],
        env={**env, "JOBPILOT_ROLLOUT_OPS_READINESS": "1"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "JOBPILOT_WORKING_DATABASE_URL" in result.stderr
    assert not log.exists()


@pytest.mark.parametrize("smoke_exit", [0, 1])
def test_rollout_runs_smoke_and_propagates_failure(fake_rollout, smoke_exit):
    root, env, log, _ = fake_rollout
    result = subprocess.run(["sh", str(root / "scripts/production_rollout.sh")],
        env={**env, "FAKE_SMOKE_EXIT": str(smoke_exit)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == smoke_exit
    assert log.read_text().splitlines() == ["preflight", "migration", "smoke"]
    if smoke_exit:
        assert "OK: schema upgraded" not in result.stdout
