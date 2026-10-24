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

import re

import pytest

from app.building.directions import directions
from app.building.model import load_building, parse_building
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
