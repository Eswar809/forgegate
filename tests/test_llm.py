"""Tests for the OpenAI / Anthropic transport switch and config fallbacks."""

from __future__ import annotations

import io
import json
import os
import unittest
import urllib.error
from unittest import mock

from forgegate.llm import LLMClient, LLMConfig, LLMError


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self.payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class ConfigTests(unittest.TestCase):
    def test_anthropic_base_url_is_a_fallback_when_forgegate_base_is_empty(self):
        env = {
            "ANTHROPIC_BASE_URL": "http://127.0.0.1:8084/",
            "ANTHROPIC_AUTH_TOKEN": "dummy-token",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            config = LLMConfig.from_env()
        self.assertEqual(config.style, "anthropic")
        self.assertEqual(config.base, "http://127.0.0.1:8084")
        self.assertEqual(config.key, "dummy-token")
        self.assertEqual(config.model, "claude-sonnet-5-5")
        self.assertTrue(config.configured)

    def test_forgegate_model_overrides_the_anthropic_default(self):
        env = {
            "ANTHROPIC_BASE_URL": "http://127.0.0.1:8084",
            "ANTHROPIC_AUTH_TOKEN": "dummy-token",
            "FORGEGATE_MODEL": "deepseek-v4.1-flash-free",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            config = LLMConfig.from_env()
        self.assertEqual(config.model, "deepseek-v4.1-flash-free")

    def test_forgegate_base_keeps_openai_style_by_default(self):
        env = {
            "FORGEGATE_API_BASE": "https://api.example.com/v1",
            "FORGEGATE_API_KEY": "key",
            "FORGEGATE_MODEL": "model",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            config = LLMConfig.from_env()
        self.assertEqual(config.style, "openai")
        self.assertEqual(config.base, "https://api.example.com/v1")

    def test_style_can_be_forced_for_a_forgegate_base(self):
        env = {
            "FORGEGATE_API_BASE": "http://127.0.0.1:8084",
            "FORGEGATE_API_KEY": "dummy-token",
            "FORGEGATE_MODEL": "claude-haiku-5-5",
            "FORGEGATE_API_STYLE": "anthropic",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            config = LLMConfig.from_env()
        self.assertEqual(config.style, "anthropic")


class TimeoutTests(unittest.TestCase):
    def test_timeout_defaults_to_two_minutes(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(LLMClient().timeout, 120.0)

    def test_timeout_can_be_raised_for_a_slow_endpoint(self):
        env = {"FORGEGATE_HTTP_TIMEOUT": "300"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(LLMClient().timeout, 300.0)

    def test_an_unparseable_timeout_falls_back_to_the_default(self):
        for raw in ("soon", "0", "-5"):
            with mock.patch.dict(
                os.environ, {"FORGEGATE_HTTP_TIMEOUT": raw}, clear=True,
            ):
                self.assertEqual(LLMClient().timeout, 120.0)


class CompletionTests(unittest.TestCase):
    def _client(self, base: str = "http://127.0.0.1:8084") -> LLMClient:
        return LLMClient(LLMConfig(
            base=base, key="dummy-token",
            model="claude-haiku-5-5", style="anthropic",
        ))

    def test_anthropic_completion_posts_to_messages_and_joins_text_blocks(self):
        captured: dict = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["headers"] = {
                key.lower(): value for key, value in request.header_items()
            }
            captured["body"] = json.loads(request.data)
            return FakeResponse({
                "content": [
                    {"type": "text", "text": "Hello "},
                    {"type": "tool_use", "id": "x"},
                    {"type": "text", "text": "world"},
                ]
            })

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            reply = self._client().complete("be terse", "say hi")

        self.assertEqual(reply, "Hello world")
        self.assertEqual(captured["url"], "http://127.0.0.1:8084/v1/messages")
        self.assertEqual(captured["headers"]["x-api-key"], "dummy-token")
        self.assertEqual(captured["headers"]["anthropic-version"], "2023-06-01")
        self.assertEqual(captured["body"]["system"], "be terse")
        self.assertEqual(
            captured["body"]["messages"],
            [{"role": "user", "content": "say hi"}],
        )

    def test_base_already_ending_in_v1_is_not_doubled(self):
        captured: dict = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            return FakeResponse({"content": [{"type": "text", "text": "ok"}]})

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            self._client("http://127.0.0.1:8084/v1").complete("s", "u")
        self.assertEqual(captured["url"], "http://127.0.0.1:8084/v1/messages")

    def test_empty_anthropic_content_fails_closed(self):
        with mock.patch(
            "urllib.request.urlopen",
            return_value=FakeResponse({"content": []}),
        ):
            with self.assertRaises(LLMError):
                self._client().complete("s", "u")

    def test_openai_completion_still_uses_chat_completions(self):
        captured: dict = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            return FakeResponse({
                "choices": [{"message": {"content": "openai ok"}}],
            })

        client = LLMClient(LLMConfig(
            base="https://api.example.com/v1", key="key", model="model",
        ))
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            reply = client.complete("s", "u")
        self.assertEqual(reply, "openai ok")
        self.assertEqual(
            captured["url"], "https://api.example.com/v1/chat/completions"
        )


class RetryTests(unittest.TestCase):
    def _client(self, **kwargs) -> LLMClient:
        return LLMClient(
            LLMConfig(
                base="http://127.0.0.1:8084", key="dummy-token",
                model="claude-haiku-5-5", style="anthropic",
            ),
            **kwargs,
        )

    @staticmethod
    def _http_error(request, code: int) -> urllib.error.HTTPError:
        return urllib.error.HTTPError(
            request.full_url, code, "boom", {}, io.BytesIO(b'{"error":"boom"}'),
        )

    def test_a_5xx_is_retried_once_by_default(self):
        calls: list[int] = []

        def fake_urlopen(request, timeout):
            calls.append(1)
            if len(calls) == 1:
                raise self._http_error(request, 502)
            return FakeResponse({"content": [{"type": "text", "text": "ok"}]})

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen), \
                mock.patch("forgegate.llm.time.sleep"):
            self.assertEqual(self._client().complete("s", "u"), "ok")
        self.assertEqual(len(calls), 2)

    def test_a_4xx_is_not_retried(self):
        calls: list[int] = []

        def fake_urlopen(request, timeout):
            calls.append(1)
            raise self._http_error(request, 400)

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen), \
                mock.patch("forgegate.llm.time.sleep"):
            with self.assertRaises(LLMError):
                self._client().complete("s", "u")
        self.assertEqual(len(calls), 1)

    def test_retries_can_be_switched_off(self):
        calls: list[int] = []

        def fake_urlopen(request, timeout):
            calls.append(1)
            raise self._http_error(request, 503)

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen), \
                mock.patch("forgegate.llm.time.sleep"):
            with self.assertRaises(LLMError):
                self._client(retries=0).complete("s", "u")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
