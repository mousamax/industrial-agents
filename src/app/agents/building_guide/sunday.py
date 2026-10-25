"""Sunday benchmark: one command from Synera's input to answers.json, no manual steps (challenge PDF).

    uv run run_benchmark <building_dir> <questions.json> <answers.json>      # same as the line below
    uv run python -m app.agents.building_guide.sunday <building_dir> <questions.json> <answers.json>
    ... --check                 # then check every route the way Synera scores it (invalid = 0 points)
    ... --csv answers.csv       # also write the kit's CSV (question_id, full reply)

Rehearsal on a kit building with known answers: scripts/synera/sunday_rehearsal.py.

Input: a building folder in the kit format (anywhere on disk, not only in synera-kit/data) and
questions.json with one entry per question: {"id", "text", "constraints"?} (a list, or {"questions": [...]}).
Output: answers.json, one record per question id with the located item or room id, the route as an
ordered list of node ids, and the route length in metres, plus the full reply.

Synera hasn't published the schema yet (PDF: "a schema and an example answer file are in the data folder
(TODO)"). Until then a record is:

    {"id": "...", "reply": "<full reply>", "answer": "<text after the last 'Answer:'>",
     "item_id": "<asset id>" | null, "space_id": "<space id>" | null, "count": int | null,
     "reachable": bool | null, "route": ["<space id>", ...] | null, "elements": ["<opening/stair/lift id>", ...] | null,
     "length_m": float | null, "stops": ["<space id>", ...] | null,
     "edits": [{"edit": "AddDoor", "first_space_id": ..., ...}, ...] | null}

plus "error": "<exception>" when answering that question crashed (every other field null).

`route` is the ordered space path (docs/synera/ROUTE_SCORING.md scores routes as spaces), `elements` the
openings, stairs and lifts used, `stops` a tour's stops in visiting order (start, each stop, end). The
structured fields come from the agent's own tool results (the last find/route/plan_visits/count call),
never from parsing prose, so they are exactly what the code computed. `edits` are the plan changes the
agent made while answering ("add a door between X and Y, then ..."), as app.building.state edits: the
answer holds on the building with them applied, and --check replays them before checking the route.

Scaffold: tests/building/test_sunday.py drives it with a deterministic answerer (no model). The real
answerer runs building_guide and returns its reply and tool calls, like bench.py does.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import typer

ToolCall = dict[str, Any]
"""{"tool": name, "args": {...}, "result": the tool's JSON string}, in call order (bench.py's format)."""

Answerer = Callable[
    [Path, dict[str, Any]], tuple[str, list[ToolCall]] | tuple[str, list[ToolCall], list[dict[str, Any]]]
]
"""(building_dir, question) -> (full reply, tool calls[, plan edits made, see edits_of])."""

ANSWER_INSTRUCTION = (
    "Finish with a line 'Answer: ...' giving what was asked: the room (for where something is, not the "
    "walk there), the number (how many, how big, how far), or the route length in metres (how to get "
    "somewhere; 'Answer: no route' if there is none)."
)


def empty_record(question_id: str) -> dict[str, Any]:
    """Every field of a record, unset. The one place the answers.json schema lives: change it here."""
    return {
        "id": question_id,
        "reply": "",
        "answer": None,
        "item_id": None,
        "space_id": None,
        "count": None,
        "reachable": None,
        "route": None,
        "elements": None,
        "length_m": None,
        "stops": None,
        "edits": None,
    }


def _results(tool_calls: list[ToolCall], *tools: str) -> list[dict[str, Any]]:
    """The parsed JSON results of every call to one of `tools`, in call order (failed or non-JSON skipped)."""
    out = []
    for c in tool_calls:
        if c.get("tool") not in tools or c.get("result") is None:
            continue
        result = c["result"]
        try:
            parsed = json.loads(result) if isinstance(result, str) else result
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            out.append(parsed | {"_args": c.get("args") or {}})
    return out


def _query(args: dict[str, Any]) -> str:
    query = args.get("query") or next(iter(args.get("args") or []), "")
    return str(query).casefold().strip()


def _located(found: dict[str, Any]) -> tuple[str | None, str | None]:
    """(item id, space id) from one find result: the item named exactly like the query if there is one,
    else the first item; a room when no item matched."""
    items, rooms = found.get("items") or [], found.get("rooms") or []
    if items:
        query = _query(found["_args"])
        item = next((i for i in items if str(i.get("item", "")).casefold().strip() == query), items[0])
        return item.get("id"), item.get("space_id")
    if rooms:
        return None, rooms[0].get("id")
    return None, None


def _joined(legs: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """One space path (and element list) from a tour's legs, each junction stop once."""
    spaces: list[str] = []
    elements: list[str] = []
    for leg in legs:
        path = list(leg.get("space_path") or [])
        if spaces and path and path[0] == spaces[-1]:
            path = path[1:]
        spaces += path
        elements += list(leg.get("element_path") or [])
    return spaces, elements


