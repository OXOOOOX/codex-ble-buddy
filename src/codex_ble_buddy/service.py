"""Local persistent BLE approval service."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from .ble import _load_bleak, device_matches
from .config import (
    DEFAULT_SERVICE_HOST,
    DEFAULT_SERVICE_PORT,
    DEFAULT_TASK_NAME,
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
StatusHandler = Callable[[], dict[str, Any]]


def _powershell_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


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

    @property
    def is_connected(self) -> bool:
        client = self._client
        return self._connected.is_set() and client is not None and bool(getattr(client, "is_connected", False))

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
                        logger.warning("Timed out waiting for BLE Buddy notification")
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

    def status(self) -> dict[str, Any]:
        return {
            "ok": True,
            "ble_connected": self.manager.is_connected,
        }

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()


class PermissionHTTPServer(ThreadingHTTPServer):
    decision_handler: DecisionHandler
    status_handler: StatusHandler


class PermissionRequestHandler(BaseHTTPRequestHandler):
    server: PermissionHTTPServer

    def do_GET(self) -> None:
        if self.path != "/health":
            self.send_error(404)
            return
        self._write_json(self.server.status_handler())

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


def service_command(
    host: str = DEFAULT_SERVICE_HOST,
    port: int = DEFAULT_SERVICE_PORT,
    scan_timeout: float = 8.0,
    decision_timeout: float = 30.0,
) -> str:
    args = [
        sys.executable,
        "-m",
        "codex_ble_buddy.cli",
        "serve",
        "--host",
        host,
        "--port",
        str(port),
        "--scan-timeout",
        str(int(scan_timeout) if float(scan_timeout).is_integer() else scan_timeout),
        "--timeout",
        str(int(decision_timeout) if float(decision_timeout).is_integer() else decision_timeout),
    ]
    return subprocess.list2cmdline(args)


def service_task_script_path() -> Path:
    return Path(__file__).resolve().parents[2] / ".tmp" / "codex-ble-buddy-service.cmd"


def service_task_launcher_path() -> Path:
    return Path(__file__).resolve().parents[2] / ".tmp" / "codex-ble-buddy-service.vbs"


def _vbscript_string(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def write_service_task_script(
    host: str = DEFAULT_SERVICE_HOST,
    port: int = DEFAULT_SERVICE_PORT,
    scan_timeout: float = 8.0,
    decision_timeout: float = 30.0,
) -> Path:
    repo_root = Path(__file__).resolve().parents[2]
    script_path = service_task_script_path()
    log_dir = Path(tempfile.gettempdir()) / "codex-ble-buddy"
    log_path = log_dir / "service.log"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "@echo off",
        f'if not exist "{log_dir}" mkdir "{log_dir}"',
        f'cd /d "{repo_root}"',
        f'{service_command(host, port, scan_timeout, decision_timeout)} >> "{log_path}" 2>&1',
        "",
    ]
    script_path.write_text("\r\n".join(lines), encoding="ascii")
    return script_path


def write_service_task_launcher(script_path: Path) -> Path:
    launcher_path = service_task_launcher_path()
    launcher_path.parent.mkdir(parents=True, exist_ok=True)
    command = f'cmd.exe /c "{script_path}"'
    lines = [
        'Set shell = CreateObject("WScript.Shell")',
        f"shell.Run {_vbscript_string(command)}, 0, False",
        "",
    ]
    launcher_path.write_text("\r\n".join(lines), encoding="ascii")
    return launcher_path


def task_is_installed(task_name: str = DEFAULT_TASK_NAME) -> bool:
    completed = subprocess.run(
        ["schtasks", "/Query", "/TN", task_name],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0


def install_service_task(
    task_name: str = DEFAULT_TASK_NAME,
    host: str = DEFAULT_SERVICE_HOST,
    port: int = DEFAULT_SERVICE_PORT,
    scan_timeout: float = 8.0,
    decision_timeout: float = 30.0,
) -> bool:
    if os.name != "nt":
        raise RuntimeError("Scheduled task install is only supported on Windows")
    script_path = write_service_task_script(host, port, scan_timeout, decision_timeout)
    launcher_path = write_service_task_launcher(script_path)
    task_command = f'wscript.exe //B //Nologo "{launcher_path}"'
    completed = subprocess.run(
        [
            "schtasks",
            "/Create",
            "/TN",
            task_name,
            "/SC",
            "ONCE",
            "/ST",
            "00:00",
            "/TR",
            task_command,
            "/F",
            "/RL",
            "LIMITED",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout).strip() or "failed to install scheduled task")
    return True


def uninstall_service_task(task_name: str = DEFAULT_TASK_NAME) -> bool:
    if os.name != "nt":
        raise RuntimeError("Scheduled task uninstall is only supported on Windows")
    completed = subprocess.run(
        ["schtasks", "/Delete", "/TN", task_name, "/F"],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0


def start_service_task(task_name: str = DEFAULT_TASK_NAME) -> bool:
    if os.name != "nt":
        return False
    completed = subprocess.run(
        ["schtasks", "/Run", "/TN", task_name],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0


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
    status = service_status(host=host, port=port, timeout=timeout)
    return bool(status and status.get("ok") is True)


def service_status(
    host: str = DEFAULT_SERVICE_HOST,
    port: int = DEFAULT_SERVICE_PORT,
    timeout: float = 1.0,
) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(service_url(host, port, "/health"), timeout=timeout) as response:
            decoded = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None
    if not isinstance(decoded, dict):
        return None
    return decoded


def start_service_background(
    host: str = DEFAULT_SERVICE_HOST,
    port: int = DEFAULT_SERVICE_PORT,
    scan_timeout: float = 8.0,
    decision_timeout: float = 30.0,
) -> subprocess.Popen[Any]:
    """Start the local BLE Buddy service in the background."""

    executable = sys.executable
    if os.name == "nt":
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        if pythonw.exists():
            executable = str(pythonw)
    args = [
        executable,
        "-m",
        "codex_ble_buddy.cli",
        "serve",
        "--host",
        host,
        "--port",
        str(port),
        "--scan-timeout",
        str(int(scan_timeout) if float(scan_timeout).is_integer() else scan_timeout),
        "--timeout",
        str(int(decision_timeout) if float(decision_timeout).is_integer() else decision_timeout),
    ]
    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    log_dir = Path(tempfile.gettempdir()) / "codex-ble-buddy"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "service.log"
    if os.name == "nt":
        arg_list = "@(" + ",".join(_powershell_string(arg) for arg in args[1:]) + ")"
        command = " ".join(
            [
                "Start-Process",
                "-FilePath",
                _powershell_string(executable),
                "-ArgumentList",
                arg_list,
                "-WorkingDirectory",
                _powershell_string(str(Path(__file__).resolve().parents[2])),
                "-WindowStyle",
                "Hidden",
            ]
        )
        return subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", command],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    log_handle = log_path.open("ab")
    return subprocess.Popen(
        args,
        cwd=str(Path(__file__).resolve().parents[2]),
        stdin=subprocess.DEVNULL,
        stdout=log_handle,
        stderr=log_handle,
        startupinfo=startupinfo,
        creationflags=creationflags,
        close_fds=True,
    )


def wait_for_service(
    host: str = DEFAULT_SERVICE_HOST,
    port: int = DEFAULT_SERVICE_PORT,
    timeout: float = 5.0,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if service_is_available(host=host, port=port, timeout=0.5):
            return True
        time.sleep(0.1)
    return service_is_available(host=host, port=port, timeout=0.5)


def run_service(config: BleBuddyConfig, host: str = DEFAULT_SERVICE_HOST, port: int = DEFAULT_SERVICE_PORT) -> int:
    runtime = ServiceRuntime(config)
    runtime.start()
    server = PermissionHTTPServer((host, port), PermissionRequestHandler)
    server.decision_handler = runtime.handle_permission
    server.status_handler = runtime.status
    logger.info("codex-ble-buddy service listening on http://%s:%d", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopping codex-ble-buddy service")
    finally:
        server.server_close()
        runtime.stop()
    return 0
