import asyncio
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import AsyncMock, patch

from codex_ble_buddy.config import BleBuddyConfig
from codex_ble_buddy.hook import run_permission_request
from codex_ble_buddy.protocol import PermissionPrompt, codex_allow_output
from codex_ble_buddy.service import (
    PermissionRequestHandler,
    PersistentBleBuddyManager,
    call_permission_service,
    install_service_task,
    service_command,
    service_status,
    write_service_task_launcher,
    write_service_task_script,
)


class PersistentBleBuddyManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_returns_none_when_not_connected(self) -> None:
        manager = PersistentBleBuddyManager(BleBuddyConfig(scan_timeout=0.01, connect_timeout=0.01))
        prompt = PermissionPrompt("req", "title", "tool", "command", "message")

        self.assertIsNone(await manager.request_decision(prompt))

    async def test_busy_request_returns_none(self) -> None:
        manager = PersistentBleBuddyManager(BleBuddyConfig(scan_timeout=0.01))
        await manager._request_lock.acquire()
        try:
            prompt = PermissionPrompt("req", "title", "tool", "command", "message")

            self.assertIsNone(await manager.request_decision(prompt))
        finally:
            manager._request_lock.release()

    async def test_malformed_decision_is_ignored_until_timeout(self) -> None:
        class FakeClient:
            is_connected = True

            async def write_gatt_char(self, *args, **kwargs) -> None:
                return None

        manager = PersistentBleBuddyManager(BleBuddyConfig(scan_timeout=0.01, decision_timeout=0.01))
        manager._client = FakeClient()
        manager._connected.set()
        await manager._notifications.put(b'{"cmd":"permission","id":"req","decision":"maybe"}')
        prompt = PermissionPrompt("req", "title", "tool", "command", "message")

        self.assertIsNone(await manager.request_decision(prompt))

    async def test_request_sends_idle_snapshot_after_decision(self) -> None:
        class FakeClient:
            is_connected = True

            def __init__(self, manager: PersistentBleBuddyManager) -> None:
                self.manager = manager
                self.writes: list[bytes] = []

            async def write_gatt_char(self, _uuid, data, *args, **kwargs) -> None:
                self.writes.append(bytes(data))
                if b'"prompt"' in data:
                    await self.manager._notifications.put(b'{"cmd":"permission","id":"req","decision":"once"}')

        manager = PersistentBleBuddyManager(BleBuddyConfig(scan_timeout=0.01, decision_timeout=0.01))
        client = FakeClient(manager)
        manager._client = client
        manager._connected.set()
        prompt = PermissionPrompt("req", "title", "tool", "command", "message")

        decision = await manager.request_decision(prompt)

        self.assertIsNotNone(decision)
        self.assertIn(b'"prompt"', client.writes[0])
        self.assertNotIn(b'"prompt"', client.writes[-1])

    async def test_keepalive_sends_idle_snapshot(self) -> None:
        class FakeClient:
            is_connected = True

            def __init__(self) -> None:
                self.writes: list[bytes] = []

            async def write_gatt_char(self, _uuid, data, *args, **kwargs) -> None:
                self.writes.append(bytes(data))

        client = FakeClient()
        manager = PersistentBleBuddyManager(BleBuddyConfig(write_timeout=0.1, keepalive_interval=0.01))
        manager._client = client
        manager._connected.set()
        task = asyncio.create_task(manager._keepalive_loop())
        try:
            await asyncio.sleep(0.03)
        finally:
            await manager.stop()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertTrue(client.writes)
        self.assertNotIn(b'"prompt"', client.writes[-1])

    async def test_keepalive_skips_while_request_is_pending(self) -> None:
        class FakeClient:
            is_connected = True

            def __init__(self) -> None:
                self.writes: list[bytes] = []

            async def write_gatt_char(self, _uuid, data, *args, **kwargs) -> None:
                self.writes.append(bytes(data))

        client = FakeClient()
        manager = PersistentBleBuddyManager(BleBuddyConfig(write_timeout=0.1, keepalive_interval=0.01))
        manager._client = client
        manager._connected.set()
        await manager._request_lock.acquire()
        task = asyncio.create_task(manager._keepalive_loop())
        try:
            await asyncio.sleep(0.03)
        finally:
            manager._request_lock.release()
            await manager.stop()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertEqual(client.writes, [])


