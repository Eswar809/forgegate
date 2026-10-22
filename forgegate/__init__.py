"""ForgeGate — verified self-extension for AI ops agents."""

from .core import Memory, Registry, ToolEntry, repo_root
from .gate import Check, GateResult, run_cli, verify_tool

__all__ = [
    "Memory",
    "Registry",
    "ToolEntry",
    "repo_root",
    "Check",
    "GateResult",
    "run_cli",
    "verify_tool",
]
