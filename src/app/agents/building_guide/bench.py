"""Benchmark building_guide with the judges' scorer (benchmark.score), in parallel, on three suites:

- benchmark: the scored question kinds (questions.jsonl), exactly as Synera sends them.
- warmup: the Sunday-style kinds (questions_basic.jsonl: locate, count, route, step-free, closures, tours),
  with an "Answer:" instruction appended so the same scorer applies. Routes count when the length is
  within 1% of the best route (the kit's own check) or the agent rightly says there is no route.
- reworded: the scored kinds in other words (evals/datasets/reworded_<split>.jsonl, made by
  scripts/synera/make_reworded.py), standing in for the unseen Sunday phrasing.
- unseen / holdout: 12 question kinds Synera's sets don't have, answers computed by our code
  (scripts/synera/make_unseen.py). unseen is on dev buildings (read its failures); holdout is on test
  buildings and is only ever scored, never read, so it stays an honest estimate.

    uv run python -m app.agents.building_guide.bench dev --per-kind 3
    uv run python -m app.agents.building_guide.bench dev --suite warmup --per-kind 10
    uv run python -m app.agents.building_guide.bench dev --model smart --thinking medium --label opus-med
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
from benchmark.score import committed_answer, numbers_in, score_reply
from pydantic_ai.messages import ModelResponse, ToolCallPart

from app import observability
from app.building.model import building_dir, load_building
from app.models import model as resolve_model
from app.settings import ROOT, get_settings

from .agent import make_agent
from .tools import Deps

REPORTS = ROOT / "evals" / "reports"


DATASETS = ROOT / "evals" / "datasets"
SUITES = ("benchmark", "warmup", "reworded", "unseen", "holdout")
DATASET_FILES = {
    "reworded": "reworded_{split}.jsonl",
    "unseen": "unseen_dev.jsonl",
    "holdout": "unseen_holdout.jsonl",
}
NO_ROUTE = "'Answer: no route' if there is none"
WARMUP_INSTRUCTIONS = {
    "locate": "Finish with a line 'Answer: <room name or number>'.",
    "count": "Finish with a line 'Answer: <number>'.",
    "route": f"Finish with a line 'Answer: <number>' giving the route length in metres, or {NO_ROUTE}.",
}


def warmup_question(q: dict) -> dict:
    """A warm-up line in the benchmark's shape: an Answer instruction and a kit-format answer."""
    a, kind = q["answer"], q["kind"]
    if kind == "locate":  # like the scored kinds: the room's name or its number (refs are unique)
        ref = load_building(q["building_id"]).spaces[a["space_id"]].ref
        answer = {"type": "space", "accept": [a["space_name"], *([ref] if ref else [])], "reject": []}
    elif kind == "count":
        answer = {"type": "number", "value": a["count"], "relative_tolerance": 0.0, "absolute_tolerance": 0.0}
    elif a.get("reachable") is False:
        answer = {"type": "no_route"}
    else:
        answer = {
            "type": "number",
            "value": a["optimal_length_m"],
            "relative_tolerance": 0.01,
            "absolute_tolerance": 0.05,
        }
    instruction = WARMUP_INSTRUCTIONS.get(kind, WARMUP_INSTRUCTIONS["route"])
    return q | {"question": f"{q['question']} {instruction}", "answer": answer}


def score(answer: dict | None, reply: str) -> bool:
    if not answer:
        return False
    if answer["type"] == "no_route":
        text = committed_answer(reply) or ""
        return "no route" in text.casefold() and not numbers_in(text)
    return score_reply(answer, reply)


def load_questions(
    split: str, per_kind: int | None, kinds: list[str] | None, suite: str = "benchmark", seed: int = 7
) -> list[dict]:
    data = get_settings().synera_kit_dir / "data" / split
    if suite in DATASET_FILES:
        path = DATASETS / DATASET_FILES[suite].format(split=split)
        if not path.exists():
            raise typer.BadParameter(
                f"{path} missing: see scripts/synera/make_reworded.py and make_unseen.py"
            )
        lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        questions = [q | {"building_dir": str(building_dir(q["building_id"]))} for q in lines]
    else:
        name = "questions.jsonl" if suite == "benchmark" else "questions_basic.jsonl"
        questions = [
            json.loads(line) | {"building_dir": str(path.parent)}
            for path in sorted(data.glob(f"*/{name}"))
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if suite == "warmup":
            questions = [warmup_question(q) for q in questions]
    by_kind: dict[str, list[dict]] = defaultdict(list)
    for q in questions:
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
        "ok": score(q.get("answer"), reply),
        "reply": reply,
        "error": error,
        "tool_calls": calls,
        "input_tokens": usage.input_tokens if usage else 0,
        "output_tokens": usage.output_tokens if usage else 0,
        "seconds": round(time.perf_counter() - started, 1),
    }


