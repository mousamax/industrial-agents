"""Our own evaluators. Built-ins (LLMJudge, ToolCorrectness, MaxDuration, ...) come from pydantic_evals.

Add a class here and to CUSTOM_EVALUATORS, and it becomes usable by name in any evals.yaml.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from pydantic_evals.evaluators import EvaluationReason, Evaluator, EvaluatorContext

from app.jsonable import to_jsonable


def _diff(expected: Any, actual: Any, path: str, rel_tol: float) -> Iterator[str]:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            yield f"{path or 'output'}: expected an object, got {actual!r}"
            return
        for key, value in expected.items():
            yield from _diff(value, actual.get(key), f"{path}.{key}" if path else key, rel_tol)
    elif isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            got = len(actual) if isinstance(actual, list) else actual
            yield f"{path}: expected {len(expected)} items, got {got}"
            return
        for i, (e, a) in enumerate(zip(expected, actual, strict=True)):
            yield from _diff(e, a, f"{path}[{i}]", rel_tol)
    elif isinstance(expected, bool) or expected is None:
        if expected != actual:
            yield f"{path}: expected {expected!r}, got {actual!r}"
    elif isinstance(expected, int | float):
        if not isinstance(actual, int | float) or abs(actual - expected) > rel_tol * max(abs(expected), 1e-9):
            yield f"{path}: expected {expected!r}, got {actual!r}"
    elif str(expected).strip().casefold() != str(actual).strip().casefold():
        yield f"{path}: expected {expected!r}, got {actual!r}"


@dataclass
class FieldsMatch(Evaluator[Any, Any, Any]):
    """Every field present in `expected_output` must match the output; fields you leave out are ignored.

    So expected outputs can be partial: pin down what matters (the decision, the totals), skip the prose.
    Numbers match within `rel_tol`, strings ignore case and surrounding whitespace, lists match by position.
    """

    rel_tol: float = 0.01

    def evaluate(self, ctx: EvaluatorContext[Any, Any, Any]) -> EvaluationReason:
        if ctx.expected_output is None:
            return EvaluationReason(value=True, reason="no expected_output")
        problems = list(_diff(to_jsonable(ctx.expected_output), to_jsonable(ctx.output), "", self.rel_tol))
        return EvaluationReason(value=not problems, reason="; ".join(problems)[:1000] or "all fields match")


@dataclass
class SyneraScore(Evaluator[Any, Any, Any]):
    """The Synera judges' scorer (synera-kit/benchmark/score.py) on the reply's last `Answer:` line.

    `expected_output` is the `answer` object of a kit question, copied as-is from questions.jsonl.
    """

    def evaluate(self, ctx: EvaluatorContext[Any, Any, Any]) -> EvaluationReason:
        from benchmark.score import committed_answer, score_reply

        if ctx.expected_output is None:
            return EvaluationReason(value=True, reason="no expected answer (interaction case)")
        reply = str(ctx.output)
        committed = committed_answer(reply)
        if committed is None:
            return EvaluationReason(value=False, reason="no 'Answer:' line")
        expected = ctx.expected_output
        target = expected.get("value", expected.get("accept"))
        return EvaluationReason(
            value=score_reply(expected, reply), reason=f"got {committed!r}, expected {target!r}"
        )


CUSTOM_EVALUATORS: list[type[Evaluator[Any, Any, Any]]] = [FieldsMatch, SyneraScore]
