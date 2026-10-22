"""Tool generation backends.

FixtureGenerator keeps the demo and tests deterministic offline.
LLMGenerator talks to any OpenAI-compatible endpoint (Meta Model API,
Baseten, Modal, Fireworks, vLLM) and is selected when env vars are present.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Any, Protocol

from .core import repo_root

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
Reply with the Python source only, no markdown fences.
"""


class Generator(Protocol):
    def generate(self, task: dict[str, Any]) -> str: ...


class FixtureGenerator:
    """Offline demo backend: one reviewed source file per tool type."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.dir = repo_root(root) / "bench" / "fixtures"

    def generate(self, task: dict[str, Any]) -> str:
        fixture = task.get("fixture") or task["tool_name"]
        path = self.dir / f"{fixture}.py"
        if not path.exists():
            raise FileNotFoundError(f"no fixture '{fixture}' for task {task['id']}")
        return path.read_text(encoding="utf-8")


def _strip_fences(text: str) -> str:
    text = text.strip()
    match = re.search(r"```(?:python)?\s*(.*?)```", text, re.DOTALL)
    return match.group(1).strip() if match else text


class LLMGenerator:
    """OpenAI-compatible chat-completions client (stdlib only)."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = repo_root(root)
        self.base = os.environ.get("FORGEGATE_API_BASE", "").rstrip("/")
        self.key = os.environ.get("FORGEGATE_API_KEY", "")
        self.model = os.environ.get("FORGEGATE_MODEL", "")

    def available(self) -> bool:
        return bool(self.base and self.key and self.model)

    def generate(self, task: dict[str, Any]) -> str:
        user = json.dumps(
            {
                "task": task["description"],
                "tool_name": task["tool_name"],
                "arguments": task.get("args", {}),
                "example_tests": task.get("tests", []),
            },
            indent=2,
        )
        payload = json.dumps({
            "model": self.model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": TOOL_SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
        }).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base}/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.key}",
            },
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        return _strip_fences(content)


def build_generator(root: str | Path | None = None) -> Generator:
    llm = LLMGenerator(root)
    return llm if llm.available() else FixtureGenerator(root)
