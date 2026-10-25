"""building_guide tools against Synera's golden answers, no model: if the agent picks the right tool with
the right arguments (the question's `inputs`), the tool's result holds the right answer.

    uv run pytest tests/building/test_agent_tools.py -q                 # dev, 6 per kind
    GOLDEN_SAMPLE=all GOLDEN_SPLIT=train uv run pytest tests/building/test_agent_tools.py -q
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.agents.building_guide.tools import (
    Deps,
    _label,
    distance_3d,
    list_rooms,
    nearest,
    reachable_rooms,
    room_details,
    route,
    what_if,
)
from app.building.model import load_building

from .test_golden import BENCHMARK, WARMUP, cases, close_to, route_length_ok


def ctx(building_id: str) -> SimpleNamespace:
    return SimpleNamespace(deps=Deps.load(building_id))


def run(tool, q, *args, **kwargs) -> dict:
    return json.loads(tool(ctx(q["building_id"]), *args, **kwargs))


def level(q, floor_id: str) -> str:
    return str(load_building(q["building_id"]).floors[floor_id].level)


@pytest.mark.parametrize("q", cases(BENCHMARK, "largest_room"))
def test_largest_room(q):
    i, a = q["inputs"], q["answer"]
    top = run(list_rooms, q, level(q, i["floor_id"]), i["room_type"])["rooms"][0]
    assert any(name in top["room"] for name in a["accept"]), top
    assert close_to(top["area_m2"], a["area"])


@pytest.mark.parametrize("q", cases(BENCHMARK, "floor_area_total"))
def test_floor_area_total(q):
    i = q["inputs"]
    assert close_to(run(list_rooms, q, level(q, i["floor_id"]), i["room_type"])["total_area_m2"], q["answer"])


@pytest.mark.parametrize("q", cases(BENCHMARK, "shared_walls_count"))
def test_shared_walls_count(q):
    assert run(room_details, q, q["inputs"]["start"])["shares_a_wall_with"]["count"] == q["answer"]["value"]


@pytest.mark.parametrize("q", cases(BENCHMARK, "room_stacked"))
def test_room_stacked(q):
    i = q["inputs"]
    got = run(room_details, q, i["start"])[f"directly_{i['direction']}"]
    assert got == _label(load_building(q["building_id"]), q["answer"]["space_id"])


@pytest.mark.parametrize("q", cases(BENCHMARK, "straight_line_3d"))
def test_straight_line_3d(q):
    i = q["inputs"]
    assert close_to(run(distance_3d, q, i["first"], i["second"])["distance_m"], q["answer"])


@pytest.mark.parametrize("q", cases(BENCHMARK, "errand"))
def test_errand(q):
    i, a = q["inputs"], q["answer"]
    b = load_building(q["building_id"])
    pickup = run(nearest, q, i["start"], item_type=i["asset_type"])["nearest"][0]["id"]
    assert pickup == a["via"], b.label(pickup)
    target = run(list_rooms, q, level(q, i["floor_id"]), i["room_type"])["rooms"][0]["id"]
    assert target == a["target"], b.label(target)
    assert close_to(run(route, q, i["start"], target, via=[pickup])["total_length_m"], a)


@pytest.mark.parametrize("q", cases(WARMUP, "route_step_free"))
def test_route_tool_step_free(q):
    """The route tool with step_free=True: the golden length, end to end, and nothing with a step on it."""
    a, b = q["answer"], load_building(q["building_id"])
    got = run(route, q, a["from_space_id"], a["to_space_id"], step_free=True)
    if a.get("reachable") is False:
        assert got["reachable"] is False, got
        return
    assert got["reachable"] and route_length_ok(got["total_length_m"], a["optimal_length_m"]), got
    (leg,) = got["legs"]
    assert leg["space_path"][0] == a["from_space_id"] and leg["space_path"][-1] == a["to_space_id"]
    for element in leg["element_path"]:
        thing = b.openings.get(element) or b.connectors.get(element)
        assert thing is not None and thing.step_free, f"{element} is not step-free"


@pytest.mark.parametrize("q", cases(BENCHMARK, "wide_delivery_count"))
def test_wide_delivery_count(q):
    i = q["inputs"]
    got = run(
        reachable_rooms, q, i["start"], level(q, i["floor_id"]), step_free=True, min_width_m=i["min_width"]
    )
    assert got["reachable_count"] == q["answer"]["value"]


@pytest.mark.parametrize("q", cases(BENCHMARK, "step_free_reachable_count"))
def test_step_free_reachable_count(q):
    i = q["inputs"]
    got = run(reachable_rooms, q, i["start"], level(q, i["floor_id"]), step_free=True, closed=[i["closed"]])
    assert got["reachable_count"] == q["answer"]["value"]


@pytest.mark.parametrize("q", cases(BENCHMARK, "what_if_door"))
def test_what_if_door(q):
    i = q["inputs"]
    got = run(what_if, q, level(q, i["floor_id"]), add_door_between=[i["first"], i["second"]])
    assert got["shorter_by_more_than_1_m"] == q["answer"]["value"]
