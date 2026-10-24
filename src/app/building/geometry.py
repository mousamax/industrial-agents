"""L2 plan facts: geometry derived from polygons and elevations. Pure functions of a Building.

OWNER: Owner B, geometry & build. Interface frozen: change signatures only after telling the team.

Backs 104 of the 145 scored test questions: floor_area_total (33), shared_walls_count (26),
largest_room (21), straight_line_3d (15), room_stacked (9). Exact definitions (synera-kit/README.md §4):
- area: polygon area. largest_room answers need the room AND its area within ±5%.
- shared wall: at least 1 m of one room's outline runs within 0.35 m of the other room's outline
  (walls have thickness), same floor. The question counts rooms only: corridors, stairwells, lifts excluded.
- directly above / below: the space on the next floor up/down whose outline covers most of this one.
- room centre: polygon centroid at the floor's elevation_m.
Golden tests: tests/building/test_golden.py::test_geometry*. Watch the OSM buildings: overlapping
spaces, rotated rooms, level gaps (the "next floor" is the next existing level, not level + 1).
"""

from __future__ import annotations

from typing import Literal

from app.building.model import XY, Building, Space

Direction = Literal["above", "below"]


def rooms_by_area(b: Building, floor_id: str, room_type: str) -> list[tuple[Space, float]]:
    """Spaces of `room_type` on a floor with their areas, largest first (largest_room)."""
    raise NotImplementedError("Owner B")


def total_area(b: Building, floor_id: str, room_type: str) -> float:
    """Summed area of every `room_type` space on a floor, m² (floor_area_total)."""
    raise NotImplementedError("Owner B")


def shared_walls(
    b: Building, space_id: str, *, min_length_m: float = 1.0, tolerance_m: float = 0.35
) -> list[tuple[str, float]]:
    """Other spaces on the same floor sharing a wall: (space_id, shared length in m), longest first.
    Returns every space type; `shared_wall_rooms` applies the question's filter."""
    raise NotImplementedError("Owner B")


def shared_wall_rooms(b: Building, space_id: str) -> list[str]:
    """Space ids counted by shared_walls_count: neighbours that are rooms (no corridors, stairwells, lifts)."""
    raise NotImplementedError("Owner B")


def shared_wall_midpoint(b: Building, first_id: str, second_id: str) -> XY | None:
    """Middle of the wall two spaces share, or None if they don't. Used by what_if_door and AddDoor."""
    raise NotImplementedError("Owner B")


def stacked(b: Building, space_id: str, direction: Direction) -> str | None:
    """The space directly above/below (most overlap with this outline on the adjacent floor) (room_stacked)."""
    raise NotImplementedError("Owner B")


def centroid_3d(b: Building, space_id: str) -> tuple[float, float, float]:
    """(x, y, elevation_m of its floor)."""
    raise NotImplementedError("Owner B")


def straight_line_3d(b: Building, first_id: str, second_id: str) -> float:
    """Distance between two room centres in 3D, m (straight_line_3d)."""
    raise NotImplementedError("Owner B")
