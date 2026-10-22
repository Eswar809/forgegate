# ForgeGate

**Verified self-extension for AI ops agents.**

An ops agent that hits a capability gap writes its own CLI tool — but **no tool runs until it passes an independent verification gate**. Verified tools are promoted to permanent procedural memory and reused.

> Built for the Meta Global AI Developer Hackathon (Oct 26 – Nov 4, 2026), indie track.
> Architecture adapted from [claude-forge](https://github.com/tomasmihalyi/claude-forge) (MIT).
> See `NOTICE.md` for attribution and `docs/ARCHITECTURE.md` for the design.

## Why

Self-extending agents already exist (claude-forge, av/mi, starpod). What none of them do is **prove that a self-written tool is safe and correct before it touches production**. ForgeGate adds that missing layer:

1. **Generate** — capability gap detected, coding agent writes a tool.
2. **Gate** — static policy checks + behavioral test cases + determinism check, in an isolated subprocess.
3. **Promote** — only on a clean report; versioned backup of any previous tool.
4. **Reuse** — registry match, with usage counters.
5. **Learn** — behavioral rules (`instructions.md`) change only through a reviewed, versioned proposal.

## Quick start

```powershell
cd C:\Users\deevi\Downloads\forgegate
$env:PYTHONPATH = "."
python -m forgegate.cli registry list
python bench\run_bench.py
```

`run_bench.py` runs 8 ops tasks (4 tool types, each with a forge + reuse case) and prints:

```
task  mode     ok  latency_ms  tool
t01   forged   yes      ...
t02   reuse    yes      ...
...
success=8/8  forged=4  reused=4  gate_pass=100%
```

## How a tool is born

```
task ──► registry lookup ──hit──► run existing tool
             │ miss
             ▼
      generator (LLM or fixture)
             │ source
             ▼
      static gate: syntax, allowlisted imports, no writes,
                   no subprocess/network/eval, CLI contract
             │
             ▼
      behavioral gate: per-case output match + determinism,
                       isolated subprocess, timeout
             │ fail ──► rejected (never promoted)
             ▼ pass
      promote to servers/<name>/tool.py + registry entry
             │
             ▼
      run, verify output, increment uses
```

## Three-tier memory

| Tier | File | Who writes it |
|---|---|---|
| Factual | `memory/facts.json` + `facts_history.jsonl` | the agent, after a verified run — ADD / UPDATE / NONE against existing facts, with soft invalidation and a full audit trail |
| Behavioral | `memory/instructions.md` | human-approved proposals only (`memory propose` → `memory approve`) |
| Procedural | `registry.json` + `servers/` | verification gate only |

Facts are never silently overwritten: an update sets `valid_to` on the old fact
and records `supersedes`, so history stays queryable (`memory fact-history`).
Batch writes are validated on a deep copy and committed once.

## Beyond the core loop

The build also includes the pieces a real ops agent needs around the gate:

- **Semantic judge** (`forgegate/judge.py`) — rubric-based LLM check for tasks
  with no exact expected string. It fails closed: an unavailable model or a
  malformed reply is a rejection.
- **Read-only database** (`forgegate/db.py`) — generated tools never get a
  connection. The agent reads through a statement allowlist *and* an engine-level
  SQLite authorizer (or a read-only Postgres transaction).
- **Sessions** (`forgegate/session.py`) — resumable `agent_sessions` /
  `agent_messages` transcripts, inspectable with `session list|show`.
- **Sandbox backends** (`forgegate/sandbox.py`) — hardened subprocess by default;
  Docker with `--network none`, read-only root, memory and PID limits when
  available.
- **Ingress adapters** (`forgegate/adapters/`) — Slack Socket Mode and IMAP/SMTP,
  both feeding `Agent.handle`.

## Repository layout

```
forgegate/
├── forgegate/            # core package (stdlib only)
│   ├── core.py           # registry + factual/behavioral memory
│   ├── gate.py           # static + behavioral verification
│   ├── generator.py      # fixture generator + LLM adapter
│   ├── memory_ai.py      # optional LLM fact extraction and decisions
│   ├── judge.py          # fail-closed semantic judge
│   ├── db.py             # read-only SQLite / Postgres
│   ├── session.py        # SQLite transcripts
│   ├── sandbox.py        # subprocess / Docker execution
│   ├── agent.py          # forge → verify → promote → reuse loop
│   ├── adapters/         # Slack and email ingress
│   └── cli.py
├── bench/
│   ├── tasks.jsonl       # 8 ops tasks
│   ├── data/             # sync jobs, logs, schemas, queues
│   ├── fixtures/         # offline tool sources (LLM substitutes)
│   └── run_bench.py
├── memory/               # instructions.md, facts.json, history/
├── servers/              # promoted, verified tools only
├── staging/              # generated tools awaiting the gate
├── tests/                # 40 gate, memory, judge, DB, session, agent tests
├── reference/            # cloned upstream repos (gitignored)
└── docs/
    ├── ARCHITECTURE.md
    └── RESEARCH.md       # upstream → license → pattern map
```

## Using a real model (hackathon build)

Set an OpenAI-compatible endpoint and swap the fixture generator for the LLM adapter:

```powershell
$env:FORGEGATE_API_BASE = "https://<meta-model-api>/v1"
$env:FORGEGATE_API_KEY  = "..."
$env:FORGEGATE_MODEL    = "muse-spark-1.3"
python bench\run_bench.py --live
```

When those env vars are present, `build_generator()` returns `LLMGenerator`; the
same configuration also enables the semantic judge and LLM memory extraction.
Otherwise the offline fixture generator is used so the demo and tests always run.

## CLI cheat sheet

```powershell
python -m forgegate.cli run --task-file task.json --session demo
python -m forgegate.cli memory facts
python -m forgegate.cli memory fact-history
python -m forgegate.cli memory forget --fact fact-1a2b3c4d --reason "stale"
python -m forgegate.cli session list
python -m forgegate.cli gate servers\sync-failure-inspect\tool.py
python -m forgegate.cli judge --task "..." --output "..." --expected SYNC-1042
python -m forgegate.cli db tables --path bench\data\ops.db
python -m forgegate.cli db query --path bench\data\ops.db --sql "SELECT * FROM jobs"
python -m forgegate.cli serve-slack          # needs slack-bolt + SLACK_* env
python -m forgegate.cli poll-email           # needs IMAP/SMTP env
```

## Safety model

- Generated tools run through `forgegate/sandbox.py`: a hardened subprocess
  (`python -I`, fresh temp cwd, minimal environment, hard timeout) by default, or
  a Docker container with `--network none`, a read-only root, `--memory 256m` and
  `--pids-limit 64` when `FORGEGATE_SANDBOX=docker`.
- Static policy is fail-closed: imports must be allowlisted; subprocess, sockets,
  `eval`/`exec`, and file writes are rejected.
- A failed gate never promotes. Promotion keeps the previous version under
  `servers/<name>/history/`.
- The semantic judge fails closed: no model, malformed JSON, or a reply without a
  boolean `pass` is a rejection.
- Generated tools never receive database credentials. Agent-side reads go through
  a statement allowlist plus an engine-level read-only guard.
- Memory mutations are atomic and audited; behavioral rules require explicit
  approval.
- Kernel-level isolation and hostile multi-tenant operation remain production
  concerns; see `docs/ARCHITECTURE.md`.

## License

MIT for this repository. Adapted portions retain their upstream MIT notice — see `NOTICE.md`.
