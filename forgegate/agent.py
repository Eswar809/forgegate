"""The forge -> verify -> promote -> reuse loop."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

from .core import Memory, Registry, ToolEntry, now_iso, repo_root, stamp
from .gate import GateResult, run_cli, verify_tool
from .generator import Generator, build_generator
from .judge import SemanticJudge
from .llm import LLMClient
from .memory_ai import remember_outcome
from .session import SessionStore

SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")


def resolve_args(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    """Expand 'data:file.json' into an absolute path under bench/data."""
    resolved: dict[str, Any] = {}
    for key, value in (args or {}).items():
        if isinstance(value, str) and value.startswith("data:"):
            resolved[key] = str((root / "bench" / "data" / value[5:]).resolve())
        elif isinstance(value, dict):
            resolved[key] = resolve_args(root, value)
        elif isinstance(value, list):
            resolved[key] = [
                str((root / "bench" / "data" / v[5:]).resolve())
                if isinstance(v, str) and v.startswith("data:") else v
                for v in value
            ]
        else:
            resolved[key] = value
    return resolved


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def promote(
    root: Path,
    source: Path,
    task: dict[str, Any],
    gate: GateResult,
    registry: Registry,
) -> ToolEntry:
    name = task["tool_name"]
    if not SAFE_NAME.match(name):
        raise ValueError(f"unsafe tool name: {name!r}")

    dest_dir = root / "servers" / name
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "tool.py"

    if dest.exists():
        history = dest_dir / "history"
        history.mkdir(exist_ok=True)
        shutil.copy2(dest, history / f"tool-{stamp()}.py")

    shutil.copy2(source, dest)
    (dest_dir / "verification.json").write_text(
        json.dumps(gate.to_dict(), indent=2) + "\n", encoding="utf-8"
    )

    entry = ToolEntry(
        name=name,
        description=task.get("tool_description") or task["description"],
        path=str(dest.relative_to(root)).replace("\\", "/"),
        tags=list(task.get("tags", [])),
        forged_for=task["description"],
        sha256=_sha256(dest),
        verification={
            "ok": gate.ok,
            "cases": gate.metrics.get("cases", 0),
            "avg_latency_ms": gate.metrics.get("avg_latency_ms", 0.0),
            "verified_at": now_iso(),
        },
    )
    registry.add(entry)
    return entry


class Agent:
    def __init__(
        self,
        root: str | Path | None = None,
        generator: Generator | None = None,
        *,
        session_id: str | None = None,
        judge: SemanticJudge | None = None,
        llm: LLMClient | None = None,
    ) -> None:
        self.root = repo_root(root)
        self.registry = Registry(self.root)
        self.memory = Memory(self.root)
        self.generator = generator or build_generator(self.root)
        self.llm = llm or LLMClient()
        self.judge = judge or SemanticJudge(self.llm)
        self.session_id = session_id
        self.sessions = (
            SessionStore(self.memory.sessions_path) if session_id else None
        )
        if self.sessions is not None:
            self.sessions.ensure(session_id)

    def _output_ok(self, task: dict[str, Any], output: str) -> bool:
        wanted = task.get("expected_contains", [])
        if isinstance(wanted, str):
            wanted = [wanted]
        return all(item in output for item in wanted)

    def _judge(self, task: dict[str, Any], output: str):
        """Run the semantic judge whenever the task asks for one.

        A requested judge is never skipped: ``SemanticJudge.judge`` returns a
        failing verdict when no model is configured, so offline runs reject
        rather than silently passing on the substring check alone.
        """
        if not (task.get("judge") or task.get("rubric")):
            return None
        return self.judge.judge(
            task["description"], output,
            expected=task.get("expected_contains", []),
            rubric=task.get("rubric", ""),
        )

    def _finalize(
        self,
        task: dict[str, Any],
        started: float,
        mode: str,
        tool: str | None,
        run: dict[str, Any],
        gate: GateResult | None = None,
    ) -> dict[str, Any]:
        output = run["stdout"].strip()
        ok = bool(run["ok"]) and self._output_ok(task, output)
        verdict = self._judge(task, output)
        if verdict is not None:
            ok = ok and verdict.ok

        memory_writes = 0
        if ok:
            try:
                memory_writes = len(remember_outcome(
                    self.memory, task, output, client=self.llm,
                ))
            except Exception:
                memory_writes = 0

        return {
            "task_id": task["id"],
            "mode": mode,
            "tool": tool,
            "ok": ok,
            "output": output,
            "error": run["stderr"].strip(),
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "gate": gate.to_dict() if gate is not None else None,
            "judge": verdict.to_dict() if verdict is not None else None,
            "memory_writes": memory_writes,
        }

    def _handle(self, task: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        query = task["description"]

        existing = self.registry.find(
            query, threshold=float(task.get("match_threshold", 0.4))
        )
        if existing is not None:
            args = resolve_args(self.root, task.get("args", {}))
            run = run_cli(
                self.root / existing.path, args,
                timeout=float(task.get("timeout", 10)),
            )
            self.registry.bump(existing.name)
            return self._finalize(
                task, started, "reuse", existing.name, run,
            )

        if (task.get("judge") or task.get("rubric")) and not self.judge.available():
            # Fail closed before forging: without a judge there is no way to
            # verify the answer, and promoting an unverified tool would grow
            # procedural memory with something nothing checked.
            return {
                "task_id": task["id"], "mode": "rejected", "tool": None,
                "ok": False, "output": "",
                "error": "judge requested but no model endpoint configured",
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "gate": None, "judge": None, "memory_writes": 0,
            }

        try:
            context = self.memory.prefetch(query)
            source = self.generator.generate(task, context=context)
        except Exception as exc:  # generation failure is a normal outcome
            return {
                "task_id": task["id"], "mode": "rejected", "tool": None,
                "ok": False, "output": "", "error": f"generation failed: {exc}",
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "gate": None, "judge": None, "memory_writes": 0,
            }

        staging = self.root / "staging" / f"{task['id']}-{task['tool_name']}.py"
        staging.parent.mkdir(parents=True, exist_ok=True)
        staging.write_text(source, encoding="utf-8")

        cases = []
        for case in task.get("tests", []):
            prepared = {k: v for k, v in case.items() if k != "args"}
            prepared["args"] = resolve_args(self.root, case.get("args", {}))
            cases.append(prepared)

        gate = verify_tool(
            staging, cases, timeout=float(task.get("timeout", 10))
        )
        if not gate.ok:
            return {
                "task_id": task["id"], "mode": "rejected", "tool": None,
                "ok": False, "output": "", "error": "verification failed",
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "gate": gate.to_dict(), "judge": None, "memory_writes": 0,
            }

        entry = promote(self.root, staging, task, gate, self.registry)

        args = resolve_args(self.root, task.get("args", {}))
        run = run_cli(
            self.root / entry.path, args, timeout=float(task.get("timeout", 10))
        )
        self.registry.bump(entry.name)
        return self._finalize(
            task, started, "forged", entry.name, run, gate,
        )

    def handle(self, task: dict[str, Any]) -> dict[str, Any]:
        """Run one task and, when a session id was given, record the exchange."""
        if self.sessions is not None:
            self.sessions.append(
                self.session_id, "user",
                json.dumps({
                    "task_id": task.get("id"),
                    "description": task.get("description"),
                }, ensure_ascii=False),
                {"task_id": task.get("id")},
            )
        result = self._handle(task)
        if self.sessions is not None:
            self.sessions.append(
                self.session_id, "assistant",
                json.dumps({
                    "mode": result.get("mode"),
                    "tool": result.get("tool"),
                    "ok": result.get("ok"),
                    "output": result.get("output"),
                }, ensure_ascii=False),
                {"task_id": task.get("id"), "mode": result.get("mode")},
            )
        return result
