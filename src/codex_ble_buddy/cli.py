"""Command-line interface for codex-ble-buddy."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from .ble import BleBuddyClient, scan_buddies
from .ble import INSTALL_HELP
from .codex_config import setup_codex_config
from .config import BleBuddyConfig
from .hook import run_hook
from .logging_utils import configure_logging
from .protocol import PermissionPrompt, make_request_id

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codex-ble-buddy")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")

    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="Scan for BLE Buddy devices")
    scan.add_argument("--timeout", type=float, default=8.0, help="Scan timeout in seconds")

    send_test = subparsers.add_parser("send-test", help="Send a test approval request")
    send_test.add_argument("--timeout", type=float, default=30.0, help="Decision timeout in seconds")
    send_test.add_argument("--scan-timeout", type=float, default=8.0, help="Scan timeout in seconds")

    approve = subparsers.add_parser("approve-request", help="Run Codex PermissionRequest hook flow")
    approve.add_argument("--timeout", type=float, default=30.0, help="Decision timeout in seconds")
    approve.add_argument("--scan-timeout", type=float, default=8.0, help="Scan timeout in seconds")

    setup = subparsers.add_parser("setup-codex", help="Configure the Codex PermissionRequest hook")
    setup.add_argument("--config-path", type=Path, help="Path to Codex config.toml")
    setup.add_argument("--timeout", type=float, default=30.0, help="Decision timeout in seconds")
    setup.add_argument("--yes", action="store_true", help="Write without interactive confirmation")
    setup.add_argument("--language", choices=("en", "zh"), default="en", help="Prompt language")

    subparsers.add_parser("doctor", help="Print environment diagnostics")

    return parser


async def _scan(args: argparse.Namespace) -> int:
    try:
        devices = await scan_buddies(BleBuddyConfig(scan_timeout=args.timeout))
    except RuntimeError as exc:
        logger.error("%s", exc)
        return 1
    if not devices:
        print("No BLE Buddy devices found.")
        return 1
    for device in devices:
        rssi = "" if device.rssi is None else f" RSSI={device.rssi}"
        print(f"{device.name} {device.address}{rssi}")
    return 0


async def _send_test(args: argparse.Namespace) -> int:
    config = BleBuddyConfig(scan_timeout=args.scan_timeout, decision_timeout=args.timeout)
    prompt = PermissionPrompt(
        request_id=make_request_id(),
        title="Codex approval request",
        tool="send-test",
        command="Test approval from codex-ble-buddy",
        message="Press allow or deny on the BLE Buddy device.",
    )
    try:
        decision = await BleBuddyClient(config).request_decision(prompt)
    except RuntimeError as exc:
        logger.error("%s", exc)
        return 1
    if decision is None:
        print("No decision received.")
        return 2
    print(json.dumps({"id": decision.request_id, "decision": decision.decision}, ensure_ascii=False))
    return 0 if decision.is_allow else 3


def _doctor() -> int:
    print(f"Python: {sys.version.split()[0]}")
    try:
        import bleak

        print(f"bleak: {getattr(bleak, '__version__', 'installed')}")
    except Exception as exc:
        print(f"bleak: unavailable ({exc})")
        print()
        print(INSTALL_HELP)
        return 1
    print("Nordic UART Service UUIDs are configured.")
    print("Run `codex-ble-buddy scan` with Bluetooth enabled to verify device discovery.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.verbose)

    if args.command == "scan":
        return asyncio.run(_scan(args))
    if args.command == "send-test":
        return asyncio.run(_send_test(args))
    if args.command == "approve-request":
        config = BleBuddyConfig(scan_timeout=args.scan_timeout, decision_timeout=args.timeout)
        return run_hook(config)
    if args.command == "setup-codex":
        return setup_codex_config(
            timeout=args.timeout,
            config_path=args.config_path,
            assume_yes=args.yes,
            language=args.language,
        )
    if args.command == "doctor":
        return _doctor()

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
