"""BuildingState: closures, new doors, moved assets, undo and export. No golden answers cover it, so the
cases are taken from dev questions whose answers prove the edit matters (a closure that changes a route, two
rooms that share a wall)."""

from __future__ import annotations

import json

import pytest

from app.building.model import load_building, parse_building
from app.building.routing import Constraints, RouteGraph
from app.building.state import AddDoor, BuildingState, Close, MoveAsset, Reopen, split_between
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


# ---- SplitRoom: a new wall that halves a room ---------------------------------------------------------------


def _ids(b, *names):
    return [b.resolve(n)[0].id for n in names]


@pytest.fixture
def office():
    """Office 2.03 (campus): Lab 2.02 to the north, Staircase 1 to the south, its door on the corridor side."""
    b = load_building("proc-campus-dev-000")
    room, lab, stairs = _ids(b, "Office 2.03", "Lab 2.02", "Staircase 1 (Floor 2)")
    return BuildingState(b), split_between(b, room, lab, stairs)


def test_split_makes_two_equal_halves_and_the_room_id_goes(office):
    state, edit = office
    before = state.building.spaces[edit.space_id]
    message = state.apply(edit)
    assert message.startswith("Split Office 2.03") and "no door" in message
    b = state.building
    assert edit.space_id not in b.spaces
    halves = [s for sid, s in b.spaces.items() if sid.startswith(edit.space_id + "-")]
    assert len(halves) == 2
    assert halves[0].area == pytest.approx(halves[1].area, rel=1e-3)
    assert sum(h.area for h in halves) == pytest.approx(before.area, rel=1e-3)
    # North and south neighbours: a north-south wall, a west and an east half.
    assert sorted(h.name for h in halves) == ["Office 2.03-1 (west half)", "Office 2.03-2 (east half)"]
    assert sorted(h.ref for h in halves) == ["2.03-1", "2.03-2"]  # distinct room numbers
    assert all(h.floor_id == before.floor_id and h.room_type == before.room_type for h in halves)


def test_split_puts_doors_and_items_in_the_half_they_are_in(office):
    state, edit = office
    base = state.building
    state.apply(edit)
    b = state.building
    east, west = (f"{edit.space_id}-{t}" for t in "EW")
    corridor_door = next(o for o in base.openings_of(edit.space_id))
    assert east in b.openings[corridor_door.id].space_ids  # the door on the corridor (east) wall
    assert not b.openings_of(west)
    for a in base.assets_in(edit.space_id):
        assert b.assets[a.id].space_id == (east if a.position[0] > 3.0 else west)


def test_a_door_between_the_halves_can_be_added_and_undo_restores_the_room(office):
    state, edit = office
    state.apply(edit)
    east, west = (f"{edit.space_id}-{t}" for t in "EW")
    assert state.apply(AddDoor(east, west)).startswith("Added")
    assert RouteGraph(state.building, state.constraints()).shortest(west, east) is not None
    state.undo()
    state.undo()
    assert edit.space_id in state.building.spaces and east not in state.building.spaces
    assert state.building is state.base


def test_split_export_has_both_halves(office):
    state, edit = office
    state.apply(edit)
    raw = state.export()
    ids = {s["id"] for s in raw["spaces"]}
    assert edit.space_id not in ids and {f"{edit.space_id}-E", f"{edit.space_id}-W"} <= ids
    assert parse_building(raw).spaces[f"{edit.space_id}-E"].area == pytest.approx(12.54, abs=0.01)


def test_a_wall_across_a_through_room_makes_the_route_go_round():
    """Logistics zone H-C2 is walked through between H-C1 and H-C3; a wall from the aisle (south) to H-C1
    (west) is a diagonal that leaves those two doors in different halves."""
    b = load_building("proc-factory-dev-007")
    room, aisle, c1, c3 = _ids(
        b, "Logistics zone H-C2", "Main aisle H-B2", "Logistics zone H-C1", "Logistics zone H-C3"
    )
    state = BuildingState(b)
    before = RouteGraph(state.building, state.constraints()).shortest(c1, c3)
    assert room in before.space_path
    state.apply(split_between(b, room, aisle, c1))
    after = RouteGraph(state.building, state.constraints()).shortest(c1, c3)
    assert after is not None and after.length_m > before.length_m + 10
    assert room not in after.space_path


@pytest.mark.parametrize(
    ("room", "first", "second", "why"),
    [
        ("Office 2.03", "Lab 2.02", "Kitchen 2.07", "doesn't share a wall"),
        ("Corridor 2.01", "Lab 2.02", "Office 2.03", "same side"),
        ("Office 2.03", "Lab 2.02", "Lab 2.02", "two different"),
    ],
)
def test_split_is_refused_with_a_reason(room, first, second, why):
    b = load_building("proc-campus-dev-000")
    state = BuildingState(b)
    with pytest.raises(ValueError, match=why):
        state.apply(split_between(b, *_ids(b, room, first, second)))
    assert not state.edits
