"""Walking directions people can follow (`app.building.directions`), no model.

    uv run pytest tests/building/test_directions.py -q

A synthetic floor checks the behaviour on a layout where the right answer is obvious:

    y=12 +-----+------------+
         |     | Meeting    |
         | C   D1  0.04   |       D1-D3 doors, E entrance
         | o   +-----+------+-----------+
     y=6 | r   | Print room 0.01|Office   |
         | r  D2    [PRINTER] D3  0.03  |
         | .   |                |         |
     y=0 +--E--+----------------+---------+
        x=-3   0               10        16

Meeting room 0.04 -> Office 0.03: out west into the corridor, turn LEFT (south) past the coffee machine,
turn LEFT (east) into the print room, go AROUND the printer that stands between its two doors (its centre
is north of the walk, so it stays on your left), then straight into the office. Every dev warm-up route
then checks the same contract on real buildings (start, arrive, lengths add up, no compass words).
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from app.agents.building_guide.tools import Deps, route, walk_directions
from app.building.directions import directions, walk_in_space, walk_path
from app.building.model import OUTSIDE, load_building, parse_building
from app.building.routing import RouteGraph

from .test_golden import WARMUP, call, cases, route_length_ok

COMPASS = re.compile(r"\b(north|south|east|west)(ward|wards|ern)?\b", re.IGNORECASE)
TURNS = ("straight", "left", "right", "back")


def _space(sid: str, name: str, ref: str, kind: str, room_type: str, polygon: list[list[float]]) -> dict:
    return {
        "id": sid,
        "floor_id": "T-F0",
        "type": kind,
        "room_type": room_type,
        "name": name,
        "ref": ref,
        "polygon": polygon,
        "synthetic": False,
    }


def _door(oid: str, a: str, b: str, xy: list[float], kind: str = "door") -> dict:
    return {
        "id": oid,
        "floor_id": "T-F0",
        "space_ids": [a, b],
        "kind": kind,
        "position": xy,
        "width_m": 0.9,
        "step_free": True,
        "synthetic": False,
    }


def _asset(
    aid: str, name: str, category: str, kind: str, sid: str, xy: list[float], w: float, d: float
) -> dict:
    return {
        "id": aid,
        "name": name,
        "category": category,
        "type": kind,
        "space_id": sid,
        "floor_id": "T-F0",
        "position": xy,
        "footprint": {"width_m": w, "depth_m": d, "rotation_deg": 0.0},
        "source": "generated",
    }


@pytest.fixture(scope="module")
def floor():
    raw = {
        "building": {"id": "T", "name": "Directions test floor", "source": "procedural"},
        "floors": [{"id": "T-F0", "level": 0, "name": "Ground floor", "elevation_m": 0.0, "height_m": 3.0}],
        "spaces": [
            _space(
                "T-S1",
                "Meeting room 0.04",
                "0.04",
                "room",
                "meeting_room",
                [[0, 8], [6, 8], [6, 12], [0, 12]],
            ),
            _space(
                "T-S2", "Corridor 0.02", "0.02", "corridor", "corridor", [[-3, 0], [0, 0], [0, 12], [-3, 12]]
            ),
            _space(
                "T-S3", "Print room 0.01", "0.01", "room", "print_room", [[0, 0], [10, 0], [10, 6], [0, 6]]
            ),
            _space("T-S4", "Office 0.03", "0.03", "room", "office", [[10, 0], [16, 0], [16, 6], [10, 6]]),
        ],
        "openings": [
            _door("T-D1", "T-S1", "T-S2", [0, 10]),
            _door("T-D2", "T-S2", "T-S3", [0, 3]),
            _door("T-D3", "T-S3", "T-S4", [10, 3]),
            _door("T-E", "T-S2", "outside", [-1.5, 0], kind="entrance"),
        ],
        "vertical_connectors": [],
        "assets": [
            _asset("T-A1", "Printer 0.01-A", "equipment", "printer", "T-S3", [5, 3.3], 2.0, 1.5),
            _asset("T-A2", "Coffee machine 0.02", "fixture", "coffee_machine", "T-S2", [-2.5, 6.5], 0.6, 0.6),
            _asset("T-A3", "Chair 0.03-A", "furniture", "chair", "T-S4", [13, 3], 0.5, 0.5),
        ],
    }
    b = parse_building(raw)
    route = RouteGraph(b).shortest("T-S1", "T-S4")
    assert route is not None and route.space_path == ["T-S1", "T-S2", "T-S3", "T-S4"]
    return b, route, call(directions, b, route)


def texts(steps) -> str:
    return " | ".join(s.text for s in steps)


def test_directions_go_around_the_printer(floor):
    _, _, steps = floor
    around = [s for s in steps if s.action == "around"]
    assert len(around) == 1, texts(steps)
    (step,) = around
    assert step.around == "T-A1" and step.space_id == "T-S3"
    assert "printer" in step.text.lower() and "left" in step.text.lower(), step.text


def test_directions_turn_left_twice(floor):
    _, _, steps = floor
    turns = [s.action for s in steps if s.action in TURNS]
    assert turns.count("left") == 2 and "right" not in turns, texts(steps)


def test_directions_name_a_landmark_people_notice(floor):
    _, _, steps = floor
    corridor = [s for s in steps if s.space_id == "T-S2"]
    assert any("T-A2" in s.landmarks and "coffee machine" in s.text.lower() for s in corridor), texts(steps)
    assert "chair" not in texts(steps).lower()  # a chair is no landmark


def test_directions_cover_the_whole_route(floor):
    _, route, steps = floor
    assert steps[0].action == "start" and steps[0].space_id == route.from_space_id
    assert steps[-1].action == "arrive" and steps[-1].space_id == route.to_space_id
    assert {s.space_id for s in steps} <= set(route.space_path)
    assert route_length_ok(sum(s.distance_m for s in steps), route.length_m), texts(steps)
    assert not COMPASS.search(texts(steps)), texts(steps)


@pytest.mark.parametrize("q", cases(WARMUP, "route"))
def test_directions_on_real_routes(q):
    a = q["answer"]
    if a.get("reachable") is False:
        pytest.skip("not applicable: no route to describe")
    b = load_building(q["building_id"])
    route = RouteGraph(b).shortest(a["from_space_id"], a["to_space_id"])
    assert route is not None
    steps = call(directions, b, route)
    assert steps[0].action == "start" and steps[-1].action == "arrive"
    assert route_length_ok(sum(s.distance_m for s in steps), route.length_m), texts(steps)
    assert not COMPASS.search(texts(steps)), texts(steps)
    rides = [e for e in route.element_path if e in b.connectors]
    assert len([s for s in steps if s.action in ("up", "down")]) == len(rides), texts(steps)


# ---- the walk itself (walk_in_space / walk_path): what the map draws ------------------------------------


def _rect(x: float, y: float, w: float, d: float) -> Polygon:
    return Polygon(
        [(x - w / 2, y - d / 2), (x + w / 2, y - d / 2), (x + w / 2, y + d / 2), (x - w / 2, y + d / 2)]
    )


def test_directions_walk_goes_round_the_printer(floor):
    b, route_, _ = floor
    walk = walk_in_space(b, "T-S3", b.openings["T-D2"].position, b.openings["T-D3"].position)
    assert walk[0] == b.openings["T-D2"].position and walk[-1] == b.openings["T-D3"].position
    printer = _rect(5, 3.3, 2.0, 1.5)
    assert not LineString(walk).intersects(printer), walk
    room = b.spaces["T-S3"].shape
    assert all(room.covers(Point(p)) for p in walk), walk


def test_directions_walk_path_is_continuous_through_doors(floor):
    b, route_, _ = floor
    path = walk_path(b, route_)
    assert len(path) == 1 and path[0]["floor_id"] == "T-F0", path
    points = path[0]["points"]
    assert points[0] == [3.0, 10.0] and points[-1] == [13.0, 3.0]  # representative points of start and end
    line = LineString(points)
    for door in ("T-D1", "T-D2", "T-D3"):  # passes through every door it uses, no gaps
        assert line.distance(Point(b.openings[door].position)) < 0.2, (door, points)
    rooms = unary_union([b.spaces[s].shape for s in route_.space_path])
    assert all(rooms.buffer(0.01).covers(Point(p)) for p in points), points
    assert not line.intersects(_rect(5, 3.3, 2.0, 1.5)), points


def test_directions_steps_carry_their_piece_of_the_walk(floor):
    _, _, steps = floor
    walked = [s for s in steps if s.action not in ("arrive",)]
    assert all(len(s.points) >= 2 for s in walked), texts(steps)
    for a, z in zip(walked, walked[1:], strict=False):  # each step starts where the last one ended
        assert a.points[-1] == z.points[0], (a.text, z.text)


def test_directions_turn_at_the_corner_of_an_l_corridor():
    raw = {
        "building": {"id": "L", "name": "L corridor", "source": "procedural"},
        "floors": [{"id": "T-F0", "level": 0, "name": "Ground floor", "elevation_m": 0.0, "height_m": 3.0}],
        "spaces": [
            _space("L-W", "Office 1", "1", "room", "office", [[-4, 0], [0, 0], [0, 2], [-4, 2]]),
            _space(
                "L-C",
                "Corridor 2",
                "2",
                "corridor",
                "corridor",
                [[0, 0], [10, 0], [10, 10], [8, 10], [8, 2], [0, 2]],
            ),
            _space("L-N", "Office 3", "3", "room", "office", [[8, 10], [10, 10], [10, 14], [8, 14]]),
        ],
        "openings": [_door("L-D1", "L-W", "L-C", [0, 1]), _door("L-D2", "L-C", "L-N", [9, 10])],
        "vertical_connectors": [],
        "assets": [],
    }
    b = parse_building(raw)
    route_ = RouteGraph(b).shortest("L-W", "L-N")
    steps = directions(b, route_)
    corridor = [s for s in steps if s.space_id == "L-C"]
    assert [s.action for s in corridor] == ["straight", "left"], texts(steps)
    assert route_length_ok(sum(s.distance_m for s in steps), route_.length_m)


def _real_route(q):
    a = q["answer"]
    if a.get("reachable") is False:
        pytest.skip("not applicable: no route to draw")
    b = load_building(q["building_id"])
    route_ = RouteGraph(b).shortest(a["from_space_id"], a["to_space_id"])
    assert route_ is not None
    return b, route_


@pytest.mark.parametrize("q", cases(WARMUP, "route"))
def test_directions_walk_path_on_real_routes(q):
    b, route_ = _real_route(q)
    path = call(walk_path, b, route_)
    rides = [e for e in route_.element_path if e in b.connectors or e in b.walkways]
    assert len(path) == 1 + len(rides), [p["floor_id"] for p in path]
    assert all(x["floor_id"] != y["floor_id"] for x, y in zip(path, path[1:], strict=False))
    for segment in path:
        rooms = unary_union(
            [
                b.spaces[s].shape.buffer(0)
                for s in route_.space_path
                if s != OUTSIDE and b.spaces[s].floor_id == segment["floor_id"]
            ]
        ).buffer(0.05)
        doors = [Point(b.openings[e].position) for e in route_.element_path if e in b.openings]
        outside = [  # a door can sit in the wall's thickness, between its two rooms
            p
            for p in segment["points"]
            if not rooms.covers(Point(p)) and not any(Point(p).distance(d) < 0.02 for d in doors)
        ]
        assert not outside, (segment["floor_id"], outside[:3])
    first, last = path[0]["points"][0], path[-1]["points"][-1]
    for end, sid in ((first, route_.from_space_id), (last, route_.to_space_id)):
        anchor = Polygon(b.spaces[sid].polygon).buffer(0).representative_point()
        assert Point(end).distance(anchor) < 0.02, (sid, end)


def _ctx(building_id: str) -> SimpleNamespace:
    return SimpleNamespace(deps=Deps.load(building_id))


def _first_route_question():
    return next(
        p.values[0] for p in cases(WARMUP, "route") if p.values[0]["answer"].get("reachable") is not False
    )


def test_directions_route_tool_legs_carry_the_walk():
    """In a conversation with a person (detail="walk") route legs carry the drawn walk; in the default
    room-level mode (benchmarks, the Sunday run) they don't."""
    q = _first_route_question()
    a = q["answer"]
    walk_ctx = _ctx(q["building_id"])
    walk_ctx.deps.detail = "walk"
    out = json.loads(route(walk_ctx, a["from_space_id"], a["to_space_id"]))
    for leg in out["legs"]:
        assert all(isinstance(s, str) for s in leg["steps"])
        assert leg["path"] and all(seg["floor_id"] and len(seg["points"]) >= 1 for seg in leg["path"])
    rooms = json.loads(route(_ctx(q["building_id"]), a["from_space_id"], a["to_space_id"]))
    assert all("path" not in leg for leg in rooms["legs"])


