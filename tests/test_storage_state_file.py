"""Session files created by root must still be replaceable by the host user."""

import json
import os
import stat
from pathlib import Path

import pytest

from app.utils.storage_state import write_json_replacing


def test_write_json_replacing_overwrites_unwritable_file(tmp_path: Path) -> None:
    target = tmp_path / "kwork_session.json"
    target.write_text('{"cookies": []}', encoding="utf-8")
    os.chmod(target, 0o444)
    with pytest.raises(PermissionError):
        with open(target, "w"):
            pass

    payload = {"cookies": [{"name": "session", "value": "abc"}], "origins": []}
    write_json_replacing(target, payload)

    assert json.loads(target.read_text(encoding="utf-8")) == payload
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert os.access(target, os.W_OK)
    assert list(tmp_path.glob(".kwork_session.json.*.tmp")) == []


def test_write_json_replacing_creates_parent_directory(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "kwork_session.json"
    write_json_replacing(target, {"cookies": [], "origins": []})
    assert target.is_file()
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
