"""Command line interface for ForgeGate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .agent import Agent
from .core import Memory, Registry, repo_root
from .db import ReadOnlyError, ReadOnlySQLite
from .gate import verify_tool
from .judge import SemanticJudge
from .session import SessionStore


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


def _print_facts(memory: Memory, limit: int = 50) -> None:
    facts = memory.active_facts()
    for fact in facts[:limit]:
        print(f"{fact['id']}  [{fact.get('kind', 'fact')}]  {fact['text']}")
        print(
            f"  source={fact.get('source', '')} "
            f"valid_from={fact.get('valid_from', '')}"
        )
    if not facts:
        print("no active facts")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="forgegate", description="Verified self-extension for ops agents"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="run one task through the agent")
    run_p.add_argument("--task", help="inline JSON task")
    run_p.add_argument("--task-file", type=Path)
    run_p.add_argument("--root", type=Path)
    run_p.add_argument("--session", help="record this exchange under a session id")

    reg_p = sub.add_parser("registry", help="inspect procedural memory")
    reg_p.add_argument("action", choices=["list"])
    reg_p.add_argument("--root", type=Path)

    mem_p = sub.add_parser("memory", help="inspect or change memory")
    mem_p.add_argument(
        "action",
        choices=[
            "show", "propose", "approve", "history",
            "facts", "fact-history", "forget",
        ],
    )
    mem_p.add_argument("--rule")
    mem_p.add_argument("--reason")
    mem_p.add_argument("--proposal")
    mem_p.add_argument("--fact", help="fact id for forget")
    mem_p.add_argument("--root", type=Path)

    gate_p = sub.add_parser("gate", help="verify a tool file")
    gate_p.add_argument("tool", type=Path)
    gate_p.add_argument(
        "--tests", help="inline JSON list of cases, or a path to a .json file"
    )
    gate_p.add_argument("--timeout", type=float, default=10.0)

    judge_p = sub.add_parser("judge", help="semantically check one output")
    judge_p.add_argument("--task", required=True)
    judge_p.add_argument("--output", required=True)
    judge_p.add_argument("--expected", action="append", default=[])
    judge_p.add_argument("--rubric", default="")

    db_p = sub.add_parser("db", help="read-only database access")
    db_p.add_argument("action", choices=["tables", "query"])
    db_p.add_argument("--path", type=Path, required=True)
    db_p.add_argument("--sql")
    db_p.add_argument("--limit", type=int, default=50)

    sess_p = sub.add_parser("session", help="inspect stored transcripts")
    sess_p.add_argument("action", choices=["list", "show"])
    sess_p.add_argument("--id")
    sess_p.add_argument("--limit", type=int, default=20)
    sess_p.add_argument("--root", type=Path)

    slack_p = sub.add_parser("serve-slack", help="run the Slack Socket Mode app")
    slack_p.add_argument("--root", type=Path)

    mail_p = sub.add_parser("poll-email", help="run one email poll cycle")
    mail_p.add_argument("--root", type=Path)
    mail_p.add_argument("--limit", type=int, default=5)

    args = parser.parse_args(argv)

    if args.command == "run":
        task = _load_task(args)
        result = Agent(root=args.root, session_id=args.session).handle(task)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["ok"] else 1

    if args.command == "registry":
        registry = Registry(args.root)
        entries = registry.all()
        for entry in entries:
            print(f"{entry.name:24} uses={entry.uses:<3} {entry.path}")
            print(f"  {entry.description}")
        if not entries:
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
        elif args.action == "facts":
            _print_facts(memory)
        elif args.action == "fact-history":
            for record in memory.fact_history(limit=50):
                print(json.dumps(record, ensure_ascii=False))
        elif args.action == "forget":
            if not args.fact:
                raise SystemExit("--fact is required")
            ok = memory.invalidate_fact(
                args.fact, reason=args.reason or "manual", source="cli"
            )
            print("invalidated" if ok else "not found")
            return 0 if ok else 1
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

    if args.command == "judge":
        verdict = SemanticJudge().judge(
            args.task, args.output,
            expected=args.expected, rubric=args.rubric,
        )
        print(json.dumps(verdict.to_dict(), indent=2, ensure_ascii=False))
        return 0 if verdict.ok else 1

    if args.command == "db":
        try:
            with ReadOnlySQLite(args.path) as database:
                if args.action == "tables":
                    print(json.dumps(database.tables(), indent=2))
                    return 0
                if not args.sql:
                    raise SystemExit("--sql is required for query")
                rows = database.query(args.sql, limit=args.limit)
        except (ReadOnlyError, FileNotFoundError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0

    if args.command == "session":
        store = SessionStore(Memory(args.root).sessions_path)
        if args.action == "list":
            for row in store.sessions(limit=args.limit):
                print(f"{row['session_id']:32} {row['updated_at']}")
            return 0
        if not args.id:
            raise SystemExit("--id is required for show")
        print(json.dumps(store.messages(args.id, limit=args.limit), indent=2))
        return 0

    if args.command == "serve-slack":
        from .adapters.slack import run_socket_mode

        run_socket_mode(Agent(root=args.root, session_id="slack"))
        return 0

    if args.command == "poll-email":
        from .adapters.email import EmailConfig, EmailPoller, task_from_email

        poller = EmailPoller(EmailConfig.from_env())
        agent = Agent(root=args.root, session_id="email")
        for message in poller.fetch_unseen(limit=args.limit):
            result = agent.handle(task_from_email(message))
            body = result.get("output") or result.get("error") or "(no output)"
            poller.reply(message["from"], message["subject"], body)
            poller.mark_seen(message["uid"])
            print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