SCORE_ONLY = {"holdout"}
"""Suites we never read question by question: no per-question output, no replies saved. Only the score."""


async def run(
    questions: list[dict], model_name: str | None, thinking: str, concurrency: int, quiet: bool = False
) -> list[dict]:
    agent = make_agent(model_name, thinking)
    gate = asyncio.Semaphore(concurrency)

    async def one(q: dict) -> dict:
        async with gate:
            with observability.entrypoint("building_guide", kind="eval", tags=["synera-bench", q["kind"]]):
                r = await answer_one(agent, q)
            if not quiet:
                print(
                    f"{'✓' if r['ok'] else '✗'} {r['id']:<34} {r['kind']:<26} {r['seconds']:>5}s", flush=True
                )
            return r

    return await asyncio.gather(*(one(q) for q in questions))


def summary(results: list[dict]) -> dict:
    by_kind: dict[str, list[bool]] = defaultdict(list)
    for r in results:
        by_kind[r["kind"]].append(r["ok"])
    return {
        "correct": sum(r["ok"] for r in results),
        "total": len(results),
        "by_kind": {k: [sum(v), len(v)] for k, v in sorted(by_kind.items())},
        "errors": sum(bool(r["error"]) for r in results),
        "input_tokens": sum(r["input_tokens"] for r in results),
        "output_tokens": sum(r["output_tokens"] for r in results),
    }


def report(results: list[dict]) -> None:
    s = summary(results)
    print()
    for kind, (ok, n) in s["by_kind"].items():
        print(f"{kind:<28} {ok:>3}/{n:<3} {ok / n:>6.0%}")
    print(f"{'TOTAL':<28} {s['correct']:>3}/{s['total']:<3} {s['correct'] / max(s['total'], 1):>6.0%}")
    print(f"tokens in/out: {s['input_tokens']:,} / {s['output_tokens']:,}; errors: {s['errors']}")


def run_suite(
    split: str = "dev",
    suite: str = "benchmark",
    *,
    per_kind: int | None = None,
    kinds: list[str] | None = None,
    model: str | None = None,
    thinking: str = "low",
    concurrency: int = 8,
    label: str | None = None,
) -> tuple[list[dict], Path]:
    """Run one suite and save its report; used by `main` and by the scoreboard."""
    observability.setup()
    questions = load_questions(split, per_kind, kinds, suite)
    model_name = resolve_model(model) if model else None
    label = label or f"{split}-{suite}-{(model or 'balanced')}-{thinking}"
    print(
        f"{len(questions)} {suite} questions, model {model_name or resolve_model('balanced')}, thinking {thinking}"
    )
    quiet = suite in SCORE_ONLY
    results = asyncio.run(run(questions, model_name, thinking, concurrency, quiet))
    if quiet:  # keep what the summary needs, drop what would let us read single questions
        results = [
            {k: r[k] for k in ("kind", "ok", "error", "input_tokens", "output_tokens")} for r in results
        ]
    REPORTS.mkdir(parents=True, exist_ok=True)
    out = REPORTS / f"bench-{label}.jsonl"
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results), encoding="utf-8")
    return results, out


def main(
    split: Annotated[str, typer.Argument(help="dev, train or test")] = "dev",
    suite: Annotated[str, typer.Option(help="benchmark, warmup, reworded, unseen or holdout")] = "benchmark",
    per_kind: Annotated[int | None, typer.Option(help="Sample this many questions per kind")] = None,
    kinds: Annotated[list[str] | None, typer.Option("--kind", help="Only these kinds (repeatable)")] = None,
    model: Annotated[str | None, typer.Option(help="Alias (smart, balanced, fast) or model id")] = None,
    thinking: Annotated[str, typer.Option(help="low, medium or high")] = "low",
    concurrency: Annotated[int, typer.Option(help="Questions in flight")] = 8,
    label: Annotated[str | None, typer.Option(help="Report name")] = None,
    submission: Annotated[Path | None, typer.Option(help="Write question_id,answer CSV here")] = None,
) -> None:
    if suite not in SUITES:
        raise typer.BadParameter(f"suite must be one of {SUITES}")
    results, out = run_suite(
        split,
        suite,
        per_kind=per_kind,
        kinds=kinds,
        model=model,
        thinking=thinking,
        concurrency=concurrency,
        label=label,
    )
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
