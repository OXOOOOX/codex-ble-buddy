"""Local persistent BLE approval service."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from .ble import _load_bleak, device_matches
from .config import (
    DEFAULT_SERVICE_HOST,
    DEFAULT_SERVICE_PORT,
    BleBuddyConfig,
    NUS_RX_CHAR_UUID,
    NUS_TX_CHAR_UUID,
)
from .protocol import (
    PermissionPrompt,
    ProtocolError,
    codex_no_decision_output,
    decode_decision,
    encode_idle_snapshot,
    encode_permission_prompt,
    prompt_from_codex_hook,
)

logger = logging.getLogger(__name__)

DecisionHandler = Callable[[dict[str, Any]], dict[str, Any]]


class PersistentBleBuddyManager:
    """Keep a BLE Buddy connection warm and serialize permission requests."""

    def __init__(self, config: BleBuddyConfig, reconnect_delay: float = 2.0) -> None:
        self.config = config
        self.reconnect_delay = reconnect_delay
        self._client: Any | None = None
        self._connected = asyncio.Event()
        self._disconnected = asyncio.Event()
        self._notifications: asyncio.Queue[bytes] = asyncio.Queue()
        self._request_lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._keepalive_task: asyncio.Task[None] | None = None

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._connect_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Persistent BLE loop failed; reconnecting")
            self._connected.clear()
            self._client = None
            if not self._stop.is_set():
                logger.info("Reconnecting to BLE Buddy in %.1fs", self.reconnect_delay)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.reconnect_delay)
                except asyncio.TimeoutError:
                    pass

    async def stop(self) -> None:
        self._stop.set()
        self._disconnected.set()

    async def request_decision(self, prompt: PermissionPrompt) -> Any | None:
        if self._request_lock.locked():
            logger.warning("BLE Buddy service is busy; returning no decision")
            return None

        try:
            async with self._request_lock:
                if not self._connected.is_set():
                    try:
                        await asyncio.wait_for(
                            self._connected.wait(),
                            timeout=self.config.scan_timeout + self.config.connect_timeout + 2,
                        )
                    except asyncio.TimeoutError:
                        logger.warning("BLE Buddy service has no active connection")
                        return None

                client = self._client
                if client is None or not getattr(client, "is_connected", False):
                    logger.warning("BLE Buddy service connection is not ready")
                    return None

                self._drain_notifications()
                try:
                    await asyncio.wait_for(
                        client.write_gatt_char(NUS_RX_CHAR_UUID, encode_permission_prompt(prompt), response=True),
                        timeout=self.config.write_timeout,
                    )
                except Exception as exc:
                    logger.warning("Failed to write permission prompt to persistent BLE connection: %s", exc)
                    return None

                logger.info("Forwarded permission request %s to BLE Buddy", prompt.request_id)
                deadline = time.monotonic() + self.config.decision_timeout
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        logger.warning("Timed out waiting for BLE Buddy decision")
                        return None
                    try:
                        data = await asyncio.wait_for(self._notifications.get(), timeout=remaining)
                    except asyncio.TimeoutError:
                        return None
                    try:
                        return decode_decision(data, expected_request_id=prompt.request_id)
                    except ProtocolError as exc:
                        logger.warning("Ignoring invalid BLE decision message: %s", exc)
        finally:
            await self._send_idle_snapshot()

    async def _connect_once(self) -> None:
        BleakClient, BleakScanner = _load_bleak()
        logger.info("Scanning for BLE Buddy devices for %.1fs", self.config.scan_timeout)
        devices = await BleakScanner.discover(timeout=self.config.scan_timeout, return_adv=False)
        device = next((candidate for candidate in devices if device_matches(candidate.name, self.config.name_prefixes)), None)
        if device is None:
            logger.warning("No BLE Buddy device found")
            return

        self._disconnected = asyncio.Event()

        def on_disconnect(_: Any) -> None:
            logger.warning("BLE Buddy disconnected")
            self._disconnected.set()

        def on_notify(_: int, data: bytearray) -> None:
            logger.debug("Received persistent BLE notification: %r", bytes(data))
            self._notifications.put_nowait(bytes(data))

        try:
            client_context = BleakClient(device, timeout=self.config.connect_timeout, disconnected_callback=on_disconnect)
        except TypeError:
            client_context = BleakClient(device, timeout=self.config.connect_timeout)

        async with client_context as client:
            if not client.is_connected:
                logger.warning("Persistent BLE client did not connect")
                return
            self._client = client
            logger.info("Persistent BLE Buddy connected: %s (%s)", device.name, device.address)
            await client.start_notify(NUS_TX_CHAR_UUID, on_notify)
            await self._send_idle_snapshot()
            if self.config.connect_settle_delay > 0:
                await asyncio.sleep(self.config.connect_settle_delay)
            self._connected.set()
            self._keepalive_task = asyncio.create_task(self._keepalive_loop())
            try:
                await self._disconnected.wait()
            finally:
                if self._keepalive_task is not None:
                    self._keepalive_task.cancel()
                    try:
                        await self._keepalive_task
                    except asyncio.CancelledError:
                        pass
                    self._keepalive_task = None
                self._connected.clear()
                self._client = None
                try:
                    await client.stop_notify(NUS_TX_CHAR_UUID)
                except Exception:
                    logger.debug("Failed to stop persistent BLE notifications cleanly", exc_info=True)

    def _drain_notifications(self) -> None:
        while True:
            try:
                self._notifications.get_nowait()
            except asyncio.QueueEmpty:
                return

    async def _keepalive_loop(self) -> None:
        while self._connected.is_set() and not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.config.keepalive_interval)
            except asyncio.TimeoutError:
                if self._request_lock.locked():
                    continue
                await self._send_idle_snapshot()

    async def _send_idle_snapshot(self) -> None:
        client = self._client
        if client is None or not getattr(client, "is_connected", False):
            return
        try:
            await asyncio.wait_for(
                client.write_gatt_char(NUS_RX_CHAR_UUID, encode_idle_snapshot(), response=True),
                timeout=self.config.write_timeout,
            )
            logger.debug("Sent idle heartbeat snapshot to BLE Buddy")
        except Exception as exc:
            logger.warning("Failed to send idle heartbeat snapshot: %s", exc)


class ServiceRuntime:
    """Run the persistent BLE manager on a background event loop."""

    def __init__(self, config: BleBuddyConfig) -> None:
        self.loop = asyncio.new_event_loop()
        self.manager = PersistentBleBuddyManager(config)
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._task: asyncio.Future[Any] | None = None

    def start(self) -> None:
        self._thread.start()
        self._task = asyncio.run_coroutine_threadsafe(self.manager.run(), self.loop)

    def stop(self) -> None:
        asyncio.run_coroutine_threadsafe(self.manager.stop(), self.loop).result(timeout=5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=5)

    def handle_permission(self, payload: dict[str, Any]) -> dict[str, Any]:
        from .protocol import codex_allow_output, codex_deny_output

        prompt = prompt_from_codex_hook(payload)
        future = asyncio.run_coroutine_threadsafe(self.manager.request_decision(prompt), self.loop)
        try:
            decision = future.result(timeout=service_request_timeout(self.manager.config))
        except Exception:
            logger.exception("Persistent service failed to process permission request")
            return codex_no_decision_output()
        if decision is None:
            return codex_no_decision_output()
        if decision.is_allow:
            return codex_allow_output()
        if decision.is_deny:
            return codex_deny_output()
        return codex_no_decision_output()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()


class PermissionHTTPServer(ThreadingHTTPServer):
    decision_handler: DecisionHandler


class PermissionRequestHandler(BaseHTTPRequestHandler):
    server: PermissionHTTPServer

    def do_GET(self) -> None:
        if self.path != "/health":
            self.send_error(404)
            return
        self._write_json({"ok": True})

    def do_POST(self) -> None:
        if self.path != "/permission":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("request body must be a JSON object")
        except Exception as exc:
            logger.warning("Invalid service request: %s", exc)
            self.send_error(400)
            return
        self._write_json(self.server.decision_handler(payload))

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("HTTP service: " + format, *args)

    def _write_json(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def service_url(host: str = DEFAULT_SERVICE_HOST, port: int = DEFAULT_SERVICE_PORT, path: str = "/permission") -> str:
    return f"http://{host}:{port}{path}"


def service_request_timeout(config: BleBuddyConfig) -> float:
    """Return enough time for initial scan, connection, write, and user decision."""

    return config.scan_timeout + config.connect_timeout + config.write_timeout + config.decision_timeout + 2


def call_permission_service(
    payload: dict[str, Any],
    host: str = DEFAULT_SERVICE_HOST,
    port: int = DEFAULT_SERVICE_PORT,
    timeout: float = 2.0,
) -> dict[str, Any] | None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        service_url(host, port),
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            decoded = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        logger.debug("Permission service unavailable: %s", exc)
        return None
    if not isinstance(decoded, dict):
        return None
    return decoded


def service_is_available(host: str = DEFAULT_SERVICE_HOST, port: int = DEFAULT_SERVICE_PORT, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(service_url(host, port, "/health"), timeout=timeout) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError, TimeoutError):
        return False


def run_service(config: BleBuddyConfig, host: str = DEFAULT_SERVICE_HOST, port: int = DEFAULT_SERVICE_PORT) -> int:
    runtime = ServiceRuntime(config)
    runtime.start()
    server = PermissionHTTPServer((host, port), PermissionRequestHandler)
    server.decision_handler = runtime.handle_permission
    logger.info("codex-ble-buddy service listening on http://%s:%d", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopping codex-ble-buddy service")
    finally:
        server.server_close()
        runtime.stop()
    return 0
