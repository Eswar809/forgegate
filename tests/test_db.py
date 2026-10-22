"""Tests for read-only database access."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from forgegate.db import ReadOnlyError, ReadOnlySQLite


class ReadOnlySQLiteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "ops.db"
        conn = sqlite3.connect(self.path)
        try:
            conn.execute("CREATE TABLE users (id INTEGER, name TEXT)")
            conn.executemany(
                "INSERT INTO users (id, name) VALUES (?, ?)",
                [(1, "ada"), (2, "grace"), (3, "linus")],
            )
            conn.commit()
        finally:
            conn.close()
        self.db = ReadOnlySQLite(self.path)

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def test_select_returns_dicts(self) -> None:
        rows = self.db.query("SELECT id, name FROM users ORDER BY id")
        self.assertEqual(rows[0], {"id": 1, "name": "ada"})
        self.assertEqual(len(rows), 3)

    def test_limit(self) -> None:
        rows = self.db.query("SELECT * FROM users", limit=2)
        self.assertEqual(len(rows), 2)

    def test_tables(self) -> None:
        self.assertEqual(self.db.tables(), ["users"])

    def test_insert_is_rejected(self) -> None:
        with self.assertRaises(ReadOnlyError):
            self.db.query("INSERT INTO users (id, name) VALUES (4, 'x')")

    def test_cte_insert_is_rejected_by_authorizer(self) -> None:
        with self.assertRaises(ReadOnlyError):
            self.db.query(
                "WITH x AS (SELECT 4) "
                "INSERT INTO users (id, name) SELECT 4, 'x' FROM x"
            )

    def test_multiple_statements_are_rejected(self) -> None:
        with self.assertRaises(ReadOnlyError):
            self.db.query("SELECT 1; SELECT 2")

    def test_read_pragma_allowed(self) -> None:
        rows = self.db.query("PRAGMA table_info(users)")
        self.assertTrue(any(row["name"] == "name" for row in rows))

    def test_write_pragma_rejected(self) -> None:
        with self.assertRaises(ReadOnlyError):
            self.db.query("PRAGMA writable_schema=ON")

    def test_database_is_unchanged(self) -> None:
        conn = sqlite3.connect(self.path)
        try:
            count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 3)


if __name__ == "__main__":
    unittest.main()
