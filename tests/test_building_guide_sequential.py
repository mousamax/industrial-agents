"""Tools that change the building halt the turn: nothing else runs until the change is recorded.

Seen on Haiku (data/extra_tricky xt-routing_rules-03): set_closed and route emitted in one turn ran in
parallel, and the route went through the hall that had just been closed.
"""

from __future__ import annotations

import time

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.agents.building_guide.agent import make_agent
from app.agents.building_guide.tools import Deps
from app.building.state import BuildingState

CHANGES = {
    "set_closed",
    "add_door",
    "move_item",
    "undo_last_change",
    "draft_building",
    "fix_draft",
    "accept_guesses",
}


def test_tools_that_change_the_building_run_alone():
    tools = make_agent("test")._function_toolset.tools
    assert CHANGES <= set(tools)
    assert {name for name, tool in tools.items() if tool.sequential} == CHANGES


def test_a_route_in_the_same_turn_waits_for_a_slow_closure(monkeypatch):
    """The closure takes 0.5 s before it is recorded; a route emitted after it in the same turn must still
    see it (without the barrier it runs meanwhile and walks through the closed hall)."""
    apply = BuildingState.apply

    def slow_apply(self, *args, **kwargs):
        time.sleep(0.5)  # outside the state's lock, so only the tool barrier can make route wait
        return apply(self, *args, **kwargs)

    monkeypatch.setattr(BuildingState, "apply", slow_apply)

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart("set_closed", {"element": "Hall access 0.22"}),
                    ToolCallPart(
                        "route",
                        {"start": "Meeting room 0.26 (Ground floor)", "destination": "Warehouse zone H-C3"},
                    ),
                ]
            )
        return ModelResponse(parts=[TextPart("done")])

    agent = make_agent("test")
    with agent.override(model=FunctionModel(model)):
        result = agent.run_sync(
            "Hall access 0.22 is closed. Route me.", deps=Deps.load("proc-factory-dev-005")
        )
    returns = {
        p.tool_name: str(p.content)
        for m in result.all_messages()
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, ToolReturnPart)
    }
    assert "Closed Hall access 0.22" in returns["set_closed"]
    assert '"reachable":false' in returns["route"], returns["route"][:300]
