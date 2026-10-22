"""ForgeGate — verified self-extension for AI ops agents."""

from .core import Fact, Memory, Registry, ToolEntry, repo_root
from .db import ReadOnlyError, ReadOnlyPostgres, ReadOnlySQLite
from .gate import Check, GateResult, run_cli, verify_tool
from .judge import SemanticJudge, Verdict
from .sandbox import DockerSandbox, SubprocessSandbox, select_sandbox
from .session import SessionStore

__all__ = [
    "Fact",
    "Memory",
    "Registry",
    "ToolEntry",
    "repo_root",
    "Check",
    "GateResult",
    "run_cli",
    "verify_tool",
    "SemanticJudge",
    "Verdict",
    "ReadOnlyError",
    "ReadOnlyPostgres",
    "ReadOnlySQLite",
    "SessionStore",
    "DockerSandbox",
    "SubprocessSandbox",
    "select_sandbox",
]
