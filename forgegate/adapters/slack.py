"""Slack ingress via Bolt.

Optional dependency:
    pip install "forgegate[slack]"

The app uses Socket Mode, so no public URL is needed during the hackathon.
A Slack message becomes a task; the agent's result is posted back in-thread.
Free-form messages require a live model endpoint, because there is no fixture
to replay.
"""

from __future__ import annotations

import os
import re
from typing import Any

from ..agent import Agent


def slugify(text: str, fallback: str = "slack-task") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    slug = slug[:64].strip("-")
    return slug if len(slug) >= 2 else fallback


def task_from_slack(text: str, ts: str = "0", user: str = "") -> dict[str, Any]:
    description = " ".join(text.split())[:4000]
    return {
        "id": f"slack-{ts}"[:80],
        "description": description,
        "tool_name": slugify(description),
        "args": {},
        "tests": [],
        "expected_contains": [],
        "tags": ["slack"],
        "requester": user,
    }


def format_result(result: dict[str, Any]) -> str:
    icon = "✅" if result.get("ok") else "⚠️"
    lines = [
        f"{icon} *{result.get('mode', '?')}* via `{result.get('tool') or 'none'}` "
        f"({result.get('latency_ms', 0)} ms)",
    ]
    if result.get("output"):
        lines.append(f"```{result['output'][:2500]}```")
    if result.get("error"):
        lines.append(f"_{result['error'][:800]}_")
    judge = result.get("judge")
    if judge:
        lines.append(
            f"judge: {'pass' if judge.get('ok') else 'fail'} "
            f"({judge.get('score', 0):.2f}) — {judge.get('reason', '')[:300]}"
        )
    return "\n".join(lines)


def build_app(agent: Agent, *, bot_token: str | None = None):
    """Return a Bolt app with one message handler."""
    try:
        from slack_bolt import App
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            'Slack support needs: pip install "forgegate[slack]"'
        ) from exc

    token = bot_token or os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        raise RuntimeError("SLACK_BOT_TOKEN is not set")
    app = App(token=token)

    @app.message("")
    def handle_message(message: dict[str, Any], say) -> None:
        text = str(message.get("text", "")).strip()
        if not text:
            return
        task = task_from_slack(
            text,
            ts=str(message.get("ts", "0")),
            user=str(message.get("user", "")),
        )
        try:
            result = agent.handle(task)
        except Exception as exc:  # never leave a Slack thread hanging
            say(f"⚠️ agent error: `{type(exc).__name__}: {exc}`")
            return
        say(format_result(result))

    return app


def run_socket_mode(
    agent: Agent,
    *,
    bot_token: str | None = None,
    app_token: str | None = None,
) -> None:  # pragma: no cover - requires Slack credentials
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    app = build_app(agent, bot_token=bot_token)
    token = app_token or os.environ.get("SLACK_APP_TOKEN", "")
    if not token:
        raise RuntimeError("SLACK_APP_TOKEN is not set")
    SocketModeHandler(app, token).start()