ROUTE_ASKED = re.compile(
    r"\b(?:route|routes|get(?:ting)?\s+(?:to|from|there|back|out)|go(?:ing)?\s+(?:to|from)|way\s+(?:to|from|out)"
    r"|how\s+far|far\s+is|distance|walk\w*|directions?|path|reach\w*|step[-\s]?free|wheelchair|tour"
    r"|visit\w*|via|metres?|meters?|outside|outdoors|entrances?|exits?|street|arriv\w*|out\s+of\s+the\s+building)\b",
    re.IGNORECASE,
)
"""Words that make a question about getting somewhere (or from or to the outside): its route is the answer."""


def _unasked_route_from_outside(question: dict[str, Any], record: dict[str, Any]) -> bool:
    """A find or count question answered with a courtesy route from the nearest entrance ("where is X":
    the agent adds how to get there from outside). Nobody asked for that route and the question gave no
    start, so it isn't part of the answer: leave route and length empty rather than hand a judge a route
    that starts at "outside" for a question that never mentioned it."""
    route = record.get("route") or []
    located = record["item_id"] or record["space_id"] or record["count"] is not None
    text = str(question.get("text") or question.get("question") or "")
    return bool(
        located
        and route
        and route[0] == "outside"
        and not _start(question)
        and not (question.get("constraints") or {}).get("step_free")
        and not ROUTE_ASKED.search(text)
    )


def _answer_line(reply: str) -> str | None:
    lines = [line for line in reply.splitlines() if "Answer:" in line]
    return lines[-1].split("Answer:", 1)[1].strip() if lines else None


