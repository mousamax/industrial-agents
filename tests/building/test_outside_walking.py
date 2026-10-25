"""Walking round the outside (Constraints.outside_walking): only for the app's chat and voice sessions.

    uv run pytest tests/building/test_outside_walking.py -q

Off everywhere we're scored (the kit and Synera's rule: no route round the outside). On, a route may leave
through one entrance, walk round the building's outline and come back in through another, when that's
shorter. The outside is never a room: it only ever shows up as OUTSIDE in a route's space_path.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.agents.building_guide.tools import Deps, find, list_rooms, nearest, walk_directions
from app.agents.building_guide.tools import route as route_tool
from app.building.directions import directions, walk_path
from app.building.model import OUTSIDE, Building, parse_building
from app.building.routing import WALKING, Constraints, RouteGraph, outside_walks
from app.building.state import BuildingState

OUT = Constraints(outside_walking=True)


def _room(sid: str, name: str, polygon: list[list[float]], floor: str = "F0") -> dict:
    return {"id": sid, "floor_id": floor, "type": "room", "room_type": "office", "name": name, "ref": "",
            "polygon": polygon, "synthetic": False}  # fmt: skip


def _door(oid: str, a: str, z: str, xy: list[float], floor: str = "F0") -> dict:
    return {"id": oid, "floor_id": floor, "space_ids": [a, z], "kind": "entrance" if OUTSIDE in (a, z) else "door",
            "position": xy, "width_m": 0.9, "step_free": True, "synthetic": False}  # fmt: skip


def _building(spaces: list[dict], openings: list[dict], floors: list[dict] | None = None) -> Building:
    return parse_building({
        "building": {"id": "t", "name": "Test", "source": "test"},
        "floors": floors or [{"id": "F0", "level": 0, "name": "Ground floor", "elevation_m": 0.0, "height_m": 3.0}],
        "spaces": spaces, "openings": openings, "vertical_connectors": [], "assets": [],
    })  # fmt: skip


def u_building() -> Building:
    """A U: two 30 m arms (each a top and a bottom room) joined by a corridor at the bottom. The two top
    rooms have entrances facing each other's way across the open mouth of the U: 8 m apart outside, about
    50 m apart inside."""
    spaces = [
        _room("AT", "Office A", [[0, 20], [2, 20], [2, 30], [0, 30]]),
        _room("AB", "Store A", [[0, 0], [2, 0], [2, 20], [0, 20]]),
        _room("C", "Corridor", [[2, 0], [8, 0], [8, 2], [2, 2]]),
        _room("BB", "Store B", [[8, 0], [10, 0], [10, 20], [8, 20]]),
        _room("BT", "Office B", [[8, 20], [10, 20], [10, 30], [8, 30]]),
    ]
    openings = [
        _door("D1", "AT", "AB", [1, 20]),
        _door("D2", "AB", "C", [2, 1]),
        _door("D3", "C", "BB", [8, 1]),
        _door("D4", "BB", "BT", [9, 20]),
        _door("EA", "AT", OUTSIDE, [1, 30]),
        _door("EB", "BT", OUTSIDE, [9, 30]),
    ]
    return _building(spaces, openings)


def test_outside_route_between_entrances_only_when_switched_on():
    b = u_building()
    inside = RouteGraph(b).shortest("AT", "BT")
    outside = RouteGraph(b, OUT).shortest("AT", "BT")
    assert inside is not None and outside is not None
    assert OUTSIDE not in inside.space_path
    assert inside.space_path == ["AT", "AB", "C", "BB", "BT"]
    assert outside.space_path == ["AT", OUTSIDE, "BT"]
    assert outside.element_path == ["EA", "EB"]
    assert outside.length_m < inside.length_m - 20
    assert WALKING.outside_walking is False  # the default everywhere


def test_outside_walk_is_not_taken_when_inside_is_shorter():
    b = u_building()
    assert RouteGraph(b, OUT).shortest("AB", "C") == RouteGraph(b).shortest("AB", "C")
    assert RouteGraph(b, OUT).shortest("AT", OUTSIDE) == RouteGraph(b).shortest("AT", OUTSIDE)


def test_outside_walk_follows_the_outline_round_a_corner():
    # a 10 x 10 m room, entrances in the middle of the bottom and the right wall: 7.07 m straight through
    # the building, 10.6 m round the corner 15 cm off the walls (plus the 15 cm step out at each door)
    b = _building(
        [_room("S", "Hall", [[0, 0], [10, 0], [10, 10], [0, 10]])],
        [_door("E1", "S", OUTSIDE, [5, 0]), _door("E2", "S", OUTSIDE, [10, 5])],
    )
    length, points = outside_walks(b)[("E1", "E2")]
    assert length == pytest.approx(10.6, abs=0.02)
    assert any(x == pytest.approx(10.15, abs=0.01) and y == pytest.approx(-0.15, abs=0.01) for x, y in points)


def test_outside_walk_never_joins_separate_buildings():
    # two buildings side by side (two ground floors, no walkway): no free way round the outside
    floors = [
        {"id": "F0", "level": 0, "name": "Ground floor", "elevation_m": 0.0, "height_m": 3.0},
        {"id": "G0", "level": 0, "name": "Ground floor B", "elevation_m": 0.0, "height_m": 3.0},
    ]
    b = _building(
        [_room("A", "Office A", [[0, 0], [4, 0], [4, 4], [0, 4]]),
         _room("B", "Office B", [[6, 0], [10, 0], [10, 4], [6, 4]], "G0")],
        [_door("EA", "A", OUTSIDE, [4, 2]), _door("EB", "B", OUTSIDE, [6, 2], "G0")],
        floors,
    )  # fmt: skip
    assert outside_walks(b) == {}
    assert RouteGraph(b, OUT).shortest("A", "B") is None


def test_outside_is_never_a_room():
    b = u_building()
    graph = RouteGraph(b, OUT)
    assert set(graph.distances_from(["AT"])) <= set(b.spaces)
    assert graph.reachable("AT") <= set(b.spaces)
    ctx = SimpleNamespace(deps=Deps(BuildingState(b), outside_walking=True))
    for scope in ("rooms", "all_spaces"):
        listed = json.loads(list_rooms(ctx, "0", scope=scope))
        assert OUTSIDE not in json.dumps(listed).casefold(), listed
    assert OUTSIDE not in json.dumps(json.loads(find(ctx, "office"))).casefold()
    near = json.loads(nearest(ctx, "AT", room_type="office"))
    assert {r["id"] for r in near["nearest"]} <= set(b.spaces) and OUTSIDE not in near["highlight"]
    assert near["rooms_in_all"] == len(b.spaces)


def test_outside_route_in_the_tools_and_directions():
    b = u_building()
    ctx = SimpleNamespace(deps=Deps(BuildingState(b), detail="walk", outside_walking=True))
    leg = json.loads(route_tool(ctx, "Office A", "Office B"))["legs"][0]
    assert leg["space_path"] == ["AT", OUTSIDE, "BT"], leg
    assert any("walk around the building" in step for step in leg["steps"])
    route = RouteGraph(b, OUT).shortest("AT", "BT")
    steps = directions(b, route)
    text = " ".join(s.text for s in steps)
    assert "Go outside through the entrance of Office A, walk" in text
    assert "around the building to the entrance of Office B" in text
    assert sum(s.distance_m for s in steps) == pytest.approx(route.length_m, abs=0.01)
    drawn = walk_path(b, route)
    assert len(drawn) == 1 and drawn[0]["points"][0][1] > 20 and max(p[1] for p in drawn[0]["points"]) > 30
    spoken = json.loads(walk_directions(ctx, "Office A", "Office B"))["legs"][0]["steps"]
    assert [s["space_id"] for s in spoken] == ["AT", OUTSIDE, "BT", "BT"]
    assert "walk 8 m around the building" in spoken[1]["text"]
    off = SimpleNamespace(deps=Deps(BuildingState(b)))
    assert json.loads(route_tool(off, "Office A", "Office B"))["legs"][0]["space_path"] == [
        "AT",
        "AB",
        "C",
        "BB",
        "BT",
    ]


def test_switch_is_off_for_everything_scored_and_on_for_app_sessions(tmp_path):
    from app.agents.building_guide.sunday import deps_for
    from app.settings import get_settings

    assert Deps(BuildingState(u_building())).outside_walking is False
    kit = get_settings().synera_kit_dir / "data" / "dev"
    if not kit.exists():
        pytest.skip("synera-kit data missing")
    building_dir = next(p for p in sorted(kit.iterdir()) if (p / "building.json").exists())
    assert Deps.load(building_dir).outside_walking is False  # bench.py and the agent card
    assert deps_for(building_dir, {"id": "q", "text": "?"}).outside_walking is False  # the Sunday run
    from app.api import main

    session = main._new_session(building_dir.name, "chat-1")  # noqa: SLF001
    assert session.outside_walking is True and session.detail == "walk"


def test_switch_off_keeps_the_first_day_coffee_answers():
    """The `first-day-coffee` scenario's tool outputs with the switch off (as in every scenario): from the
    street, Kitchen 0.31 is nearest, in through Office corridor 0.16; from the corridor itself, Kitchen 0.28.
    A scenario's Deps never turns outside walking on."""
    from app.agents.building_guide.scenarios import SCENARIOS
    from app.settings import get_settings

    if not (get_settings().synera_kit_dir / "data" / "dev" / "proc-factory-dev-003").exists():
        pytest.skip("synera-kit data missing")
    import yaml

    spec = next(
        s for s in yaml.safe_load(SCENARIOS.read_text())["scenarios"] if s["name"] == "first-day-coffee"
    )
    deps = Deps.load(spec["building"])
    deps.detail = "walk"
    assert deps.outside_walking is False
    ctx = SimpleNamespace(deps=deps, run_id="r", prompt="")
    k31, k28, corridor = (f"proc-factory-dev-003-F0-S0{n}" for n in ("31", "28", "16"))

    street = json.loads(nearest(ctx, "outside", "coffee_machine"))["nearest"]
    assert [(r["id"], r["walk_m"]) for r in street[:2]] == [(k31, 12.51), (k28, 28.54)]
    walk = json.loads(walk_directions(ctx, "outside", k31))
    assert walk["total_length_m"] == 12.51 and walk["legs"][0]["space_path"] == [OUTSIDE, corridor, k31]

    inside = json.loads(nearest(ctx, "Office corridor 0.16", "coffee_machine"))["nearest"]
    assert [(r["id"], r["walk_m"]) for r in inside[:2]] == [(k28, 12.0), (k31, 28.03)]
    there = json.loads(route_tool(ctx, "Office corridor 0.16", "Kitchen 0.31"))
    assert there["total_length_m"] == 28.03 and OUTSIDE not in there["legs"][0]["space_path"]
