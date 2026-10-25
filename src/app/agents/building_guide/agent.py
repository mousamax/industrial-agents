"""building_guide: talk to a building. Find, route, react to closures, simulate changes, plan facts.

One agent, small typed tools on the shared domain interfaces (tools.py), the building's vocabulary as
cached context (floors, room and item types, lifts, entrances), and a code check on the benchmark's
"Answer:" line. Every number comes from a tool; the model chooses tools and writes the reply.
"""

from __future__ import annotations

import json
import re

from benchmark.score import committed_answer, numbers_in
from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext, Tool
from pydantic_ai.capabilities import Caching, Thinking
from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart

from app.core import AgentCard, load_prompt
from app.models import model
from app.settings import get_settings

from .drafting import DRAFT_TOOLS, accept_guesses, draft_building, draft_of, fix_draft
from .room_calendar import calendar, calendar_context, plan_event
from .tools import (
    TOOLS,
    Deps,
    _ago,
    _clock,
    _label,
    add_door,
    add_wall,
    highlight_rooms,
    move_item,
    never_crash,
    overview,
    remove_door,
    set_closed,
    undo_last_change,
    walk_directions,
    walk_mode_only,
)


class Question(BaseModel):
    building: str = Field(
        description="Building id (e.g. proc-factory-dev-003) or path to a building directory"
    )
    question: str
    location: str | None = Field(
        default=None, description="Where the person is, if the app knows: a space id or room name"
    )


def _building_context(ctx: RunContext[Deps]) -> str:
    """The plan as drawn (not today's state), so the cached prefix stays the same while things move."""
    return "Building overview:\n" + json.dumps(overview(ctx.deps.state.base), ensure_ascii=False)


MUCL_ID = "munich-urban-colab"
MUCL_HOUSE_RULES = (
    "House rules of the Munich Urban Colab: people arrive at the Eingangsbereich [F0-EING], so unless they say "
    "where they are, routes and 'nearest' questions start there. Between floors, walking people always take "
    "the Haupttreppe (main stairs): call `route` with its stops on the start and destination floor as `via` "
    "(e.g. via=['F0-STAIR-C', 'F3-STAIR-C']), and tell them to take the Haupttreppe. Only step-free routes "
    "use the lift."
)
"""Our demo building (data/MUCL) only; no other building, and so no benchmark question, gets these rules."""


def _mucl_context(ctx: RunContext[Deps]) -> str:
    return MUCL_HOUSE_RULES if ctx.deps.building.id == MUCL_ID else ""


def _location_context(ctx: RunContext[Deps]) -> str:
    """After the overview, so the cached prefix stays the same for every person in the building."""
    if not ctx.deps.here:
        return ""
    return (
        f"The person is in {_label(ctx.deps.building, ctx.deps.here)} [{ctx.deps.here}] (from the app, not "
        "from them). Use it when they say here, this room, or don't say where they start; their own words win."
    )


def _live_context(ctx: RunContext[Deps]) -> str:
    """The clock, and whether anyone recorded changes, so "yesterday" and "what changed?" have a reference.
    As the turn started, and the same for every model request in it: a change a tool makes during the turn
    is in that tool's result, and listing it here too made the model think the door was there before its
    own call (and that it had now made two)."""
    turn = ctx.deps.turn_context
    if turn is None or turn[0] != ctx.run_id:
        turn = ctx.deps.turn_context = (ctx.run_id, _live_now(ctx))
    return turn[1]


def _live_now(ctx: RunContext[Deps]) -> str:
    state = ctx.deps.state
    now = f"It is now {_clock(state.now())}."
    if not state.edits:
        return now
    latest = state.stamps[-1]
    return (
        f"{now} {len(state.edits)} change(s) to the building are recorded (latest: {latest.said} "
        f"{_ago(latest.at, state.now())}, by {latest.by or 'this conversation'}); tools already apply them, "
        "building_changes lists them."
    )


CONFIRM_EDITS = load_prompt(__file__, "confirm_edits.md")


def _confirm_context(ctx: RunContext[Deps]) -> str:
    """Only where plan edits wait for a yes (Deps.confirm_edits, the voice call); elsewhere they apply at once."""
    return CONFIRM_EDITS if ctx.deps.confirm_edits else ""


def _calendar_context(ctx: RunContext[Deps]) -> str:
    """Only in a building with a room calendar (Creativity add-in): events are a place to look things up."""
    return calendar_context(ctx.deps)