def to_record(
    question: dict[str, Any],
    reply: str,
    tool_calls: list[ToolCall],
    edits: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One answers.json record (schema in the module docstring) from a reply and the tools behind it.

    Each structured field comes from the last call of the tool that computes it: item and room from the
    last `find` with a hit, count from the last `count_items`, route from the last `route`."""
    record = empty_record(question["id"]) | {
        "reply": reply,
        "answer": _answer_line(reply),
        "edits": edits or None,
    }
    hits = [f for f in _results(tool_calls, "find") if f.get("items") or f.get("rooms")]
    if hits:
        record["item_id"], record["space_id"] = _located(hits[-1])
    counts = [c for c in _results(tool_calls, "count_items") if "count" in c]
    if counts:
        record["count"] = counts[-1]["count"]
    # plan_visits (tours in free order) and walk_directions (chats only, Deps.detail="walk") return the same
    # reachable/legs/total_length_m as route, so a route answered with either still fills the record.
    routes = [
        r
        for r in _results(tool_calls, "route", "plan_visits", "walk_directions")
        if isinstance(r.get("reachable"), bool)
    ]
    if routes:
        last = routes[-1]
        record["reachable"] = bool(last["reachable"])
        if last["reachable"]:
            legs = last.get("legs") or []
            record["route"], record["elements"] = _joined(legs)
            if len(legs) > 1:  # a tour: where each leg starts, plus the end (bays may be stops, not passed)
                record["stops"] = [leg["space_path"][0] for leg in legs] + [legs[-1]["space_path"][-1]]
            record["length_m"] = last.get("total_length_m")
        if _unasked_route_from_outside(question, record):
            record |= {"reachable": None, "route": None, "elements": None, "stops": None, "length_m": None}
    return record


def question_text(question: dict[str, Any]) -> str:
    """The question as the agent reads it: Synera's text, its constraints spelled out, an Answer line."""
    text = str(question.get("text") or question.get("question") or "").strip()
    constraints = dict(question.get("constraints") or {})
    notes = []
    if constraints.pop("step_free", False):
        notes.append("The route must be step-free: no stairs or steps, lifts and ramps are fine.")
    constraints.pop("start", None)
    if constraints:
        notes.append(f"Constraints: {json.dumps(constraints, ensure_ascii=False)}.")
    if "Answer:" not in text:
        notes.append(ANSWER_INSTRUCTION)
    return " ".join([text, *notes])


def _start(question: dict[str, Any]) -> str | None:
    """Where the person is, if the question says (Synera: good agents know the start point)."""
    constraints = question.get("constraints") or {}
    return question.get("location") or question.get("start") or constraints.get("start")


CLOSURE_KEYS = ("closed", "closures", "closed_elements", "closed_element_id", "closed_element_ids")
"""Constraint keys that name closed lifts, stairs, doors or rooms (Synera's key isn't published yet)."""


def deps_for(building_dir: Path, question: dict[str, Any]):
    """The agent's Deps for one question: where the person is, and every closure the constraints name
    already recorded, so routing avoids it whether or not the agent calls set_closed itself."""
    from pydantic_ai import ModelRetry

    from app.building.state import Close

    from .tools import Deps, element_of

    deps = Deps.load(building_dir, here=_start(question))
    constraints = question.get("constraints") or {}
    for key in CLOSURE_KEYS:
        named = constraints.get(key) or []
        for element in [named] if isinstance(named, str) else named:
            try:
                eid = element_of(deps.building, str(element))
            except ModelRetry:
                continue  # unknown name: the question text still tells the agent
            if eid not in deps.closed:
                deps.state.apply(Close(eid), by="questions.json")
    return deps


def edits_of(deps) -> list[dict[str, Any]]:
    """The plan edits made while answering (not the closures questions.json gave), as JSON a checker can
    replay with replay_edits: {"edit": "AddDoor", "first_space_id": ..., "second_space_id": ..., ...}."""
    from dataclasses import asdict

    return [
        {"edit": type(e).__name__} | asdict(e) for _, e, s in deps.state.changes() if s.by != "questions.json"
    ]


def replay_edits(deps, edits: list[dict[str, Any]] | None) -> None:
    """Apply a record's `edits` to a fresh Deps (ValueError if one doesn't apply)."""
    from typing import get_args

    from app.building.state import Edit

    kinds = {kind.__name__: kind for kind in get_args(Edit)}
    for e in edits or []:
        fields = {k: tuple(v) if isinstance(v, list) else v for k, v in e.items() if k != "edit"}
        deps.state.apply(kinds[e["edit"]](**fields), by="answers.json")


async def agent_answer(
    building_dir: Path, question: dict[str, Any]
) -> tuple[str, list[ToolCall], list[dict[str, Any]]]:
    """building_guide on one question: its reply, every tool call with its result (bench.py's way) and the
    plan edits it made."""
    from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart

    from app import observability

    from .agent import agent

    deps = deps_for(building_dir, question)
    with observability.entrypoint("building_guide", kind="eval", tags=["synera-sunday"]):
        result = await agent.run(question_text(question), deps=deps)
    messages = result.all_messages()
    returns = {
        p.tool_call_id: p.model_response_str()
        for m in messages
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, ToolReturnPart)
    }
    calls = [
        {"tool": p.tool_name, "args": p.args_as_dict(), "result": returns.get(p.tool_call_id)}
        for m in messages
        if isinstance(m, ModelResponse)
        for p in m.parts
        if isinstance(p, ToolCallPart)
    ]
    return result.output, calls, edits_of(deps)


def agent_answerer(
    building_dir: Path, question: dict[str, Any]
) -> tuple[str, list[ToolCall], list[dict[str, Any]]]:
    """building_guide on one question: its reply, every tool call with its result and its plan edits."""
    from app import observability

    observability.setup()
    return asyncio.run(agent_answer(building_dir, question))


