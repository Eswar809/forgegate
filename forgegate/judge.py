"""Independent semantic verification of an agent's final answer.

Deterministic gate checks prove a tool runs and matches known cases. They do
not prove the answer actually addresses the task. This layer is an LLM judge
in the style of promptfoo's ``llm-rubric`` and DeepEval's G-Eval, with one
difference: it fails closed. If the judge is unreachable or returns an
unusable reply, the verdict is a failure, never a silent pass.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .llm import LLMClient, LLMError, parse_json

JUDGE_SYSTEM_PROMPT = """You are an independent verifier for an operations agent.
You do not trust the agent's claim. You check whether the OUTPUT answers the TASK
using only evidence that appears in the output itself.

Return JSON only, with exactly these keys:
{
  "pass": true or false,
  "score": a number from 0.0 to 1.0,
  "reason": "one or two sentences citing what is present or missing",
  "evidence": ["exact short substrings copied from the output"]
}

Rules:
- pass=false if any REQUIRED EVIDENCE item is missing from the output.
- pass=false if the output asserts a cause or result that its own evidence does not support.
- pass=false if the output is empty, a refusal, or only restates the task.
- score >= 0.7 only when the answer is correct, evidence-backed and complete.
- Never invent evidence; copy it verbatim from the output.
"""


@dataclass
class Verdict:
    ok: bool
    score: float = 0.0
    reason: str = ""
    evidence: list[str] = field(default_factory=list)
    raw: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clamp(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


class SemanticJudge:
    """LLM-as-judge with a fail-closed contract."""

    def __init__(self, client: LLMClient | None = None) -> None:
        self.client = client or LLMClient()

    def available(self) -> bool:
        return self.client.available()

    def judge(
        self,
        task_description: str,
        output: str,
        *,
        expected: list[str] | None = None,
        rubric: str = "",
    ) -> Verdict:
        if not self.available():
            return Verdict(False, 0.0, "judge unavailable: no model endpoint configured")
        expected = expected or []
        user = (
            f"TASK:\n{task_description}\n\n"
            f"REQUIRED EVIDENCE:\n"
            + ("\n".join(f"- {item}" for item in expected) if expected else "- (none stated)")
            + f"\n\nEXTRA RUBRIC:\n{rubric or '(none)'}\n\n"
            f"OUTPUT:\n{output[:12000]}\n"
        )
        try:
            raw = self.client.complete(
                JUDGE_SYSTEM_PROMPT, user, temperature=0.0, max_tokens=700
            )
            data = parse_json(raw)
        except LLMError as exc:
            return Verdict(False, 0.0, f"judge failed closed: {exc}")

        passed = data.get("pass")
        if not isinstance(passed, bool):
            return Verdict(
                False, 0.0,
                "judge failed closed: reply has no boolean 'pass'",
                raw=raw,
            )
        evidence = data.get("evidence", [])
        if not isinstance(evidence, list):
            evidence = [str(evidence)]
        return Verdict(
            ok=passed,
            score=_clamp(data.get("score", 1.0 if passed else 0.0)),
            reason=str(data.get("reason", ""))[:1000],
            evidence=[str(item)[:300] for item in evidence[:10]],
            raw=raw,
        )
