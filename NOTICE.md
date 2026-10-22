# Notice and attribution

ForgeGate is MIT-licensed. It adapts ideas and, where noted, code from the
following projects.

## claude-forge — MIT

- Source: https://github.com/tomasmihalyi/claude-forge
- Copyright (c) 2026 Tomas Mihalyi
- Used for: the capability-gap → generate → validate → register → reuse
  protocol, the atomic JSON registry pattern, and the static validation
  approach (generalized from MCP server checks to plain CLI tools).
- Files influenced: `forgegate/core.py` (registry), `forgegate/gate.py`
  (static checks).

The upstream MIT license text is reproduced in `reference/claude-forge/LICENSE`
and applies to adapted portions.

## Ideas studied, no code copied

The following repositories were cloned for architecture study only. They carry
**no license**, so no code from them is included in this repository:

- https://github.com/av/mi — self-extending sandboxed harness
- https://github.com/ArneJanning/local-skills-agent — local skill creator

## Ideas studied, permissive license

- https://github.com/sinaptik-ai/starpod — MIT — runtime packaging ideas only.
- https://github.com/ThreeMoonsLab/agents-shipgate — Apache-2.0 — the concept of
  a deterministic gate for agent capability changes. No Apache-licensed code is
  copied; only the problem framing is shared.

## Inspiration

- Answer This (Ayush) — "How to Build an Internal AI Agent That Evolves Itself",
  YC talk. The self-extending ops-agent pattern and three-tier memory framing.
  ForgeGate's contribution is the independent verification gate and benchmark.
