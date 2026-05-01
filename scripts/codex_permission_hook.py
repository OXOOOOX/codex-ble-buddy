from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _ensure_src_on_path() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    src = repo_root / "src"
    if src.exists():
        sys.path.insert(0, str(src))


def main(argv: list[str] | None = None) -> int:
    _ensure_src_on_path()

    from codex_ble_buddy.config import BleBuddyConfig
    from codex_ble_buddy.hook import run_hook
    from codex_ble_buddy.logging_utils import configure_logging

    parser = argparse.ArgumentParser(description="Codex PermissionRequest hook for BLE Buddy")
    parser.add_argument("--timeout", type=float, default=30.0, help="Decision timeout in seconds")
    parser.add_argument("--scan-timeout", type=float, default=8.0, help="BLE scan timeout in seconds")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args(argv)

    configure_logging(args.verbose)
    config = BleBuddyConfig(scan_timeout=args.scan_timeout, decision_timeout=args.timeout)
    return run_hook(config)


if __name__ == "__main__":
    raise SystemExit(main())
