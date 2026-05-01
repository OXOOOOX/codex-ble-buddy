import json
import unittest

from codex_ble_buddy.protocol import (
    PermissionPrompt,
    ProtocolError,
    codex_allow_output,
    codex_deny_output,
    codex_no_decision_output,
    decode_decision,
    encode_permission_prompt,
    prompt_from_codex_hook,
)


class ProtocolTests(unittest.TestCase):
    def test_encode_permission_prompt_adds_newline_json(self) -> None:
        prompt = PermissionPrompt(
            request_id="abc",
            title="Codex approval request",
            tool="shell",
            command="dir",
            message="test",
        )

        encoded = encode_permission_prompt(prompt)

        self.assertTrue(encoded.endswith(b"\n"))
        payload = json.loads(encoded.decode("utf-8"))
        self.assertEqual(payload["waiting"], 1)
        self.assertEqual(payload["msg"], "approve: shell")
        self.assertEqual(payload["prompt"]["id"], "abc")
        self.assertEqual(payload["prompt"]["tool"], "shell")
        self.assertEqual(payload["prompt"]["hint"], "dir")

    def test_decode_legacy_allow_decision(self) -> None:
        decision = decode_decision('{"type":"decision","id":"abc","decision":"allow"}', "abc")

        self.assertTrue(decision.is_allow)
        self.assertFalse(decision.is_deny)

    def test_decode_legacy_deny_decision(self) -> None:
        decision = decode_decision(b'{"type":"decision","id":"abc","decision":"deny"}\n', "abc")

        self.assertTrue(decision.is_deny)

    def test_decode_codebuddy_once_decision_as_allow(self) -> None:
        decision = decode_decision('{"cmd":"permission","id":"abc","decision":"once"}', "abc")

        self.assertTrue(decision.is_allow)

    def test_decode_codebuddy_deny_decision(self) -> None:
        decision = decode_decision('{"cmd":"permission","id":"abc","decision":"deny"}', "abc")

        self.assertTrue(decision.is_deny)

    def test_decode_rejects_wrong_request_id(self) -> None:
        with self.assertRaises(ProtocolError):
            decode_decision('{"type":"decision","id":"other","decision":"allow"}', "abc")

    def test_decode_rejects_unknown_decision(self) -> None:
        with self.assertRaises(ProtocolError):
            decode_decision('{"type":"decision","id":"abc","decision":"maybe"}', "abc")

    def test_prompt_from_codex_hook_extracts_known_fields(self) -> None:
        prompt = prompt_from_codex_hook(
            {"id": "req1", "tool": "shell", "command": "npm install", "reason": "needs network"}
        )

        self.assertEqual(prompt.request_id, "req1")
        self.assertEqual(prompt.tool, "shell")
        self.assertEqual(prompt.command, "npm install")
        self.assertEqual(prompt.message, "needs network")

    def test_codex_outputs(self) -> None:
        self.assertEqual(
            codex_allow_output()["hookSpecificOutput"]["decision"]["behavior"],
            "allow",
        )
        self.assertEqual(
            codex_deny_output()["hookSpecificOutput"]["decision"]["behavior"],
            "deny",
        )
        self.assertEqual(codex_no_decision_output(), {})


if __name__ == "__main__":
    unittest.main()
