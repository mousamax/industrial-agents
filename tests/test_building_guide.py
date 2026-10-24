"""building_guide behaviour that isn't a golden answer: search, scopes, arithmetic, two-turn edits, and
never crashing on a reply that fails the answer check. No model calls."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic_ai import ModelRetry
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.agents.building_guide.agent import make_agent
from app.agents.building_guide.tools import (
    Deps,
    add_door,
    calculate,
    find,
    list_rooms,
    move_item,
    route,
    undo_last_change,
)

FACTORY, CAMPUS = "proc-factory-dev-003", "proc-campus-dev-000"


def ctx(deps: Deps, run_id: str = "turn-1") -> SimpleNamespace:
    return SimpleNamespace(deps=deps, run_id=run_id)


def call(tool, c, *args, **kwargs) -> dict:
    return json.loads(tool(c, *args, **kwargs))


def test_find_matches_words_not_substrings():
    got = call(find, ctx(Deps.load(FACTORY)), "press")
    assert set(got["items_by_type"]) == {"drill_press"}  # not the air compressor


def test_find_says_plainly_when_nothing_matches():
    got = call(find, ctx(Deps.load(FACTORY)), "swimming pool")
    assert got["found"] == 0


def test_list_rooms_scope_includes_halls_when_asked():
    c = ctx(Deps.load(FACTORY))
    rooms = call(list_rooms, c, "0")["total_area_m2"]
    with_halls = call(list_rooms, c, "0", scope="rooms_and_halls")["total_area_m2"]
    assert with_halls > rooms * 2  # factory halls dominate the ground floor


def test_calculate_is_exact_and_safe():
    assert call(calculate, ctx(Deps.load(FACTORY)), "41.37 - 37.57")["result"] == pytest.approx(3.8)
    with pytest.raises(ModelRetry):
        calculate(ctx(Deps.load(FACTORY)), "__import__('os').system('ls')")


def test_edits_need_a_yes_in_a_later_turn():
    deps = Deps.load(CAMPUS)
    pair = ("Seminar room 2.06", "Kitchen 2.07")
    proposed = call(add_door, ctx(deps, "turn-1"), *pair)
    assert proposed["status"].startswith("proposed") and not deps.state.edits
    with pytest.raises(ModelRetry):  # same turn: the person hasn't answered
        add_door(ctx(deps, "turn-1"), *pair, confirmed=True)
    done = call(add_door, ctx(deps, "turn-2"), *pair, confirmed=True)
    assert done["status"] == "done" and len(deps.state.edits) == 1
    walk = call(route, ctx(deps, "turn-3"), "Seminar room 2.06", "Kitchen 2.07")
    assert len(walk["legs"][0]["space_path"]) == 2  # straight through the new door
    call(undo_last_change, ctx(deps, "turn-4"))
    assert not deps.state.edits


def test_confirming_without_a_proposal_is_refused():
    deps = Deps.load(FACTORY)
    with pytest.raises(ModelRetry):
        move_item(ctx(deps, "turn-2"), "CNC mill H-A1-B", "Warehouse zone H-C2", confirmed=True)


async def test_a_reply_that_keeps_failing_the_answer_check_is_returned_not_crashed():
    calls = []

    def stubborn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append(1)
        return ModelResponse(parts=[TextPart("I think it's about 12 m.\nAnswer: 12")])

    agent = make_agent()
    with agent.override(model=FunctionModel(stubborn)):
        result = await agent.run("How far? Finish with a line 'Answer: <number>'.", deps=Deps.load(FACTORY))
    assert result.output.endswith("Answer: 12")
    assert len(calls) == 5  # the check still asked four times for a grounded number before giving way


def test_location_resolves_and_reaches_the_context():
    from app.agents.building_guide.agent import _location_context

    deps = Deps.load("osm-way37850565", here="Büro 0.10 (Ground floor)")
    assert deps.here == "osm-way37850565-F0-S010"
    text = _location_context(SimpleNamespace(deps=deps))
    assert "Büro 0.10 (Ground floor)" in text and "osm-way37850565-F0-S010" in text
    assert _location_context(SimpleNamespace(deps=Deps.load("osm-way37850565"))) == ""
