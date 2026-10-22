"""Tests for temporal fact memory, batch semantics and behavioral approvals."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from forgegate.core import Memory


class FactMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.memory = Memory(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_add_and_search(self) -> None:
        result = self.memory.remember_fact(
            "Service payments-api writes to the payments table",
            source="task:t01", tags=["payments", "database"],
        )
        self.assertEqual(result["event"], "ADD")
        hits = self.memory.search_facts("payments database")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["id"], result["id"])

    def test_duplicate_is_none(self) -> None:
        text = "Payments API runs on port 8080"
        first = self.memory.remember_fact(text, source="t1")
        second = self.memory.remember_fact(text, source="t2")
        self.assertEqual(first["event"], "ADD")
        self.assertEqual(second["event"], "NONE")
        self.assertEqual(len(self.memory.fact_list()), 1)

    def test_update_invalidates_old_version(self) -> None:
        first = self.memory.remember_fact(
            "Payments API runs on port 8080", source="t1"
        )
        second = self.memory.remember_fact(
            "Payments API runs on port 9090", source="t2"
        )
        self.assertEqual(second["event"], "UPDATE")
        self.assertEqual(second["old_id"], first["id"])
        active = self.memory.fact_list()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["id"], second["id"])
        everything = self.memory.fact_list(include_invalid=True)
        old = next(f for f in everything if f["id"] == first["id"])
        self.assertIsNotNone(old["valid_to"])
        self.assertEqual(old["invalidated_by"], second["id"])

    def test_invalidate_keeps_history(self) -> None:
        fact = self.memory.remember_fact(
            "The legacy billing worker is deprecated", source="t1"
        )
        self.assertTrue(self.memory.invalidate_fact(
            fact["id"], reason="worker removed", source="test"
        ))
        self.assertEqual(self.memory.fact_list(), [])
        self.assertEqual(len(self.memory.fact_list(include_invalid=True)), 1)
        events = [row["event"] for row in self.memory.fact_history()]
        self.assertEqual(events, ["ADD", "DELETE"])

    def test_batch_is_atomic(self) -> None:
        one = self.memory.remember_fact("Alpha service owns the users table")
        two = self.memory.remember_fact("Beta service owns the orders table")
        before = self.memory.facts_path.read_text(encoding="utf-8")

        with self.assertRaises(ValueError):
            self.memory.apply_operations([
                {"event": "UPDATE", "id": one["id"], "text": "Alpha owns accounts"},
                {"event": "DELETE", "id": "fact-missing"},
            ])
        self.assertEqual(
            self.memory.facts_path.read_text(encoding="utf-8"), before
        )

        results = self.memory.apply_operations([
            {"event": "UPDATE", "id": one["id"], "text": "Alpha owns accounts"},
            {"event": "DELETE", "id": two["id"], "reason": "decommissioned"},
            {"event": "ADD", "text": "Gamma service owns invoices"},
        ])
        self.assertEqual([r["event"] for r in results], ["UPDATE", "DELETE", "ADD"])
        self.assertEqual(len(self.memory.fact_list()), 2)

    def test_old_snapshot_is_migrated(self) -> None:
        # setUp already created memory/, so build a fresh root with only the
        # legacy schema in place and let Memory upgrade it on construction.
        other = tempfile.TemporaryDirectory()
        try:
            other_root = Path(other.name)
            (other_root / "memory").mkdir()
            (other_root / "memory" / "facts.json").write_text(
                json.dumps({"services": {"sync": {"port": 8080}},
                            "databases": {}}),
                encoding="utf-8",
            )
            upgraded = Memory(other_root)
            facts = upgraded.fact_list()
            self.assertEqual(len(facts), 1)
            self.assertIn("Service sync", facts[0]["text"])
            self.assertEqual(facts[0]["source"], "migration")
        finally:
            other.cleanup()

    def test_secrets_are_redacted_before_storage(self) -> None:
        self.memory.remember_fact(
            "Sync uses password=hunter2 and api_key: abc123, "
            "with Authorization: Bearer eyJhbGciOiJIUzI1NiJ9 and "
            "key AKIAABCDEFGHIJKLMNOP"
        )
        fact = self.memory.fact_list()[0]["text"]
        self.assertNotIn("hunter2", fact)
        self.assertNotIn("abc123", fact)
        self.assertNotIn("eyJhbGciOiJIUzI1NiJ9", fact)
        self.assertNotIn("AKIAABCDEFGHIJKLMNOP", fact)
        self.assertIn("password=[REDACTED]", fact)
        self.assertIn("Bearer [REDACTED]", fact)
        # Auditing must not reintroduce the value that was redacted.
        self.assertNotIn("hunter2", json.dumps(self.memory.fact_history()))

    def test_instruction_snapshot_and_approval(self) -> None:
        before = self.memory.instructions_snapshot()
        proposal = self.memory.propose_rule(
            "Escalate P1 incidents before P2.", "incident review"
        )
        self.assertEqual(self.memory.instructions_snapshot(), before)
        backup = self.memory.approve(proposal.name)
        self.assertTrue(backup.exists())
        self.assertIn("Escalate P1 incidents", self.memory.instructions())
        self.assertIn("Escalate P1 incidents", self.memory.instructions_snapshot())


if __name__ == "__main__":
    unittest.main()
