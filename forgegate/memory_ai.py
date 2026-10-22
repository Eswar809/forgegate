"""LLM-assisted memory: fact extraction and ADD/UPDATE/DELETE decisions.

The prompts are written for operations work rather than personalization.
They follow the same shape as Mem0's extraction/update pipeline: extract
candidate facts, compare them with the closest active memories, then choose
an explicit event instead of blindly appending. Without an endpoint the
caller falls back to deterministic storage, so the demo stays offline.
"""

from __future__ import annotations

import json
from typing import Any

from .core import Memory
from .llm import LLMClient, LLMError, parse_json

EXTRACT_SYSTEM_PROMPT = """You extract durable operational facts from one completed task.
A durable fact helps a future task: service names, endpoints, schemas, ownership,
constraints, recurring failure modes, or confirmed procedures.

Do not store: greetings, task restatements, transient timestamps, secrets,
credentials, personal data, or guesses not supported by the observed output.

Return JSON only:
{"facts": [{"text": "...", "kind": "infrastructure|constraint|procedure|preference|episodic",
            "tags": ["..."], "confidence": 0.0}]}

Rules:
- At most 5 facts. Prefer fewer, higher-value facts.
- One sentence per fact, under 240 characters.
- Include an identifier (service, table, job id) when one exists.
- If nothing is durable, return {"facts": []}.
"""

DECIDE_SYSTEM_PROMPT = """You are a memory manager. Compare one NEW fact with CANDIDATES
retrieved from memory and choose exactly one event:

- ADD: the new fact is new information.
- UPDATE: it corrects, extends, or replaces one candidate. Return that candidate's id.
- DELETE: the new fact proves one candidate is no longer true. Return that id.
- NONE: a candidate already says the same thing.

Never invent ids. Never choose UPDATE or DELETE without a candidate id.
Return JSON only:
{"event": "ADD|UPDATE|DELETE|NONE", "id": "candidate id or null",
 "text": "final fact text", "reason": "short reason"}
"""


def _task_payload(task: dict[str, Any], output: str) -> str:
    return json.dumps(
        {
            "task": task.get("description", ""),
            "tool": task.get("tool_name", ""),
            "tags": task.get("tags", []),
            "args": task.get("args", {}),
            "observed_output": output[:6000],
        },
        indent=2,
    )


def extract_facts(
    client: LLMClient,
    task: dict[str, Any],
    output: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    raw = client.complete(
        EXTRACT_SYSTEM_PROMPT, _task_payload(task, output),
        temperature=0.1, max_tokens=800,
    )
    data = parse_json(raw)
    items = data.get("facts", [])
    if not isinstance(items, list):
        raise LLMError("extractor returned a non-list 'facts'")

    cleaned: list[dict[str, Any]] = []
    for item in items[:limit]:
        if isinstance(item, str):
            item = {"text": item}
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text", "")).split())
        if len(text) < 3:
            continue
        cleaned.append({
            "text": text[:400],
            "kind": str(item.get("kind", "fact"))[:40],
            "tags": [str(tag)[:40] for tag in item.get("tags", [])][:8]
                    if isinstance(item.get("tags", []), list) else [],
            "confidence": max(0.0, min(1.0, float(item.get("confidence", 0.8)))),
        })
    return cleaned


def decide(
    client: LLMClient,
    text: str,
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    user = json.dumps({
        "new_fact": text,
        "candidates": [
            {"id": fact["id"], "text": fact["text"]}
            for fact in candidates
        ],
    }, indent=2)
    data = parse_json(
        client.complete(DECIDE_SYSTEM_PROMPT, user, temperature=0.0, max_tokens=400)
    )
    event = str(data.get("event", "")).upper()
    if event not in {"ADD", "UPDATE", "DELETE", "NONE"}:
        raise LLMError(f"invalid memory event: {event!r}")
    return {
        "event": event,
        "id": data.get("id"),
        "text": str(data.get("text", text)),
        "reason": str(data.get("reason", ""))[:300],
    }


def remember_outcome(
    memory: Memory,
    task: dict[str, Any],
    output: str,
    *,
    client: LLMClient | None = None,
) -> list[dict[str, Any]]:
    """Store what a successful task taught the agent.

    With a model endpoint: extract facts, then let the model choose an event
    against the closest candidates. Without one: store one deterministic
    episodic fact. Either way memory writes stay outside the tool code.
    """
    source = f"task:{task.get('id', '?')}"
    tags = list(task.get("tags", []))
    extracted: list[dict[str, Any]] = []
    if client is not None and client.available():
        try:
            extracted = extract_facts(client, task, output)
        except (LLMError, ValueError, TypeError):
            extracted = []

    if not extracted:
        summary = " ".join((output or "").split())[:240] or "(no output)"
        text = (
            f"Task '{task.get('description', '')}' succeeded with "
            f"{task.get('tool_name', 'a tool')}; result: {summary}"
        )
        return [memory.remember_fact(
            text, source=source, tags=tags, kind="episodic",
        )]

    results: list[dict[str, Any]] = []
    for item in extracted:
        text = item["text"]
        candidates = memory.search_facts(text, k=3)
        choice: dict[str, Any] | None = None
        if candidates and client is not None:
            try:
                choice = decide(client, text, candidates)
            except (LLMError, ValueError, TypeError):
                choice = None
        if choice and choice["event"] in {"UPDATE", "DELETE"}:
            candidate_ids = {fact["id"] for fact in candidates}
            if choice.get("id") in candidate_ids:
                memory.apply_operations([{
                    "event": choice["event"],
                    "id": choice["id"],
                    "text": choice.get("text", text),
                    "source": source,
                    "reason": choice.get("reason", ""),
                }])
                results.append(choice)
                continue
        if choice and choice["event"] == "NONE":
            results.append(choice)
            continue
        results.append(memory.remember_fact(
            text, source=source, tags=item.get("tags", tags),
            kind=item.get("kind", "fact"),
            confidence=item.get("confidence", 0.8),
        ))
    return results
