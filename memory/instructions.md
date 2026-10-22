# ForgeGate behavioral memory

Rules injected into every agent turn. Change them only through a reviewed,
versioned proposal (`forgegate memory propose` then `forgegate memory approve`).

## Rules
- Never write to production databases or external systems.
- Prefer reusing a verified tool over forging a new one.
- A tool that fails verification must never be promoted.
- Report evidence (file, line, job id) alongside the conclusion.
