"""BuildingState: closures, new doors, moved assets, undo and export. No golden answers cover it, so the
cases are taken from dev questions whose answers prove the edit matters (a closure that changes a route, two
rooms that share a wall)."""

from __future__ import annotations

import json

import pytest

from app.building.model import load_building, parse_building
from app.building.routing import Constraints, RouteGraph
from app.building.state import AddDoor, BuildingState, Close, MoveAsset, Reopen
from app.settings import get_settings

DATA = get_settings().synera_kit_dir / "data" / "dev"

if not DATA.exists():
    pytest.skip("synera-kit data missing: git submodule update --init", allow_module_level=True)


def _first(filename: str, kind: str, accept=lambda q: True) -> dict:
    for path in sorted(DATA.glob(f"*/{filename}")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() and (q := json.loads(line))["kind"] == kind and accept(q):
                return q
    pytest.skip(f"no dev {kind} question")


@pytest.fixture
def closure():
    """A closure that changes a still-reachable route: (state, start, goal, closed id, golden length)."""
    q = _first("questions_basic.jsonl", "route_with_closure", lambda q: q["answer"].get("reachable", True))
    a = q["answer"]
    return BuildingState(load_building(q["building_id"])), a["from_space_id"], a["to_space_id"], a


@pytest.fixture
def wall():
    """Two spaces that share a wall, from a what_if_door question."""
    q = _first("questions.jsonl", "what_if_door")
    return BuildingState(load_building(q["building_id"])), q["inputs"]["first"], q["inputs"]["second"]


def test_close_reroutes_and_reopen_restores(closure):
    state, start, goal, a = closure
    before = RouteGraph(state.building, state.constraints()).shortest(start, goal)
    message = state.apply(Close(a["closed_element_id"]))
    assert message.startswith("Closed")
    assert a["closed_element_id"] in state.constraints().closed
    closed = RouteGraph(state.building, state.constraints()).shortest(start, goal)
    assert closed is not None and a["closed_element_id"] not in closed.element_path
    assert closed.length_m == pytest.approx(a["optimal_length_m"], abs=0.05)
    assert closed.length_m > before.length_m

    state.apply(Reopen(a["closed_element_id"]))
    assert state.constraints().closed == frozenset()
    assert RouteGraph(state.building, state.constraints()).shortest(start, goal).length_m == before.length_m


def test_constraints_keep_the_base(closure):
    state, *_, a = closure
    state.apply(Close(a["closed_element_id"]))
    c = state.constraints(Constraints(step_free=True, min_width_m=1.1))
    assert c.step_free and c.min_width_m == 1.1 and a["closed_element_id"] in c.closed


def test_invalid_edits_raise_and_are_not_recorded(closure, wall):
    state, *_, a = closure
    with pytest.raises(ValueError):
        state.apply(Close("no-such-element"))
    with pytest.raises(ValueError):
        state.apply(Reopen(a["closed_element_id"]))  # not closed
    with pytest.raises(ValueError):
        state.apply(MoveAsset("no-such-asset", a["from_space_id"]))
    assert state.edits == []

    state, first, second = wall
    other_floor = next(
        s for s in state.base.spaces.values() if s.floor_id != state.base.spaces[first].floor_id
    )
    with pytest.raises(ValueError):
        state.apply(AddDoor(first, other_floor.id))
    assert state.edits == []


def test_add_door_shortens_the_route_and_undo_restores(wall):
    state, first, second = wall
    before = RouteGraph(state.base).shortest(first, second)
    message = state.apply(AddDoor(first, second))
    assert "door" in message
    after = RouteGraph(state.building, state.constraints()).shortest(first, second)
    assert after is not None and (before is None or after.length_m < before.length_m)
    new_door = after.element_path[0]
    assert new_door in state.building.openings and new_door not in state.base.openings
    assert len(after.space_path) == 2

    assert state.undo() == AddDoor(first, second)
    assert state.building is state.base
    assert state.undo() is None


def test_move_asset(wall):
    state, first, second = wall
    asset = next(a for a in state.base.assets.values() if a.space_id != second)
    state.apply(MoveAsset(asset.id, second))
    moved = state.building.assets[asset.id]
    assert moved.space_id == second and moved.position == pytest.approx(
        state.base.spaces[second].anchor, abs=1e-3
    )
    assert state.base.assets[asset.id].space_id == asset.space_id  # the base is untouched


def test_export_round_trips(wall):
    state, first, second = wall
    state.apply(AddDoor(first, second, width_m=1.2))
    asset = next(iter(state.base.assets.values()))
    state.apply(MoveAsset(asset.id, first, position=(1.0, 2.0)))
    raw = json.loads(json.dumps(state.export()))
    again = parse_building(raw)
    assert set(again.openings) == set(state.building.openings)
    assert len(again.openings) == len(state.base.openings) + 1
    door = next(o for o in again.openings.values() if o.id not in state.base.openings)
    assert door.width_m == 1.2 and door.synthetic and set(door.space_ids) == {first, second}
    assert again.assets[asset.id].space_id == first and again.assets[asset.id].position == (1.0, 2.0)
    assert len(state.base.raw["openings"]) == len(state.base.openings)  # base raw not mutated
