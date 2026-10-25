"""Spaces marked `"through": false` are destinations (Synera, Sun 25 Oct; docs/synera/ROUTE_SCORING.md): a leg
may start or end in one, never pass through one. Only the flag decides it, never the room type. Checked on
the hand-verified extra-tricky cases (data/extra_tricky, `extra.reasoning`) that run on `-xt-nothrough`
copies of kit factories, where every production, assembly and additive zone carries the flag; their
expected answers were computed by closing those zones except the leg's ends. No model calls.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from app.building import queries
from app.building.model import load_building
from app.building.routing import FORKLIFT, WALKING, RouteGraph, validate_route

ROOT = Path(__file__).resolve().parents[2]
TRICKY = ROOT / "data/extra_tricky"

ROUTE_CASES = [
    "xt-routing_rules-01",
    "xt-routing_rules-02",
    "xt-routing_rules-03",
    "xt-routing_rules-04",
    "xt-routing_rules-05",
    "xt-routing_rules-09",
    "xt-adversarial_language-05",
]


def _case(case_id: str) -> dict:
    for path in TRICKY.glob("*/questions.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if f'"{case_id}"' in line:
                q = json.loads(line)
                if q["id"] == case_id:
                    return q
    pytest.skip(f"{case_id} not in data/extra_tricky")


def _building(q: dict):
    bid = q["building_id"]
    for folder in (ROOT / "synera-kit/data/dev" / bid, ROOT / "synera-kit/data/train" / bid, TRICKY / bid):
        if (folder / "building.json").exists():
            return load_building(folder)
    pytest.skip(f"building {bid} not found")


def _bays_inside(b, path: list[str]) -> list[str]:
    return [s for s in path[1:-1] if s in b.spaces and not b.spaces[s].through]


@pytest.mark.parametrize("case_id", ROUTE_CASES)
def test_route_never_passes_through_a_bay(case_id):
    q = _case(case_id)
    b, i, a = _building(q), q["inputs"], q["answer"]
    start = i.get("from_space_id") or i["start"]
    goal = i.get("to_space_id") or i["goal"]
    c = FORKLIFT if i.get("constraints") == "forklift" else WALKING
    if i.get("closed_element_id"):
        c = c.with_closed(i["closed_element_id"])
    got = RouteGraph(b, c).shortest(start, goal)
    if a["type"] == "no_route":
        assert got is None, got.space_path
        return
    assert got is not None
    assert _bays_inside(b, got.space_path) == []
    assert validate_route(b, got.space_path, c) == []
    assert got.space_path == a["spaces"]
    assert got.length_m == pytest.approx(a["length_m"], abs=0.05)


def test_nearest_holder_is_not_reached_through_a_bay():
    q = _case("xt-routing_rules-07")
    b, i, a = _building(q), q["inputs"], q["answer"]
    got = queries.nearest_with_asset(b, i["from_space_id"], i["asset_type"])
    assert got is not None
    assert got[0] == a["space_id"]
    assert got[1] == pytest.approx(a["length_m"], abs=0.05)


def test_tour_legs_never_pass_through_a_bay():
    q = _case("xt-routing_rules-20")
    b, a = _building(q), q["answer"]
    tour = RouteGraph(b).tour(q["inputs"]["stops"])
    assert tour is not None
    for leg, want in zip(tour.legs, a["legs"], strict=True):
        assert _bays_inside(b, leg.space_path) == []
        assert leg.space_path == want["spaces"]
    assert tour.length_m == pytest.approx(a["length_m"], abs=0.05)


def test_distances_agree_with_shortest_under_the_bay_rule():
    """distances_from (nearest, errands, what-if) and shortest must use the same rule."""
    q = _case("xt-routing_rules-01")
    b = _building(q)
    graph = RouteGraph(b)
    start = q["inputs"]["from_space_id"]
    dist = graph.distances_from([start])
    for sid in sorted(dist)[:40]:
        route = graph.shortest(start, sid)
        assert route is not None and route.length_m == pytest.approx(dist[sid], abs=0.01)


def test_what_if_door_counts_a_sealed_room_that_becomes_reachable():
    q = _case("xt-altered_plans-07")
    b, i = _building(q), q["inputs"]
    assert queries.what_if_door(b, i["first"], i["second"], i["floor_id"]) == q["answer"]["rooms"]


FACTORY = ROOT / "synera-kit/data/dev/proc-factory-dev-007"


def _factory():
    if not (FACTORY / "building.json").exists():
        pytest.skip("synera-kit not checked out")
    return load_building(FACTORY)


def _with_no_through(b, *space_ids: str):
    spaces = {sid: dataclasses.replace(s, through=s.id not in space_ids) for sid, s in b.spaces.items()}
    return dataclasses.replace(b, spaces=spaces)


def test_kit_factories_cross_production_zones():
    """No kit space has the flag, so a production zone is crossed like a corridor (Synera's expected routes and
    delivery counts do): Main aisle H-B3 to the offices goes through Production zone H-A3."""
    b = _factory()
    assert all(s.through for s in b.spaces.values())
    start, goal = (b.resolve(name)[0].id for name in ("Main aisle H-B3", "Server room 0.14"))
    route = RouteGraph(b).shortest(start, goal)
    assert route is not None and any(
        b.spaces[s].room_type == "production_zone" for s in route.space_path[1:-1]
    )
    assert validate_route(b, route.space_path) == []


def test_a_flagged_space_is_never_crossed_but_can_be_an_end():
    b = _factory()
    start, goal = (b.resolve(name)[0].id for name in ("Main aisle H-B3", "Server room 0.14"))
    through = RouteGraph(b).shortest(start, goal)
    zone = next(s for s in through.space_path[1:-1] if b.spaces[s].room_type == "production_zone")
    flagged = _with_no_through(b, zone)
    graph = RouteGraph(flagged)
    around = graph.shortest(start, goal)
    assert around is None or zone not in around.space_path
    assert any("no-through" in p for p in validate_route(flagged, through.space_path))
    assert graph.shortest(start, zone) is not None and graph.shortest(zone, goal) is not None
    assert zone in graph.reachable(start) and (goal in graph.reachable(start)) == (around is not None)
    assert validate_route(flagged, graph.shortest(start, zone).space_path) == []
