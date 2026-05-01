"""JSON protocol mapping between Codex hooks and BLE Buddy firmware."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any


class ProtocolError(ValueError):
    """Raised when a BLE protocol message is invalid."""


@dataclass(frozen=True)
class PermissionPrompt:
    request_id: str
    title: str
    tool: str
    command: str
    message: str


@dataclass(frozen=True)
class Decision:
    request_id: str
    decision: str

    @property
    def is_allow(self) -> bool:
        return self.decision == "allow"

    @property
    def is_deny(self) -> bool:
        return self.decision == "deny"


def make_request_id() -> str:
    return uuid.uuid4().hex


def _first_string(data: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _extract_nested_string(data: dict[str, Any], paths: tuple[tuple[str, ...], ...]) -> str:
    for path in paths:
        cursor: Any = data
        for key in path:
            if not isinstance(cursor, dict):
                cursor = None
                break
            cursor = cursor.get(key)
        if isinstance(cursor, str) and cursor.strip():
            return cursor.strip()
    return ""


def prompt_from_codex_hook(payload: dict[str, Any]) -> PermissionPrompt:
    """Build a device prompt from a Codex PermissionRequest hook payload."""

    request_id = _first_string(payload, ("id", "request_id", "requestId")) or make_request_id()
    tool = (
        _first_string(payload, ("tool", "tool_name", "toolName", "name"))
        or _extract_nested_string(payload, (("tool", "name"), ("permission", "tool")))
        or "Codex"
    )
    command = (
        _first_string(payload, ("command", "cmd", "summary"))
        or _extract_nested_string(payload, (("input", "command"), ("arguments", "command")))
    )
    reason = (
        _first_string(payload, ("reason", "message", "description"))
        or _extract_nested_string(payload, (("permission", "reason"), ("input", "reason")))
    )

    if not command:
        command = summarize_payload(payload)

    return PermissionPrompt(
        request_id=request_id,
        title="Codex approval request",
        tool=tool,
        command=truncate(command, 180),
        message=truncate(reason or "Approve this Codex request?", 180),
    )


def summarize_payload(payload: dict[str, Any]) -> str:
    """Return a compact fallback summary for unknown hook payload shapes."""

    try:
        return truncate(json.dumps(payload, ensure_ascii=False, sort_keys=True), 180)
    except TypeError:
        return "Codex PermissionRequest"


def truncate(value: str, max_len: int) -> str:
    clean = " ".join(value.split())
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 1] + "..."


def encode_permission_prompt(prompt: PermissionPrompt) -> bytes:
    """Encode a CodeBuddy-compatible heartbeat snapshot with a pending prompt."""

    message = {
        "total": 1,
        "running": 0,
        "waiting": 1,
        "msg": truncate(f"approve: {prompt.tool}", 80),
        "entries": [truncate(prompt.command, 120)],
        "tokens": 0,
        "tokens_today": 0,
        "prompt": {
            "id": prompt.request_id,
            "tool": prompt.tool,
            "hint": prompt.command or prompt.message,
        },
    }
    return (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def decode_decision(data: bytes | str, expected_request_id: str | None = None) -> Decision:
    if isinstance(data, bytes):
        text = data.decode("utf-8", errors="strict").strip()
    else:
        text = data.strip()
    if not text:
        raise ProtocolError("empty decision message")

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"invalid decision JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ProtocolError("decision message must be a JSON object")
    is_legacy_decision = payload.get("type") == "decision"
    is_codebuddy_decision = payload.get("cmd") == "permission"
    if not is_legacy_decision and not is_codebuddy_decision:
        raise ProtocolError("decision message must be a decision or permission command")

    request_id = payload.get("id")
    if not isinstance(request_id, str) or not request_id:
        raise ProtocolError("decision id is required")
    if expected_request_id is not None and request_id != expected_request_id:
        raise ProtocolError("decision id does not match request id")

    decision = payload.get("decision")
    if is_codebuddy_decision and decision == "once":
        decision = "allow"
    if decision not in ("allow", "deny"):
        raise ProtocolError("decision must be 'allow', 'once', or 'deny'")

    return Decision(request_id=request_id, decision=decision)


def codex_allow_output() -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": {"behavior": "allow"},
        }
    }


def codex_deny_output(message: str = "Denied from BLE Buddy device.") -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": {"behavior": "deny", "message": message},
        }
    }


def codex_no_decision_output() -> dict[str, Any]:
    """Return an empty hook output so Codex can continue its normal flow."""

    return {}
