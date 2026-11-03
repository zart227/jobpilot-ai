#!/usr/bin/env python3
"""Clear Kwork connects pause after owner confirms connects available (e.g. 30/30)."""

from __future__ import annotations

import json
import sys

from app.config import get_settings
from app.services.kwork_pause import apply_connects_probe_result, is_kwork_offers_paused


def main() -> int:
    settings = get_settings()
    result = apply_connects_probe_result(
        limit_exhausted=False,
        page_text="осталось 29 из 30",
        source="ops_clear_kwork_pause",
        confident=True,
        connects_available_confirmed=True,
        settings=settings,
    )
    print(json.dumps({"probe": result, "paused_after": is_kwork_offers_paused(settings)}, ensure_ascii=False))
    if is_kwork_offers_paused(settings):
        print("Pause still active — check data/kwork_connects_pause.json and KWORK_PAUSE_UNTIL", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
