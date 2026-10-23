"""The one contract every agent and flow follows.

A *card* is what the rest of the repo (CLI, API, chat UI, evals) knows about a unit of work:
its name, its input type, and an async `run(inputs) -> output`. Build your agent however you like,
then wrap it in a card. Nothing else needs to change for it to show up everywhere.

- `AgentCard`: a single pydantic-ai `Agent` (gets chat UI + streaming for free).
- `FlowCard`: any async function, usually orchestrating several agents with plain Python.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, TypeAdapter
from pydantic_ai import Agent, format_as_xml

from app import observability


def load_prompt(module_file: str, name: str = "prompt.md") -> str:
    """Read a prompt that lives next to the agent: `load_prompt(__file__)`.

    Prompts are markdown files so they diff cleanly in PRs and anyone on the team can edit them.
    """
    return (Path(module_file).parent / name).read_text()


def to_prompt(inputs: Any) -> str:
    """Strings go in as-is; structured inputs are rendered as XML, which Claude reads reliably."""
    if isinstance(inputs, str):
        return inputs
    if isinstance(inputs, BaseModel):
        inputs = inputs.model_dump(mode="json", exclude_none=True)
    return format_as_xml(inputs, root_tag="input")


@dataclass(kw_only=True)
class AgentCard[InputT, DepsT, OutputT]:
    name: str
    description: str
    agent: Agent[DepsT, OutputT]
    input_type: type[InputT] = str  # type: ignore[assignment]
    deps: Callable[[], DepsT] | None = None
    """Builds fresh deps per run (DB handles, API clients, loaded data...)."""

    def coerce(self, inputs: Any) -> InputT:
        return TypeAdapter(self.input_type).validate_python(inputs)

    async def run(self, inputs: InputT | dict[str, Any] | str, *, model: str | None = None) -> OutputT:
        result = await self.agent.run(
            to_prompt(self.coerce(inputs)),
            deps=self.deps() if self.deps else None,  # type: ignore[arg-type]
            model=model,
        )
        return result.output


@dataclass(kw_only=True)
class FlowCard[InputT, OutputT]:
    name: str
    description: str
    fn: Callable[..., Awaitable[OutputT]]
    """`async def fn(inputs: InputT, *, model: str | None = None) -> OutputT`"""
    input_type: type[InputT] = str  # type: ignore[assignment]

    def coerce(self, inputs: Any) -> InputT:
        return TypeAdapter(self.input_type).validate_python(inputs)

    async def run(self, inputs: InputT | dict[str, Any] | str, *, model: str | None = None) -> OutputT:
        inputs = self.coerce(inputs)
        with observability.flow_span(self.name, inputs) as span:
            output = await self.fn(inputs, model=model)
            observability.set_output(span, output)
        return output


Card = AgentCard[Any, Any, Any] | FlowCard[Any, Any]
