"""Tool generation backends.

FixtureGenerator keeps the demo and tests deterministic offline.
LLMGenerator talks to any OpenAI-compatible endpoint (Meta Model API,
Baseten, Modal, Fireworks, vLLM) and is selected when env vars are present.

Both receive the same memory context (behavioral rules + recalled facts),
following the prefetch pattern used by Hermes Agent and Anthropic's memory
tool: memory is read before generation, never written by the generator.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from .core import repo_root
from .llm import LLMClient, LLMError, strip_code_fences

TOOL_SYSTEM_PROMPT = """You write ONE small Python CLI tool.

Hard requirements:
- Define `def run(...) -> str:` with type hints and a docstring.
- Every import must be from this allowlist:
  __future__, base64, collections, csv, dataclasses, datetime, decimal,
  functools, hashlib, itertools, json, math, pathlib, random, re, statistics,
  string, sys, textwrap, time, typing, uuid, zoneinfo
- Never import subprocess, socket, urllib, requests, httpx, os, shutil,
  sqlite3, pickle, ctypes, multiprocessing, signal.
- Never call eval, exec, compile, __import__, input, breakpoint.
- Never write files: no open(..., "w"/"a"/"x"/"+"), no os.remove/rename.
- No top-level side effects: only imports, constants, function/class defs.
- End the file with exactly this entry point:

if __name__ == "__main__":
    import json, sys
    if "--call" in sys.argv:
        i = sys.argv.index("--call")
        payload = json.loads(sys.argv[i + 1]) if len(sys.argv) > i + 1 else {}
        print(run(**payload))
    else:
        print("usage: tool.py --call '<json>'", file=sys.stderr)

Return plain text. Be deterministic. Handle bad input with a short error string.
Keep the file under 80 lines: no example data, no tests, no commentary.
Reply with the Python source only, no markdown fences.
"""


class Generator(Protocol):
    def generate(self, task: dict[str, Any], context: str = "") -> str: ...


class FixtureGenerator:
    """Offline demo backend: one reviewed source file per tool type."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.dir = repo_root(root) / "bench" / "fixtures"

    def generate(self, task: dict[str, Any], context: str = "") -> str:
        fixture = task.get("fixture") or task["tool_name"]
        path = self.dir / f"{fixture}.py"
        if not path.exists():
            raise FileNotFoundError(f"no fixture '{fixture}' for task {task['id']}")
        return path.read_text(encoding="utf-8")


class LLMGenerator:
    """Generates a tool from the task plus recalled memory context."""

    def __init__(
        self,
        root: str | Path | None = None,
        client: LLMClient | None = None,
    ) -> None:
        self.root = repo_root(root)
        self.client = client or LLMClient()

    def available(self) -> bool:
        return self.client.available()

    def generate(self, task: dict[str, Any], context: str = "") -> str:
        user = json.dumps(
            {
                "task": task["description"],
                "tool_name": task["tool_name"],
                "arguments": task.get("args", {}),
                "example_tests": task.get("tests", []),
                "memory_context": context[-6000:],
            },
            indent=2,
        )
        try:
            reply = self.client.complete(TOOL_SYSTEM_PROMPT, user, temperature=0.2)
        except LLMError:
            raise
        return strip_code_fences(reply)


def build_generator(root: str | Path | None = None) -> Generator:
    llm = LLMGenerator(root)
    return llm if llm.available() else FixtureGenerator(root)
