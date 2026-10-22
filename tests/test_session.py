"""Tests for the SQLite session transcript store."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from forgegate.session import SessionStore


class SessionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "sessions.db"
        self.store = SessionStore(self.path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_append_and_order(self) -> None:
        self.store.append("run-1", "user", "first")
        self.store.append("run-1", "assistant", "second")
        messages = self.store.messages("run-1")
        self.assertEqual([m["role"] for m in messages], ["user", "assistant"])
        self.assertEqual(messages[0]["content"], "first")

    def test_limit_returns_latest_in_order(self) -> None:
        for index in range(5):
            self.store.append("run-2", "user", str(index))
        messages = self.store.messages("run-2", limit=2)
        self.assertEqual([m["content"] for m in messages], ["3", "4"])

    def test_sessions_are_isolated(self) -> None:
        self.store.append("a", "user", "alpha")
        self.store.append("b", "user", "beta")
        self.assertEqual(len(self.store.messages("a")), 1)
        self.assertEqual(self.store.messages("a")[0]["content"], "alpha")
        self.assertEqual(len(self.store.sessions()), 2)

    def test_metadata_round_trip(self) -> None:
        self.store.append("run-3", "assistant", "done", {"mode": "forged"})
        messages = self.store.messages("run-3")
        self.assertEqual(messages[0]["metadata"], {"mode": "forged"})

    def test_persists_across_instances(self) -> None:
        self.store.append("run-4", "user", "persist me")
        reopened = SessionStore(self.path)
        self.assertEqual(reopened.messages("run-4")[0]["content"], "persist me")


if __name__ == "__main__":
    unittest.main()
