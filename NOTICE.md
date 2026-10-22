# Notice and attribution

ForgeGate is MIT-licensed. It adapts ideas from the projects below. No source
lines from Apache-2.0 projects are copied: their patterns were reimplemented, as
recorded in `docs/RESEARCH.md`. MIT-licensed code that was adapted keeps its
upstream notice.

## Code adapted

### claude-forge — MIT

- Source: https://github.com/tomasmihalyi/claude-forge
- Copyright (c) 2026 Tomas Mihalyi
- Used for: the capability-gap → generate → validate → register → reuse
  protocol, the atomic JSON registry pattern, and the static validation
  approach (generalized from MCP server checks to plain CLI tools).
- Files influenced: `forgegate/core.py` (registry), `forgegate/gate.py`
  (static checks).

The upstream MIT license text is reproduced in `reference/claude-forge/LICENSE`
and applies to adapted portions.

## Patterns reimplemented

These projects were cloned into `reference/` for study. Their licenses permit
reuse; ForgeGate nevertheless reimplements the patterns in stdlib Python and
ships no upstream code.

### Hermes Agent — MIT

- Source: https://github.com/NousResearch/hermes-agent
- Copyright (c) 2025 Nous Research
- Patterns: memory-provider lifecycle, frozen per-session snapshot, staged
  approval for destructive writes, atomic writes, egress redaction.
- Files influenced: `forgegate/core.py` (`Memory`).

### Mem0 — Apache-2.0

- Source: https://github.com/mem0ai/mem0
- Patterns: fact extraction followed by ADD / UPDATE / DELETE / NONE decisions;
  audit of the superseded value.
- Files influenced: `forgegate/core.py` (`remember_fact`, `apply_operations`),
  `forgegate/memory_ai.py`.

### Graphiti (Zep) — Apache-2.0

- Source: https://github.com/getzep/graphiti
- Patterns: temporal validity (`valid_at` / `invalid_at` / `expired_at`) and soft
  invalidation in place of deletion.
- Files influenced: `forgegate/core.py` (`Fact`, `fact_history`).

### Claude Agent SDK — MIT

- Source: https://github.com/anthropics/claude-agent-sdk-python
- Copyright (c) 2025 Anthropic, PBC
- Patterns: filesystem memory with explicit, auditable operations.
- Files influenced: `forgegate/core.py` (memory directory layout).

### OpenAI Agents SDK — MIT

- Source: https://github.com/openai/openai-agents-python
- Copyright (c) 2025 OpenAI
- Patterns: SQLite session tables (`agent_sessions`, `agent_messages`) for
  resumable transcripts.
- Files influenced: `forgegate/session.py`.

### promptfoo — MIT

- Source: https://github.com/promptfoo/promptfoo
- Patterns: structured `llm-rubric` verdicts.
- Files influenced: `forgegate/judge.py`.

### Bolt for Python — MIT

- Source: https://github.com/slackapi/bolt-python
- Copyright (c) 2020-2025 Slack Technologies, LLC
- Patterns: Socket Mode ingress.
- Files influenced: `forgegate/adapters/slack.py` (optional dependency).

### tau-bench — MIT

- Source: https://github.com/sierra-research/tau-bench
- Copyright (c) 2024 Sierra
- Patterns: task success measured against explicit expected evidence.
- Files influenced: `bench/run_bench.py`.

### MCP servers — MIT / Apache-2.0 transition

- Source: https://github.com/modelcontextprotocol/servers
- Patterns: read-only database capability with allowlisted statements.
- Files influenced: `forgegate/db.py`.

### Letta (MemGPT) — Apache-2.0

- Source: https://github.com/letta-ai/letta
- Patterns: self-editing core-memory blocks with explicit tools. Studied from
  documentation only; the checkout at HEAD contained no runtime code.
- Files influenced: behavioral-memory design in `docs/ARCHITECTURE.md`.

### DeepEval — Apache-2.0

- Source: https://github.com/confident-ai/deepeval
- Patterns: G-Eval-style rubric criteria and evidence.
- Files influenced: `forgegate/judge.py` (rubric contract).

### E2B — Apache-2.0

- Source: https://github.com/e2b-dev/E2B
- Patterns: disposable, no-network, read-only-root sandbox for generated code.
- Files influenced: `forgegate/sandbox.py` (`DockerSandbox` flags). Implemented
  against the local Docker CLI; E2B is not a dependency.

### OpenHands — MIT

- Source: https://github.com/All-Hands-AI/OpenHands
- Patterns: runtime/agent separation so the policy layer never shares a process
  with the code it evaluates.
- Files influenced: `forgegate/sandbox.py`.

## Ideas studied, no code copied, no license

The following repositories carry **no license file**, so nothing from them is
included in this repository:

- https://github.com/av/mi — self-extending sandboxed harness
- https://github.com/ArneJanning/local-skills-agent — local skill creator

## Ideas studied, permissive license, no code copied

- https://github.com/sinaptik-ai/starpod — MIT — runtime packaging ideas only.
- https://github.com/ThreeMoonsLab/agents-shipgate — Apache-2.0 — the concept of
  a deterministic gate for agent capability changes. ForgeGate's gate predates
  the study and shares no code.

## Inspiration

- Answer This (Ayush) — "How to Build an Internal AI Agent That Evolves Itself",
  YC talk. The self-extending ops-agent pattern and three-tier memory framing.
  ForgeGate's contribution is the independent verification gate and benchmark.
