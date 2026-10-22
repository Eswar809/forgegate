"""Unit tests for the verification gate, registry and memory."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from forgegate.core import Memory, Registry, ToolEntry
from forgegate.gate import verify_tool

GOOD_TOOL = '''"""Read a count from a JSON file."""
from __future__ import annotations
import json

def run(path: str) -> str:
    """Return the count field as text."""
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    return f"count={data['count']}"

if __name__ == "__main__":
    import json
    import sys
    if "--call" in sys.argv:
        i = sys.argv.index("--call")
        payload = json.loads(sys.argv[i + 1]) if len(sys.argv) > i + 1 else {}
        print(run(**payload))
    else:
        print("usage", file=sys.stderr)
'''

BAD_IMPORT = GOOD_TOOL.replace(
    "import json\n\ndef run", "import json\nimport subprocess\n\ndef run"
)

BAD_WRITE = GOOD_TOOL.replace(
    "    return f\"count={data['count']}\"",
    "    open(path, 'w').write('x')\n    return f\"count={data['count']}\"",
)

NONDETERMINISTIC = GOOD_TOOL.replace(
    "    return f\"count={data['count']}\"",
    "    import time\n    return str(time.time_ns())",
)


class GateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / "data.json"
        self.data.write_text(json.dumps({"count": 3}), encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, name: str, source: str) -> Path:
        path = self.root / name
        path.write_text(source, encoding="utf-8")
        return path

    def test_good_tool_passes(self) -> None:
        path = self.write("good.py", GOOD_TOOL)
        result = verify_tool(
            path, [{"args": {"path": str(self.data)}, "expect_exact": "count=3"}]
        )
        self.assertTrue(result.ok, [c for c in result.checks if not c.ok])

    def test_forbidden_import_fails(self) -> None:
        path = self.write("bad_import.py", BAD_IMPORT)
        result = verify_tool(path, [{"args": {"path": str(self.data)}}])
        self.assertFalse(result.ok)
        self.assertFalse(next(c for c in result.checks if c.name == "no_forbidden_imports").ok)

    def test_file_write_fails(self) -> None:
        path = self.write("bad_write.py", BAD_WRITE)
        result = verify_tool(path, [{"args": {"path": str(self.data)}}])
        self.assertFalse(result.ok)
        self.assertFalse(next(c for c in result.checks if c.name == "no_file_writes").ok)

    def test_wrong_output_fails(self) -> None:
        path = self.write("good2.py", GOOD_TOOL)
        result = verify_tool(
            path, [{"args": {"path": str(self.data)}, "expect_exact": "count=999"}]
        )
        self.assertFalse(result.ok)
        self.assertFalse(next(c for c in result.checks if c.name == "case_1").ok)

    def test_nondeterminism_fails(self) -> None:
        path = self.write("non_det.py", NONDETERMINISTIC)
        result = verify_tool(path, [{"args": {"path": str(self.data)}}])
        self.assertFalse(result.ok)
        self.assertFalse(next(c for c in result.checks if c.name == "deterministic").ok)


class RegistryTests(unittest.TestCase):
    def test_add_find_and_dedup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = Registry(tmp)
            entry = ToolEntry(
                name="log-scan", description="scan logs for errors",
                path="servers/log-scan/tool.py", tags=["log", "error"],
            )
            registry.add(entry)
            self.assertIsNotNone(registry.find("scan the log for error lines"))
            with self.assertRaises(ValueError):
                registry.add(entry)

    def test_bump(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = Registry(tmp)
            registry.add(ToolEntry(name="x-tool", description="x", path="x.py"))
            registry.bump("x-tool")
            self.assertEqual(registry.get("x-tool").uses, 1)


class MemoryTests(unittest.TestCase):
    def test_rule_requires_approval_and_versions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = Memory(tmp)
            before = memory.instructions()
            proposal = memory.propose_rule("Escalate P1 incidents first.", "incident review")
            self.assertEqual(memory.instructions(), before)  # not active yet
            backup = memory.approve(proposal.name)
            self.assertTrue(backup.exists())
            self.assertIn("Escalate P1 incidents first.", memory.instructions())


if __name__ == "__main__":
    unittest.main()
