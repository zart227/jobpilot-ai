"""C016: Cursor bridge bind/auth policy and drill artifacts."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

C016_BRIDGE_EVIDENCE = (
    ("policy_tests", "tests/test_cursor_bridge_policy.py"),
    ("security_doc", "docs/cursor-bridge-security.md"),
    ("host_drill", "scripts/cursor_bridge_drill.sh"),
    ("container_probe", "scripts/cursor_bridge_from_container.sh"),
    ("dev_agent_docker_guard", "tests/test_dev_agent_service.py"),
)


def test_c016_bridge_evidence_catalog_is_complete():
    assert len(C016_BRIDGE_EVIDENCE) >= 5
    for _name, rel in C016_BRIDGE_EVIDENCE:
        assert (ROOT / rel).is_file()


def test_c016_proxy_forwards_only_to_loopback_bridge():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "TCP-LISTEN:9247,bind=0.0.0.0" in compose
    assert "TCP:127.0.0.1:19247" in compose
    script = (ROOT / "scripts" / "run_cursor_bridge.py").read_text(encoding="utf-8")
    assert 'default="127.0.0.1"' in script
