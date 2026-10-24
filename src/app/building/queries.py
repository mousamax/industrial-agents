"""Graph questions: answers that need routing or reachability on top of RouteGraph.

OWNER: Owner A, graph & state. Interface frozen: change signatures only after telling the team.

Backs errand (20 test questions), wide_delivery_count (18), what_if_door (3), step_free_reachable_count
(0 in test, 2 in train) and the Sunday route questions. Arguments mirror the `inputs` of the train/dev
questions, so tests/building/test_golden.py checks them directly against the golden answers. Which space
types a "room" count includes is defined by those answers: let the tests tell you, then write it down here.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.building.model import Building
from app.building.routing import WALKING, Constraints


@dataclass(frozen=True)
class Errand:
    pickup_space_id: str  # golden `via`: the room with the item, nearest to start
    target_space_id: str  # golden `target`: the largest room_type room on floor_id
    length_m: float  # start -> pickup -> target


def nearest_with_asset(
    b: Building, start: str, asset_type: str, constraints: Constraints = WALKING
) -> tuple[str, float] | None:
    """The space holding an `asset_type` with the shortest route from start: (space_id, metres)."""
    raise NotImplementedError("Owner A")


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
    raise NotImplementedError("Owner A")


def deliverable_rooms(b: Building, start: str, floor_id: str, min_width_m: float) -> list[str]:
    """wide_delivery_count: rooms on floor_id reachable from the entrance space `start` with no stairs
    or steps and every opening >= min_width_m."""
    raise NotImplementedError("Owner A")


def step_free_reachable_rooms(b: Building, start: str, floor_id: str, closed: list[str]) -> list[str]:
    """step_free_reachable_count: rooms on floor_id a wheelchair user reaches from `start` with `closed` out."""
    raise NotImplementedError("Owner A")


def what_if_door(
    b: Building, first: str, second: str, floor_id: str, *, min_gain_m: float = 1.0
) -> list[str]:
    """Rooms on floor_id whose walk to the nearest entrance gets shorter by more than min_gain_m when a
    door is added in the middle of the wall first/second share (geometry.shared_wall_midpoint)."""
    raise NotImplementedError("Owner A")
