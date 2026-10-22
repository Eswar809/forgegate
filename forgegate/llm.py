"""Minimal chat client with OpenAI- and Anthropic-compatible transports.

One client is shared by the tool generator, the semantic judge and the
LLM-assisted memory pipeline.

OpenAI style (default) posts to ``{base}/chat/completions``. Set
``FORGEGATE_API_STYLE=anthropic`` (or only ``ANTHROPIC_BASE_URL``) to post to
``{base}/v1/messages`` instead, which is what Claude Code-compatible local
proxies and the Anthropic API expose.

Environment:
    FORGEGATE_API_BASE   e.g. https://api.example.com/v1
    FORGEGATE_API_KEY    bearer token / API key
    FORGEGATE_MODEL      model id
    FORGEGATE_API_STYLE  "openai" (default) or "anthropic"
    FORGEGATE_HTTP_TIMEOUT  seconds to wait for one completion (default 120)
    FORGEGATE_HTTP_RETRIES  extra attempts on 5xx/timeout (default 1)

Anthropic fallback (used when FORGEGATE_API_BASE is empty):
    ANTHROPIC_BASE_URL    e.g. http://127.0.0.1:8084
    ANTHROPIC_AUTH_TOKEN  or ANTHROPIC_API_KEY
    FORGEGATE_MODEL / ANTHROPIC_MODEL (defaults to claude-sonnet-5-5)
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


class LLMError(RuntimeError):
    """Raised when the endpoint is unreachable or returns an unusable reply."""


@dataclass(frozen=True)
class LLMConfig:
    base: str = ""
    key: str = ""
    model: str = ""
    style: str = "openai"

    @classmethod
    def from_env(cls) -> "LLMConfig":
        base = os.environ.get("FORGEGATE_API_BASE", "").rstrip("/")
        if base:
            return cls(
                base=base,
                key=os.environ.get("FORGEGATE_API_KEY", ""),
                model=os.environ.get("FORGEGATE_MODEL", ""),
                style=os.environ.get("FORGEGATE_API_STYLE", "openai").lower(),
            )
        base = os.environ.get("ANTHROPIC_BASE_URL", "").rstrip("/")
        if base:
            return cls(
                base=base,
                key=(
                    os.environ.get("ANTHROPIC_AUTH_TOKEN")
                    or os.environ.get("ANTHROPIC_API_KEY", "")
                ),
                model=(
                    os.environ.get("FORGEGATE_MODEL")
                    or os.environ.get("ANTHROPIC_MODEL")
                    or "claude-sonnet-5-5"
                ),
                style="anthropic",
            )
        return cls()

    @property
    def configured(self) -> bool:
        return bool(self.base and self.key and self.model)


_FENCE = re.compile(r"```(?:python|json)?\s*(.*?)```", re.DOTALL)


def strip_code_fences(text: str) -> str:
    """Return the content of the first fenced block, or the stripped text."""
    text = (text or "").strip()
    match = _FENCE.search(text)
    return match.group(1).strip() if match else text


def parse_json(text: str) -> dict:
    """Parse a model reply that should be JSON, tolerating fences and prose."""
    cleaned = strip_code_fences(text)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise LLMError(f"reply is not JSON: {cleaned[:200]!r}")
        try:
            value = json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError as exc:
            raise LLMError(f"reply is not JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise LLMError(f"expected a JSON object, got {type(value).__name__}")
    return value


def _env_timeout(default: float = 120.0) -> float:
    """Read FORGEGATE_HTTP_TIMEOUT, falling back to the default when unusable."""
    raw = os.environ.get("FORGEGATE_HTTP_TIMEOUT", "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _env_retries(default: int = 1) -> int:
    """Read FORGEGATE_HTTP_RETRIES: extra attempts after a 5xx or timeout."""
    raw = os.environ.get("FORGEGATE_HTTP_RETRIES", "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


class LLMClient:
    """Chat-completions client with a hard timeout and no third-party deps."""

    def __init__(
        self,
        config: LLMConfig | None = None,
        *,
        timeout: float | None = None,
        retries: int | None = None,
    ) -> None:
        self.config = config or LLMConfig.from_env()
        if timeout is None:
            timeout = _env_timeout()
        self.timeout = timeout
        self.retries = _env_retries() if retries is None else max(0, retries)

    def available(self) -> bool:
        return self.config.configured

    def _post(self, request: urllib.request.Request) -> dict:
        attempts = self.retries + 1
        for attempt in range(1, attempts + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:500]
                error = LLMError(f"HTTP {exc.code}: {detail}")
                # 5xx and 429 are the provider saying "try again", not a bug here.
                retryable = exc.code >= 500 or exc.code == 429
            except (urllib.error.URLError, TimeoutError) as exc:
                error = LLMError(f"endpoint unreachable: {exc}")
                retryable = True
            except json.JSONDecodeError as exc:
                raise LLMError(f"endpoint returned invalid JSON: {exc}") from exc
            if not retryable or attempt == attempts:
                raise error
            time.sleep(min(2 ** attempt, 10))
        raise AssertionError("unreachable")

    def _complete_openai(
        self, system: str, user: str, *, temperature: float, max_tokens: int,
    ) -> str:
        payload = json.dumps({
            "model": self.config.model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }).encode("utf-8")
        request = urllib.request.Request(
            f"{self.config.base}/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.key}",
            },
        )
        data = self._post(request)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected response shape: {str(data)[:300]}") from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMError("model returned empty content")
        return content

    def _complete_anthropic(
        self, system: str, user: str, *, temperature: float, max_tokens: int,
    ) -> str:
        base = self.config.base
        url = f"{base}/messages" if base.endswith("/v1") else f"{base}/v1/messages"
        payload = json.dumps({
            "model": self.config.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
                "x-api-key": self.config.key,
                # Claude Code-compatible proxies authenticate with a bearer
                # token; the real Anthropic API uses x-api-key. Sending both
                # keeps either backend happy.
                "Authorization": f"Bearer {self.config.key}",
            },
        )
        data = self._post(request)
        blocks = data.get("content")
        if not isinstance(blocks, list):
            raise LLMError(f"unexpected response shape: {str(data)[:300]}")
        content = "".join(
            block.get("text", "")
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        ).strip()
        if not content:
            raise LLMError("model returned empty content")
        return content

    def complete(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> str:
        if not self.available():
            raise LLMError("no model endpoint configured")
        if self.config.style == "anthropic":
            return self._complete_anthropic(
                system, user, temperature=temperature, max_tokens=max_tokens,
            )
        return self._complete_openai(
            system, user, temperature=temperature, max_tokens=max_tokens,
        )
