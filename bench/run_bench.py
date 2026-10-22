#!/usr/bin/env python3
"""Run the ForgeGate benchmark: forge, verify, promote, reuse."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forgegate.agent import Agent  # noqa: E402
from forgegate.generator import LLMGenerator  # noqa: E402


def reset_state(root: Path) -> None:
    registry = root / "registry.json"
    if registry.exists():
        registry.unlink()
    for name in ("servers", "staging"):
        path = root / name
        if path.exists():
            shutil.rmtree(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true", help="keep existing registry")
    parser.add_argument(
        "--live", action="store_true",
        help="force the LLM generator (needs FORGEGATE_API_BASE/KEY/MODEL)",
    )
    parser.add_argument("--tasks", type=Path, default=ROOT / "bench" / "tasks.jsonl")
    args = parser.parse_args()

    generator = None
    if args.live:
        generator = LLMGenerator(ROOT)
        if not generator.available():
            parser.error(
                "--live needs FORGEGATE_API_BASE, FORGEGATE_API_KEY and FORGEGATE_MODEL"
            )

    if not args.keep:
        reset_state(ROOT)

    tasks = [
        json.loads(line)
        for line in args.tasks.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    agent = Agent(ROOT, generator=generator)
    rows = [agent.handle(task) for task in tasks]

    print(f"{'task':4} {'mode':8} {'ok':3} {'ms':>9}  tool")
    print("-" * 56)
    for row in rows:
        print(
            f"{row['task_id']:4} {row['mode']:8} "
            f"{('yes' if row['ok'] else 'no'):3} {row['latency_ms']:9.1f}  "
            f"{row['tool'] or '-'}"
        )

    forged = [r for r in rows if r["mode"] == "forged"]
    reused = [r for r in rows if r["mode"] == "reuse"]
    rejected = [r for r in rows if r["mode"] == "rejected"]
    passed = [r for r in rows if r["ok"]]

    avg_forge = sum(r["latency_ms"] for r in forged) / len(forged) if forged else 0.0
    avg_reuse = sum(r["latency_ms"] for r in reused) / len(reused) if reused else 0.0
    speedup = avg_forge / avg_reuse if avg_reuse else 0.0
    attempts = len(forged) + len(rejected)
    gate_rate = 100 * len(forged) / attempts if attempts else 0.0

    print("-" * 56)
    print(
        f"success={len(passed)}/{len(rows)}  forged={len(forged)}  "
        f"reused={len(reused)}  rejected={len(rejected)}"
    )
    print(
        f"avg forge={avg_forge:.0f} ms  avg reuse={avg_reuse:.0f} ms  "
        f"speedup={speedup:.1f}x  gate_pass={gate_rate:.0f}%"
    )

    results_dir = ROOT / "bench" / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    out = results_dir / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ.json")
    out.write_text(
        json.dumps(
            {
                "tasks": rows,
                "summary": {
                    "success": len(passed),
                    "total": len(rows),
                    "forged": len(forged),
                    "reused": len(reused),
                    "rejected": len(rejected),
                    "avg_forge_ms": round(avg_forge, 1),
                    "avg_reuse_ms": round(avg_reuse, 1),
                    "speedup": round(speedup, 2),
                    "gate_pass_pct": round(gate_rate, 1),
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"results: {out.relative_to(ROOT)}")
    return 0 if len(passed) == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
