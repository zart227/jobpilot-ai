#!/usr/bin/env python3
"""Strip non-Weblancer cookies from data/weblancer_session.json."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from app.services.weblancer_session import write_sanitized_storage

DEFAULT = Path("data/weblancer_session.json")


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    if not path.is_file():
        print(f"Нет файла: {path}")
        sys.exit(1)
    before = len(json.loads(path.read_text(encoding="utf-8")).get("cookies", []))
    cleaned = write_sanitized_storage(path, json.loads(path.read_text(encoding="utf-8")))
    after = len(cleaned.get("cookies", []))
    print(f"{path}: {before} → {after} cookies")


if __name__ == "__main__":
    main()