def _draft_context(ctx: RunContext[Deps]) -> str:
    """Only for a building drafted from plans in the app: the questions its plans left open."""
    draft = draft_of(ctx.deps.state)
    if draft is None:
        return ""
    left = len(draft.open)
    if not left:
        return "This building was drafted from floor plans; every question about the draft is settled."
    return (
        f"This building was just drafted from floor plans and {left} question(s) about what the plans don't "
        "say are open (review_draft lists them). Unless the person asks something else, walk them through "
        "these, a couple at a time; their answers go to fix_draft or accept_guesses."
    )


def _tool_numbers(ctx: RunContext[Deps]) -> list[float]:
    found: list[float] = []
    for message in ctx.messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, ToolReturnPart):
                    found += numbers_in(part.model_response_str())
    return found


def _grounded(value: float, known: list[float]) -> bool:
    return any(abs(value - k) <= max(0.006 * abs(k), 0.06) for k in known)


FORM = re.compile(r"Answer:\s*([^'\"\n]*<[^'\"\n]*)")
NUMERIC = re.compile(
    r"number|percent|metres|meters|minutes|m²|m2|\bper\b|\bdb\b|count|area|length|distance|°c"
)
NOT_NUMERIC = re.compile(r"room|name|floor|space|time|type|door|route")
NOT_IN_PLAN = re.compile(r"not in the plan|not recorded|isn't recorded|no \w+ data")
"""How a reply says the value asked for isn't in the building data at all (prompt: 'Answer: not in the plan')."""
NO_ROUTE_SAID = re.compile(
    r"\bno\b[\w\s-]{0,25}\b(?:route|path)\b|unreachable|not reachable|can(?:no|')t reach"
)


def _form(prompt: str) -> tuple[str, list[str]]:
    """The Answer form the question asks for and its <placeholders>."""
    m = FORM.search(prompt)
    form = m[1] if m else ""
    return form, re.findall(r"<([^>]+)>", form)


def _one_number(placeholders: list[str]) -> bool:
    """One placeholder and it is a quantity: <number>, <percent>, <total metres>, <items per m²>."""
    return len(placeholders) == 1 and bool(
        NUMERIC.search(placeholders[0].casefold()) and not NOT_NUMERIC.search(placeholders[0].casefold())
    )


def _answer_line_is_committed_and_grounded(ctx: RunContext[Deps], reply: str) -> str:
    """When asked for an 'Answer:' line: there is one, a quantity asked for is one number that came from a
    tool, and no route is said as 'no route', the scorer's words. One number even when a tool flagged the
    place as ambiguous: the scorer gives no credit to "A or B" (it wants exactly one number), so the reply
    commits to its best reading and puts the other options above the line. The scorer reads only that line, so this is where a reply wins or loses. Parsing is the scorer's
    own (benchmark.score), so what passes here is read the same way there.

    On the last retry the reply goes through as it is: a best effort can still score, a crash never does."""
    prompt = ctx.prompt if isinstance(ctx.prompt, str) else ""
    if "Answer:" not in prompt:  # a person, not a test: no "Answer:" line they didn't ask for
        lines = reply.rstrip().splitlines()
        if len(lines) > 1 and re.match(r"^\W*answer\W*:", lines[-1], flags=re.IGNORECASE):
            return "\n".join(lines[:-1]).rstrip()
        return reply
    if ctx.retry >= ctx.max_retries:
        return reply
    line = committed_answer(reply)
    if line is None:
        raise ModelRetry("End your reply with the line 'Answer: ...' in the form the question asks for.")
    numbers = numbers_in(line)
    form, placeholders = _form(prompt)
    about_routes = "no route" in prompt or re.search(r"metres|meters|route|spaces i pass", form.casefold())
    if about_routes and "no route" in line.casefold() and not numbers:
        return reply
    if about_routes and NO_ROUTE_SAID.search(line.casefold()):
        raise ModelRetry(
            f"When there is no route, the line is exactly 'Answer: no route' (no other words, no numbers), "
            f"got {line!r}. Put the reason above it."
        )
    if not numbers and NOT_IN_PLAN.search(line.casefold()):
        return reply  # the value isn't in the plan at all (hours, noise): said in words, not a made-up number
    room_answer = "<room name or number>" in prompt
    wants_number = "<number>" in prompt or "<area>" in prompt or _one_number(placeholders)
    if wants_number and not room_answer and len(numbers) != 1:
        raise ModelRetry(
            f"The Answer line must hold exactly one number, the value asked for, got {line!r}. Leave room "
            "numbers, counts and working out of it; put them above the line. If several places fit the "
            "question, commit to the most plausible one (the first a tool listed when nothing in the question "
            "settles it), say above the line which you took and give the others' values there."
        )
    if "<area>" in prompt and "m²" not in line and "m2" not in line:
        raise ModelRetry("Give the room, then its area in m², e.g. 'Answer: Kitchen 1.14, 28.0 m²'.")
    value = numbers[-1] if numbers else None
    if wants_number and value is not None and not _grounded(value, _tool_numbers(ctx)):
        raise ModelRetry(
            f"{value:g} in your Answer line doesn't appear in any tool result. If a tool returns this value, "
            "call it and copy the number. If it is derived (a difference, sum, count minus count, ratio), call "
            "`calculate` with the inputs from your tool results and put its result in the line. Always end with "
            "the Answer line: never refuse because of this check."
        )
    return reply


