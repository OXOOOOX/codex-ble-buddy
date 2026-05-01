"""Runtime configuration defaults."""

from __future__ import annotations

from dataclasses import dataclass

NUS_SERVICE_UUID = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
NUS_RX_CHAR_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
NUS_TX_CHAR_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

DEFAULT_NAME_PREFIXES = ("Codex-", "CodeBuddy", "Buddy")


@dataclass(frozen=True)
class BleBuddyConfig:
    """BLE connection settings."""

    name_prefixes: tuple[str, ...] = DEFAULT_NAME_PREFIXES
    scan_timeout: float = 8.0
    connect_timeout: float = 12.0
    decision_timeout: float = 30.0
    write_timeout: float = 5.0
    connect_retries: int = 2
