"""Interactive Codex configuration helper."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import TextIO

BEGIN_MARKER = "# BEGIN codex-ble-buddy PermissionRequest hook"
END_MARKER = "# END codex-ble-buddy PermissionRequest hook"
SUPPORTED_LANGUAGES = ("en", "zh")
CANCEL_ANSWERS = {"q", "quit", "cancel"}
YES_ANSWERS = {"y", "yes"}

MESSAGES = {
    "en": {
        "config_path_prompt": "Codex config path [{default_path}]: ",
        "install_intro": "\nThe following Codex hook configuration will be installed:\n\n",
        "target_file": "\nTarget file: {config_path}\n",
        "confirmation_skipped": "Confirmation skipped because --yes was provided.\n",
        "write_prompt": "Write this configuration? [y/N]: ",
        "cancelled": "Cancelled.\n",
        "write_failed": "Failed to write Codex config: {error}\n",
        "configured": "Codex hook configured: {config_path}\n",
    },
    "zh": {
        "config_path_prompt": "Codex 配置文件路径 [{default_path}]: ",
        "install_intro": "\n将安装以下 Codex hook 配置：\n\n",
        "target_file": "\n目标文件：{config_path}\n",
        "confirmation_skipped": "已提供 --yes，跳过确认。\n",
        "write_prompt": "是否写入此配置？[y/N]：",
        "cancelled": "已取消。\n",
        "write_failed": "写入 Codex 配置失败：{error}\n",
        "configured": "Codex hook 已配置：{config_path}\n",
    },
}


def normalize_language(language: str) -> str:
    """Return a supported language code."""

    normalized = language.lower()
    if normalized in SUPPORTED_LANGUAGES:
        return normalized
    return "en"


def default_codex_config_path() -> Path:
    """Return the default Codex config path for the current system."""

    return Path.home() / ".codex" / "config.toml"


def build_hook_command(timeout: float) -> str:
    """Build a shell command that uses the current Python environment."""

    args = [
        sys.executable,
        "-m",
        "codex_ble_buddy.cli",
        "approve-request",
        "--timeout",
        str(int(timeout) if timeout.is_integer() else timeout),
    ]
    if os.name == "nt":
        return subprocess.list2cmdline(args)
    return shlex.join(str(arg) for arg in args)


def toml_string(value: str) -> str:
    """Return a double-quoted TOML string."""

    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def hook_config_block(command: str) -> str:
    return "\n".join(
        [
            BEGIN_MARKER,
            "[features]",
            "codex_hooks = true",
            "",
            "[[hooks.PermissionRequest]]",
            'matcher = ".*"',
            "",
            "[[hooks.PermissionRequest.hooks]]",
            'type = "command"',
            f"command = {toml_string(command)}",
            "timeout = 30",
            'statusMessage = "Checking approval request"',
            END_MARKER,
            "",
        ]
    )


def upsert_hook_block(existing: str, block: str) -> str:
    """Insert or replace the managed hook block."""

    start = existing.find(BEGIN_MARKER)
    end = existing.find(END_MARKER)
    if start != -1 and end != -1 and end > start:
        end += len(END_MARKER)
        suffix = existing[end:]
        if suffix.startswith("\r\n"):
            suffix = suffix[2:]
        elif suffix.startswith("\n"):
            suffix = suffix[1:]
        return existing[:start].rstrip() + "\n\n" + block + suffix.lstrip("\r\n")

    prefix = existing.rstrip()
    if prefix:
        return prefix + "\n\n" + block
    return block


def has_managed_hook_config(config_path: Path | None = None) -> bool:
    """Return whether the managed Codex hook block is present."""

    path = config_path or default_codex_config_path()
    try:
        contents = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return BEGIN_MARKER in contents and END_MARKER in contents


def prompt_config_path(default_path: Path, stdin: TextIO, stdout: TextIO, language: str = "en") -> Path | None:
    messages = MESSAGES[normalize_language(language)]
    stdout.write(messages["config_path_prompt"].format(default_path=default_path))
    stdout.flush()
    answer = stdin.readline().strip()
    if answer.lower() in CANCEL_ANSWERS:
        return None
    if not answer:
        return default_path
    return Path(answer.strip('"')).expanduser()


def confirm_write(
    config_path: Path,
    block: str,
    stdin: TextIO,
    stdout: TextIO,
    assume_yes: bool,
    language: str = "en",
) -> bool:
    messages = MESSAGES[normalize_language(language)]
    stdout.write(messages["install_intro"])
    stdout.write(block)
    stdout.write(messages["target_file"].format(config_path=config_path))
    if assume_yes:
        stdout.write(messages["confirmation_skipped"])
        return True
    stdout.write(messages["write_prompt"])
    stdout.flush()
    return stdin.readline().strip().lower() in YES_ANSWERS


def setup_codex_config(
    timeout: float,
    config_path: Path | None = None,
    assume_yes: bool = False,
    language: str = "en",
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
) -> int:
    """Interactively configure the Codex PermissionRequest hook."""

    language = normalize_language(language)
    messages = MESSAGES[language]
    selected_path = config_path
    if selected_path is None:
        selected_path = prompt_config_path(default_codex_config_path(), stdin, stdout, language)
        if selected_path is None:
            stdout.write(messages["cancelled"])
            return 1

    command = build_hook_command(timeout)
    block = hook_config_block(command)
    if not confirm_write(selected_path, block, stdin, stdout, assume_yes, language):
        stdout.write(messages["cancelled"])
        return 1

    try:
        existing = selected_path.read_text(encoding="utf-8") if selected_path.exists() else ""
        updated = upsert_hook_block(existing, block)
        selected_path.parent.mkdir(parents=True, exist_ok=True)
        selected_path.write_text(updated, encoding="utf-8")
    except OSError as exc:
        stdout.write(messages["write_failed"].format(error=exc))
        return 1
    stdout.write(messages["configured"].format(config_path=selected_path))
    return 0