class ServiceClientTests(unittest.TestCase):
    def test_call_permission_service_returns_hook_output(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), PermissionRequestHandler)
        server.decision_handler = lambda _: codex_allow_output()
        server.status_handler = lambda: {"ok": True, "ble_connected": False}
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = call_permission_service({"id": "req"}, port=server.server_port)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.assertEqual(result, codex_allow_output())

    def test_call_permission_service_returns_none_when_offline(self) -> None:
        self.assertIsNone(call_permission_service({"id": "req"}, port=9, timeout=0.01))

    def test_service_status_reports_ble_connection(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), PermissionRequestHandler)
        server.decision_handler = lambda _: codex_allow_output()
        server.status_handler = lambda: {"ok": True, "ble_connected": True}
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = service_status(port=server.server_port)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.assertEqual(result, {"ok": True, "ble_connected": True})

    def test_service_command_builds_serve_command(self) -> None:
        command = service_command(decision_timeout=30.0)

        self.assertIn("codex_ble_buddy.cli", command)
        self.assertIn("serve", command)
        self.assertIn("--timeout", command)

    def test_service_task_script_redirects_logs(self) -> None:
        script_path = write_service_task_script(decision_timeout=30.0)

        content = script_path.read_text(encoding="ascii")
        self.assertIn("codex_ble_buddy.cli", content)
        self.assertIn("service.log", content)
        self.assertIn("2>&1", content)

    def test_install_service_task_uses_cmd_wrapper(self) -> None:
        with patch("codex_ble_buddy.service.write_service_task_script") as write_script:
            with patch("codex_ble_buddy.service.write_service_task_launcher") as write_launcher:
                with patch("codex_ble_buddy.service.subprocess.run") as run:
                    write_script.return_value = r"C:\repo\.tmp\codex-ble-buddy-service.cmd"
                    write_launcher.return_value = r"C:\repo\.tmp\codex-ble-buddy-service.vbs"
                    run.return_value.returncode = 0

                    self.assertTrue(install_service_task())

        args = run.call_args.args[0]
        self.assertEqual(args[0], "schtasks")
        self.assertIn('wscript.exe //B //Nologo "C:\\repo\\.tmp\\codex-ble-buddy-service.vbs"', args)

    def test_service_task_launcher_hides_cmd_window(self) -> None:
        launcher_path = write_service_task_launcher(r"C:\repo\.tmp\codex-ble-buddy-service.cmd")

        content = launcher_path.read_text(encoding="ascii")
        self.assertIn("WScript.Shell", content)
        self.assertIn("cmd.exe /c", content)
        self.assertIn(", 0, False", content)

    def test_install_service_task_writes_launcher_for_script(self) -> None:
        with patch("codex_ble_buddy.service.write_service_task_script") as write_script:
            with patch("codex_ble_buddy.service.subprocess.run") as run:
                write_script.return_value = r"C:\repo\.tmp\codex-ble-buddy-service.cmd"
                run.return_value.returncode = 0

                self.assertTrue(install_service_task())

        write_script.assert_called_once()


class HookServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_hook_uses_service_when_available(self) -> None:
        with patch("codex_ble_buddy.hook.call_permission_service", return_value=codex_allow_output()):
            output = await run_permission_request({"id": "req", "tool": "shell", "command": "dir"}, BleBuddyConfig())

        self.assertEqual(output, codex_allow_output())

    async def test_hook_falls_back_when_service_unavailable(self) -> None:
        with patch("codex_ble_buddy.hook.call_permission_service", return_value=None):
            with patch("codex_ble_buddy.hook.BleBuddyClient") as client_type:
                client_type.return_value.request_decision = AsyncMock(return_value=None)
                output = await run_permission_request(
                    {"id": "req", "tool": "shell", "command": "dir"},
                    BleBuddyConfig(scan_timeout=0.01),
                )

        self.assertEqual(output, {})
        client_type.return_value.request_decision.assert_awaited_once()

    async def test_hook_auto_starts_service_when_enabled(self) -> None:
        with patch("codex_ble_buddy.hook.service_is_available", return_value=False):
            with patch("codex_ble_buddy.hook.task_is_installed", return_value=False):
                with patch("codex_ble_buddy.hook.start_service_background") as start:
                    with patch("codex_ble_buddy.hook.wait_for_service", return_value=True) as wait:
                        with patch("codex_ble_buddy.hook.call_permission_service", return_value=codex_allow_output()):
                            output = await run_permission_request(
                                {"id": "req", "tool": "shell", "command": "dir"},
                                BleBuddyConfig(),
                                auto_start_service=True,
                            )

        self.assertEqual(output, codex_allow_output())
        start.assert_called_once()
        wait.assert_called_once()

    async def test_hook_auto_starts_installed_task_when_enabled(self) -> None:
        with patch("codex_ble_buddy.hook.service_is_available", return_value=False):
            with patch("codex_ble_buddy.hook.task_is_installed", return_value=True):
                with patch("codex_ble_buddy.hook.start_service_task") as start:
                    with patch("codex_ble_buddy.hook.wait_for_service", return_value=True):
                        with patch("codex_ble_buddy.hook.call_permission_service", return_value=codex_allow_output()):
                            output = await run_permission_request(
                                {"id": "req", "tool": "shell", "command": "dir"},
                                BleBuddyConfig(),
                                auto_start_service=True,
                            )

        self.assertEqual(output, codex_allow_output())
        start.assert_called_once()


if __name__ == "__main__":
    unittest.main()
