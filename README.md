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
| Factual | `memory/facts.json` | read-only snapshot of services/DB schema |
| Behavioral | `memory/instructions.md` | human-approved proposals only (`memory propose` → `memory approve`) |
| Procedural | `registry.json` + `servers/` | verification gate only |

## Repository layout

```
forgegate/
├── forgegate/            # core package (stdlib only)
│   ├── core.py           # registry + 3-tier memory
│   ├── gate.py           # static + behavioral verification
│   ├── generator.py      # fixture generator + LLM adapter
│   ├── agent.py          # forge → verify → promote → reuse loop
│   └── cli.py
├── bench/
│   ├── tasks.jsonl       # 8 ops tasks
│   ├── data/             # sync jobs, logs, schemas, queues
│   ├── fixtures/         # offline tool sources (LLM substitutes)
│   └── run_bench.py
├── memory/               # instructions.md, facts.json, history/
├── servers/              # promoted, verified tools only
├── staging/              # generated tools awaiting the gate
├── tests/                # gate + registry unit tests
├── reference/            # cloned upstream repos (gitignored)
└── docs/ARCHITECTURE.md
```

## Using a real model (hackathon build)

Set an OpenAI-compatible endpoint and swap the fixture generator for the LLM adapter:

```powershell
$env:FORGEGATE_API_BASE = "https://<meta-model-api>/v1"
$env:FORGEGATE_API_KEY  = "..."
$env:FORGEGATE_MODEL    = "muse-spark-1.3"
python bench\run_bench.py --live
```

When those env vars are present, `build_generator()` returns `LLMGenerator`; otherwise the offline fixture generator is used so the demo and tests always run.

## Safety model

- Generated tools run with `python -I` in a fresh temp working directory, minimal environment, hard timeout.
- Static policy is fail-closed: imports must be allowlisted; subprocess, sockets, `eval`/`exec`, and file writes are rejected.
- A failed gate never promotes. Promotion keeps the previous version under `servers/<name>/history/`.
- Production hardening (Docker/Firecracker, egress rules, read-only DB roles) is described in `docs/ARCHITECTURE.md`.

## License

MIT for this repository. Adapted portions retain their upstream MIT notice — see `NOTICE.md`.
