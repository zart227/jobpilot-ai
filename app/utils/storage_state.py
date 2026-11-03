"""Write Playwright storage state without truncating an existing file.

Playwright's ``storage_state(path=...)`` opens the destination with mode ``"w"``.
That needs write permission on the existing inode, so a root-owned session file
(mode 0644) cannot be updated by the host user even when the parent directory
is writable. Replacing the path only needs write permission on the directory.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def write_json_replacing(path: Path, payload: object) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    # Browser state contains authentication cookies; only its owner may read it.
    os.chmod(path, 0o600)


async def save_browser_storage_state(context: Any, path: Path) -> None:
    state = await context.storage_state()
    write_json_replacing(path, state)
