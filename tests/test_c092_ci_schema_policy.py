"""C092: CI must cover fresh migrate, legacy upgrade path, and Docker image build."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"


def test_ci_integration_runs_legacy_schema_upgrade_test() -> None:
    integration = CI_YML.read_text(encoding="utf-8").split("integration:", 1)[1].split(
        "browser-form-contract:", 1
    )[0]
    assert "alembic upgrade head" in integration
    assert "test_c018_legacy_upgrade_acceptance.py" in integration


def test_ci_integration_runs_restore_drill_acceptance_when_client_installed() -> None:
    integration = CI_YML.read_text(encoding="utf-8").split("integration:", 1)[1].split(
        "browser-form-contract:", 1
    )[0]
    assert "test_c098_restore_drill_acceptance.py" in integration
    assert "postgresql-client" in integration


def test_ci_builds_docker_image() -> None:
    text = CI_YML.read_text(encoding="utf-8")
    assert "docker-build:" in text
    assert "docker build" in text
