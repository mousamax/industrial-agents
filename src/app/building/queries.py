"""Graph questions: answers that need routing or reachability on top of RouteGraph.

OWNER: Owner A, graph & state. Interface frozen: change signatures only after telling the team.

Backs errand (20 test questions), wide_delivery_count (18), what_if_door (3), step_free_reachable_count
(0 in test, 2 in train) and the Sunday route questions. Arguments mirror the `inputs` of the train/dev
questions, so tests/building/test_golden.py checks them directly against the golden answers. Which space
types a "room" count includes is defined by those answers: let the tests tell you, then write it down here.

Measured on every dev + train answer (80 wide_delivery, 2 step_free_reachable, 4 what_if_door, 93 errand):
- A "room" in the counts is a space with `type == "room"`: halls, corridors, stairs, lifts and outdoor
  spaces don't count (counting halls too breaks 19 of 80 wide_delivery answers).
- errand: the pickup is the asset holder with the shortest walking route from start (any floor; the start
  itself if it holds one), the target is geometry.rooms_by_area(...)[0], the length is both legs summed.
- what_if_door: a 0.9 m step-free door at geometry.shared_wall_midpoint; rooms that were unreachable
  before and reachable after count as gaining more than min_gain_m.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass

from app.building import geometry
from app.building.model import OUTSIDE, Building, Opening
from app.building.routing import WALKING, Constraints, RouteGraph


@dataclass(frozen=True)
class Errand:
    pickup_space_id: str  # golden `via`: the room with the item, nearest to start
    target_space_id: str  # golden `target`: the largest room_type room on floor_id
    length_m: float  # start -> pickup -> target


def _rooms_on(b: Building, floor_id: str, space_ids: set[str] | dict[str, float]) -> list[str]:
    """Space ids from `space_ids` that are rooms (type "room") on floor_id, in building order."""
    return [s.id for s in b.spaces_on(floor_id, type="room") if s.id in space_ids]


def nearest_with_asset(
    b: Building, start: str, asset_type: str, constraints: Constraints = WALKING
) -> tuple[str, float] | None:
    """The space holding an `asset_type` with the shortest route from start: (space_id, metres)."""
    holders = {a.space_id for a in b.assets_of_type(asset_type)}
    distances = RouteGraph(b, constraints).distances_from([start])
    reached = [(distances[sid], sid) for sid in holders if sid in distances]
    if not reached:
        return None
    metres, space_id = min(reached)
    return space_id, round(metres, 2)


def errand(
    b: Building,
    start: str,
    asset_type: str,
    floor_id: str,
    room_type: str,
    constraints: Constraints = WALKING,
) -> Errand | None:
    """'Grab a <asset_type> from the room that has one closest to me, then carry it to the largest
    <room_type> on <floor>.' Uses geometry.rooms_by_area for the target."""
    pickup = nearest_with_asset(b, start, asset_type, constraints)
    ranked = geometry.rooms_by_area(b, floor_id, room_type)
    if pickup is None or not ranked:
        return None
    pickup_id, first_leg = pickup
    target_id = ranked[0][0].id
    second_leg = RouteGraph(b, constraints).shortest(pickup_id, target_id)
    if second_leg is None:
        return None
    return Errand(pickup_id, target_id, round(first_leg + second_leg.length_m, 2))


def deliverable_rooms(b: Building, start: str, floor_id: str, min_width_m: float) -> list[str]:
    """wide_delivery_count: rooms on floor_id reachable from the entrance space `start` with no stairs
    or steps and every opening >= min_width_m."""
    graph = RouteGraph(b, Constraints(step_free=True, min_width_m=min_width_m))
    return _rooms_on(b, floor_id, graph.reachable(start))


def step_free_reachable_rooms(b: Building, start: str, floor_id: str, closed: list[str]) -> list[str]:
    """step_free_reachable_count: rooms on floor_id a wheelchair user reaches from `start` with `closed` out."""
    graph = RouteGraph(b, Constraints(step_free=True, closed=frozenset(closed)))
    return _rooms_on(b, floor_id, graph.reachable(start))


def what_if_door(
    b: Building, first: str, second: str, floor_id: str, *, min_gain_m: float = 1.0
) -> list[str]:
    """Rooms on floor_id whose walk to the nearest entrance gets shorter by more than min_gain_m when a
    door is added in the middle of the wall first/second share (geometry.shared_wall_midpoint)."""
    midpoint = geometry.shared_wall_midpoint(b, first, second)
    if midpoint is None:
        raise ValueError(f"{b.label(first)} and {b.label(second)} don't share a wall")
    door = Opening(
        id=f"{b.spaces[first].floor_id}-O-whatif",
        floor_id=b.spaces[first].floor_id,
        space_ids=(first, second),
        kind="door",
        position=midpoint,
        width_m=0.9,
        step_free=True,
        synthetic=True,
    )
    with_door = dataclasses.replace(b, openings={**b.openings, door.id: door})
    before = RouteGraph(b).distances_from([OUTSIDE])
    after = RouteGraph(with_door).distances_from([OUTSIDE])
    gained = {sid for sid, d in after.items() if before.get(sid, math.inf) - d > min_gain_m}
    return _rooms_on(b, floor_id, gained)
