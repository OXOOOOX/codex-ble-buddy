import io
import unittest
from pathlib import Path

from codex_ble_buddy.codex_config import (
    build_hook_command,
    confirm_write,
    default_codex_config_path,
    has_managed_hook_config,
    hook_config_block,
    prompt_config_path,
    toml_string,
    upsert_approval_policy,
    upsert_hook_block,
)


class CodexConfigTests(unittest.TestCase):
    def test_toml_string_escapes_windows_path(self) -> None:
        self.assertEqual(toml_string(r'C:\Path With Spaces\python.exe "x"'), r'"C:\\Path With Spaces\\python.exe \"x\""')

    def test_hook_config_block_contains_permission_request(self) -> None:
        block = hook_config_block("python -m codex_ble_buddy.cli approve-request --timeout 30")

        self.assertIn("[features]", block)
        self.assertIn("codex_hooks = true", block)
        self.assertIn("[[hooks.PermissionRequest]]", block)
        self.assertIn('matcher = ".*"', block)
        self.assertIn("[[hooks.PermissionRequest.hooks]]", block)
        self.assertIn('type = "command"', block)
        self.assertIn("command =", block)
        self.assertIn("timeout = 30", block)
        self.assertIn("BEGIN codex-ble-buddy", block)

    def test_build_hook_command_supports_auto_start_service(self) -> None:
        command = build_hook_command(30.0, auto_start_service=True)

        self.assertIn("approve-request", command)
        self.assertIn("--auto-start-service", command)

    def test_upsert_hook_block_appends_when_missing(self) -> None:
        updated = upsert_hook_block("model = \"x\"\n", hook_config_block("cmd"))

        self.assertIn('model = "x"', updated)
        self.assertIn("[[hooks.PermissionRequest]]", updated)

    def test_upsert_hook_block_replaces_managed_block(self) -> None:
        old = upsert_hook_block("model = \"x\"\n", hook_config_block("old"))
        new = upsert_hook_block(old, hook_config_block("new"))

        self.assertIn("new", new)
        self.assertNotIn("old", new)
        self.assertEqual(new.count("[[hooks.PermissionRequest]]"), 1)

    def test_upsert_approval_policy_replaces_existing_policy(self) -> None:
        updated = upsert_approval_policy('model = "x"\napproval_policy = "on-request"\n')

        self.assertIn('approval_policy = "untrusted"', updated)
        self.assertNotIn('approval_policy = "on-request"', updated)

    def test_upsert_approval_policy_inserts_before_first_table(self) -> None:
        updated = upsert_approval_policy('model = "x"\n\n[features]\ncodex_hooks = true\n')

        self.assertLess(updated.index('approval_policy = "untrusted"'), updated.index("[features]"))

    def test_prompt_config_path_supports_chinese(self) -> None:
        stdout = io.StringIO()

        result = prompt_config_path(Path(r"C:\Users\me\.codex\config.toml"), io.StringIO("\n"), stdout, language="zh")

        self.assertEqual(result, Path(r"C:\Users\me\.codex\config.toml"))
        self.assertIn("Codex 配置文件路径", stdout.getvalue())

    def test_default_codex_config_path_uses_current_home(self) -> None:
        self.assertEqual(default_codex_config_path(), Path.home() / ".codex" / "config.toml")

    def test_confirm_write_supports_chinese(self) -> None:
        stdout = io.StringIO()

        confirmed = confirm_write(Path("config.toml"), hook_config_block("cmd"), io.StringIO("y\n"), stdout, False, language="zh")

        self.assertTrue(confirmed)
        output = stdout.getvalue()
        self.assertIn("将安装以下 Codex hook 配置", output)
        self.assertIn("是否写入此配置", output)

    def test_has_managed_hook_config_detects_managed_block(self) -> None:
        path = Path("test-managed-config.toml")
        try:
            path.write_text(hook_config_block("cmd"), encoding="utf-8")

            self.assertTrue(has_managed_hook_config(path))
        finally:
            if path.exists():
                path.unlink()

    def test_has_managed_hook_config_returns_false_for_missing_file(self) -> None:
        self.assertFalse(has_managed_hook_config(Path("does-not-exist.toml")))


if __name__ == "__main__":
    unittest.main()
