"""Owner A checks that test_golden.py doesn't cover: one test per item in notes/todo.md.

Golden answers only exist for some of Owner A's interfaces. These tests check the rest by consistency
(distances_from agrees with shortest, a closed door is never used, an edit can be undone) on the same
sampled warm-up and benchmark questions. Test names start with the to-do id (a1_4 = item 1.4). The
to-do progress printed at the end of the run is built in conftest.py.
"""

from __future__ import annotations

import dataclasses
import itertools

import pytest

from app.building import queries
from app.building.model import OUTSIDE, load_building, parse_building
from app.building.routing import FORKLIFT, WALKING, Constraints, RouteGraph, validate_route
from app.building.state import AddDoor, BuildingState, Close, MoveAsset, Reopen

from .test_golden import BENCHMARK, WARMUP, call, cases, route_length_ok

FORKLIFT_MIN_WIDTH_M = 3.0
LIFT_WAIT_M = 15.0  # Synera: 15 m plus 1 m per metre of height
LIFT_PER_M = 1.0
STAIRS_PER_M = 2.0  # Synera: 2 m of walking per metre climbed

ROUTE_BUILDINGS = sorted({q["building_id"] for q in WARMUP.get("route", [])})


def _reachable_routes(kind: str = "route") -> list[pytest.param]:
    return [p for p in cases(WARMUP, kind) if p.values[0]["answer"].get("reachable") is not False]


def _used_openings(b, route):
    return [b.openings[e] for e in route.element_path if e in b.openings]


# ---- 1. routing.py: RouteGraph ----------------------------------------------------------------------------


@pytest.mark.parametrize("building_id", ROUTE_BUILDINGS)
def test_a1_1_graph_builds(building_id):
    b = load_building(building_id)
    for constraints in (WALKING, Constraints(step_free=True), Constraints(min_width_m=1.0)):
        call(RouteGraph, b, constraints)


@pytest.mark.parametrize("q", _reachable_routes("route_step_free"))
def test_a1_2_step_free_never_uses_steps(q):
    b, a = load_building(q["building_id"]), q["answer"]
    route = call(
        call(RouteGraph, b, Constraints(step_free=True)).shortest, a["from_space_id"], a["to_space_id"]
    )
    assert route is not None
    for element in route.element_path:
        thing = b.openings.get(element) or b.connectors.get(element)
        assert thing is not None and thing.step_free, f"{element} is not step-free"


@pytest.mark.parametrize("q", cases(WARMUP, "route_with_closure"))
def test_a1_3_closed_element_never_used(q):
    b, a = load_building(q["building_id"]), q["answer"]
    closed = a["closed_element_id"]
    route = call(
        call(RouteGraph, b, WALKING.with_closed(closed)).shortest, a["from_space_id"], a["to_space_id"]
    )
    if route is not None:
        assert closed not in route.element_path and closed not in route.space_path


@pytest.mark.parametrize("q", _reachable_routes())
def test_a1_4_min_width_respected(q):
    b, a = load_building(q["building_id"]), q["answer"]
    graph = call(RouteGraph, b, Constraints(min_width_m=1.1))
    route = call(graph.shortest, a["from_space_id"], a["to_space_id"])
    if route is not None:
        narrow = [o.id for o in _used_openings(b, route) if o.width_m < 1.1]
        assert not narrow, f"openings narrower than 1.1 m: {narrow}"


# 1.5 shortest and 1.6 tour: test_golden.py::test_route*, test_route_tour


@pytest.mark.parametrize("q", _reachable_routes())
def test_a1_7_distances_from_matches_shortest(q):
    b, a = load_building(q["building_id"]), q["answer"]
    graph = call(RouteGraph, b, WALKING)
    distances = call(graph.distances_from, [a["from_space_id"]])
    route = call(graph.shortest, a["from_space_id"], a["to_space_id"])
    assert a["to_space_id"] in distances
    assert route_length_ok(distances[a["to_space_id"]], route.length_m)


@pytest.mark.parametrize("q", cases(WARMUP, "route_step_free"))
def test_a1_8_reachable_matches_shortest(q):
    b, a = load_building(q["building_id"]), q["answer"]
    graph = call(RouteGraph, b, Constraints(step_free=True))
    reached = call(graph.reachable, a["from_space_id"])
    expected = a.get("reachable") is not False
    assert (a["to_space_id"] in reached) == expected


