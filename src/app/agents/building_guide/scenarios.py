"""Run the tricky-user scenarios (scenarios.yaml) and judge them on Synera's interaction rubric.

    uv run python -m app.agents.building_guide.scenarios                 # all, default model
    uv run python -m app.agents.building_guide.scenarios --only vague-press --model smart

Each scenario plays its user turns in one conversation (fresh session state), runs the code checks on each
turn's tool calls. A turn {who, say} is a colleague in their own chat on the same live building, and
`setup` records scanner events before anyone talks (the Level 5 "live building"). Then then an LLM judge reads the transcript (replies, tool calls and results) and rules on
the three rubric points and every `expect` line, three times; the majority decides. Saves evals/reports/scenarios-<label>.jsonl.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart

from app import observability
from app.models import model as resolve_model
from app.settings import ROOT, get_settings

from .agent import make_agent
from .drafting import create_draft
from .tools import Deps, overview, report_item

SCENARIOS = Path(__file__).with_name("scenarios.yaml")
REPORTS = ROOT / "evals" / "reports"


class Expectation(BaseModel):
    expect: str
    met: bool
    why: str


class Verdict(BaseModel):
    asks_only_what_it_needs: bool = Field(
        description="No unneeded questions; one short question when truly ambiguous"
    )
    shows_what_it_understood: bool = Field(
        description="States its reading (places, constraints) so the person can check"
    )
    followable: bool = Field(
        description="Directions a person can follow while walking: rooms, lifts/stairs, floors"
    )
    expectations: list[Expectation]
    summary: str = Field(description="One sentence: the most important thing to fix, or 'fine'")


judge = Agent(
    resolve_model("judge"),
    output_type=Verdict,
    instructions=(
        "You judge an indoor-navigation assistant for a hackathon jury. Read the conversation, including "
        "the tool calls and results (the ground truth about the building). Rule strictly on the three rubric "
        "points and on each expectation, one by one. A rubric point that doesn't apply to this conversation "
        "(e.g. no directions were needed) counts as met. Facts in replies must match the tool results."
    ),
)


def _calls(messages: list) -> list[dict[str, Any]]:
    calls, results = [], {}
    for m in messages:
        if isinstance(m, ModelRequest):
            for p in m.parts:
                if isinstance(p, ToolReturnPart):
                    results[p.tool_call_id] = p.model_response_str()
    for m in messages:
        if isinstance(m, ModelResponse):
            for p in m.parts:
                if isinstance(p, ToolCallPart):
                    calls.append(
                        {
                            "tool": p.tool_name,
                            "args": p.args_as_dict(),
                            "result": results.get(p.tool_call_id, ""),
                        }
                    )
    return calls


JUDGES = 3
"""Each transcript is judged this many times; the majority decides, so one noisy ruling can't flip it."""


def _passes(v: Verdict) -> bool:
    rubric = v.asks_only_what_it_needs and v.shows_what_it_understood and v.followable
    return rubric and all(e.met for e in v.expectations)


def check(rule: dict, turn: dict) -> str | None:
    """None if the rule holds for this turn, else what went wrong."""
    calls = turn["tool_calls"]
    if "tool" in rule:
        wanted = rule.get("args", {})
        if not any(
            c["tool"] == rule["tool"] and all(c["args"].get(k) == v for k, v in wanted.items()) for c in calls
        ):
            return f"turn {rule['turn']}: no {rule['tool']} call with {wanted or 'any args'}"
    if "no_tool" in rule and any(c["tool"] == rule["no_tool"] for c in calls):
        return f"turn {rule['turn']}: called {rule['no_tool']}, shouldn't have"
    if "max_words" in rule and (n := len(turn["reply"].split())) > rule["max_words"]:
        return f"turn {rule['turn']}: {n} words, max {rule['max_words']}"
    return None


def transcript(scenario: dict, turns: list[dict], deps: Deps, recorded: list[dict] | None = None) -> str:
    """What the agent knew (the building overview it gets as context) plus every turn, tool call and result."""
    out = [
        f"Persona: {scenario['persona']}",
        "Building overview the assistant is given as context (also ground truth):",
        json.dumps(overview(deps.state.base), ensure_ascii=False),
    ]
    if recorded:
        out.append("Recorded by scanners and colleagues before the conversation (ground truth):")
        out += [json.dumps(r, ensure_ascii=False) for r in recorded]
    for i, t in enumerate(turns, 1):
        speaker = f"USER ({t['who']}, own chat)" if t.get("who") else "USER"
        out.append(f"\n--- Turn {i}\n{speaker}: {t['user']}")
        for c in t["tool_calls"]:
            out.append(
                f"TOOL {c['tool']}({json.dumps(c['args'], ensure_ascii=False)}) -> {c['result'][:1500]}"
            )
        out.append(f"ASSISTANT: {t['reply']}")
    out.append("\nExpectations:\n" + "\n".join(f"- {e}" for e in scenario["expect"]))
    return "\n".join(out)


