"""BuildingState: closures, new doors, moved assets, undo and export. No golden answers cover it, so the
cases are taken from dev questions whose answers prove the edit matters (a closure that changes a route, two
rooms that share a wall)."""

from __future__ import annotations

import json

import pytest
from shapely.geometry import LineString, Point

from app.building.model import load_building, parse_building
from app.building.routing import Constraints, RouteGraph
from app.building.state import AddDoor, BuildingState, Close, MoveAsset, RemoveDoor, Reopen, split_between
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


# ---- new entrances, removed doors, width changes, door ids ------------------------------------------------

CAMPUS = "proc-campus-dev-000"


def _kit_map(raw: dict):
    """Synera's own BuildingMap on our export: it must load it and route through the edits."""
    import sys

    sys.path.insert(0, str(get_settings().synera_kit_dir / "starter_agent"))
    from building_map import BuildingMap

    return BuildingMap(json.loads(json.dumps(raw)))


def _kit_length(kit, start: str, goal: str) -> float | None:
    import networkx as nx

    try:
        return nx.dijkstra_path_length(kit.graph(step_free=False), start, goal, weight="weight")
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None


def test_a_door_to_the_outside_is_a_new_entrance_the_kit_routes_through():
    """Seminar room 0.17 is the far end of the ground floor (about 71 m from an entrance); a door in its
    outside wall makes it the nearest way in."""
    from app.building.model import OUTSIDE

    b = load_building(CAMPUS)
    state = BuildingState(b)
    (room,) = _ids(b, "Seminar room 0.17")
    before = RouteGraph(b).shortest(OUTSIDE, room)
    message = state.apply(AddDoor(room, OUTSIDE, width_m=1.5))
    assert "entrance" in message and "Seminar room 0.17" in message
    door = next(o for o in state.building.openings.values() if o.id not in b.openings)
    assert door.space_ids == (room, OUTSIDE) and door.kind == "entrance" and door.width_m == 1.5
    assert b.spaces[room].shape.exterior.distance(Point(door.position)) < 0.01  # in its outside wall
    after = RouteGraph(state.building, state.constraints()).shortest(OUTSIDE, room)
    assert after is not None and after.length_m < before.length_m - 30
    assert after.element_path == [door.id] and after.space_path == [OUTSIDE, room]

    raw = state.export()
    exported = next(o for o in raw["openings"] if o["id"] == door.id)
    assert exported["space_ids"] == [room, OUTSIDE] and exported["kind"] == "entrance"
    kit = _kit_map(raw)
    assert any(e["into"].startswith("Seminar room 0.17") for e in kit.overview()["entrances"])
    assert _kit_length(kit, room, "door:" + door.id) == pytest.approx(after.length_m, abs=0.05)


def test_a_door_to_the_outside_is_refused_on_a_floor_without_entrances():
    from app.building.model import OUTSIDE

    b = load_building(CAMPUS)
    state = BuildingState(b)
    (room,) = _ids(b, "Office 2.03")
    with pytest.raises(ValueError, match="no entrance"):
        state.apply(AddDoor(room, OUTSIDE))
    (corridor,) = _ids(b, "Corridor 0.01")
    with pytest.raises(ValueError):
        state.apply(AddDoor(OUTSIDE, OUTSIDE))
    assert not state.edits and corridor in b.spaces


def test_remove_door_makes_the_route_longer_and_undo_restores_it():
    b = load_building(CAMPUS)
    office, kitchen = _ids(b, "Office 0.02", "Kitchen 0.03")
    state = BuildingState(b)
    state.apply(AddDoor(office, kitchen))
    (door,) = [o for o in state.building.openings.values() if o.id not in b.openings]
    assert RouteGraph(state.building).shortest(office, kitchen).length_m == pytest.approx(4.17, abs=0.01)
    message = state.apply(RemoveDoor(door.id))
    assert message.startswith("Removed") and "Office 0.02" in message
    assert door.id not in state.building.openings
    assert RouteGraph(state.building).shortest(office, kitchen).length_m == pytest.approx(10.77, abs=0.01)

    # An existing door of the plan: the office's own door to the corridor leaves it unreachable.
    (own,) = [o for o in b.openings_of(office)]
    state.apply(RemoveDoor(own.id))
    raw = state.export()
    assert own.id not in {o["id"] for o in raw["openings"]} and door.id not in {
        o["id"] for o in raw["openings"]
    }
    assert RouteGraph(state.building).shortest(office, kitchen) is None
    assert _kit_length(_kit_map(raw), office, kitchen) is None
    with pytest.raises(ValueError):
        state.apply(RemoveDoor(own.id))  # already gone

    assert state.undo() == RemoveDoor(own.id)
    assert own.id in state.building.openings
    assert _kit_length(_kit_map(state.export()), office, kitchen) == pytest.approx(10.77, abs=0.01)


