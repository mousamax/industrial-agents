"""Level 4 by conversation: the agent's load_floor_plans tool sets up a building from plans, no model.

    uv run pytest tests/building/test_chat_build.py -q

The brief asks to "create or update a building from floor plans, by conversation" in minutes. The
drafting itself is checked in test_build.py; this checks the conversation around it: the tool is the
agent's, it shows what it understood before anything changes, and after a yes every other tool works on
the new building.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.agents.building_guide.tools import TOOLS, Deps, find, load_floor_plans, route
from app.building.model import building_dir, load_building

from .test_golden import STRICT, call

FACTORY = "proc-factory-dev-003"
START = "proc-campus-dev-000"  # the conversation starts on another building


def ctx(deps: Deps, run_id: str) -> SimpleNamespace:
    return SimpleNamespace(deps=deps, run_id=run_id)


def plans(suffix: str) -> list[str]:
    return [str(p) for p in sorted(building_dir(FACTORY).glob(f"floor_*{suffix}"))]


def test_chat_build_is_an_agent_tool():
    if load_floor_plans not in TOOLS:
        message = "not implemented yet: add load_floor_plans to TOOLS once it works"
        pytest.fail(message) if STRICT else pytest.skip(message)


@pytest.mark.parametrize("suffix", [".svg", "_plain.png"])
def test_chat_build_shows_what_it_understood_first(suffix):
    deps = Deps.load(START)
    got = json.loads(call(load_floor_plans, ctx(deps, "turn-1"), plans(suffix), name="New hall"))
    truth = load_building(FACTORY)
    assert deps.building.id == load_building(START).id  # nothing switched yet
    assert len(got["floors"]) == len(truth.floors), got
    assert "guesses" in got and got["status"].startswith("proposed"), got


@pytest.mark.parametrize("suffix", [".svg", "_plain.png"])
def test_chat_build_after_yes_every_tool_works_on_it(suffix):
    deps = Deps.load(START)
    call(load_floor_plans, ctx(deps, "turn-1"), plans(suffix), name="New hall")
    call(load_floor_plans, ctx(deps, "turn-2"), plans(suffix), name="New hall", confirmed=True)
    b = deps.building
    assert b.name == "New hall"
    rooms = [s for s in b.spaces.values() if s.type in ("room", "hall")]
    assert len(rooms) >= 2
    walk = json.loads(route(ctx(deps, "turn-3"), rooms[0].id, rooms[-1].id))
    assert walk["reachable"], walk
    assert json.loads(find(ctx(deps, "turn-3"), rooms[0].name))["rooms_total"] >= 1