def load_questions(path: Path) -> list[dict[str, Any]]:
    """questions.json as a list of questions: a plain list, or {"questions": [...]}."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    questions = data["questions"] if isinstance(data, dict) else data
    return [q | {"id": str(q["id"])} for q in questions]


def run_benchmark(
    building_dir: Path,
    questions_path: Path,
    answers_path: Path,
    answerer: Answerer | None = None,
    concurrency: int = 8,
) -> list[dict[str, Any]]:
    """Answer every question in questions_path about the building in building_dir and write answers_path.
    One record per question id, in input order; a question that errors still gets a record (fields null,
    "error" set) so one failure never loses the file."""
    building, questions = Path(building_dir), load_questions(questions_path)

    def failed(question: dict[str, Any], e: Exception) -> dict[str, Any]:
        return empty_record(question["id"]) | {"error": f"{type(e).__name__}: {e}"}

    if answerer is None or answerer is agent_answerer:  # the agent: questions in parallel on one event loop
        from app import observability

        observability.setup()
        gate = asyncio.Semaphore(max(1, concurrency))

        async def one(question: dict[str, Any]) -> dict[str, Any]:
            async with gate:
                try:
                    return to_record(question, *await agent_answer(building, question))
                except Exception as e:  # one failure never loses the file
                    return failed(question, e)

        async def every() -> list[dict[str, Any]]:
            return list(await asyncio.gather(*(one(q) for q in questions)))

        records = asyncio.run(every())
    else:
        records = []
        for question in questions:
            try:
                records.append(to_record(question, *answerer(building, question)))
            except Exception as e:  # one failure never loses the file
                records.append(failed(question, e))
    answers_path = Path(answers_path)
    answers_path.parent.mkdir(parents=True, exist_ok=True)
    answers_path.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return records


def check_records(
    building_dir: Path, questions: list[dict[str, Any]], records: list[dict[str, Any]]
) -> dict[str, list[str]]:
    """Problems that would cost points, per question id (empty when none): a crash, no Answer line, a route
    Synera would score 0 (routing.validate_route under the question's step-free and closure constraints;
    a tour's stops may be production bays), or a route whose fields don't agree."""
    from app.building.routing import Constraints, validate_route

    by_id = {q["id"]: q for q in questions}
    problems: dict[str, list[str]] = {}
    for r in records:
        found = []
        if "error" in r:
            found.append(f"crashed: {r['error']}")
        elif r.get("answer") is None:
            found.append("no 'Answer:' line in the reply")
        if r.get("reachable") and (not r.get("route") or r.get("length_m") is None):
            found.append("reachable but no route or length")
        if r.get("route"):
            question = by_id.get(r["id"], {"id": r["id"]})
            deps = deps_for(building_dir, question)
            try:
                replay_edits(deps, r.get("edits"))
            except (KeyError, TypeError, ValueError) as e:
                found.append(f"its edits don't apply: {e}")
            rules = Constraints(
                step_free=bool((question.get("constraints") or {}).get("step_free")), closed=deps.closed
            )
            stops = r.get("stops") or []
            legs = [r["route"]] if not stops else _split(r["route"], stops)
            found += [p for leg in legs for p in validate_route(deps.building, leg, rules)]
        if found:
            problems[r["id"]] = found
    return problems


def _split(route: list[str], stops: list[str]) -> list[list[str]]:
    """A joined tour route cut back into legs at its stops (each stop ends one leg and starts the next)."""
    legs, current, wanted = [], [], list(stops[1:])
    for sid in route:
        current.append(sid)
        if wanted and sid == wanted[0] and len(current) > 1:
            legs.append(current)
            current, wanted = [sid], wanted[1:]
    if len(current) > 1:
        legs.append(current)
    return legs


def write_csv(records: list[dict[str, Any]], path: Path) -> None:
    """The kit's submission format: question_id, answer (the full reply; the scorer reads its last
    'Answer:' line)."""
    import csv

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["question_id", "answer"])
        writer.writerows([r["id"], r.get("reply") or ""] for r in records)


def main(
    building_dir: Annotated[Path, typer.Argument(help="The building folder (building.json, plans, ...)")],
    questions: Annotated[Path, typer.Argument(help="questions.json from Synera")],
    answers: Annotated[Path, typer.Argument(help="Where to write answers.json")],
    concurrency: Annotated[int, typer.Option(help="Questions in flight")] = 8,
    check: Annotated[
        bool, typer.Option(help="Check every answer the way Synera scores it; exit 1 on problems")
    ] = False,
    csv_path: Annotated[
        Path | None, typer.Option("--csv", help="Also write the kit's CSV (question_id, answer)")
    ] = None,
) -> None:
    records = run_benchmark(building_dir, questions, answers, agent_answerer, concurrency)
    errors = sum("error" in r for r in records)
    if errors:
        typer.echo(f"{errors} question(s) errored (see their 'error' field)")
    typer.echo(f"{len(records)} answers -> {answers}")
    if csv_path:
        write_csv(records, csv_path)
        typer.echo(f"CSV -> {csv_path}")
    if check:
        problems = check_records(building_dir, load_questions(questions), records)
        for qid, found in problems.items():
            typer.echo(f"  {qid}: " + "; ".join(found))
        typer.echo(f"check: {len(records) - len(problems)}/{len(records)} answers without problems")
        if problems:
            raise typer.Exit(1)


def cli() -> None:
    """`uv run run_benchmark <building_dir> <questions.json> <answers.json>` (pyproject [project.scripts])."""
    typer.run(main)


if __name__ == "__main__":
    cli()
