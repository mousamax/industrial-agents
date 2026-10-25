"""Run building_guide on the multi-building cases and score every reply with the judges' scorer.

Like bench.py's warm-up suite: each question gets an "Answer:" line (route or tour length in metres, or "no
route"), and a length counts when it is within 1 % of the best (for tour_best_order only the best order is).
Clarification cases pass when the reply commits to no number and names both buildings that have the room.
Length only: a route as long as the best but invalid (e.g. through a production bay) still passes here;
tests/building/test_multi_building.py checks validity on RouteGraph itself.

    uv run python data/multi_building/_bench.py --model fast
    uv run python data/multi_building/_bench.py --model fast --case uwing --concurrency 4

Writes evals/reports/multi-building-<label>.jsonl and prints the score per scenario and kind.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from pathlib import Path
from typing import Annotated

import typer
from benchmark.score import committed_answer, numbers_in

from app.agents.building_guide.agent import make_agent
from app.agents.building_guide.bench import NO_ROUTE, answer_one, score
from app.building.model import load_building
from app.models import model as resolve_model
from app.settings import ROOT

HERE = Path(__file__).resolve().parent
INSTRUCTION = f"Finish with a line 'Answer: <number>' giving the total length in metres, or {NO_ROUTE}."


def kit_answer(case: dict) -> dict | None:
    a = case["answer"]
    if a["type"] == "no_route":
        return {"type": "no_route"}
    if a["type"] in ("route", "tour"):
        return {"type": "number", "value": a["optimal_length_m"], "relative_tolerance": 0.01,
                "absolute_tolerance": 0.05}  # fmt: skip
    return None


def clarified(case: dict, reply: str) -> bool:
    b = load_building(ROOT / case["building_path"])
    buildings = {load_building(bid).name for bid in b.info["buildings"]}
    owners = {name for name in buildings if any(
        sid.startswith(bid) for sid in case["answer"]["options"] for bid in b.info["buildings"]
        if load_building(bid).name == name)}  # fmt: skip
    text = committed_answer(reply) or ""
    return not numbers_in(text) and all(name.casefold() in reply.casefold() for name in owners)


def load_cases(only: str | None) -> list[dict]:
    cases = [
        json.loads(line) | {"building_dir": str(ROOT / json.loads(line)["building_path"])}
        for path in sorted(HERE.glob("*/questions.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for c in cases:
        if c["answer"]["type"] != "clarification":
            c["question"] = f"{c['question']} {INSTRUCTION}"
    return [c for c in cases if not only or only in c["id"]]


async def run(cases: list[dict], model_name: str, thinking: str, concurrency: int) -> list[dict]:
    agent = make_agent(model_name, thinking)
    gate = asyncio.Semaphore(concurrency)

    async def one(case: dict) -> dict:
        async with gate:
            # No `inputs` to the agent (they hold the resolved stops); it must find them from the question.
            r = await answer_one(agent, {k: v for k, v in case.items() if k != "inputs"} | {"answer": None})
            expected = kit_answer(case)
            r["ok"] = score(expected, r["reply"]) if expected else clarified(case, r["reply"])
            r["scenario"] = case["building_id"]
            print(
                f"{'✓' if r['ok'] else '✗'} {r['id']:<16} {case['kind']:<20} {r['seconds']:>5}s", flush=True
            )
            return r

    return await asyncio.gather(*(one(c) for c in cases))


def table(results: list[dict], key: str) -> None:
    groups: dict[str, list[bool]] = defaultdict(list)
    for r in results:
        groups[r[key]].append(r["ok"])
    print(f"\n{key}")
    for name, oks in sorted(groups.items()):
        print(f"  {name:<22} {sum(oks):>3}/{len(oks):<3} {sum(oks) / len(oks):>5.0%}")


def main(
    model: Annotated[str, typer.Option(help="Alias (smart, balanced, fast) or model id")] = "fast",
    thinking: str = "low",
    case: Annotated[str | None, typer.Option(help="only cases whose id contains this")] = None,
    concurrency: int = 8,
    label: str | None = None,
) -> None:
    cases = load_cases(case)
    model_name = resolve_model(model)
    print(f"{len(cases)} cases, model {model_name}, thinking {thinking}")
    results = asyncio.run(run(cases, model_name, thinking, concurrency))
    out = ROOT / "evals" / "reports" / f"multi-building-{label or model}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in sorted(results, key=lambda r: r["id"]))
    )
    table(results, "scenario")
    table(results, "kind")
    ok = sum(r["ok"] for r in results)
    print(
        f"\nTOTAL {ok}/{len(results)} {ok / max(len(results), 1):.0%}   errors {sum(bool(r['error']) for r in results)}"
    )
    print(f"replies: {out}")


if __name__ == "__main__":
    typer.run(main)
