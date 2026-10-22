"""Execution sandboxes for generated tools.

The default is an isolated subprocess; ``FORGEGATE_SANDBOX=docker`` switches
to a no-network, read-only container. The Docker backend is the production
shape recommended in docs/ARCHITECTURE.md; the subprocess backend keeps the
demo and CI dependency-free.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Protocol


def _base_env() -> dict[str, str]:
    env = {
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if os.environ.get("PATH"):
        env["PATH"] = os.environ["PATH"]
    if os.name == "nt" and os.environ.get("SYSTEMROOT"):
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    return env


def _timeout(timeout: float) -> dict[str, Any]:
    return {
        "ok": False, "returncode": -1, "stdout": "",
        "stderr": f"timeout after {timeout}s",
        "elapsed_ms": round(timeout * 1000, 1),
    }


def _result(proc: subprocess.CompletedProcess[str], elapsed_ms: float) -> dict[str, Any]:
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "elapsed_ms": round(elapsed_ms, 1),
    }


class Sandbox(Protocol):
    name: str

    def available(self) -> bool: ...

    def run(
        self, tool_path: str | Path, args: dict[str, Any] | None = None,
        timeout: float = 10.0,
    ) -> dict[str, Any]: ...


class SubprocessSandbox:
    """``python -I`` in a fresh temp cwd with a minimal environment."""

    name = "subprocess"

    def available(self) -> bool:
        return True

    def run(
        self, tool_path: str | Path, args: dict[str, Any] | None = None,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        tool_path = Path(tool_path).resolve()
        command = [
            sys.executable, "-I", str(tool_path),
            "--call", json.dumps(args or {}, ensure_ascii=False),
        ]
        with tempfile.TemporaryDirectory(prefix="forgegate-run-") as workdir:
            started = time.perf_counter()
            try:
                proc = subprocess.run(
                    command, cwd=workdir, env=_base_env(), capture_output=True,
                    text=True, encoding="utf-8", errors="replace", timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                return _timeout(timeout)
            return _result(proc, (time.perf_counter() - started) * 1000)


class DockerSandbox:
    """Read-only, no-network container with CPU/memory/PID limits."""

    name = "docker"

    def __init__(self, image: str | None = None) -> None:
        self.image = image or os.environ.get(
            "FORGEGATE_DOCKER_IMAGE", "python:3.12-slim"
        )

    def available(self) -> bool:
        return shutil.which("docker") is not None

    def run(
        self, tool_path: str | Path, args: dict[str, Any] | None = None,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        tool_path = Path(tool_path).resolve()
        mount = f"type=bind,source={tool_path},target=/tool.py,readonly"
        command = [
            "docker", "run", "--rm",
            "--network", "none",
            "--read-only",
            "--tmpfs", "/tmp:rw,size=16m",
            "--memory", "256m",
            "--pids-limit", "64",
            "--mount", mount,
            "--env", "PYTHONDONTWRITEBYTECODE=1",
            "--env", "PYTHONIOENCODING=utf-8",
            self.image,
            "python", "-I", "/tool.py",
            "--call", json.dumps(args or {}, ensure_ascii=False),
        ]
        started = time.perf_counter()
        try:
            proc = subprocess.run(
                command, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return _timeout(timeout)
        return _result(proc, (time.perf_counter() - started) * 1000)


def select_sandbox() -> Sandbox:
    choice = os.environ.get("FORGEGATE_SANDBOX", "subprocess").strip().lower()
    if choice in {"", "subprocess"}:
        return SubprocessSandbox()
    if choice == "docker":
        docker = DockerSandbox()
        if not docker.available():
            raise RuntimeError(
                "FORGEGATE_SANDBOX=docker was requested but the docker CLI is "
                "not available; refusing to fall back to the weaker subprocess"
            )
        return docker
    raise ValueError(f"unknown FORGEGATE_SANDBOX value: {choice!r}")


def run_tool(
    tool_path: str | Path,
    args: dict[str, Any] | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    return select_sandbox().run(tool_path, args, timeout=timeout)
