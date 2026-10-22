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
| Memory | `forgegate/core.py` | Factual memory with ADD/UPDATE/DELETE/NONE decisions and temporal invalidation; behavioral rules with proposal → approval → versioned history. |
| Memory extractor | `forgegate/memory_ai.py` | Optional LLM pass that extracts candidate facts from a task/result pair and decides each write. Deterministic episodic fallback when no model is configured. |
| Static gate | `forgegate/gate.py` | Fail-closed AST policy. Unknown imports are rejected; writes, subprocess, sockets and dynamic execution are blocked. |
| Behavioral gate | `forgegate/gate.py` | Runs every case through `--call` in an isolated subprocess (`python -I`, temp cwd, minimal env, timeout), compares output, checks determinism. |
| Sandbox | `forgegate/sandbox.py` | Execution backends for the behavioral gate and for verified tools: hardened subprocess by default, Docker with `--network none`, read-only root and memory/PID limits when available. |
| Semantic judge | `forgegate/judge.py` | Optional LLM rubric check for tasks whose success is not an exact string match. Fails closed: no endpoint or malformed reply means rejection. |
| Read-only DB | `forgegate/db.py` | SQLite (and optional Postgres) reader with two independent guards: statement allowlist and engine-level authorizer / read-only transaction. |
| Sessions | `forgegate/session.py` | `agent_sessions` + ordered `agent_messages`, so a run can resume and be audited without replaying the transcript. |
| Adapters | `forgegate/adapters/` | Slack Socket Mode and IMAP/SMTP ingress. They only parse a message into a task and format the result; all execution goes through `Agent.handle`. |
| Agent | `forgegate/agent.py` | Orchestrates lookup → generate → verify → promote → run, prefetches memory, records the session, applies the judge, writes memory. |
| Generator | `forgegate/generator.py` | `FixtureGenerator` for offline demo; `LLMGenerator` for any OpenAI-compatible endpoint (Meta Model API, Baseten, Modal, Fireworks, vLLM). |

## Memory write path

Factual writes follow the Mem0 shape, with Graphiti-style temporal history:

```
candidate fact ──► compare with active facts (token overlap)
                      │ exact normalized text   → NONE
                      │ overlap ≥ 0.45          → UPDATE: old.valid_to = now,
                      │                           new.supersedes = old.id
                      │ otherwise               → ADD
                      ▼
                 one atomic write + append-only facts_history.jsonl
```

`apply_operations` accepts a batch and is all-or-nothing: a single invalid
operation raises before any file is written. Behavioral memory is separate and
stricter — `propose_rule` writes a proposal file, and only `approve` changes
`instructions.md` after snapshotting the previous version to `memory/history/`.
Each session reads a frozen snapshot, so mid-run edits cannot change the prompt
underneath the model (Hermes behavior).

## Three-tier memory

| Tier | Artifact | Write path |
|---|---|---|
| Factual | `memory/facts.json` + `facts_history.jsonl` | Agent after a verified run (`remember_fact` / `apply_operations`); every mutation audited. Facts expire by validity window, never by silent overwrite. |
| Behavioral | `memory/instructions.md` | Only `memory propose` → human review → `memory approve`; every approval snapshots the prior file to `memory/history/`. |
| Procedural | `servers/` + `registry.json` | Only the verification gate. Failed tools never enter. |

## Threat model

In scope and handled by the current build:

- Generated code that tries to escape the workspace, spawn processes, open sockets,
  write files, or use dynamic execution. Static policy rejects it before it runs;
  the sandbox removes the rest at run time.
- Nondeterministic tools that pass once and fail later.
- Silent regressions when a tool is regenerated (previous version is kept).
- Generated code reaching a database: tools never receive a connection, and
  `forgegate/db.py` reads through a statement allowlist plus an engine-level
  authorizer (SQLite) or a read-only transaction (Postgres).
- A judge that is unavailable or returns prose: the verdict fails closed, so a
  missing model can never promote an unverified tool.
- Memory corruption from a malformed write batch: operations are validated on a
  deep copy and committed once, so the store is never half-updated.

Out of scope here, documented for production:

- Kernel-level isolation. The Docker backend is the default hardening path; use
  Firecracker/gVisor for hostile multi-tenant code. The boundary is `run_tool()`.
- Network egress. The Docker backend sets `--network none`; the subprocess backend
  relies on the static policy and is not a security boundary.
- Database credentials. Prefer a read-only role with table-level grants; the
  in-process guards are defence in depth, not a substitute for database ACLs.
- Prompt injection from task text or logs. Treat task content as data, never as
  instructions; the generator prompt separates them, but a production build needs
  content provenance, allowlisted tool actions and egress filtering.

## Benchmark methodology

`bench/tasks.jsonl` contains 8 ops tasks across 4 tool types. Each type appears twice:
the first occurrence forges, the second must reuse. `bench/run_bench.py` reports:

- success rate (output satisfies the task's expected evidence)
- forged vs reused counts
- average forge latency vs average reuse latency (speedup)
- gate pass rate

The offline fixture generator keeps the measurement reproducible. Swapping in
`LLMGenerator` changes only who writes the source; the gate and metrics stay identical.
A separate 40-test unit suite covers the gate, registry, memory, judge, read-only
database, session store and the full forge → reuse loop. `--keep` reruns the
benchmark against an existing registry to measure warm reuse.

## Roadmap after the hackathon

1. Wire the real Meta Model API endpoint and record a live forge → gate → reuse run.
2. Expand the benchmark to 25 tasks and 8 tool types, with pass^k over repeated
   trials (tau-bench methodology) instead of a single run.
3. Slack/email adapters are in place; add a durable queue and idempotency keys.
4. Tool provenance: signed registry entries (`sha256` is already stored per tool).
5. Cross-agent tool sharing with the same gate at import time.

## Licensing

Core registry and static-check ideas are adapted from claude-forge (MIT). Memory,
judge, database, session and sandbox patterns were studied from Hermes, Mem0,
Graphiti, the Claude Agent SDK, the OpenAI Agents SDK, promptfoo, DeepEval,
MCP servers, E2B and tau-bench; see `docs/RESEARCH.md` for the per-project map and
`NOTICE.md` for attribution. No code from unlicensed repositories (`av/mi`,
`local-skills-agent`) is included.