# ---- 2. queries.py ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("q", cases(BENCHMARK, "errand"))
def test_a2_1_nearest_with_asset(q):
    b, i, a = load_building(q["building_id"]), q["inputs"], q["answer"]
    found = call(queries.nearest_with_asset, b, i["start"], i["asset_type"])
    assert found is not None
    space_id, metres = found
    assert space_id == a["via"], f"{b.label(space_id)} vs {b.label(a['via'])}"
    assert metres >= 0


# 2.2 errand, 2.3 deliverable_rooms, 2.4 step_free_reachable_rooms, 2.5 what_if_door: test_golden.py


# ---- 3. state.py: BuildingState ---------------------------------------------------------------------------


def _state_case():
    """A building from the sample with a door between two spaces, and an asset to move."""
    b = load_building(ROUTE_BUILDINGS[0])
    door = next(o for o in b.openings.values() if OUTSIDE not in o.space_ids)
    asset = next(iter(b.assets.values()))
    elsewhere = next(s.id for s in b.spaces_on(asset.floor_id) if s.id != asset.space_id)
    return b, door, asset, elsewhere


@pytest.mark.skipif(not ROUTE_BUILDINGS, reason="no sampled buildings")
def test_a3_1_apply_close_reopen():
    b, door, _, _ = _state_case()
    state = BuildingState(b)
    assert isinstance(call(state.apply, Close(door.id)), str)
    assert door.id in state.constraints().closed
    call(state.apply, Reopen(door.id))
    assert door.id not in state.constraints().closed
    with pytest.raises(ValueError):
        state.apply(Close("no-such-element"))


@pytest.mark.skipif(not ROUTE_BUILDINGS, reason="no sampled buildings")
def test_a3_2_building_has_structural_edits():
    b, door, asset, elsewhere = _state_case()
    state = BuildingState(b)
    first, second = door.space_ids
    call(state.apply, AddDoor(first, second, position=door.position))
    call(state.apply, MoveAsset(asset.id, elsewhere))
    edited = call(lambda: state.building)
    assert len(edited.openings) == len(b.openings) + 1
    assert edited.assets[asset.id].space_id == elsewhere
    assert b.assets[asset.id].space_id == asset.space_id, "the shared base building must not change"


@pytest.mark.parametrize("q", cases(WARMUP, "route_with_closure"))
def test_a3_3_constraints_drive_routing(q):
    b, a = load_building(q["building_id"]), q["answer"]
    state = BuildingState(b)
    call(state.apply, Close(a["closed_element_id"]))
    graph = call(RouteGraph, call(lambda: state.building), call(state.constraints))
    route = call(graph.shortest, a["from_space_id"], a["to_space_id"])
    if a.get("reachable") is False:
        assert route is None
    else:
        assert route is not None and route_length_ok(route.length_m, a["optimal_length_m"])


@pytest.mark.skipif(not ROUTE_BUILDINGS, reason="no sampled buildings")
def test_a3_4_undo():
    b, door, _, _ = _state_case()
    state = BuildingState(b)
    assert call(state.undo) is None
    edit = Close(door.id)
    call(state.apply, edit)
    assert call(state.undo) == edit
    assert door.id not in call(state.constraints).closed


@pytest.mark.skipif(not ROUTE_BUILDINGS, reason="no sampled buildings")
def test_a3_5_export_round_trips():
    b, door, _, _ = _state_case()
    state = BuildingState(b)
    first, second = door.space_ids
    call(state.apply, AddDoor(first, second, position=door.position))
    exported = parse_building(call(state.export))
    assert len(exported.openings) == len(b.openings) + 1
    assert exported.spaces.keys() == b.spaces.keys()


# ---- 4. Synera's scoring rule (docs/synera/ROUTE_SCORING.md) ----------------------------------------------


@pytest.mark.parametrize("q", _reachable_routes())
def test_a4_1_no_shortcut_through_a_no_through_space(q):
    """Synera: only spaces marked "through": false are destinations; no kit space is, so every golden route
    stays reachable (production zones are crossed like any space). The flag itself: tests/building/test_no_through.py."""
    b, a = load_building(q["building_id"]), q["answer"]
    route = call(call(RouteGraph, b, WALKING).shortest, a["from_space_id"], a["to_space_id"])
    assert route is not None
    through = [s for s in route.space_path[1:-1] if s in b.spaces and not b.spaces[s].through]
    assert not through, f"passes through no-through spaces: {[b.label(s) for s in through]}"


