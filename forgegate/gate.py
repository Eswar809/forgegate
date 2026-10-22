"""The ForgeGate verification gate.

Static policy checks plus behavioral tests in an isolated subprocess.
A tool is promoted only when every check passes.

Static-check approach generalized from claude-forge's validate_server.py
(MIT, Copyright (c) 2026 Tomas Mihalyi). See NOTICE.md.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Fail closed: every import must appear here.
ALLOWED_IMPORTS = {
    "__future__", "base64", "collections", "csv", "dataclasses", "datetime",
    "decimal", "functools", "hashlib", "itertools", "json", "math", "pathlib",
    "random", "re", "statistics", "string", "sys", "textwrap", "time",
    "typing", "uuid", "zoneinfo",
}

FORBIDDEN_IMPORTS = {
    "ctypes", "ftplib", "http", "httplib", "multiprocessing", "paramiko",
    "pickle", "pty", "requests", "shutil", "signal", "socket", "sqlite3",
    "subprocess", "telnetlib", "urllib",
}

FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__", "breakpoint", "input"}

FORBIDDEN_ATTRS = {
    ("os", "system"), ("os", "popen"), ("os", "remove"), ("os", "unlink"),
    ("os", "rmdir"), ("os", "rename"), ("os", "replace"), ("os", "chmod"),
}

WRITE_FLAGS = set("wax+")


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class GateResult:
    ok: bool
    checks: list[Check] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": [asdict(c) for c in self.checks],
            "metrics": self.metrics,
        }


def _imports(tree: ast.Module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[0])
    return found


def static_checks(path: str | Path) -> list[Check]:
    path = Path(path)
    checks: list[Check] = []
    if not path.exists():
        return [Check("file_exists", False, f"not found: {path}")]

    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [Check("syntax", False, str(exc))]
    checks.append(Check("syntax", True))

    imports = _imports(tree)
    blocked = sorted(imports & FORBIDDEN_IMPORTS)
    checks.append(Check(
        "no_forbidden_imports", not blocked,
        f"forbidden: {', '.join(blocked)}" if blocked else "",
    ))
    unknown = sorted(imports - ALLOWED_IMPORTS - FORBIDDEN_IMPORTS)
    checks.append(Check(
        "imports_allowlisted", not unknown,
        f"not allowlisted: {', '.join(unknown)}" if unknown else "",
    ))

    run_fn = next(
        (n for n in tree.body
         if isinstance(n, ast.FunctionDef) and n.name == "run"),
        None,
    )
    checks.append(Check("has_sync_run", run_fn is not None))
    if run_fn is not None:
        doc = ast.get_docstring(run_fn)
        checks.append(Check("run_docstring", bool(doc), "" if doc else "missing docstring"))
        missing = [
            arg.arg for arg in run_fn.args.args
            if arg.annotation is None
        ]
        checks.append(Check(
            "run_type_hints", not missing,
            f"missing hints: {', '.join(missing)}" if missing else "",
        ))

    checks.append(Check(
        "cli_call_block", '"--call"' in source or "'--call'" in source,
        "" if ("--call" in source) else "missing --call entry point",
    ))

    dangerous: list[str] = []
    writes: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            if func.id in FORBIDDEN_CALLS:
                dangerous.append(f"{func.id}@{node.lineno}")
            if func.id == "open":
                modes = [
                    a.value for a in node.args[1:2] if isinstance(a, ast.Constant)
                ]
                modes += [
                    kw.value.value for kw in node.keywords
                    if kw.arg == "mode" and isinstance(kw.value, ast.Constant)
                ]
                if any(isinstance(m, str) and WRITE_FLAGS & set(m) for m in modes):
                    writes.append(f"line {node.lineno}")
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if (func.value.id, func.attr) in FORBIDDEN_ATTRS:
                dangerous.append(f"{func.value.id}.{func.attr}@{node.lineno}")

    checks.append(Check(
        "no_dangerous_calls", not dangerous,
        f"blocked: {', '.join(dangerous)}" if dangerous else "",
    ))
    checks.append(Check(
        "no_file_writes", not writes,
        f"write-mode open: {', '.join(writes)}" if writes else "",
    ))

    top_level_calls = [
        n for n in tree.body
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
    ]
    checks.append(Check(
        "no_import_side_effects", not top_level_calls,
        f"{len(top_level_calls)} top-level call(s)" if top_level_calls else "",
    ))
    return checks


def run_cli(
    tool_path: str | Path,
    args: dict[str, Any] | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Run a tool through its --call entry point, isolated and time-boxed."""
    tool_path = Path(tool_path).resolve()
    env = {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    if os.environ.get("PATH"):
        env["PATH"] = os.environ["PATH"]
    if os.name == "nt" and os.environ.get("SYSTEMROOT"):
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]

    command = [
        sys.executable, "-I", str(tool_path),
        "--call", json.dumps(args or {}, ensure_ascii=False),
    ]
    with tempfile.TemporaryDirectory(prefix="forgegate-run-") as workdir:
        started = time.perf_counter()
        try:
            proc = subprocess.run(
                command, cwd=workdir, env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return {
                "ok": False, "returncode": -1, "stdout": "",
                "stderr": f"timeout after {timeout}s",
                "elapsed_ms": round(timeout * 1000, 1),
            }
        elapsed = (time.perf_counter() - started) * 1000
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "elapsed_ms": round(elapsed, 1),
        }


