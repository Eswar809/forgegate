# Research notes: what ForgeGate borrowed

ForgeGate's gate is original. The memory, judging, database, session and sandbox
layers were designed after reading production agent runtimes, because those
problems have already been solved well by teams with far more traffic than a
hackathon demo will see.

Method: each repository was cloned into `reference/` (gitignored, never shipped),
the relevant module was read, and the pattern was reimplemented in stdlib Python.
No source lines were copied. Apache-2.0 and MIT both permit code reuse, but a
clean reimplementation keeps the dependency surface at zero and makes the
licensing story simple. Attribution is still recorded in `NOTICE.md`.

## Adopted patterns

| Project | License | Pattern adopted | Where it landed |
|---|---|---|---|
| [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) | MIT | Pluggable `MemoryProvider` lifecycle (`prefetch` before a turn, `sync_turn` after), a frozen memory snapshot per session, explicit add/patch/remove operations, staged approval for destructive writes, atomic file replacement, egress redaction before a write leaves the process | `Memory.prefetch`, `Memory.instructions_snapshot`, `Memory.propose_rule` / `approve`, `_atomic_write_text` in `forgegate/core.py` |
| [mem0ai/mem0](https://github.com/mem0ai/mem0) | Apache-2.0 | Two-phase write: extract candidate facts, then compare each against existing memory and choose ADD / UPDATE / DELETE / NONE. Updates keep `old_memory` for audit instead of overwriting silently | `Memory.remember_fact`, `Memory.apply_operations`, `forgegate/memory_ai.py` |
| [getzep/graphiti](https://github.com/getzep/graphiti) | Apache-2.0 | Temporal validity on memory: `valid_at` / `invalid_at` / `expired_at` and reference time. Superseded facts are invalidated, not deleted, so "what did we believe last week?" stays answerable | `Fact.valid_from` / `valid_to` / `supersedes` / `invalidated_by`, `fact_history` |
| [anthropics/claude-agent-sdk-python](https://github.com/anthropics/claude-agent-sdk-python) | MIT | Memory as a plain filesystem the model can read and diff; explicit, auditable mutations rather than hidden vector state | `memory/instructions.md`, `memory/facts.json`, `memory/facts_history.jsonl` |
| [openai/openai-agents-python](https://github.com/openai/openai-agents-python) | MIT | `SQLiteSession`: `agent_sessions` + ordered `agent_messages`, so a run can resume without replaying the transcript; connection-per-operation with explicit close | `forgegate/session.py` |
| [letta-ai/letta](https://github.com/letta-ai/letta) | Apache-2.0 | Core-memory blocks that the agent edits through explicit tools. Studied for the approval flow; the checkout at HEAD contained docs only, so no code was read | Behavioral-memory design in `docs/ARCHITECTURE.md` |
| [promptfoo/promptfoo](https://github.com/promptfoo/promptfoo) | MIT | `llm-rubric`: an LLM judge returns a structured pass/fail plus a reason, not a free-text opinion | `forgegate/judge.py` |
| [confident-ai/deepeval](https://github.com/confident-ai/deepeval) | Apache-2.0 | G-Eval-style rubric judging with explicit criteria and evidence | `SemanticJudge` rubric contract |
| [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers) | MIT / Apache-2.0 (transition) | Read-only database access as a first-class server capability: allowlisted statements, no credentials in the generated tool | `forgegate/db.py` |
| [e2b-dev/E2B](https://github.com/e2b-dev/E2B) | Apache-2.0 | Disposable sandbox with no network and a read-only root filesystem for generated code | `DockerSandbox` flags in `forgegate/sandbox.py` (implemented with the local Docker CLI, no E2B dependency) |
| [All-Hands-AI/OpenHands](https://github.com/All-Hands-AI/OpenHands) | MIT | Runtime/agent separation: the policy layer never shares a process with the code it evaluates | `SubprocessSandbox` vs `DockerSandbox` selection |
| [sierra-research/tau-bench](https://github.com/sierra-research/tau-bench) | MIT | Task-success measured against explicit expected evidence, with repeated trials rather than a single happy path | `bench/run_bench.py` metrics, roadmap item for pass^k |
| [slackapi/bolt-python](https://github.com/slackapi/bolt-python) | MIT | Socket Mode ingress so the agent needs no public HTTP endpoint | `forgegate/adapters/slack.py` |
| [tomasmihalyi/claude-forge](https://github.com/tomasmihalyi/claude-forge) | MIT | Capability-gap → generate → validate → register → reuse; atomic JSON registry; static validation | `Registry`, static checks in `gate.py` |

## Deliberate differences

- **No vector database.** Mem0 and Graphiti assume embeddings and an LLM in the
  write path. The demo must run offline, so `Memory` uses deterministic token
  overlap, and the LLM extractor/decider is optional (`memory_ai.py`). The
  interface is the same, so swapping in Mem0 later touches one class.
- **Exact duplicates only.** A fact is `NONE` only when its normalized text is
  unchanged. Near-identical wording is treated as an update, because facts that
  differ by a port, owner or version are different assertions and must supersede.
  Character similarity ranks search results; token overlap decides writes.
- **All-or-nothing batches.** Hermes and Mem0 apply operations one at a time.
  `apply_operations` deep-copies, validates every operation, and writes once, so
  a malformed batch cannot leave memory half-updated.
- **The judge fails closed.** A missing endpoint, a malformed reply, or a reply
  without a boolean `pass` is a rejection. An unavailable judge never becomes a
  silent success.
- **No provider plugin API.** Hermes supports multiple memory backends. ForgeGate
  has one `Memory` class; a plugin registry would be speculative surface for a
  single-process agent.

## Repositories studied but not used

| Project | Why not |
|---|---|
| [av/mi](https://github.com/av/mi) | No license file. Architecture studied only; nothing copied. |
| [ArneJanning/local-skills-agent](https://github.com/ArneJanning/local-skills-agent) | No license file. Same treatment. |
| [ThreeMoonsLab/agents-shipgate](https://github.com/ThreeMoonsLab/agents-shipgate) | Apache-2.0. Useful framing, but ForgeGate's gate predates it and shares no code. |
| [sinaptik-ai/starpod](https://github.com/sinaptik-ai/starpod) | MIT. Packaging ideas only; not needed for a single-machine demo. |

## Reading list for the demo narrative

1. Hermes `MemoryProvider` — why a frozen snapshot beats live memory reads
   mid-session (prompt stability).
2. Mem0's update path — why blind appends corrupt long-lived memory.
3. Graphiti's temporal edges — why deletion loses the audit trail.
4. tau-bench — why a single successful run is not a benchmark.
