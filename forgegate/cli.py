"""Command line interface for ForgeGate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .agent import Agent
from .core import Memory, Registry, repo_root
from .gate import verify_tool


def _load_task(args: argparse.Namespace) -> dict:
    if args.task_file:
        return json.loads(Path(args.task_file).read_text(encoding="utf-8"))
    if args.task:
        return json.loads(args.task)
    raise SystemExit("provide --task or --task-file")


def _prepare_cases(cases: list[dict]) -> list[dict]:
    """Make case file paths absolute; tools run in an isolated cwd."""
    root = repo_root()

    def walk(value):
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v) for v in value]
        if isinstance(value, str):
            if value.startswith("data:"):
                return str((root / "bench" / "data" / value[5:]).resolve())
            path = Path(value)
            try:
                if not path.is_absolute() and path.is_file():
                    return str(path.resolve())
            except OSError:
                return value
        return value

    return [walk(case) for case in cases]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="forgegate", description="Verified self-extension for ops agents"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="run one task through the agent")
    run_p.add_argument("--task", help="inline JSON task")
    run_p.add_argument("--task-file", type=Path)
    run_p.add_argument("--root", type=Path)

    reg_p = sub.add_parser("registry", help="inspect procedural memory")
    reg_p.add_argument("action", choices=["list"])
    reg_p.add_argument("--root", type=Path)

    mem_p = sub.add_parser("memory", help="inspect or change behavioral memory")
    mem_p.add_argument("action", choices=["show", "propose", "approve", "history"])
    mem_p.add_argument("--rule")
    mem_p.add_argument("--reason")
    mem_p.add_argument("--proposal")
    mem_p.add_argument("--root", type=Path)

    gate_p = sub.add_parser("gate", help="verify a tool file")
    gate_p.add_argument("tool", type=Path)
    gate_p.add_argument(
        "--tests", help="inline JSON list of cases, or a path to a .json file"
    )
    gate_p.add_argument("--timeout", type=float, default=10.0)

    args = parser.parse_args(argv)

    if args.command == "run":
        task = _load_task(args)
        result = Agent(root=args.root).handle(task)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["ok"] else 1

    if args.command == "registry":
        registry = Registry(args.root)
        for entry in registry.all():
            print(f"{entry.name:24} uses={entry.uses:<3} {entry.path}")
            print(f"  {entry.description}")
        if not registry.all():
            print("registry is empty")
        return 0

    if args.command == "memory":
        memory = Memory(args.root)
        if args.action == "show":
            print(memory.instructions())
        elif args.action == "history":
            files = sorted(memory.history_dir.glob("*.md"))
            for path in files:
                print(path.name)
            if not files:
                print("no history yet")
        elif args.action == "propose":
            if not (args.rule and args.reason):
                raise SystemExit("--rule and --reason are required")
            path = memory.propose_rule(args.rule, args.reason)
            print(f"proposed: {path}")
        elif args.action == "approve":
            if not args.proposal:
                raise SystemExit("--proposal is required")
            backup = memory.approve(args.proposal)
            print(f"approved; previous version saved to {backup}")
        return 0

    if args.command == "gate":
        cases = []
        if args.tests:
            raw = args.tests.strip()
            if raw.startswith("["):
                cases = json.loads(raw)
            else:
                cases = json.loads(Path(raw).read_text(encoding="utf-8"))
            cases = _prepare_cases(cases)
        result = verify_tool(args.tool, cases, timeout=args.timeout)
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        return 0 if result.ok else 1

    return 1


if __name__ == "__main__":
    sys.exit(main())