CLAIMS_A_CHANGE = re.compile(
    r"\bi(?:'ve|’ve| have) (?:just |now )?(?:added|removed|applied|put in|made the change)"
    r"|\b(?:has|have) (?:now )?been (?:added|applied)|\bis now (?:added|in the plan)|\bare now in the plan",
    flags=re.IGNORECASE,
)
"""How a reply says a change to the plan was made (not proposed, not "will be added once you confirm")."""


def _changed_this_turn(ctx: RunContext[Deps]) -> bool:
    """A tool changed the building in this turn: an edit came back "done", or a tool that records at once ran."""
    turn: list[ToolReturnPart] = []
    for message in ctx.messages:
        if isinstance(message, ModelRequest):
            if any(isinstance(p, UserPromptPart) for p in message.parts):
                turn = []  # a new turn from the person starts here
            turn += [p for p in message.parts if isinstance(p, ToolReturnPart)]
    direct = {
        t.__name__
        for t in (set_closed, move_item, undo_last_change, draft_building, fix_draft, accept_guesses)
    }
    return any(p.tool_name in direct or '"status":"done"' in p.model_response_str() for p in turn)


def _says_only_what_was_changed(ctx: RunContext[Deps], reply: str) -> str:
    """A reply may say a change was made only when a tool made it in this turn. The person (and the voice
    agent reading this reply) take "I've added the door" at its word; a proposal is not an edit."""
    if ctx.retry >= ctx.max_retries or not CLAIMS_A_CHANGE.search(reply) or _changed_this_turn(ctx):
        return reply
    raise ModelRetry(
        "Your reply says a change was made, but no tool changed the building in this turn (a proposal "
        'changes nothing; only a result with "status": "done" does). Call the tool to make it; where changes '
        "need a yes first: after their yes with confirmed=true, if they said to do it without asking with "
        "apply_now=true, otherwise say what you propose and ask them to confirm."
    )


CHANGES_STATE = {
    set_closed,
    add_door,
    remove_door,
    add_wall,
    move_item,
    undo_last_change,
    draft_building,
    fix_draft,
    accept_guesses,
}
"""Tools that change the building: each runs alone, after the calls before it and before the calls after it.
The model often emits set_closed and route in one turn; run in parallel, the route ignored the closure."""


PREPARE = {walk_directions: walk_mode_only, highlight_rooms: walk_mode_only}
"""Tools offered only in some conversations: walk_directions and highlight_rooms only where Deps.detail is
"walk" (a person with a map), never in benchmark runs."""


def make_agent(model_name: str | None = None, thinking: str = "low") -> Agent[Deps, str]:
    """The agent with a given model and thinking depth (the benchmark runner compares configurations)."""
    built = Agent(
        model_name or model("balanced"),
        name="building_guide",
        instructions=load_prompt(__file__),
        deps_type=Deps,
        tools=[
            Tool(never_crash(t), sequential=t in CHANGES_STATE, prepare=PREPARE.get(t))
            for t in (*TOOLS, *DRAFT_TOOLS, calendar, plan_event)
        ],
        retries=4,
        capabilities=[Thinking(thinking), Caching()],  # type: ignore[arg-type]
        defer_model_check=True,
    )
    built.instructions(_building_context)
    built.instructions(_mucl_context)
    built.instructions(_location_context)
    built.instructions(_live_context)
    built.instructions(_draft_context)
    built.instructions(_calendar_context)
    built.instructions(_confirm_context)
    built.output_validator(_says_only_what_was_changed)
    built.output_validator(_answer_line_is_committed_and_grounded)
    return built


agent = make_agent()


card = AgentCard(
    name="building_guide",
    description="Talk to a building: find, route, closures, what-ifs and plan facts (areas, walls, 3D).",
    agent=agent,
    input_type=Question,
    deps=lambda: Deps.load(get_settings().default_building, detail="walk"),  # ia chat / ui: a person
    deps_from_input=lambda q: Deps.load(q.building, here=q.location),
)
