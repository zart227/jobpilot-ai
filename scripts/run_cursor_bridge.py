#!/usr/bin/env python3
"""Run Cursor SDK bridge on the host for Docker-based JobPilot dev agent.

Writes credentials to data/cursor_bridge.env — telegram-bot container reads them
via docker-compose env_file.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app.services.dev_agent_service import BRIDGE_ENV_FILE, write_bridge_env_file

PID_FILE = ROOT / "data" / "cursor_bridge.pid"
LOG_FILE = ROOT / "data" / "cursor_bridge.log"


def _read_pid() -> int | None:
    if not PID_FILE.is_file():
        return None
    try:
        return int(PID_FILE.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def _pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _print_existing_config() -> bool:
    if not BRIDGE_ENV_FILE.is_file():
        return False
    print("Cursor bridge already configured:")
    print(BRIDGE_ENV_FILE.read_text(encoding="utf-8"))
    pid = _read_pid()
    if pid and _pid_running(pid):
        print(f"Bridge process running (pid {pid}).")
    else:
        print("Bridge process is not running — restart with: python scripts/run_cursor_bridge.py")
    return True


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run Cursor SDK bridge for JobPilot dev agent")
    parser.add_argument(
        "--workspace",
        default=os.environ.get("CURSOR_WORKSPACE_HOST")
        or os.environ.get("CURSOR_WORKSPACE")
        or str(ROOT),
        help="Host project root for the local Cursor agent",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bridge listen host (use 127.0.0.1)")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("CURSOR_BRIDGE_PORT", "9247")),
        help="Bridge listen port",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run in background (used by ensure_cursor_bridge.sh)",
    )
    args = parser.parse_args()

    pid = _read_pid()
    if pid and _pid_running(pid):
        _print_existing_config()
        return

    from cursor_sdk import AsyncClient, LocalAgentOptions

    workspace = str(Path(args.workspace).expanduser().resolve())
    print(f"Starting Cursor bridge for workspace: {workspace}")

    client = await AsyncClient.launch_bridge(
        workspace=workspace,
        host=args.host,
        port=args.port,
        local=LocalAgentOptions(cwd=workspace),
    )
    endpoint = client._owned_bridge.endpoint if client._owned_bridge else None
    if endpoint is None:
        print("Bridge started but endpoint is unknown", file=sys.stderr)
        await client.close()
        raise SystemExit(1)

    docker_url = endpoint.url.replace("127.0.0.1", "host.docker.internal")
    if args.host not in {"127.0.0.1", "localhost", ""}:
        docker_url = endpoint.url

    env_path = write_bridge_env_file(docker_url, endpoint.auth_token, workspace)
    print(f"\nSaved bridge config: {env_path}")
    print(f"Docker URL: {docker_url}")
    print("\nRestart telegram-bot after first setup:")
    print("  docker compose restart telegram-bot\n")

    stop = asyncio.Event()

    def _handle_signal(*_: object) -> None:
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            signal.signal(sig, lambda *_: stop.set())

    if args.daemon:
        PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        PID_FILE.write_text(str(os.getpid()), encoding="utf-8")

    try:
        await stop.wait()
    finally:
        PID_FILE.unlink(missing_ok=True)
        await client.close()
        print("Bridge stopped.")


if __name__ == "__main__":
    asyncio.run(main())