@pytest.mark.parametrize("building_id", ROUTE_BUILDINGS)
def test_a4_2_forklift_uses_wide_openings_only(building_id):
    b = load_building(building_id)
    graph = call(RouteGraph, b, Constraints(step_free=True, min_width_m=FORKLIFT_MIN_WIDTH_M))
    # Every space a forklift has a route to (distances_from applies the production-bay rule; reachable
    # doesn't, like Synera's counts).
    for target in call(graph.distances_from, [OUTSIDE]):
        route = graph.shortest(OUTSIDE, target)
        assert route is not None
        narrow = [o.id for o in _used_openings(b, route) if o.width_m < FORKLIFT_MIN_WIDTH_M]
        assert not narrow, f"forklift route to {b.label(target)} uses narrow openings {narrow}"


@pytest.mark.parametrize("building_id", ROUTE_BUILDINGS)
def test_a4_3_stairs_and_lift_cost_per_metre(building_id):
    b = load_building(building_id)
    graph = call(RouteGraph, b, WALKING)
    checked = 0
    for connector in b.connectors.values():
        for (low_floor, low), (high_floor, high) in itertools.pairwise(connector.stops):
            route = graph.shortest(low, high)
            if route is None or route.element_path != [connector.id]:
                continue
            height = abs(b.floors[high_floor].elevation_m - b.floors[low_floor].elevation_m)
            expected = (
                LIFT_WAIT_M + LIFT_PER_M * height if connector.kind == "lift" else STAIRS_PER_M * height
            )
            assert route_length_ok(route.length_m, expected), (
                f"{connector.id}: {route.length_m} vs {expected}"
            )
            checked += 1
    if not checked:
        pytest.skip("not applicable: no direct stair or lift leg in this building")


def _golden_legs(q):
    a = q["answer"]
    if q["kind"] == "tour":  # kit 1.1.0: an unreachable tour has no legs
        return [(leg["space_path"], WALKING) for leg in a.get("legs", [])]
    c = Constraints(step_free=q["kind"] == "route_step_free")
    return [(a.get("space_path"), c.with_closed(a["closed_element_id"]) if "closed_element_id" in a else c)]


@pytest.mark.parametrize(
    "q",
    [p for kind in ("route", "route_step_free", "route_with_closure", "tour") for p in cases(WARMUP, kind)],
)
def test_a4_6_validator_accepts_golden_routes(q):
    b = load_building(q["building_id"])
    for path, c in _golden_legs(q):
        if path:
            assert call(validate_route, b, path, c) == [], path


@pytest.mark.parametrize("q", _reachable_routes())
def test_a4_6_validator_rejects_invalid_routes(q):
    b, a = load_building(q["building_id"]), q["answer"]
    path, elements = a["space_path"], a["element_path"]
    assert call(validate_route, b, path, WALKING) == []
    assert validate_route(b, [], WALKING)
    assert validate_route(b, [path[0], path[0]], WALKING), "a space isn't connected to itself"
    assert validate_route(b, [*path, "no-such-space"], WALKING)
    assert validate_route(b, path, WALKING.with_closed(elements[0])), "closed element used"
    closed_space = next((sid for sid in path if sid in b.spaces), None)
    if closed_space is not None:
        problems = validate_route(b, path, WALKING.with_closed(closed_space))
        assert f"closed space {b.label(closed_space)}" in problems
    if any(b.openings[e].width_m < FORKLIFT.min_width_m for e in elements if e in b.openings):
        assert validate_route(b, path, FORKLIFT), "narrow opening on a forklift route"
    # Only the space sequence is scored: a step counts if *any* step-free door, stair or lift joins the pair.
    stepped_only = [
        (x, y)
        for x, y in itertools.pairwise(path)
        if not any(
            {x, y} <= ({*o.space_ids} if hasattr(o, "space_ids") else {sid for _, sid in o.stops})
            and o.step_free
            for o in [*b.openings.values(), *b.connectors.values()]
        )
    ]
    if stepped_only:
        assert validate_route(b, path, Constraints(step_free=True)), f"steps only between {stepped_only[0]}"
    if len(path) >= 3:
        middle = b.spaces.get(path[1])
        if middle is not None:
            spaces = {**b.spaces, middle.id: dataclasses.replace(middle, through=False)}
            flagged = dataclasses.replace(b, spaces=spaces)
            assert any("no-through" in p for p in validate_route(flagged, path, WALKING))
            assert not any("no-through" in p for p in validate_route(flagged, path[:2], WALKING)), (
                "may end in one"
            )
