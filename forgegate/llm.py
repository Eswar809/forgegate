"""Minimal OpenAI-compatible chat client (standard library only).

One client is shared by the tool generator, the semantic judge and the
LLM-assisted memory pipeline. Any endpoint that speaks
``POST {base}/chat/completions`` works: Meta Model API, vLLM, Baseten,
Modal, Fireworks, OpenAI, Anthropic-compatible gateways.

Environment:
    FORGEGATE_API_BASE   e.g. https://api.example.com/v1
    FORGEGATE_API_KEY    bearer token
    FORGEGATE_MODEL      model id
"""

from __future__ import annotations

import json
import os
import re
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

    @classmethod
    def from_env(cls) -> "LLMConfig":
        return cls(
            base=os.environ.get("FORGEGATE_API_BASE", "").rstrip("/"),
            key=os.environ.get("FORGEGATE_API_KEY", ""),
            model=os.environ.get("FORGEGATE_MODEL", ""),
        )

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


class LLMClient:
    """Chat-completions client with a hard timeout and no third-party deps."""

    def __init__(self, config: LLMConfig | None = None, *, timeout: float = 120.0) -> None:
        self.config = config or LLMConfig.from_env()
        self.timeout = timeout

    def available(self) -> bool:
        return self.config.configured

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
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise LLMError(f"HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise LLMError(f"endpoint unreachable: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise LLMError(f"endpoint returned invalid JSON: {exc}") from exc

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected response shape: {str(data)[:300]}") from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMError("model returned empty content")
        return content
