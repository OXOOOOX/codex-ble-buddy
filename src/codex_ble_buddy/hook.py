"""Codex PermissionRequest hook flow."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any, TextIO

from .ble import BleBuddyClient
from .config import BleBuddyConfig
from .protocol import (
    codex_allow_output,
    codex_deny_output,
    codex_no_decision_output,
    prompt_from_codex_hook,
)
from .service import call_permission_service, service_request_timeout

logger = logging.getLogger(__name__)


async def run_permission_request(
    payload: dict[str, Any],
    config: BleBuddyConfig,
    use_service: bool = True,
) -> dict[str, Any]:
    if use_service:
        output = call_permission_service(payload, timeout=service_request_timeout(config))
        if output is not None:
            logger.info("Permission request handled by local BLE Buddy service")
            return output
        logger.info("Local BLE Buddy service unavailable; falling back to one-shot BLE request")

    prompt = prompt_from_codex_hook(payload)
    client = BleBuddyClient(config)
    decision = await client.request_decision(prompt)

    if decision is None:
        logger.warning("No BLE decision received; returning no decision")
        return codex_no_decision_output()
    if decision.is_allow:
        logger.info("BLE Buddy allowed request %s", decision.request_id)
        return codex_allow_output()
    if decision.is_deny:
        logger.info("BLE Buddy denied request %s", decision.request_id)
        return codex_deny_output()

    logger.warning("Unknown decision state; returning no decision")
    return codex_no_decision_output()


def read_stdin_json(stdin: TextIO = sys.stdin) -> dict[str, Any]:
    raw = stdin.read().lstrip("\ufeff")
    if not raw.strip():
        logger.warning("Hook received empty stdin")
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("Hook received invalid JSON: %s", exc)
        return {}
    if not isinstance(payload, dict):
        logger.error("Hook payload must be a JSON object")
        return {}
    return payload


def write_hook_output(output: dict[str, Any], stdout: TextIO = sys.stdout) -> None:
    stdout.write(json.dumps(output, ensure_ascii=False, separators=(",", ":")))
    stdout.write("\n")
    stdout.flush()


def run_hook(
    config: BleBuddyConfig,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
    use_service: bool = True,
) -> int:
    payload = read_stdin_json(stdin)
    if not payload:
        write_hook_output(codex_no_decision_output(), stdout)
        return 0

    try:
        output = asyncio.run(run_permission_request(payload, config, use_service=use_service))
    except RuntimeError as exc:
        logger.error("Hook failed safely: %s", exc)
        output = codex_no_decision_output()
    except Exception:
        logger.exception("Hook failed; returning no decision")
        output = codex_no_decision_output()
    write_hook_output(output, stdout)
    return 0
