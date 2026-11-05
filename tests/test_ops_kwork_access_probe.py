"""ops_kwork_access_probe uses the same direct-first proxy order as live send."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "ops_kwork_access_probe",
    ROOT / "scripts" / "ops_kwork_access_probe.py",
)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_access_probe_proxy_candidates_try_direct_first(monkeypatch):
    from app.config import Settings

    settings = Settings(_env_file=None, PROXY_ENABLED=True, OPENAI_PROXY_LIST="proxy-lists/openai.txt")
    monkeypatch.setattr(
        probe,
        "get_proxy_candidates",
        lambda _s, _svc: ["http://user:pass@proxy.example:8080"],
    )
    candidates = probe.access_probe_proxy_candidates(settings)
    assert candidates[0] is None
    assert len(candidates) == 2


def test_access_probe_keeps_explicit_direct_only(monkeypatch):
    from app.config import Settings

    settings = Settings(_env_file=None, PROXY_ENABLED=False)
    monkeypatch.setattr(probe, "get_proxy_candidates", lambda _s, _svc: [None])
    assert probe.access_probe_proxy_candidates(settings) == [None]
