"""Run building_guide on the extra tricky cases and grade every reply.

Exact answers (number, space, no_route, with an "Answer:" line asked) use the judges' scorer, as in bench.py.
Everything else (clarification, false premise, sets, routes, out of scope, caveats) goes to an LLM judge
that sees the case's verified reasoning (_judge.md).

    uv run python data/extra_tricky/_bench.py --model fast --label haiku
    uv run python data/extra_tricky/_bench.py --model fast --direction altered_plans --concurrency 4
    uv run python data/extra_tricky/_bench.py --ids xt-routing_rules-01,xt-routing_rules-02 --label check

Writes evals/reports/extra-tricky-<label>.jsonl and prints the score per direction and expected behaviour.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Annotated

import typer
from benchmark.score import committed_answer, numbers_in, within
from pydantic import BaseModel
from pydantic_ai import Agent

from app.agents.building_guide.agent import make_agent
from app.agents.building_guide.bench import answer_one, score
from app.models import model as resolve_model
from app.settings import ROOT

HERE = Path(__file__).resolve().parent
CODE_TYPES = {"number", "space", "no_route"}


class Verdict(BaseModel):
    passed: bool
    why: str


def load_cases(direction: str | None, ids: set[str] | None = None) -> list[dict]:
    cases = [
        json.loads(line) | {"building_dir": str(ROOT / json.loads(line)["building_path"])}
        for path in sorted(HERE.glob("*/questions.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return [
        c
        for c in cases
        if (not direction or c["extra"]["direction"] == direction) and (not ids or c["id"] in ids)
    ]


def code_gradable(case: dict) -> bool:
    return case["answer"]["type"] in CODE_TYPES and "Answer:" in case["question"]


def _form(case: dict) -> list[str]:
    """The <placeholders> of the Answer form the question asks for."""
    return re.findall(r"<[^>]+>", case["question"].split("Answer:", 1)[1])


def score_case(case: dict, reply: str) -> bool:
    """The judges' scorer, except when the form asks for names before the number ("<door between room A
    and room B>, <number of rooms>", "<name>, <metres>"): room names carry numbers too (K22, 1.12), so the
    scorer's one-number rule can't hold; then the last number on the line is the answer."""
    answer = case["answer"]
    if answer["type"] == "number" and len(_form(case)) > 1:
        numbers = numbers_in(committed_answer(reply) or "")
        return bool(numbers) and within(numbers[-1], answer)
    return score(answer, reply)


async def judge_reply(judge: Agent, case: dict, reply: str) -> Verdict:
    e = case["extra"]
    prompt = json.dumps(
        {
            "question": case["question"],
            "expected_answer": case["answer"],
            "expected_behavior": e["expected_behavior"],
            "trap": e["trap"],
            "verified_reasoning": e["reasoning"],
            "reply": reply or "(no reply: the agent crashed)",
        },
        ensure_ascii=False,
        indent=1,
    )
    return (await judge.run(prompt)).output


async def run(cases: list[dict], model_name: str, thinking: str, concurrency: int) -> list[dict]:
    agent = make_agent(model_name, thinking)
    judge = Agent(resolve_model("judge"), output_type=Verdict, instructions=(HERE / "_judge.md").read_text())
    gate = asyncio.Semaphore(concurrency)

    async def one(case: dict) -> dict:
        async with gate:
            r = await answer_one(
                agent, case | {"answer": None}
            )  # graded below: the kit scorer lacks our types
            if code_gradable(case):
                r |= {"ok": score_case(case, r["reply"]), "graded_by": "code", "why": ""}
            else:
                v = await judge_reply(judge, case, r["reply"])
                r |= {"ok": v.passed, "graded_by": "judge", "why": v.why}
            r |= {k: case["extra"][k] for k in ("direction", "expected_behavior", "tool_coverage")}
            print(
                f"{'✓' if r['ok'] else '✗'} {r['id']:<30} {r['expected_behavior']:<20} {r['seconds']:>5}s",
                flush=True,
            )
            return r

    return await asyncio.gather(*(one(c) for c in cases))


def table(results: list[dict], key: str) -> None:
    groups: dict[str, list[bool]] = defaultdict(list)
    for r in results:
        groups[r[key]].append(r["ok"])
    print(f"\n{key}")
    for name, oks in sorted(groups.items()):
        print(f"  {name:<24} {sum(oks):>3}/{len(oks):<3} {sum(oks) / len(oks):>5.0%}")


def failed_cases(labels: str) -> set[str]:
    """Ids that failed in any of the given runs (evals/reports/extra-tricky-<label>.jsonl)."""
    reports = ROOT / "evals" / "reports"
    paths = sorted(
        {p for label in labels.split(",") for p in reports.glob(f"extra-tricky-{label.strip()}.jsonl")}
    )
    if not paths:
        raise typer.BadParameter(f"no report matches {labels!r} in {reports}")
    failed = {
        r["id"]
        for p in paths
        for r in map(json.loads, p.read_text(encoding="utf-8").splitlines())
        if not r["ok"]
    }
    print(
        f"{len(failed)} cases failed in {len(paths)} runs: {', '.join(p.stem.removeprefix('extra-tricky-') for p in paths)}"
    )
    return failed


def main(
    model: Annotated[str, typer.Option(help="Alias (smart, balanced, fast) or model id")] = "fast",
    thinking: str = "low",
    direction: str | None = None,
    concurrency: int = 8,
    label: str | None = None,
    ids: Annotated[
        str | None,
        typer.Option(help="Comma-separated case ids, e.g. xt-routing_rules-01,xt-routing_rules-02"),
    ] = None,
    failed_in: Annotated[
        str | None,
        typer.Option(
            help="Rerun only cases that failed in these earlier runs: comma-separated labels, "
            "wildcards allowed, e.g. 'haiku-r*' or 'haiku,haiku-r7,haiku-r8'"
        ),
    ] = None,
) -> None:
    wanted = set(ids.split(",")) if ids else None
    if failed_in:
        failed = failed_cases(failed_in)
        wanted = failed if wanted is None else wanted | failed
    cases = load_cases(direction, wanted)
    model_name = resolve_model(model)
    print(f"{len(cases)} cases, model {model_name}, thinking {thinking}")
    results = asyncio.run(run(cases, model_name, thinking, concurrency))
    out = ROOT / "evals" / "reports" / f"extra-tricky-{label or model}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in sorted(results, key=lambda r: r["id"]))
    )
    for key in ("direction", "expected_behavior", "tool_coverage"):
        table(results, key)
    ok = sum(r["ok"] for r in results)
    print(
        f"\nTOTAL {ok}/{len(results)} {ok / max(len(results), 1):.0%}   errors {sum(bool(r['error']) for r in results)}"
    )
    print(f"replies: {out}")


if __name__ == "__main__":
    typer.run(main)
