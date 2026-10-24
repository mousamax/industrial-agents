"""Make the reworded suite: scored dev questions rewritten the way real people (and Sunday) might ask them.

Every place name, number and the closing "Finish with a line 'Answer: ...'" sentence stay verbatim, so the
golden answer still holds; only the wording changes (casual, terse, spoken, a typo, a different order).
Writes evals/datasets/reworded_<split>.jsonl (committed: same file for everyone, so scores compare).

    uv run python scripts/synera/make_reworded.py dev --per-kind 6
"""

from __future__ import annotations

import asyncio
import json
import random
import re
from typing import Annotated

import typer
from pydantic import BaseModel
from pydantic_ai import Agent, ModelRetry, RunContext

from app.agents.building_guide.bench import DATASETS, load_questions
from app.models import model

STYLES = [
    "casual and chatty, like a colleague on a phone call",
    "terse, few words, like a busy technician",
    "spoken aloud by voice: no capitals, no punctuation except the last sentence, filler words",
    "polite and formal, slightly long-winded",
    "the facts in a different order, with one small typo in a non-name word",
    "a facility manager's ticket: context first, then the question",
]
INSTRUCTION = re.compile(r"Finish with a line '[^']*'\.?\s*$")
KEEP = re.compile(r"[^\s(),.?]*\d[^\s(),?]*|\(([^()]+)\)")  # room refs, numbers, "(Floor name)"


class Rewrite(BaseModel):
    question: str


rewriter = Agent(
    model("balanced"),
    output_type=Rewrite,
    deps_type=dict,
    instructions=(
        "Rewrite a question about a building in the requested style. Keep every place name, room number, "
        "item, number, width and floor name exactly as written, keep every constraint and the meaning, and "
        "keep the final sentence starting 'Finish with a line' verbatim at the end. Change only the wording."
    ),
)


@rewriter.output_validator
def keeps_what_matters(ctx: RunContext[dict], out: Rewrite) -> Rewrite:
    original = ctx.deps["question"]
    instruction = INSTRUCTION.search(original)
    if instruction and not out.question.rstrip().endswith(instruction.group(0).strip()):
        raise ModelRetry(f"End with exactly: {instruction.group(0).strip()}")
    missing = [m.group(0) for m in KEEP.finditer(original) if m.group(0) not in out.question]
    if missing:
        raise ModelRetry(f"These must appear verbatim: {missing}")
    return out


async def rewrite(q: dict, style: str) -> dict:
    result = await rewriter.run(f"Style: {style}\n\nQuestion: {q['question']}", deps=q)
    return {
        "id": f"{q['id']}-R",
        "building_id": q["building_id"],
        "kind": q["kind"],
        "style": style,
        "original": q["question"],
        "question": result.output.question,
        "answer": q["answer"],
    }


def main(
    split: Annotated[str, typer.Argument()] = "dev",
    per_kind: Annotated[int, typer.Option(help="Questions per kind")] = 6,
    seed: Annotated[int, typer.Option()] = 11,
) -> None:
    questions = load_questions(split, per_kind, None, "benchmark", seed=seed)
    rng = random.Random(seed)

    async def run_all() -> list[dict]:
        gate = asyncio.Semaphore(8)

        async def one(q: dict) -> dict:
            async with gate:
                return await rewrite(q, rng.choice(STYLES))

        return await asyncio.gather(*(one(q) for q in questions))

    rows = asyncio.run(run_all())
    DATASETS.mkdir(parents=True, exist_ok=True)
    out = DATASETS / f"reworded_{split}.jsonl"
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    print(f"{len(rows)} questions -> {out}")
    for r in rows[:3]:
        print(f"\n[{r['style']}]\n  {r['original']}\n  {r['question']}")


if __name__ == "__main__":
    typer.run(main)
