#!/usr/bin/env python3
"""Run Cursor SDK bridge on the host for Docker-based JobPilot dev agent."""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app.services.dev_agent_service import BRIDGE_ENV_FILE, write_bridge_env_file

PID_FILE = ROOT / "data" / "cursor_bridge.pid"
DEFAULT_INTERNAL_PORT = int(os.environ.get("CURSOR_BRIDGE_INTERNAL_PORT", "19247"))
DEFAULT_PUBLIC_PORT = int(os.environ.get("CURSOR_BRIDGE_PORT", "9247"))


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


def _port_listening(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def _bridge_healthy(port: int) -> bool:
    pid = _read_pid()
    return bool(pid and _pid_running(pid) and _port_listening("127.0.0.1", port))


def _print_existing_config(port: int) -> None:
    if BRIDGE_ENV_FILE.is_file():
        print("Cursor bridge already running:")
        print(BRIDGE_ENV_FILE.read_text(encoding="utf-8"))
    pid = _read_pid()
    if pid:
        print(f"Bridge process pid {pid}, port {port}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run Cursor SDK bridge for JobPilot dev agent")
    parser.add_argument(
        "--workspace",
        default=os.environ.get("CURSOR_WORKSPACE_HOST")
        or os.environ.get("CURSOR_WORKSPACE")
        or str(ROOT),
        help="Host project root for the local Cursor agent",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bridge listen host")
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_INTERNAL_PORT,
        help="Internal bridge listen port",
    )
    parser.add_argument(
        "--public-port",
        type=int,
        default=DEFAULT_PUBLIC_PORT,
        help="Docker-facing port (written to cursor_bridge.env)",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run in background (used by ensure_cursor_bridge.sh)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Restart even if bridge appears healthy",
    )
    args = parser.parse_args()

    if not args.force and _bridge_healthy(args.port) and BRIDGE_ENV_FILE.is_file():
        _print_existing_config(args.port)
        return

    if _port_listening("127.0.0.1", args.port):
        print(f"Port {args.port} is busy — stop bridge first: ./scripts/stop_cursor_bridge.sh")
        raise SystemExit(1)

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

    docker_url = f"http://host.docker.internal:{args.public_port}"
    env_path = write_bridge_env_file(docker_url, endpoint.auth_token, workspace)
    print(f"Saved bridge config: {env_path}")
    print(f"Docker URL: {docker_url}")

    stop = asyncio.Event()

    def _handle_signal(*_: object) -> None:
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            signal.signal(sig, lambda *_: stop.set())

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
