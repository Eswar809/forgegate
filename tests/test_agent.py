"""Integration tests for the agent loop: forge, verify, memory, sessions."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from forgegate.agent import Agent
from forgegate.judge import SemanticJudge

REPO = Path(__file__).resolve().parents[1]


class NoModel:
    """Deterministic stand-in for an unconfigured LLM endpoint."""

    def available(self) -> bool:
        return False

    def complete(self, *args: object, **kwargs: object) -> str:
        raise AssertionError("no model should be called")


class FixtureFromRepo:
    def __init__(self) -> None:
        self.dir = REPO / "bench" / "fixtures"

    def generate(self, task: dict, context: str = "") -> str:
        fixture = task.get("fixture") or task["tool_name"]
        return (self.dir / f"{fixture}.py").read_text(encoding="utf-8")


class AgentLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        shutil.copytree(REPO / "bench" / "data", self.root / "bench" / "data")
        self.agent = Agent(
            self.root,
            generator=FixtureFromRepo(),
            session_id="test-run",
            llm=NoModel(),
        )
        self.task = json.loads(
            (REPO / "bench" / "tasks.jsonl").read_text(encoding="utf-8").splitlines()[0]
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_forge_then_reuse(self) -> None:
        first = self.agent.handle(self.task)
        self.assertEqual(first["mode"], "forged")
        self.assertTrue(first["ok"])
        self.assertIsNotNone(first["gate"])
        self.assertTrue(first["gate"]["ok"])
        self.assertGreaterEqual(first["memory_writes"], 1)

        second = self.agent.handle(self.task)
        self.assertEqual(second["mode"], "reuse")
        self.assertTrue(second["ok"])
        self.assertEqual(second["tool"], first["tool"])

    def test_session_is_recorded(self) -> None:
        self.agent.handle(self.task)
        messages = self.agent.sessions.messages("test-run")
        self.assertEqual([m["role"] for m in messages], ["user", "assistant"])
        self.assertEqual(
            json.loads(messages[1]["content"])["mode"], "forged"
        )

    def test_verified_run_writes_an_episodic_fact(self) -> None:
        self.agent.handle(self.task)
        facts = self.agent.memory.active_facts()
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["kind"], "episodic")
        self.assertEqual(facts[0]["source"], f"task:{self.task['id']}")

    def test_requested_judge_fails_closed_without_a_model(self) -> None:
        verdict = self.agent._judge(
            {"description": "inspect the sync failure", "judge": True},
            "SYNC-1042 failed",
        )
        self.assertIsNotNone(verdict)
        self.assertFalse(verdict.ok)
        self.assertIn("unavailable", verdict.reason)

    def test_judge_is_skipped_when_not_requested(self) -> None:
        self.assertIsNone(self.agent._judge({"description": "x"}, "output"))

    def test_judge_requested_without_a_model_rejects_before_forging(self) -> None:
        task = dict(self.task)
        task["id"] = "judge-gate"
        task["judge"] = True
        result = self.agent.handle(task)
        self.assertEqual(result["mode"], "rejected")
        self.assertIn("judge", result["error"])
        self.assertEqual(self.agent.registry.all(), [])


if __name__ == "__main__":
    unittest.main()
