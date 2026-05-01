"""Interactive Claude Code configuration helper."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, TextIO

from .codex_config import build_hook_command, normalize_language

MANAGED_COMMAND_MARKERS = (
    "codex_ble_buddy.cli approve-request",
    "codex_permission_hook.py",
)
SUPPORTED_LANGUAGES = ("en", "zh")
CANCEL_ANSWERS = {"q", "quit", "cancel"}
YES_ANSWERS = {"y", "yes"}

MESSAGES = {
    "en": {
        "settings_path_prompt": "Claude Code settings path [{default_path}]: ",
        "install_intro": "\nThe following Claude Code hook configuration will be installed:\n\n",
        "target_file": "\nTarget file: {settings_path}\n",
        "confirmation_skipped": "Confirmation skipped because --yes was provided.\n",
        "write_prompt": "Write this configuration? [y/N]: ",
        "cancelled": "Cancelled.\n",
        "read_failed": "Failed to read Claude Code settings: {error}\n",
        "write_failed": "Failed to write Claude Code settings: {error}\n",
        "configured": "Claude Code hook configured: {settings_path}\n",
    },
    "zh": {
        "settings_path_prompt": "Claude Code 设置文件路径 [{default_path}]: ",
        "install_intro": "\n将安装以下 Claude Code hook 配置：\n\n",
        "target_file": "\n目标文件：{settings_path}\n",
        "confirmation_skipped": "已提供 --yes，跳过确认。\n",
        "write_prompt": "是否写入此配置？[y/N]：",
        "cancelled": "已取消。\n",
        "read_failed": "读取 Claude Code 设置失败：{error}\n",
        "write_failed": "写入 Claude Code 设置失败：{error}\n",
        "configured": "Claude Code hook 已配置：{settings_path}\n",
    },
}


def default_claude_settings_path() -> Path:
    """Return the default Claude Code user settings path."""

    return Path.home() / ".claude" / "settings.json"


def command_is_managed(command: object) -> bool:
    if not isinstance(command, str):
        return False
    return any(marker in command for marker in MANAGED_COMMAND_MARKERS)


def build_claude_hook_group(command: str, timeout: float) -> dict[str, Any]:
    """Build a Claude Code PermissionRequest hook group."""

    timeout_value = int(timeout) if timeout.is_integer() else timeout
    return {
        "matcher": "*",
        "hooks": [
            {
                "type": "command",
                "command": command,
                "timeout": timeout_value,
            }
        ],
    }


def upsert_claude_hook_settings(existing: dict[str, Any], command: str, timeout: float) -> dict[str, Any]:
    """Insert or replace the managed Claude Code PermissionRequest hook."""

    updated = deepcopy(existing)
    hooks = updated.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
        updated["hooks"] = hooks

    groups = hooks.get("PermissionRequest")
    if not isinstance(groups, list):
        groups = []

    cleaned_groups: list[Any] = []
    for group in groups:
        if not isinstance(group, dict):
            cleaned_groups.append(group)
            continue
        handlers = group.get("hooks")
        if not isinstance(handlers, list):
            cleaned_groups.append(group)
            continue
        cleaned_handlers = [
            handler
            for handler in handlers
            if not (isinstance(handler, dict) and command_is_managed(handler.get("command")))
        ]
        if cleaned_handlers:
            cleaned_group = deepcopy(group)
            cleaned_group["hooks"] = cleaned_handlers
            cleaned_groups.append(cleaned_group)

    cleaned_groups.append(build_claude_hook_group(command, timeout))
    hooks["PermissionRequest"] = cleaned_groups
    return updated


def has_managed_claude_hook_settings(settings_path: Path | None = None) -> bool:
    """Return whether the managed Claude Code hook is present."""

    path = settings_path or default_claude_settings_path()
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(settings, dict):
        return False
    groups = settings.get("hooks", {}).get("PermissionRequest", [])
    if not isinstance(groups, list):
        return False
    for group in groups:
        if not isinstance(group, dict):
            continue
        handlers = group.get("hooks")
        if not isinstance(handlers, list):
            continue
        if any(isinstance(handler, dict) and command_is_managed(handler.get("command")) for handler in handlers):
            return True
    return False


def prompt_settings_path(default_path: Path, stdin: TextIO, stdout: TextIO, language: str = "en") -> Path | None:
    messages = MESSAGES[normalize_language(language)]
    stdout.write(messages["settings_path_prompt"].format(default_path=default_path))
    stdout.flush()
    answer = stdin.readline().strip()
    if answer.lower() in CANCEL_ANSWERS:
        return None
    if not answer:
        return default_path
    return Path(answer.strip('"')).expanduser()


def confirm_write(
    settings_path: Path,
    settings: dict[str, Any],
    stdin: TextIO,
    stdout: TextIO,
    assume_yes: bool,
    language: str = "en",
) -> bool:
    messages = MESSAGES[normalize_language(language)]
    stdout.write(messages["install_intro"])
    stdout.write(json.dumps(settings, ensure_ascii=False, indent=2))
    stdout.write("\n")
    stdout.write(messages["target_file"].format(settings_path=settings_path))
    if assume_yes:
        stdout.write(messages["confirmation_skipped"])
        return True
    stdout.write(messages["write_prompt"])
    stdout.flush()
    return stdin.readline().strip().lower() in YES_ANSWERS


def setup_claude_settings(
    timeout: float,
    settings_path: Path | None = None,
    assume_yes: bool = False,
    language: str = "en",
    stdin: TextIO = None,
    stdout: TextIO = None,
) -> int:
    """Interactively configure the Claude Code PermissionRequest hook."""

    import sys

    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    language = normalize_language(language)
    messages = MESSAGES[language]
    selected_path = settings_path
    if selected_path is None:
        selected_path = prompt_settings_path(default_claude_settings_path(), stdin, stdout, language)
        if selected_path is None:
            stdout.write(messages["cancelled"])
            return 1

    try:
        existing = json.loads(selected_path.read_text(encoding="utf-8")) if selected_path.exists() else {}
    except (OSError, json.JSONDecodeError) as exc:
        stdout.write(messages["read_failed"].format(error=exc))
        return 1
    if not isinstance(existing, dict):
        stdout.write(messages["read_failed"].format(error="settings root must be a JSON object"))
        return 1

    command = build_hook_command(timeout)
    updated = upsert_claude_hook_settings(existing, command, timeout)
    if not confirm_write(selected_path, updated, stdin, stdout, assume_yes, language):
        stdout.write(messages["cancelled"])
        return 1

    try:
        selected_path.parent.mkdir(parents=True, exist_ok=True)
        selected_path.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        stdout.write(messages["write_failed"].format(error=exc))
        return 1
    stdout.write(messages["configured"].format(settings_path=selected_path))
    return 0
