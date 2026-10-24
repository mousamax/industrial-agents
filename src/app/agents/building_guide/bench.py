"""Benchmark building_guide on the kit's questions with the judges' scorer (benchmark.score), in parallel.

    uv run python -m app.agents.building_guide.bench dev --per-kind 3
    uv run python -m app.agents.building_guide.bench dev --model balanced --thinking medium --label sonnet-med
    uv run python -m app.agents.building_guide.bench test --submission submission/team_answers.csv

Each run writes evals/reports/bench-<label>.jsonl (reply, tool calls, tokens per question) and prints the
score per kind. The official sequential command in BENCHMARK.md gives the same score; this one is faster.
"""

from __future__ import annotations

import asyncio
import csv
import json
import random
import time
from collections import defaultdict
from pathlib import Path
from typing import Annotated

import typer
from benchmark.score import score_reply
from pydantic_ai.messages import ModelResponse, ToolCallPart

from app import observability
from app.models import model as resolve_model
from app.settings import ROOT, get_settings

from .agent import make_agent
from .tools import Deps

REPORTS = ROOT / "evals" / "reports"


def load_questions(split: str, per_kind: int | None, kinds: list[str] | None, seed: int = 7) -> list[dict]:
    by_kind: dict[str, list[dict]] = defaultdict(list)
    for path in sorted((get_settings().synera_kit_dir / "data" / split).glob("*/questions.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                q = json.loads(line) | {"building_dir": str(path.parent)}
                if not kinds or q["kind"] in kinds:
                    by_kind[q["kind"]].append(q)
    if per_kind:
        rng = random.Random(seed)
        by_kind = {k: rng.sample(v, min(per_kind, len(v))) for k, v in by_kind.items()}
    return [q for k in sorted(by_kind) for q in by_kind[k]]


async def answer_one(agent, q: dict) -> dict:
    started = time.perf_counter()
    try:
        result = await agent.run(q["question"], deps=Deps.load(q["building_dir"]))
        reply, messages, usage = result.output, result.all_messages(), result.usage
        error = None
    except Exception as e:  # a crash is a wrong answer, not a stopped run
        reply, messages, usage, error = "", [], None, f"{type(e).__name__}: {e}"
    calls = [
        {"tool": p.tool_name, "args": p.args_as_dict()}
        for m in messages
        if isinstance(m, ModelResponse)
        for p in m.parts
        if isinstance(p, ToolCallPart)
    ]
    return {
        "id": q["id"],
        "kind": q["kind"],
        "ok": bool(q.get("answer")) and score_reply(q["answer"], reply),
        "reply": reply,
        "error": error,
        "tool_calls": calls,
        "input_tokens": usage.input_tokens if usage else 0,
        "output_tokens": usage.output_tokens if usage else 0,
        "seconds": round(time.perf_counter() - started, 1),
    }


async def run(questions: list[dict], model_name: str | None, thinking: str, concurrency: int) -> list[dict]:
    agent = make_agent(model_name, thinking)
    gate = asyncio.Semaphore(concurrency)

    async def one(q: dict) -> dict:
        async with gate:
            with observability.entrypoint("building_guide", kind="eval", tags=["synera-bench", q["kind"]]):
                r = await answer_one(agent, q)
            print(f"{'✓' if r['ok'] else '✗'} {r['id']:<34} {r['kind']:<26} {r['seconds']:>5}s", flush=True)
            return r

    return await asyncio.gather(*(one(q) for q in questions))


def report(results: list[dict]) -> None:
    by_kind: dict[str, list[bool]] = defaultdict(list)
    for r in results:
        by_kind[r["kind"]].append(r["ok"])
    print()
    for kind, oks in sorted(by_kind.items()):
        print(f"{kind:<28} {sum(oks):>3}/{len(oks):<3} {sum(oks) / len(oks):>6.0%}")
    total = sum(r["ok"] for r in results)
    tokens = sum(r["input_tokens"] for r in results), sum(r["output_tokens"] for r in results)
    print(f"{'TOTAL':<28} {total:>3}/{len(results):<3} {total / max(len(results), 1):>6.0%}")
    print(f"tokens in/out: {tokens[0]:,} / {tokens[1]:,}; errors: {sum(bool(r['error']) for r in results)}")


def main(
    split: Annotated[str, typer.Argument(help="dev, train or test")] = "dev",
    per_kind: Annotated[int | None, typer.Option(help="Sample this many questions per kind")] = None,
    kinds: Annotated[list[str] | None, typer.Option("--kind", help="Only these kinds (repeatable)")] = None,
    model: Annotated[str | None, typer.Option(help="Alias (smart, balanced, fast) or model id")] = None,
    thinking: Annotated[str, typer.Option(help="low, medium or high")] = "low",
    concurrency: Annotated[int, typer.Option(help="Questions in flight")] = 8,
    label: Annotated[str | None, typer.Option(help="Report name")] = None,
    submission: Annotated[Path | None, typer.Option(help="Write question_id,answer CSV here")] = None,
) -> None:
    observability.setup()
    questions = load_questions(split, per_kind, kinds)
    model_name = resolve_model(model) if model else None
    label = label or f"{split}-{(model or 'balanced')}-{thinking}"
    print(f"{len(questions)} questions, model {model_name or resolve_model('balanced')}, thinking {thinking}")
    results = asyncio.run(run(questions, model_name, thinking, concurrency))
    REPORTS.mkdir(parents=True, exist_ok=True)
    out = REPORTS / f"bench-{label}.jsonl"
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results), encoding="utf-8")
    if split != "test":
        report(results)
    if submission:
        submission.parent.mkdir(parents=True, exist_ok=True)
        with submission.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["question_id", "answer"])
            writer.writerows((r["id"], r["reply"]) for r in results)
        print(f"submission: {submission}")
    print(f"report: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    typer.run(main)
