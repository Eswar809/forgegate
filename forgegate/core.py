"""ForgeGate core: tool registry and three-tier memory.

Registry design adapted from claude-forge (MIT, Copyright (c) 2026 Tomas
Mihalyi). See NOTICE.md.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_STOP = {
    "a", "an", "the", "for", "of", "to", "in", "on", "and", "or", "with",
    "from", "by", "is", "are", "was", "were", "be", "this", "that", "it",
    "at", "as", "into", "per", "new", "get", "check", "latest",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def repo_root(root: str | Path | None = None) -> Path:
    if root is not None:
        return Path(root).resolve()
    env = os.environ.get("FORGEGATE_ROOT")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[1]


def tokens(text: str) -> set[str]:
    return {
        w for w in re.findall(r"[a-z0-9]+", text.lower())
        if w not in _STOP and len(w) > 2
    }


@dataclass
class ToolEntry:
    name: str
    description: str
    path: str
    tags: list[str] = field(default_factory=list)
    forged_for: str = ""
    created_at: str = field(default_factory=now_iso)
    uses: int = 0
    sha256: str = ""
    verification: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Registry:
    """Procedural memory: every promoted tool, atomically persisted."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = repo_root(root)
        self.path = self.root / "registry.json"

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"tools": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            data.setdefault("tools", [])
            return data
        except (json.JSONDecodeError, OSError):
            return {"tools": []}

    def _write(self, data: dict[str, Any]) -> None:
        content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=".registry_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def all(self) -> list[ToolEntry]:
        return [ToolEntry(**entry) for entry in self._read()["tools"]]

    def get(self, name: str) -> ToolEntry | None:
        return next((e for e in self.all() if e.name == name), None)

    def add(self, entry: ToolEntry) -> None:
        data = self._read()
        if any(e["name"] == entry.name for e in data["tools"]):
            raise ValueError(f"tool '{entry.name}' already exists")
        data["tools"].append(entry.to_dict())
        self._write(data)

    def bump(self, name: str) -> None:
        data = self._read()
        for entry in data["tools"]:
            if entry["name"] == name:
                entry["uses"] = int(entry.get("uses", 0)) + 1
        self._write(data)

    def remove(self, name: str) -> bool:
        data = self._read()
        kept = [e for e in data["tools"] if e["name"] != name]
        if len(kept) == len(data["tools"]):
            return False
        data["tools"] = kept
        self._write(data)
        return True

    def find(self, query: str, threshold: float = 0.4) -> ToolEntry | None:
        wanted = tokens(query)
        if not wanted:
            return None
        best: ToolEntry | None = None
        best_score = 0.0
        for entry in self.all():
            haystack = tokens(
                " ".join([entry.name, entry.description, " ".join(entry.tags)])
            )
            if not haystack:
                continue
            score = len(wanted & haystack) / len(wanted)
            if score > best_score:
                best, best_score = entry, score
        return best if best_score >= threshold else None


DEFAULT_INSTRUCTIONS = """# ForgeGate behavioral memory

Rules injected into every agent turn. Change them only through a reviewed,
versioned proposal (`forgegate memory propose` then `forgegate memory approve`).

## Rules
- Never write to production databases or external systems.
- Prefer reusing a verified tool over forging a new one.
- A tool that fails verification must never be promoted.
- Report evidence (file, line, job id) alongside the conclusion.
"""


class Memory:
    """Factual + behavioral memory. Procedural memory lives in Registry."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = repo_root(root)
        self.dir = self.root / "memory"
        self.instructions_path = self.dir / "instructions.md"
        self.facts_path = self.dir / "facts.json"
        self.history_dir = self.dir / "history"
        self.proposals_dir = self.dir / "proposals"
        for directory in (self.dir, self.history_dir, self.proposals_dir):
            directory.mkdir(parents=True, exist_ok=True)
        if not self.instructions_path.exists():
            self.instructions_path.write_text(DEFAULT_INSTRUCTIONS, encoding="utf-8")
        if not self.facts_path.exists():
            self.facts_path.write_text(
                json.dumps({"services": {}, "databases": {}}, indent=2) + "\n",
                encoding="utf-8",
            )

    # factual memory -----------------------------------------------------
    def facts(self) -> dict[str, Any]:
        try:
            return json.loads(self.facts_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    # behavioral memory --------------------------------------------------
    def instructions(self) -> str:
        return self.instructions_path.read_text(encoding="utf-8")

    def propose_rule(self, rule: str, reason: str) -> Path:
        """Stage a rule change. Nothing changes until approve()."""
        path = self.proposals_dir / f"{stamp()}.md"
        path.write_text(
            "# Proposed behavioral rule\n\n"
            f"- **Rule:** {rule}\n"
            f"- **Reason:** {reason}\n"
            f"- **Proposed at:** {now_iso()}\n\n"
            f"Approve with: `python -m forgegate.cli memory approve {path.name}`\n",
            encoding="utf-8",
        )
        return path

    def approve(self, proposal: str) -> Path:
        """Apply a proposal, after snapshotting the previous instructions."""
        path = Path(proposal)
        if not path.is_absolute():
            path = self.proposals_dir / path
        text = path.read_text(encoding="utf-8")
        rule_lines = [ln for ln in text.splitlines() if ln.startswith("- **Rule:**")]
        if not rule_lines:
            raise ValueError(f"no rule found in proposal: {path}")
        rule = rule_lines[0].split("**Rule:**", 1)[1].strip()

        backup = self.history_dir / f"instructions-{stamp()}.md"
        backup.write_text(self.instructions(), encoding="utf-8")
        with self.instructions_path.open("a", encoding="utf-8") as fh:
            fh.write(f"\n- {rule}  <!-- approved {now_iso()} -->\n")
        path.replace(self.history_dir / path.name)
        return backup