def test_directions_tool_only_offered_in_walk_mode():
    """walk_directions is hidden from the model unless the conversation is detail="walk"."""
    import asyncio

    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    from app.agents.building_guide.agent import agent
    from app.agents.building_guide.tools import Deps

    offered: list[set[str]] = []

    def model(messages, info: AgentInfo) -> ModelResponse:
        offered.append({t.name for t in info.function_tools})
        return ModelResponse(parts=[TextPart("ok")])

    q = _first_route_question()
    for detail in ("rooms", "walk"):
        with agent.override(model=FunctionModel(model)):
            asyncio.run(agent.run("hi", deps=Deps.load(q["building_id"], detail=detail)))
    rooms, walk = offered
    assert "route" in rooms and "walk_directions" not in rooms
    assert "walk_directions" in walk


def test_directions_walk_directions_legs_have_steps_and_path():
    q = _first_route_question()
    a = q["answer"]
    out = json.loads(walk_directions(_ctx(q["building_id"]), a["from_space_id"], a["to_space_id"]))
    b = load_building(q["building_id"])
    for leg in out["legs"]:
        assert {"length_m", "space_path", "steps", "path"} <= set(leg)
        assert leg["path"] and all(seg["floor_id"] for seg in leg["path"])
        for step in leg["steps"]:
            assert {
                "action",
                "text",
                "space_id",
                "floor_id",
                "distance_m",
                "landmarks",
                "around",
                "points",
            } <= set(step)
            if step["space_id"] in b.spaces:
                assert step["floor_id"] == b.spaces[step["space_id"]].floor_id
