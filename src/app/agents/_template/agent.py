"""__NAME__: one-line description of what this agent does.

Copied from agents/_template by `ia new __NAME__`. Checklist:
1. Write the job in prompt.md.
2. Shape the output in Output (structured output is the agent's API: be strict).
3. Add tools below (or reuse app.tools).
4. Add 3+ cases to evals.yaml, then iterate with `ia eval __NAME__`.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.capabilities import Caching, Thinking

from app.core import AgentCard, load_prompt
from app.models import model


class Input(BaseModel):
    text: str = Field(description="What the agent works on")


class Output(BaseModel):
    answer: str
    confidence: float = Field(ge=0, le=1)


@dataclass
class Deps:
    """Per-run dependencies: API clients, loaded data, the current user..."""


agent = Agent(
    model("smart"),
    name="__NAME__",
    instructions=load_prompt(__file__),
    deps_type=Deps,
    output_type=Output,
    capabilities=[Thinking("low"), Caching()],
    defer_model_check=True,
)


@agent.tool
async def example_tool(ctx: RunContext[Deps], query: str) -> str:
    """Describe when the model should call this tool: the docstring IS the tool description."""
    return f"no data for {query!r} yet"


card = AgentCard(
    name="__NAME__",
    description="One line shown in `ia list`, the API and the demo UI.",
    agent=agent,
    input_type=Input,
    deps=Deps,
)