def record(deps: Deps, events: list[dict]) -> list[dict]:
    """Apply a scenario's `setup`: {item, to, type?, day (0 today, -1 yesterday, 2), time "09:30", by,
    source} each, as a scanner would have reported it. Returns what was recorded, for the judge."""
    today = deps.state.now()
    out = []
    for e in events:
        day = (today + timedelta(days=e.get("day", 0))).date()
        at = datetime.combine(
            day, time.fromisoformat(e.get("time", "09:00"))
        ).astimezone()  # that day's offset
        r = report_item(
            deps.state,
            e["item"],
            e["to"],
            at=at,
            new_item_type=e.get("type"),
            by=e.get("by", "scanner"),
            source=e.get("source", "barcode"),
        )
        out.append({k: r[k] for k in ("item", "from", "to", "when", "status", "reported_by")})
    return out


def drafted(scenario: dict, root: Path) -> Deps:
    """A Level 4 scenario's building: drafted fresh from `plans` (a glob under synera-kit/data/), the way
    the app does after an upload, so every run starts with all the draft's questions open."""
    plans = sorted((get_settings().synera_kit_dir / "data").glob(scenario["plans"]))
    return Deps(create_draft(plans, name=scenario.get("name_as", "New building"), root=root).state)


async def play(agent, scenario: dict) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        return await _play(agent, scenario, Path(tmp))


async def _play(agent, scenario: dict, tmp: Path) -> dict:
    deps = drafted(scenario, tmp) if "plans" in scenario else Deps.load(scenario["building"])
    deps.detail = "walk"  # scenarios play people chatting in the app, not the benchmark
    turns: list[dict] = []
    recorded = record(deps, scenario.get("setup", []))
    chats: dict[str, tuple[Deps, list]] = {"": (deps, [])}  # one chat per colleague, one shared building
    with observability.entrypoint("building_guide", kind="eval", tags=["synera-scenario", scenario["name"]]):
        for turn in scenario["turns"]:
            who, text = (turn["who"], turn["say"]) if isinstance(turn, dict) else ("", turn)
            own, history = chats.setdefault(who, (Deps(deps.state, who=who, detail="walk"), []))
            try:
                result = await agent.run(text, deps=own, message_history=history)
                chats[who] = (own, result.all_messages())
                turns.append(
                    {
                        "who": who,
                        "user": text,
                        "reply": result.output,
                        "tool_calls": _calls(result.new_messages()),
                    }
                )
            except Exception as e:
                reply = f"[crash: {type(e).__name__}: {e}]"
                turns.append({"who": who, "user": text, "reply": reply, "tool_calls": []})
    failed_checks = [f for rule in scenario.get("checks", []) if (f := check(rule, turns[rule["turn"] - 1]))]
    text = transcript(scenario, turns, deps, recorded)
    verdicts = [v.output for v in await asyncio.gather(*(judge.run(text) for _ in range(JUDGES)))]
    passes = [_passes(v) for v in verdicts]
    verdict = next(
        (v for v, p in zip(verdicts, passes, strict=True) if not p), verdicts[0]
    )  # show a failing one
    ok = not failed_checks and sum(passes) > len(passes) / 2
    return {
        "name": scenario["name"],
        "ok": ok,
        "failed_checks": failed_checks,
        "judges_passed": f"{sum(passes)}/{len(passes)}",
        "verdict": verdict.model_dump(),
        "turns": turns,
    }


def names() -> list[str]:
    return [s["name"] for s in yaml.safe_load(SCENARIOS.read_text(encoding="utf-8"))["scenarios"]]


def run_scenarios(
    only: list[str] | None = None, model: str | None = None, thinking: str = "low", label: str | None = None
) -> tuple[list[dict], Path]:
    observability.setup()
    scenarios = yaml.safe_load(SCENARIOS.read_text(encoding="utf-8"))["scenarios"]
    scenarios = [s for s in scenarios if not only or s["name"] in only]
    agent = make_agent(resolve_model(model) if model else None, thinking)

    async def run_all() -> list[dict]:
        gate = asyncio.Semaphore(6)

        async def one(s: dict) -> dict:
            async with gate:
                r = await play(agent, s)
            why = "; ".join(r["failed_checks"]) or r["verdict"]["summary"]
            print(f"{'✓' if r['ok'] else '✗'} {r['name']:<30} {'' if r['ok'] else why}", flush=True)
            return r

        return await asyncio.gather(*(one(s) for s in scenarios))

    results = asyncio.run(run_all())
    REPORTS.mkdir(parents=True, exist_ok=True)
    out = REPORTS / f"scenarios-{label or (model or 'balanced')}.jsonl"
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results), encoding="utf-8")
    return results, out


def main(
    only: Annotated[list[str] | None, typer.Option("--only", help="Scenario names (repeatable)")] = None,
    model: Annotated[str | None, typer.Option(help="Alias or model id for the agent")] = None,
    thinking: Annotated[str, typer.Option()] = "low",
    label: Annotated[str | None, typer.Option()] = None,
) -> None:
    results, out = run_scenarios(only, model, thinking, label)
    print(
        f"\n{sum(r['ok'] for r in results)}/{len(results)} scenarios pass · report: {out.relative_to(ROOT)}"
    )


if __name__ == "__main__":
    typer.run(main)
