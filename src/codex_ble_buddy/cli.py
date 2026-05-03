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
from .claude_config import default_claude_settings_path, has_managed_claude_hook_settings, setup_claude_settings
from .codex_config import default_codex_config_path, has_managed_hook_config, setup_codex_config
from .config import DEFAULT_SERVICE_HOST, DEFAULT_SERVICE_PORT, BleBuddyConfig
from .hook import run_hook
from .logging_utils import configure_logging
from .protocol import PermissionPrompt, make_request_id
from .service import call_permission_service, run_service, service_is_available, service_request_timeout

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
    send_test.add_argument("--service", action="store_true", help="Send the test request through the local service")

    approve = subparsers.add_parser("approve-request", help="Run Codex or Claude Code PermissionRequest hook flow")
    approve.add_argument("--timeout", type=float, default=30.0, help="Decision timeout in seconds")
    approve.add_argument("--scan-timeout", type=float, default=8.0, help="Scan timeout in seconds")
    approve.add_argument("--no-service", action="store_true", help="Skip the local service and use one-shot BLE")

    serve = subparsers.add_parser("serve", help="Run a persistent local BLE Buddy approval service")
    serve.add_argument("--host", default=DEFAULT_SERVICE_HOST, help="Service bind host")
    serve.add_argument("--port", type=int, default=DEFAULT_SERVICE_PORT, help="Service bind port")
    serve.add_argument("--scan-timeout", type=float, default=8.0, help="BLE scan timeout in seconds")
    serve.add_argument("--timeout", type=float, default=30.0, help="Decision timeout in seconds")

    setup = subparsers.add_parser("setup-codex", help="Configure the Codex PermissionRequest hook")
    setup.add_argument("--config-path", type=Path, help="Path to Codex config.toml")
    setup.add_argument("--timeout", type=float, default=30.0, help="Decision timeout in seconds")
    setup.add_argument("--yes", action="store_true", help="Write without interactive confirmation")
    setup.add_argument("--language", choices=("en", "zh"), default="en", help="Prompt language")

    setup_claude = subparsers.add_parser("setup-claude", help="Configure the Claude Code PermissionRequest hook")
    setup_claude.add_argument("--settings-path", type=Path, help="Path to Claude Code settings.json")
    setup_claude.add_argument("--timeout", type=float, default=30.0, help="Decision timeout in seconds")
    setup_claude.add_argument("--yes", action="store_true", help="Write without interactive confirmation")
    setup_claude.add_argument("--language", choices=("en", "zh"), default="en", help="Prompt language")

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
    if args.service:
        output = call_permission_service(
            {
                "hookEventName": "PermissionRequest",
                "id": prompt.request_id,
                "tool": prompt.tool,
                "command": prompt.command,
                "reason": prompt.message,
            },
            timeout=service_request_timeout(config),
        )
        if output is None:
            print("Local BLE Buddy service is not available.")
            return 2
        print(json.dumps(output, ensure_ascii=False))
        decision = output.get("hookSpecificOutput", {}).get("decision", {})
        return 0 if decision.get("behavior") == "allow" else 3

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
    codex_config_path = default_codex_config_path()
    if has_managed_hook_config(codex_config_path):
        print(f"Codex CLI hook: configured in {codex_config_path}")
    else:
        print(f"Codex CLI hook: not configured in {codex_config_path}")
        print("Run `codex-ble-buddy setup-codex` to configure it.")
    claude_settings_path = default_claude_settings_path()
    if has_managed_claude_hook_settings(claude_settings_path):
        print(f"Claude Code hook: configured in {claude_settings_path}")
    else:
        print(f"Claude Code hook: not configured in {claude_settings_path}")
        print("Run `codex-ble-buddy setup-claude` to configure it.")
    if service_is_available():
        print(f"Local BLE Buddy service: online at http://{DEFAULT_SERVICE_HOST}:{DEFAULT_SERVICE_PORT}")
    else:
        print(f"Local BLE Buddy service: offline at http://{DEFAULT_SERVICE_HOST}:{DEFAULT_SERVICE_PORT}")
        print("Run `codex-ble-buddy serve` to keep a persistent BLE connection warm.")
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
        return run_hook(config, use_service=not args.no_service)
    if args.command == "serve":
        config = BleBuddyConfig(scan_timeout=args.scan_timeout, decision_timeout=args.timeout)
        return run_service(config, host=args.host, port=args.port)
    if args.command == "setup-codex":
        return setup_codex_config(
            timeout=args.timeout,
            config_path=args.config_path,
            assume_yes=args.yes,
            language=args.language,
        )
    if args.command == "setup-claude":
        return setup_claude_settings(
            timeout=args.timeout,
            settings_path=args.settings_path,
            assume_yes=args.yes,
            language=args.language,
        )
    if args.command == "doctor":
        return _doctor()

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
