"""building_guide: Synera track baseline. Talk to a building: find, route, react to closures, add doors.

A port of the kit's starter agent (synera-kit/starter_agent) onto our stack, so it gets `ia chat`,
`ia ui`, the API, Langfuse traces and `ia eval` for free. Same tools, same prompt, so it starts at the
kit's baseline: right on the warm-up questions, under 5% on the scored benchmark, whose questions need
plan facts (room areas, door widths, reachability, stacking, shared walls) the tools don't expose yet.
That gap is where the work is.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.capabilities import Caching, Thinking

from app.core import AgentCard, load_prompt
from app.models import model
from app.settings import get_settings

from .tools import TOOLS, Deps


class Question(BaseModel):
    building: str = Field(
        description="Building id (e.g. proc-factory-dev-003) or path to a building directory"
    )
    question: str


agent = Agent(
    model("smart"),
    name="building_guide",
    instructions=load_prompt(__file__),
    deps_type=Deps,
    tools=TOOLS,
    capabilities=[Thinking("medium"), Caching()],
    defer_model_check=True,
)


@agent.instructions
def building_overview_context(ctx: RunContext[Deps]) -> str:
    return "Building overview:\n" + json.dumps(ctx.deps.map.overview(), ensure_ascii=False)


card = AgentCard(
    name="building_guide",
    description="Synera baseline: answers find / route / closure / edit questions about one building.",
    agent=agent,
    input_type=Question,
    deps=lambda: Deps.load(get_settings().default_building),
    deps_from_input=lambda q: Deps.load(q.building),
)
