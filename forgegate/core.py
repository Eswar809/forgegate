"""ForgeGate core: tool registry and three-tier memory.

Registry design adapted from claude-forge (MIT, Copyright (c) 2026 Tomas
Mihalyi). See NOTICE.md.
"""

from __future__ import annotations

import copy
import difflib
import json
import os
import re
import tempfile
import uuid
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


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.stem}_", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


DEFAULT_INSTRUCTIONS = """# ForgeGate behavioral memory

Rules injected into every agent turn. Change them only through a reviewed,
versioned proposal (`forgegate memory propose` then `forgegate memory approve`).

## Rules
- Never write to production databases or external systems.
- Prefer reusing a verified tool over forging a new one.
- A tool that fails verification must never be promoted.
- Report evidence (file, line, job id) alongside the conclusion.
"""

DEFAULT_FACTS: dict[str, Any] = {"version": 2, "facts": []}


@dataclass
class Fact:
    """One durable fact.

    Invalidated facts stay on disk with ``valid_to`` set, so the history is
    auditable. This temporal-validity shape follows Graphiti's entity edges;
    the ADD/UPDATE/DELETE decision follows Mem0's update prompt.
    """

    id: str
    text: str
    kind: str = "fact"
    source: str = ""
    tags: list[str] = field(default_factory=list)
    confidence: float = 1.0
    valid_from: str = field(default_factory=now_iso)
    valid_to: str | None = None
    supersedes: str | None = None
    invalidated_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalize_text(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


# Secret redaction before a fact is persisted. Memory is later injected into a
# model prompt, so credentials observed in logs or output must not survive the
# write (Hermes does the same on memory egress).
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?key|secret|password|passwd|token)\b"
    r"(\s*[:=]\s*)([^\s,;\"']+)"
)
_SECRET_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_SECRET_PREFIX = re.compile(
    r"\b(?:sk|pk|ghp|gho|ghs|github_pat|xox[abprs])[-_][A-Za-z0-9_-]{8,}"
)
_SECRET_AWS = re.compile(r"\bAKIA[0-9A-Z]{16}\b")


def redact_secrets(text: str) -> str:
    """Replace credential-shaped values while keeping the surrounding fact."""
    text = _SECRET_ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", text
    )
    text = _SECRET_BEARER.sub("Bearer [REDACTED]", text)
    text = _SECRET_PREFIX.sub("[REDACTED]", text)
    return _SECRET_AWS.sub("[REDACTED]", text)


