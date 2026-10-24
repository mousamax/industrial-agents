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

from .tools import TOOLS, Deps, overview


class Question(BaseModel):
    building: str = Field(
        description="Building id (e.g. proc-factory-dev-003) or path to a building directory"
    )
    question: str


def _building_context(ctx: RunContext[Deps]) -> str:
    return "Building overview:\n" + json.dumps(overview(ctx.deps.building), ensure_ascii=False)


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
        tools=TOOLS,
        retries=4,
        capabilities=[Thinking(thinking), Caching()],  # type: ignore[arg-type]
        defer_model_check=True,
    )
    built.instructions(_building_context)
    built.output_validator(_answer_line_is_committed_and_grounded)
    return built


agent = make_agent()


card = AgentCard(
    name="building_guide",
    description="Talk to a building: find, route, closures, what-ifs and plan facts (areas, walls, 3D).",
    agent=agent,
    input_type=Question,
    deps=lambda: Deps.load(get_settings().default_building),
    deps_from_input=lambda q: Deps.load(q.building),
)
