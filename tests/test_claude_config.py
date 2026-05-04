import io
import json
import unittest
from pathlib import Path

from codex_ble_buddy.claude_config import (
    build_claude_hook_group,
    command_is_managed,
    default_claude_settings_path,
    has_managed_claude_hook_settings,
    prompt_settings_path,
    setup_claude_settings,
    upsert_claude_hook_settings,
)


class ClaudeConfigTests(unittest.TestCase):
    def test_build_claude_hook_group_matches_all_permission_requests(self) -> None:
        group = build_claude_hook_group("python -m codex_ble_buddy.cli approve-request --timeout 30", 30.0)

        self.assertEqual(group["matcher"], "*")
        self.assertEqual(group["hooks"][0]["type"], "command")
        self.assertIn("approve-request", group["hooks"][0]["command"])
        self.assertEqual(group["hooks"][0]["timeout"], 30)

    def test_command_is_managed_detects_known_commands(self) -> None:
        self.assertTrue(command_is_managed("python -m codex_ble_buddy.cli approve-request --timeout 30"))
        self.assertTrue(command_is_managed(r"C:\Users\me\Scripts\codex-ble-buddy.exe approve-request --timeout 30"))
        self.assertTrue(command_is_managed("codex-ble-buddy approve-request --timeout 30"))
        self.assertTrue(command_is_managed("python C:\\repo\\scripts\\codex_permission_hook.py --timeout 30"))
        self.assertFalse(command_is_managed("python other_hook.py"))
        self.assertFalse(command_is_managed(None))

    def test_upsert_replaces_managed_handler_and_preserves_other_handlers(self) -> None:
        existing = {
            "hooks": {
                "PermissionRequest": [
                    {
                        "matcher": "*",
                        "hooks": [
                            {"type": "command", "command": "python -m codex_ble_buddy.cli approve-request --timeout 10"},
                            {"type": "command", "command": "python other_hook.py"},
                        ],
                    }
                ]
            }
        }

        updated = upsert_claude_hook_settings(existing, "python -m codex_ble_buddy.cli approve-request --timeout 30", 30.0)
        groups = updated["hooks"]["PermissionRequest"]
        commands = [handler["command"] for group in groups for handler in group.get("hooks", [])]

        self.assertIn("python other_hook.py", commands)
        self.assertIn("python -m codex_ble_buddy.cli approve-request --timeout 30", commands)
        self.assertNotIn("python -m codex_ble_buddy.cli approve-request --timeout 10", commands)

    def test_has_managed_claude_hook_settings_detects_managed_handler(self) -> None:
        path = Path("test-claude-settings.json")
        try:
            settings = upsert_claude_hook_settings({}, "python -m codex_ble_buddy.cli approve-request --timeout 30", 30.0)
            path.write_text(json.dumps(settings), encoding="utf-8")

            self.assertTrue(has_managed_claude_hook_settings(path))
        finally:
            if path.exists():
                path.unlink()

    def test_prompt_settings_path_supports_chinese(self) -> None:
        stdout = io.StringIO()

        result = prompt_settings_path(Path(r"C:\Users\me\.claude\settings.json"), io.StringIO("\n"), stdout, language="zh")

        self.assertEqual(result, Path(r"C:\Users\me\.claude\settings.json"))
        self.assertIn("Claude Code 设置文件路径", stdout.getvalue())

    def test_default_claude_settings_path_uses_current_home(self) -> None:
        self.assertEqual(default_claude_settings_path(), Path.home() / ".claude" / "settings.json")

    def test_setup_claude_settings_supports_auto_start_service(self) -> None:
        path = Path("test-claude-auto-start-settings.json")
        stdout = io.StringIO()
        try:
            result = setup_claude_settings(
                timeout=30.0,
                settings_path=path,
                assume_yes=True,
                auto_start_service=True,
                stdout=stdout,
            )

            settings = json.loads(path.read_text(encoding="utf-8"))
            command = settings["hooks"]["PermissionRequest"][-1]["hooks"][0]["command"]

            self.assertEqual(result, 0)
            self.assertIn("--auto-start-service", command)
        finally:
            if path.exists():
                path.unlink()


if __name__ == "__main__":
    unittest.main()