def test_a_width_change_is_remove_and_re_add_in_the_same_place():
    b = load_building(CAMPUS)
    (office,) = _ids(b, "Office 0.02")
    (own,) = b.openings_of(office)
    first, second = own.space_ids
    state = BuildingState(b)
    state.apply(RemoveDoor(own.id))
    state.apply(AddDoor(first, second, width_m=1.5, step_free=own.step_free, position=own.position))
    raw = state.export()
    (new,) = [o for o in raw["openings"] if o["id"] not in b.openings]
    assert own.id not in {o["id"] for o in raw["openings"]}
    assert new["position"] == list(own.position) and new["width_m"] == 1.5
    assert set(new["space_ids"]) == {first, second}
    assert len(raw["openings"]) == len(b.openings)


def test_an_added_door_keeps_its_id_when_others_are_undone_or_removed():
    b = load_building(CAMPUS)
    office, kitchen, lobby = _ids(b, "Office 0.02", "Kitchen 0.03", "Main lobby")
    (seminar,) = _ids(b, "Seminar room 0.17")
    from app.building.model import OUTSIDE

    state = BuildingState(b)
    state.apply(AddDoor(office, kitchen))
    state.apply(AddDoor(seminar, OUTSIDE))
    a, c = (o.id for o in state.building.openings.values() if o.id not in b.openings)
    assert a != c
    state.apply(RemoveDoor(a))
    assert c in state.building.openings and state.building.openings[c].space_ids == (seminar, OUTSIDE)
    state.apply(Close(c))
    assert c in state.constraints().closed
    assert RouteGraph(state.building, state.constraints()).shortest(OUTSIDE, seminar).element_path != [c]
    # Undo the closure and the removal, then the door: a door added after that gets a new number, never a
    # used one, so an old closure can't point at it.
    state.undo()
    state.undo()
    state.undo()
    state.apply(AddDoor(office, kitchen))
    (again,) = [o.id for o in state.building.openings.values() if o.id not in b.openings and o.id != a]
    assert again not in (a, c)
    assert {o["id"] for o in state.export()["openings"]} >= {a, again}


# ---- a new door always fits on the wall it is in ----------------------------------------------------------


@pytest.fixture
def mucl_kitchen():
    """MUCL Floor 3: Küche shares a 5.96 m straight wall with Flur 3. OG (x 37.22..43.18 at y 18.8); its
    door O150 (1.0 m) sits 1.25 m from the wall's west end."""
    b = load_building(get_settings().data_dir / "MUCL")
    return b, b.openings["O150"]


def _door_ends_on_both_outlines(b, door, width):
    x, y = door.position
    for end in ((x - width / 2, y), (x + width / 2, y)):
        for sid in door.space_ids:
            assert b.spaces[sid].shape.exterior.distance(Point(end)) < 1e-3, (end, sid)


@pytest.mark.parametrize(("width", "centre_x"), [(2.0, 38.47), (5.0, 37.22 + 2.5 + 0.1), (5.96, 40.2)])
def test_a_widened_door_slides_along_its_wall_just_enough_to_fit(mucl_kitchen, width, centre_x):
    b, old = mucl_kitchen
    state = BuildingState(b)
    state.apply(RemoveDoor(old.id))
    message = state.apply(AddDoor(*old.space_ids, width_m=width, position=old.position))
    (door,) = [o for o in state.building.openings.values() if o.id not in b.openings]
    assert door.position == pytest.approx((centre_x, 18.8), abs=1e-3)
    assert f"({centre_x:.1f}, 18.8)" in message  # the message reports where it ended up
    _door_ends_on_both_outlines(state.building, door, width)
    raw = state.export()
    assert next(o for o in raw["openings"] if o["id"] == door.id)["position"] == list(door.position)
    _kit_map(raw)  # the kit still loads it


def test_a_door_wider_than_its_wall_is_refused_with_the_most_that_fits(mucl_kitchen):
    b, old = mucl_kitchen
    state = BuildingState(b)
    with pytest.raises(ValueError, match=r"only 5.96 m long, so a door there can be at most 5.9 m wide"):
        state.apply(AddDoor(*old.space_ids, width_m=7.0))
    assert not state.edits


def test_a_mid_wall_door_stays_in_the_middle(wall):
    from app.building import geometry

    state, first, second = wall
    state.apply(AddDoor(first, second))
    (door,) = [o for o in state.building.openings.values() if o.id not in state.base.openings]
    assert door.position == pytest.approx(geometry.shared_wall_midpoint(state.base, first, second), abs=1e-3)


def test_a_wide_entrance_stays_on_one_straight_outside_wall():
    from app.building.model import OUTSIDE

    b = load_building(CAMPUS)
    (room,) = _ids(b, "Seminar room 0.17")
    state = BuildingState(b)
    with pytest.raises(ValueError, match="at most"):
        state.apply(AddDoor(room, OUTSIDE, width_m=50))
    state.apply(AddDoor(room, OUTSIDE, width_m=3.0))
    (door,) = [o for o in state.building.openings.values() if o.id not in b.openings]
    ring = b.spaces[room].shape.exterior
    corners = list(ring.coords)
    edge = min(
        zip(corners, corners[1:], strict=False), key=lambda e: LineString(e).distance(Point(door.position))
    )
    (x0, y0), (x1, y1) = edge
    length = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
    ux, uy = (x1 - x0) / length, (y1 - y0) / length
    t = (door.position[0] - x0) * ux + (door.position[1] - y0) * uy
    assert 1.5 <= t <= length - 1.5  # the whole 3 m lies on that one straight wall
