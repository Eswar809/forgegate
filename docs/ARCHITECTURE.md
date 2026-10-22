# ForgeGate architecture

## Thesis

Self-extension is solved; **trusting self-extension is not**. ForgeGate keeps the
capability-gap → generate → reuse loop and inserts an independent gate that every
generated tool must pass before promotion.

```
                 ┌─────────────────────────────────────────────┐
 task ──────────►│ Agent                                        │
 (Slack/email/   │  1. registry.find(description)               │
  CLI)           │  2. hit  → run verified tool                 │
                 │     miss → generator.generate(task)          │
                 └───────────────┬─────────────────────────────┘
                                 │ source
                                 ▼
                 ┌─────────────────────────────────────────────┐
                 │ Verification gate (gate.py)                  │
                 │  static: syntax, imports allowlist,          │
                 │          no writes/subprocess/network/eval,  │
                 │          run() contract, --call entry point  │
                 │  behavioral: per-case output match,          │
                 │              determinism, timeout            │
                 └───────────────┬─────────────────────────────┘
                         fail ───┴─── pass
                          │            │
                          ▼            ▼
                    rejected      promote (servers/<name>/tool.py)
                  (never runs)    + registry entry + verification.json
                                       │
                                       ▼
                                  run + reuse counter
```

## Components

| Component | File | Responsibility |
|---|---|---|
| Registry | `forgegate/core.py` | Procedural memory. Atomic JSON writes, dedup, usage counters, keyword retrieval. |
| Memory | `forgegate/core.py` | Factual snapshot (`facts.json`) and behavioral rules (`instructions.md`) with proposal → approval → versioned history. |
| Static gate | `forgegate/gate.py` | Fail-closed AST policy. Unknown imports are rejected; writes, subprocess, sockets and dynamic execution are blocked. |
| Behavioral gate | `forgegate/gate.py` | Runs every case through `--call` in an isolated subprocess (`python -I`, temp cwd, minimal env, timeout), compares output, checks determinism. |
| Agent | `forgegate/agent.py` | Orchestrates lookup → generate → verify → promote → run. Promotion snapshots any previous version. |
| Generator | `forgegate/generator.py` | `FixtureGenerator` for offline demo; `LLMGenerator` for any OpenAI-compatible endpoint (Meta Model API, Baseten, Modal, Fireworks, vLLM). |

## Three-tier memory

| Tier | Artifact | Write path |
|---|---|---|
| Factual | `memory/facts.json` | Read-only snapshot, refreshed outside the agent. |
| Behavioral | `memory/instructions.md` | Only `memory propose` → human review → `memory approve`; every approval snapshots the prior file to `memory/history/`. |
| Procedural | `servers/` + `registry.json` | Only the verification gate. Failed tools never enter. |

## Threat model

In scope for the hackathon build:

- Generated code that tries to escape the workspace, spawn processes, open sockets,
  write files, or use dynamic execution.
- Nondeterministic tools that pass once and fail later.
- Silent regressions when a tool is regenerated (previous version is kept).

Out of scope here, documented for production:

- Kernel-level isolation. Use Docker/Firecracker/gVisor instead of a subprocess for
  untrusted code; the sandbox boundary is `run_cli()`.
- Network egress. Enforce at the container/network-policy layer.
- Database permissions. Use a read-only role; never hand the agent write credentials.
- Prompt injection from task text or logs. Treat task content as data, never as
  instructions; the generator prompt already separates them, but a production build
  needs content provenance and allowlisted tool actions.

## Benchmark methodology

`bench/tasks.jsonl` contains 8 ops tasks across 4 tool types. Each type appears twice:
the first occurrence forges, the second must reuse. `bench/run_bench.py` reports:

- success rate (output satisfies the task's expected evidence)
- forged vs reused counts
- average forge latency vs average reuse latency (speedup)
- gate pass rate

The offline fixture generator keeps the measurement reproducible. Swapping in
`LLMGenerator` changes only who writes the source; the gate and metrics stay identical.

## Roadmap after the hackathon

1. Slack/email adapters around `Agent.handle` (the queue is intentionally a plain dict).
2. Docker sandbox backend with no-egress networking.
3. Tool provenance: signed registry entries, `sha256` already stored per tool.
4. Cross-agent tool sharing with the same gate at import time.
5. Public benchmark expansion to 25 tasks and 8 tool types.

## Licensing

Core registry and static-check ideas are adapted from claude-forge (MIT). No code
from unlicensed repositories (`av/mi`, `local-skills-agent`) is included. See
`NOTICE.md`.
