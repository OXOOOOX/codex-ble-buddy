"""BLE Nordic UART transport using bleak."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .config import BleBuddyConfig, NUS_RX_CHAR_UUID, NUS_TX_CHAR_UUID
from .protocol import Decision, PermissionPrompt, ProtocolError, decode_decision, encode_permission_prompt

if TYPE_CHECKING:
    from bleak.backends.device import BLEDevice
else:
    BLEDevice = Any

logger = logging.getLogger(__name__)

INSTALL_HELP = (
    "bleak is not installed. Install project dependencies first:\n"
    "  python -m pip install -e .\n"
    "or:\n"
    "  python -m pip install -r requirements.txt"
)


@dataclass(frozen=True)
class BuddyDevice:
    name: str
    address: str
    rssi: int | None = None


def device_matches(name: str | None, prefixes: tuple[str, ...]) -> bool:
    return bool(name) and any(name.startswith(prefix) for prefix in prefixes)


def _load_bleak() -> tuple[Any, Any]:
    try:
        from bleak import BleakClient, BleakScanner
    except ImportError as exc:
        raise RuntimeError(INSTALL_HELP) from exc
    return BleakClient, BleakScanner


async def scan_buddies(config: BleBuddyConfig) -> list[BuddyDevice]:
    _, BleakScanner = _load_bleak()
    logger.info("Scanning for BLE Buddy devices for %.1fs", config.scan_timeout)
    discovered = await BleakScanner.discover(timeout=config.scan_timeout, return_adv=True)
    matches: list[BuddyDevice] = []
    for device, advertisement in discovered.values():
        name = device.name or ""
        if device_matches(name, config.name_prefixes):
            matches.append(BuddyDevice(name=name, address=device.address, rssi=advertisement.rssi))
    logger.info("Found %d matching BLE Buddy device(s)", len(matches))
    return matches


async def find_first_buddy(config: BleBuddyConfig) -> BLEDevice | None:
    _, BleakScanner = _load_bleak()
    devices = await BleakScanner.discover(timeout=config.scan_timeout, return_adv=False)
    for device in devices:
        if device_matches(device.name, config.name_prefixes):
            logger.info("Selected BLE Buddy device %s (%s)", device.name, device.address)
            return device
    logger.warning("No BLE Buddy device found")
    return None


class BleBuddyClient:
    """Connect, send one permission request, and wait for a decision."""

    def __init__(self, config: BleBuddyConfig) -> None:
        self.config = config

    async def request_decision(self, prompt: PermissionPrompt) -> Decision | None:
        device = await find_first_buddy(self.config)
        if device is None:
            return None

        last_error: Exception | None = None
        for attempt in range(1, self.config.connect_retries + 1):
            try:
                return await self._request_decision_once(device, prompt)
            except (asyncio.TimeoutError, OSError, ProtocolError) as exc:
                last_error = exc
                logger.warning("BLE request attempt %d failed: %s", attempt, exc)
            except Exception as exc:
                last_error = exc
                logger.exception("Unexpected BLE request failure on attempt %d", attempt)
            if attempt < self.config.connect_retries:
                await asyncio.sleep(0.5)

        logger.error("BLE request failed after %d attempt(s): %s", self.config.connect_retries, last_error)
        return None

    async def _request_decision_once(self, device: BLEDevice, prompt: PermissionPrompt) -> Decision | None:
        BleakClient, _ = _load_bleak()
        queue: asyncio.Queue[bytes] = asyncio.Queue()

        def on_notify(_: int, data: bytearray) -> None:
            logger.debug("Received BLE notification: %r", bytes(data))
            queue.put_nowait(bytes(data))

        async with BleakClient(device, timeout=self.config.connect_timeout) as client:
            if not client.is_connected:
                logger.warning("BLE client did not connect")
                return None

            logger.info("Connected to BLE Buddy")
            await client.start_notify(NUS_TX_CHAR_UUID, on_notify)
            try:
                payload = encode_permission_prompt(prompt)
                await asyncio.wait_for(
                    client.write_gatt_char(NUS_RX_CHAR_UUID, payload, response=True),
                    timeout=self.config.write_timeout,
                )
                logger.info("Permission prompt sent; waiting %.1fs for decision", self.config.decision_timeout)

                while True:
                    data = await asyncio.wait_for(queue.get(), timeout=self.config.decision_timeout)
                    try:
                        return decode_decision(data, expected_request_id=prompt.request_id)
                    except ProtocolError as exc:
                        logger.warning("Ignoring invalid BLE decision message: %s", exc)
            finally:
                try:
                    await client.stop_notify(NUS_TX_CHAR_UUID)
                except Exception:
                    logger.debug("Failed to stop BLE notifications cleanly", exc_info=True)
