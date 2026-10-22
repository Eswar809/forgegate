"""Tests for the fail-closed semantic judge."""

from __future__ import annotations

import unittest

from forgegate.judge import SemanticJudge


class FakeClient:
    def __init__(self, reply: str, available: bool = True) -> None:
        self.reply = reply
        self._available = available

    def available(self) -> bool:
        return self._available

    def complete(self, system: str, user: str, **kwargs: object) -> str:
        return self.reply


class JudgeTests(unittest.TestCase):
    def test_pass_verdict(self) -> None:
        client = FakeClient(
            '{"pass": true, "score": 0.91, "reason": "job id and cause present",'
            ' "evidence": ["SYNC-1042", "timeout"]}'
        )
        verdict = SemanticJudge(client).judge(
            "Inspect the ACME sync failure",
            "SYNC-1042 failed with timeout after 30s",
            expected=["SYNC-1042", "timeout"],
        )
        self.assertTrue(verdict.ok)
        self.assertAlmostEqual(verdict.score, 0.91)
        self.assertEqual(verdict.evidence, ["SYNC-1042", "timeout"])

    def test_fenced_json_is_accepted(self) -> None:
        client = FakeClient(
            '```json\n{"pass": false, "score": 0.2, '
            '"reason": "missing job id", "evidence": []}\n```'
        )
        verdict = SemanticJudge(client).judge("task", "nothing useful")
        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.reason, "missing job id")

    def test_malformed_reply_fails_closed(self) -> None:
        verdict = SemanticJudge(FakeClient("looks fine to me")).judge(
            "task", "output"
        )
        self.assertFalse(verdict.ok)
        self.assertIn("failed closed", verdict.reason)

    def test_missing_pass_key_fails_closed(self) -> None:
        verdict = SemanticJudge(
            FakeClient('{"score": 0.99, "reason": "great"}')
        ).judge("task", "output")
        self.assertFalse(verdict.ok)
        self.assertIn("boolean", verdict.reason)

    def test_unavailable_judge_fails_closed(self) -> None:
        verdict = SemanticJudge(FakeClient("{}", available=False)).judge(
            "task", "output"
        )
        self.assertFalse(verdict.ok)
        self.assertIn("unavailable", verdict.reason)


if __name__ == "__main__":
    unittest.main()
