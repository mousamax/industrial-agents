"""Qualitative error analysis with LLM judges: open coding, then closed coding.

Assertions tell you *that* a case failed; coding tells you *why*, across many runs:

1. Open coding (`ia analyze <name>`): a judge reads every case of an eval run (input, output,
   expected output, failed checks) and writes short, specific notes on what went wrong. No categories
   up front, so you discover failure modes you didn't think to test for.
2. Axial step (same command): the notes are grouped into a small codebook of failure modes, written to
   `failure_modes.yaml` next to the agent. A human edits it: merge, rename, sharpen definitions.
3. Closed coding (automatic in `ia eval` once `failure_modes.yaml` exists): the `ClosedCoding`
   evaluator labels every output against that fixed codebook, so the report shows one
   `no_<mode>` assertion per failure mode and you can see which ones a change fixed.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.capabilities import Thinking
from pydantic_evals.evaluators import EvaluationReason, Evaluator, EvaluatorContext
from pydantic_evals.reporting import EvaluationReport

from app.core import load_prompt
from app.jsonable import to_jsonable
from app.models import model


class FailureMode(BaseModel):
    name: str = Field(description="snake_case, e.g. misses_currency_flag")
    definition: str = Field(description="One sentence a second judge can apply consistently")
    examples: list[str] = Field(default_factory=list, description="Case names where it occurred")


class Codebook(BaseModel):
    task: str = Field(description="What the agent under test is supposed to do")
    failure_modes: list[FailureMode]


class OpenCode(BaseModel):
    case: str
    ok: bool = Field(description="True if the output is fully acceptable")
    notes: list[str] = Field(description="Specific observed problems, one per item. Empty if ok.")


class Labels(BaseModel):
    present: dict[str, bool] = Field(description="failure mode name -> does this output exhibit it")
    reasons: dict[str, str] = Field(default_factory=dict, description="Short evidence for each True")


_open_coder = Agent(
    model("judge"),
    name="open_coder",
    output_type=OpenCode,
    instructions=load_prompt(__file__, "prompts/open_coder.md"),
    capabilities=[Thinking("medium")],
    defer_model_check=True,
)

_axial_coder = Agent(
    model("smart"),
    name="axial_coder",
    output_type=Codebook,
    instructions=load_prompt(__file__, "prompts/axial_coder.md"),
    capabilities=[Thinking("high")],
    defer_model_check=True,
)

_closed_coder = Agent(
    model("judge"),
    name="closed_coder",
    output_type=Labels,
    instructions=load_prompt(__file__, "prompts/closed_coder.md"),
    capabilities=[Thinking("low")],
    defer_model_check=True,
)


def _render(**sections: Any) -> str:
    """Prompt sections as <name>JSON</name>: data stays JSON, so the judge sees exactly what the agent returned."""
    return "\n".join(
        f"<{name}>\n{value if isinstance(value, str) else json.dumps(to_jsonable(value), indent=2, ensure_ascii=False)}\n</{name}>"
        for name, value in sections.items()
    )


def _case_view(case: Any) -> dict[str, Any]:
    return {
        "name": case.name,
        "inputs": case.inputs,
        "output": case.output,
        "expected_output": case.expected_output,
        "checks": {k: {"pass": bool(a.value), "reason": a.reason} for k, a in case.assertions.items()},
    }


async def open_code(
    report: EvaluationReport[Any, Any, Any], task: str, concurrency: int = 8
) -> list[OpenCode]:
    sem = asyncio.Semaphore(concurrency)

    async def one(case: Any) -> OpenCode:
        async with sem:
            prompt = _render(task=task, case=_case_view(case))
            return (await _open_coder.run(prompt, model=model("judge"))).output

    codes = list(await asyncio.gather(*(one(c) for c in report.cases)))
    codes += [OpenCode(case=f.name, ok=False, notes=[f"crashed: {f.error_message}"]) for f in report.failures]
    return codes


async def axial_code(codes: list[OpenCode], task: str, existing: Codebook | None = None) -> Codebook:
    notes = [{"case": c.case, "notes": c.notes} for c in codes if c.notes]
    payload: dict[str, Any] = {"task": task, "notes": notes}
    if existing:
        payload["existing_codebook"] = existing.model_dump()
    return (await _axial_coder.run(_render(**payload), model=model("smart"))).output


def load_codebook(path: Path) -> Codebook:
    return Codebook.model_validate(yaml.safe_load(path.read_text()))


@cache
def _cached_codebook(path: str) -> Codebook:
    return load_codebook(Path(path))


def save_codebook(codebook: Codebook, path: Path) -> None:
    path.write_text(yaml.safe_dump(codebook.model_dump(), sort_keys=False, allow_unicode=True, width=110))


@dataclass
class ClosedCoding(Evaluator[Any, Any, Any]):
    """One assertion per failure mode in the codebook: `no_<mode>` passes when the output doesn't show it.

    Added automatically by the eval runner when the agent folder has `failure_modes.yaml`.
    """

    codebook: str
    """Path to the codebook YAML."""

    async def evaluate(self, ctx: EvaluatorContext[Any, Any, Any]) -> dict[str, EvaluationReason]:
        book = _cached_codebook(self.codebook)
        prompt = _render(codebook=book.model_dump(), inputs=ctx.inputs, output=ctx.output)
        labels = (await _closed_coder.run(prompt, model=model("judge"))).output
        return {
            f"no_{m.name}": EvaluationReason(
                value=not labels.present.get(m.name, False), reason=labels.reasons.get(m.name)
            )
            for m in book.failure_modes
        }
