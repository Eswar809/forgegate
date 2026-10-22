"""Tests for the Slack ingress helpers and .env loading."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from forgegate.adapters.slack import task_from_slack
from forgegate.cli import load_env_file


class SlackTaskTests(unittest.TestCase):
    def test_free_form_task_always_requests_the_judge(self):
        task = task_from_slack(
            "list the failing cron jobs", ts="123.45", user="U1",
        )
        self.assertTrue(task["judge"])
        self.assertEqual(task["id"], "slack-123.45")
        self.assertEqual(task["tool_name"], "list-the-failing-cron-jobs")
        self.assertEqual(task["tags"], ["slack"])

    def test_blank_text_is_not_turned_into_a_task_by_the_helper(self):
        task = task_from_slack("   ", ts="1")
        self.assertEqual(task["description"], "")


class EnvFileTests(unittest.TestCase):
    def test_loads_values_but_never_overrides_the_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "FORGEGATE_TEST_A=one\n"
                "FORGEGATE_TEST_B='two'\n"
                "FORGEGATE_TEST_EMPTY=\n"
                "# comment\n"
                "not a pair\n",
                encoding="utf-8",
            )
            os.environ.pop("FORGEGATE_TEST_A", None)
            os.environ.pop("FORGEGATE_TEST_EMPTY", None)
            os.environ["FORGEGATE_TEST_B"] = "keep"
            try:
                load_env_file(path)
                self.assertEqual(os.environ["FORGEGATE_TEST_A"], "one")
                self.assertEqual(os.environ["FORGEGATE_TEST_B"], "keep")
                self.assertNotIn("FORGEGATE_TEST_EMPTY", os.environ)
            finally:
                os.environ.pop("FORGEGATE_TEST_A", None)
                os.environ.pop("FORGEGATE_TEST_B", None)
                os.environ.pop("FORGEGATE_TEST_EMPTY", None)


if __name__ == "__main__":
    unittest.main()