def _is_subset(expected: Any, actual: Any) -> bool:
    if isinstance(expected, dict) and isinstance(actual, dict):
        return all(k in actual and _is_subset(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list) and isinstance(actual, list):
        return len(expected) <= len(actual) and all(
            _is_subset(e, a) for e, a in zip(expected, actual)
        )
    return expected == actual


def _match(run: dict[str, Any], case: dict[str, Any]) -> tuple[bool, str]:
    if not run["ok"]:
        return False, f"exit {run['returncode']}: {run['stderr'].strip()[:300]}"
    out = run["stdout"].strip()

    if "expect_exact" in case:
        expected = str(case["expect_exact"]).strip()
        return (out == expected,
                "" if out == expected else f"expected {expected!r}, got {out[:300]!r}")
    if "expect_contains" in case:
        wanted = case["expect_contains"]
        if isinstance(wanted, str):
            wanted = [wanted]
        missing = [w for w in wanted if w not in out]
        return (not missing,
                "" if not missing else f"missing {missing}; got {out[:300]!r}")
    if "expect_regex" in case:
        ok = re.search(case["expect_regex"], out) is not None
        return ok, "" if ok else f"regex {case['expect_regex']!r} did not match {out[:300]!r}"
    if "expect_json" in case:
        try:
            actual = json.loads(out)
        except json.JSONDecodeError as exc:
            return False, f"output is not JSON: {exc}"
        ok = _is_subset(case["expect_json"], actual)
        return ok, "" if ok else f"JSON mismatch: {out[:300]!r}"
    return bool(out), "" if out else "empty output"


def verify_tool(
    tool_path: str | Path,
    cases: list[dict[str, Any]] | None = None,
    timeout: float = 10.0,
    determinism: bool = True,
) -> GateResult:
    """Run static checks, then every behavioral case, then determinism."""
    checks = static_checks(tool_path)
    metrics: dict[str, Any] = {}

    if not all(c.ok for c in checks):
        return GateResult(False, checks, metrics)

    cases = cases or [{"args": {}}]
    latencies: list[float] = []
    for index, case in enumerate(cases, start=1):
        run = run_cli(tool_path, case.get("args", {}), timeout=timeout)
        latencies.append(run["elapsed_ms"])
        ok, detail = _match(run, case)
        checks.append(Check(f"case_{index}", ok, detail))

    if determinism and cases:
        first = cases[0].get("args", {})
        one = run_cli(tool_path, first, timeout=timeout)
        two = run_cli(tool_path, first, timeout=timeout)
        same = (
            one["returncode"] == two["returncode"]
            and one["stdout"] == two["stdout"]
        )
        checks.append(Check(
            "deterministic", same,
            "" if same else "output changed between identical runs",
        ))

    metrics["cases"] = len(cases)
    metrics["avg_latency_ms"] = (
        round(sum(latencies) / len(latencies), 1) if latencies else 0.0
    )
    metrics["max_latency_ms"] = round(max(latencies), 1) if latencies else 0.0
    return GateResult(all(c.ok for c in checks), checks, metrics)