def _similarity(left: str, right: str) -> float:
    """Token Jaccard or character ratio, whichever is higher (stdlib only)."""
    a, b = _normalize_text(left), _normalize_text(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ta, tb = tokens(left), tokens(right)
    jaccard = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    return max(jaccard, ratio)


def _overlap(left: str, right: str) -> float:
    """Token Jaccard alone, used for ADD/UPDATE decisions.

    Character similarity is right for search ranking but wrong for decisions:
    ``Alpha owns users`` and ``Beta owns orders`` are 0.8+ similar as strings
    while sharing only half their content tokens. Decisions need the latter.
    """
    a, b = tokens(left), tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class Memory:
    """Factual + behavioral memory. Procedural memory lives in Registry.

    Design inputs, all permissively licensed:
    - Hermes Agent (MIT): frozen instructions snapshot per session, explicit
      add/patch/remove operations, staged approval for destructive writes.
    - Mem0 (Apache-2.0): extract facts, then ADD/UPDATE/DELETE/NONE against
      the closest existing memories instead of appending blindly.
    - Graphiti (Apache-2.0): soft invalidation with valid_from/valid_to rather
      than destroying history.
    - Anthropic memory tool: memory is a plain filesystem the model can read,
      with every mutation auditable.
    """

    UPDATE_THRESHOLD = 0.45
    DUPLICATE_THRESHOLD = 0.85
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = repo_root(root)
        self.dir = self.root / "memory"
        self.instructions_path = self.dir / "instructions.md"
        self.facts_path = self.dir / "facts.json"
        self.facts_history_path = self.dir / "facts_history.jsonl"
        self.sessions_path = self.dir / "sessions.db"
        self.history_dir = self.dir / "history"
        self.proposals_dir = self.dir / "proposals"
        self._snapshot: str | None = None
        for directory in (self.dir, self.history_dir, self.proposals_dir):
            directory.mkdir(parents=True, exist_ok=True)
        if not self.instructions_path.exists():
            _atomic_write_text(self.instructions_path, DEFAULT_INSTRUCTIONS)
        if not self.facts_path.exists():
            self._write_facts(copy.deepcopy(DEFAULT_FACTS))
        else:
            self._migrate_facts()

    # storage -------------------------------------------------------------
    def _read_facts(self) -> dict[str, Any]:
        try:
            data = json.loads(self.facts_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return copy.deepcopy(DEFAULT_FACTS)
        if not isinstance(data, dict):
            return copy.deepcopy(DEFAULT_FACTS)
        data.setdefault("version", 2)
        data.setdefault("facts", [])
        if not isinstance(data["facts"], list):
            data["facts"] = []
        return data

    def _write_facts(self, data: dict[str, Any]) -> None:
        _atomic_write_text(
            self.facts_path,
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        )

    def _migrate_facts(self) -> None:
        """Convert the original {services, databases} snapshot into v2 facts."""
        try:
            raw = json.loads(self.facts_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            self._write_facts(copy.deepcopy(DEFAULT_FACTS))
            return
        if not isinstance(raw, dict):
            self._write_facts(copy.deepcopy(DEFAULT_FACTS))
            return
        if isinstance(raw.get("facts"), list):
            return
        data = raw
        migrated: list[dict[str, Any]] = []
        groups = (
            ("service", data.get("services", {})),
            ("database", data.get("databases", {})),
        )
        for kind, group in groups:
            if not isinstance(group, dict):
                continue
            for name, info in group.items():
                migrated.append(Fact(
                    id=f"fact-{uuid.uuid4().hex[:8]}",
                    text=f"{kind.capitalize()} {name}: "
                         f"{json.dumps(info, ensure_ascii=False, sort_keys=True)}",
                    kind=kind,
                    source="migration",
                ).to_dict())
        self._write_facts({"version": 2, "facts": migrated})

    def _audit(self, record: dict[str, Any]) -> None:
        entry = {"at": now_iso(), **record}
        with self.facts_history_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # factual memory ------------------------------------------------------
    def facts(self) -> dict[str, Any]:
        """Raw v2 store, including invalidated facts."""
        return self._read_facts()

    def fact_list(self, include_invalid: bool = False) -> list[dict[str, Any]]:
        facts = self._read_facts()["facts"]
        if include_invalid:
            return list(facts)
        return [f for f in facts if not f.get("valid_to")]

    def active_facts(self) -> list[dict[str, Any]]:
        return self.fact_list()

    def search_facts(
        self, query: str, k: int = 5, threshold: float = 0.2
    ) -> list[dict[str, Any]]:
        wanted = tokens(query)
        if not wanted:
            return []
        scored: list[tuple[float, dict[str, Any]]] = []
        for fact in self.fact_list():
            haystack = tokens(
                f"{fact.get('text', '')} {' '.join(fact.get('tags', []))}"
            )
            if not haystack:
                continue
            score = len(wanted & haystack) / len(wanted)
            if score >= threshold:
                scored.append((score, fact))
        scored.sort(key=lambda pair: (-pair[0], pair[1].get("valid_from", "")))
        return [fact for _, fact in scored[:k]]

    def prefetch(self, query: str, k: int = 5) -> str:
        """Prompt block: behavioral rules plus facts relevant to this task.

        Read-only by construction; generation never writes memory.
        """
        lines = [self.instructions_snapshot().strip(), "", "## Recalled facts"]
        recalled = self.search_facts(query, k=k)
        if recalled:
            for fact in recalled:
                lines.append(f"- [{fact.get('kind', 'fact')}] {fact['text']}")
        else:
            lines.append("- (none)")
        return "\n".join(lines) + "\n"

    def remember_fact(
        self,
        text: str,
        *,
        source: str = "",
        tags: list[str] | None = None,
        kind: str = "fact",
        confidence: float = 1.0,
        decision: str | None = None,
    ) -> dict[str, Any]:
        """Store one fact using Mem0-style ADD / UPDATE / NONE semantics."""
        text = redact_secrets(" ".join(str(text).split()))
        if len(text) < 3:
            raise ValueError("fact text is too short")

        data = self._read_facts()
        active = [f for f in data["facts"] if not f.get("valid_to")]
        best: dict[str, Any] | None = None
        best_score = 0.0
        best_overlap = 0.0
        for fact in active:
            score = _similarity(text, fact.get("text", ""))
            if score > best_score:
                best, best_score = fact, score
                best_overlap = _overlap(text, fact.get("text", ""))

        if decision is None:
            # A near-identical wording is still a new assertion unless the
            # normalized text is unchanged; otherwise a changed port or owner
            # would be discarded as a duplicate instead of superseding.
            duplicate = (
                best is not None
                and best_score >= self.DUPLICATE_THRESHOLD
                and _normalize_text(text) == _normalize_text(str(best.get("text", "")))
            )
            if duplicate:
                event = "NONE"
            elif best_overlap >= self.UPDATE_THRESHOLD:
                event = "UPDATE"
            else:
                event = "ADD"
        else:
            event = decision.upper()
        if event not in {"ADD", "UPDATE", "NONE"}:
            raise ValueError(f"unsupported memory decision: {event}")

        if event == "NONE":
            return {
                "event": "NONE",
                "id": best["id"] if best else None,
                "text": text,
            }

        if event == "UPDATE" and best is not None:
            new_id = f"fact-{uuid.uuid4().hex[:8]}"
            now = now_iso()
            best["valid_to"] = now
            best["invalidated_by"] = new_id
            new_fact = Fact(
                id=new_id, text=text, kind=kind, source=source,
                tags=list(tags or []), confidence=confidence,
                valid_from=now, supersedes=best["id"],
            ).to_dict()
            data["facts"].append(new_fact)
            self._write_facts(data)
            self._audit({
                "event": "UPDATE", "fact_id": new_id, "old_id": best["id"],
                "text": text, "old_text": best.get("text", ""),
                "source": source, "reason": "superseded",
            })
            return {
                "event": "UPDATE", "id": new_id, "old_id": best["id"],
                "text": text,
            }

        fact = Fact(
            id=f"fact-{uuid.uuid4().hex[:8]}", text=text, kind=kind,
            source=source, tags=list(tags or []), confidence=confidence,
        ).to_dict()
        data["facts"].append(fact)
        self._write_facts(data)
        self._audit({
            "event": "ADD", "fact_id": fact["id"], "text": text,
            "source": source,
        })
        return {"event": "ADD", "id": fact["id"], "text": text}

    def apply_operations(self, operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Apply a batch atomically (all-or-nothing) and audit it.

        Op shape: ``{"event": "ADD|UPDATE|DELETE|NONE", "id": ..., "text": ...}``.
        """
        if not isinstance(operations, list):
            raise TypeError("operations must be a list")
        data = self._read_facts()
        work = copy.deepcopy(data)
        active = {f["id"]: f for f in work["facts"] if not f.get("valid_to")}
        results: list[dict[str, Any]] = []

        for op in operations:
            if not isinstance(op, dict):
                raise TypeError("each operation must be an object")
            event = str(op.get("event", "")).upper()
            if event == "NONE":
                results.append({"event": "NONE", "id": op.get("id")})
                continue
            if event == "ADD":
                text = redact_secrets(" ".join(str(op.get("text", "")).split()))
                if len(text) < 3:
                    raise ValueError("ADD needs text of at least 3 characters")
                fact = Fact(
                    id=f"fact-{uuid.uuid4().hex[:8]}", text=text,
                    kind=str(op.get("kind", "fact")),
                    source=str(op.get("source", "")),
                    tags=list(op.get("tags", [])),
                    confidence=float(op.get("confidence", 1.0)),
                ).to_dict()
                work["facts"].append(fact)
                active[fact["id"]] = fact
                results.append({
                    "event": "ADD", "id": fact["id"], "text": text,
                    "source": op.get("source", ""),
                })
                continue
            if event not in {"UPDATE", "DELETE"}:
                raise ValueError(f"unsupported event: {event!r}")
            fact_id = str(op.get("id", ""))
            if fact_id not in active:
                raise ValueError(f"active fact not found: {fact_id!r}")
            old = active.pop(fact_id)
            old["valid_to"] = now_iso()
            if event == "DELETE":
                results.append({
                    "event": "DELETE", "id": fact_id, "text": old.get("text", ""),
                    "source": op.get("source", ""),
                    "reason": op.get("reason", ""),
                })
                continue
            text = redact_secrets(" ".join(str(op.get("text", "")).split()))
            if len(text) < 3:
                raise ValueError("UPDATE needs text of at least 3 characters")
            new_fact = Fact(
                id=f"fact-{uuid.uuid4().hex[:8]}", text=text,
                kind=str(op.get("kind", old.get("kind", "fact"))),
                source=str(op.get("source", "")),
                tags=list(op.get("tags", old.get("tags", []))),
                confidence=float(op.get("confidence", 1.0)),
                supersedes=fact_id,
            ).to_dict()
            old["invalidated_by"] = new_fact["id"]
            work["facts"].append(new_fact)
            active[new_fact["id"]] = new_fact
            results.append({
                "event": "UPDATE", "id": new_fact["id"], "old_id": fact_id,
                "text": text, "source": op.get("source", ""),
                "reason": op.get("reason", "superseded"),
            })

        if results and any(r["event"] != "NONE" for r in results):
            self._write_facts(work)
            for result in results:
                if result["event"] == "NONE":
                    continue
                self._audit({
                    "event": result["event"],
                    "fact_id": result.get("id"),
                    "old_id": result.get("old_id"),
                    "text": result.get("text", ""),
                    "source": result.get("source", ""),
                    "reason": result.get("reason", ""),
                })
        return results

    def invalidate_fact(
        self, fact_id: str, reason: str = "", source: str = ""
    ) -> bool:
        if not any(f["id"] == fact_id for f in self.fact_list()):
            return False
        self.apply_operations([{
            "event": "DELETE", "id": fact_id,
            "reason": reason, "source": source,
        }])
        return True

    def fact_history(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self.facts_history_path.exists():
            return []
        lines = self.facts_history_path.read_text(
            encoding="utf-8"
        ).splitlines()
        out: list[dict[str, Any]] = []
        for line in lines[-limit:]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    # behavioral memory ---------------------------------------------------
    def instructions(self) -> str:
        return self.instructions_path.read_text(encoding="utf-8")

    def instructions_snapshot(self) -> str:
        """Frozen per process, mirroring Hermes' session-start prompt snapshot."""
        if self._snapshot is None:
            self._snapshot = self.instructions()
        return self._snapshot

    def refresh_snapshot(self) -> str:
        self._snapshot = None
        return self.instructions_snapshot()

    def propose_rule(self, rule: str, reason: str) -> Path:
        """Stage a rule change. Nothing changes until approve()."""
        path = self.proposals_dir / f"{stamp()}-{uuid.uuid4().hex[:4]}.md"
        _atomic_write_text(
            path,
            "# Proposed behavioral rule\n\n"
            f"- **Rule:** {rule}\n"
            f"- **Reason:** {reason}\n"
            f"- **Proposed at:** {now_iso()}\n\n"
            f"Approve with: `python -m forgegate.cli memory approve {path.name}`\n",
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

        current = self.instructions()
        backup = self.history_dir / f"instructions-{stamp()}.md"
        _atomic_write_text(backup, current)
        updated = current.rstrip("\n")
        updated += f"\n- {rule}  <!-- approved {now_iso()} -->\n"
        _atomic_write_text(self.instructions_path, updated)
        path.replace(self.history_dir / path.name)
        self._snapshot = None
        return backup
