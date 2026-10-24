"""building_guide: talk to a building. Find, route, react to closures, simulate changes, plan facts.

One agent, small typed tools on the shared domain interfaces (tools.py), the building's vocabulary as
cached context (floors, room and item types, lifts, entrances), and a code check on the benchmark's
"Answer:" line. Every number comes from a tool; the model chooses tools and writes the reply.
"""

from __future__ import annotations

import json

from benchmark.score import committed_answer, numbers_in
from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.capabilities import Caching, Thinking
from pydantic_ai.messages import ModelRequest, ToolReturnPart

from app.core import AgentCard, load_prompt
from app.models import model
from app.settings import get_settings

from .drafting import DRAFT_TOOLS, draft_of
from .room_calendar import calendar, calendar_context
from .tools import TOOLS, Deps, _ago, _clock, _label, overview


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
    """The clock, and whether anyone recorded changes, so "yesterday" and "what changed?" have a reference."""
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


def _answer_line_is_committed_and_grounded(ctx: RunContext[Deps], reply: str) -> str:
    """When asked for an 'Answer:' line: there is one, it holds one number, and that number came from a
    tool. The scorer reads only that line, so this is where a reply wins or loses. Parsing is the scorer's
    own (benchmark.score), so what passes here is read the same way there.

    On the last retry the reply goes through as it is: a best effort can still score, a crash never does."""
    prompt = ctx.prompt if isinstance(ctx.prompt, str) else ""
    if "Answer:" not in prompt or ctx.retry >= ctx.max_retries:
        return reply
    line = committed_answer(reply)
    if line is None:
        raise ModelRetry("End your reply with the line 'Answer: ...' in the form the question asks for.")
    numbers = numbers_in(line)
    if "no route" in prompt and "no route" in line.casefold() and not numbers:
        return reply
    room_answer = "<room name or number>" in prompt
    wants_number = "<number>" in prompt or "<area>" in prompt
    if wants_number and not room_answer and len(numbers) != 1:
        raise ModelRetry(f"The Answer line must hold exactly one number, got {line!r}.")
    if "<area>" in prompt and "m²" not in line and "m2" not in line:
        raise ModelRetry("Give the room, then its area in m², e.g. 'Answer: Kitchen 1.14, 28.0 m²'.")
    value = numbers[-1] if numbers else None
    if wants_number and value is not None and not _grounded(value, _tool_numbers(ctx)):
        raise ModelRetry(
            f"{value:g} in your Answer line doesn't appear in any tool result. Don't calculate: call the tool "
            "that returns this number and copy it."
        )
    return reply


def make_agent(model_name: str | None = None, thinking: str = "low") -> Agent[Deps, str]:
    """The agent with a given model and thinking depth (the benchmark runner compares configurations)."""
    built = Agent(
        model_name or model("balanced"),
        name="building_guide",
        instructions=load_prompt(__file__),
        deps_type=Deps,
        tools=[*TOOLS, *DRAFT_TOOLS, calendar],
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
    built.output_validator(_answer_line_is_committed_and_grounded)
    return built


agent = make_agent()


card = AgentCard(
    name="building_guide",
    description="Talk to a building: find, route, closures, what-ifs and plan facts (areas, walls, 3D).",
    agent=agent,
    input_type=Question,
    deps=lambda: Deps.load(get_settings().default_building),
    deps_from_input=lambda q: Deps.load(q.building, here=q.location),
)
