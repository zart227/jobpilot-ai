#!/usr/bin/env python3
"""Explicitly reassess one Kwork job; no message or offer is sent."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('job_id', type=uuid.UUID)
    parser.add_argument('--apply', action='store_true', help='Permit writing the new assessment')
    parser.add_argument('--confirm-llm', action='store_true', help='Permit a configured LLM request')
    args = parser.parse_args()
    if not os.environ.get('DATABASE_URL', '').strip() or not args.apply or not args.confirm_llm:
        print('Explicit DATABASE_URL, --apply and --confirm-llm are required.', file=sys.stderr)
        return 2
    from app.services.kwork_reassessment import reassess_kwork_job

    try:
        result = asyncio.run(reassess_kwork_job(args.job_id))
    except Exception:
        print('Reassessment failed; inspect service logs without exposing credentials.', file=sys.stderr)
        return 1
    print(json.dumps({'job_id': str(args.job_id), **result}, ensure_ascii=False))
    return 0 if result['status'] in ('accepted', 'rejected') else 1


if __name__ == '__main__':
    raise SystemExit(main())
