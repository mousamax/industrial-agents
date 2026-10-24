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

Checked against every dev + train answer: areas 277/277, shared walls 143/143, stacked 26/26, 3D 30/30.
What counts as a "room" for shared walls was measured, not guessed: rooms and halls (factory zones,
lobbies) count; corridors, stairs, lifts and outdoor spaces (balconies) don't.

Also for routing (Owner A): `walking_distance` and `door_to_door`, Synera's route rule inside a space
(door to door, 15 cm off the walls). Tests: tests/building/test_walking.py.
"""

from __future__ import annotations

import heapq
import itertools
import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

from shapely.geometry import LineString, MultiLineString, Point, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import linemerge, nearest_points
from shapely.prepared import prep

from app.building.model import XY, Building, Space

Direction = Literal["above", "below"]

WALL_TOLERANCE_M = 0.35
MIN_WALL_M = 1.0
WALL_ROOM_TYPES = frozenset({"room", "hall"})
NOT_ROOMS = frozenset({"corridor", "stairwell", "lift"})


def rooms_by_area(b: Building, floor_id: str, room_type: str) -> list[tuple[Space, float]]:
    """Spaces of `room_type` on a floor with their areas, largest first (largest_room)."""
    spaces = b.spaces_on(floor_id, room_type=room_type)
    return sorted(((s, s.area) for s in spaces), key=lambda t: t[1], reverse=True)


def total_area(b: Building, floor_id: str, room_type: str) -> float:
    """Summed area of every `room_type` space on a floor, m² (floor_area_total)."""
    return sum(s.area for s in b.spaces_on(floor_id, room_type=room_type))


def _wall_on(outline_of: Polygon, other: Polygon, tolerance_m: float) -> LineString | MultiLineString:
    """The part of `outline_of`'s outline that runs within `tolerance_m` of `other`'s outline."""
    return outline_of.exterior.intersection(other.exterior.buffer(tolerance_m))


def _wall_length(first: Polygon, second: Polygon, tolerance_m: float) -> float:
    """Shared wall length: the longer of the two outlines' stretches along the other ("one room's outline")."""
    return max(
        _wall_on(first, second, tolerance_m).length,
        _wall_on(second, first, tolerance_m).length,
    )


def _near(first: Polygon, second: Polygon, tolerance_m: float) -> bool:
    ax0, ay0, ax1, ay1 = first.bounds
    bx0, by0, bx1, by1 = second.bounds
    t = tolerance_m
    return ax0 - t <= bx1 and bx0 - t <= ax1 and ay0 - t <= by1 and by0 - t <= ay1


def shared_walls(
    b: Building, space_id: str, *, min_length_m: float = 1.0, tolerance_m: float = 0.35
) -> list[tuple[str, float]]:
    """Other spaces on the same floor sharing a wall: (space_id, shared length in m), longest first.
    Returns every space type; `shared_wall_rooms` applies the question's filter."""
    space = b.spaces[space_id]
    out = []
    for other in b.spaces_on(space.floor_id):
        if other.id == space_id or not _near(space.shape, other.shape, tolerance_m):
            continue
        length = _wall_length(space.shape, other.shape, tolerance_m)
        if length >= min_length_m:
            out.append((other.id, length))
    return sorted(out, key=lambda t: t[1], reverse=True)


def is_wall_room(space: Space) -> bool:
    """Counted by shared_walls_count: rooms and halls, not corridors, stairwells, lifts or balconies."""
    return space.type in WALL_ROOM_TYPES and space.room_type not in NOT_ROOMS


def shared_wall_rooms(b: Building, space_id: str) -> list[str]:
    """Space ids counted by shared_walls_count: neighbours that are rooms (no corridors, stairwells, lifts)."""
    return [
        sid
        for sid, _ in shared_walls(b, space_id, min_length_m=MIN_WALL_M, tolerance_m=WALL_TOLERANCE_M)
        if is_wall_room(b.spaces[sid])
    ]


def _middle(wall: LineString | MultiLineString) -> XY | None:
    """Middle of the longest connected piece of a wall."""
    if wall.is_empty or wall.length == 0:
        return None
    merged = linemerge(wall) if isinstance(wall, MultiLineString) else wall
    pieces = list(merged.geoms) if isinstance(merged, MultiLineString) else [merged]
    longest = max(pieces, key=lambda g: g.length)
    p = longest.interpolate(0.5, normalized=True)
    return (p.x, p.y)


def shared_wall_midpoint(b: Building, first_id: str, second_id: str) -> XY | None:
    """Middle of the wall two spaces share, or None if they don't. Used by what_if_door and AddDoor."""
    first, second = b.spaces[first_id], b.spaces[second_id]
    if first.floor_id != second.floor_id or first_id == second_id:
        return None
    if _wall_length(first.shape, second.shape, WALL_TOLERANCE_M) < MIN_WALL_M:
        return None
    # Walls have thickness: take the middle on each side and meet in between.
    a = _middle(_wall_on(first.shape, second.shape, WALL_TOLERANCE_M))
    c = _middle(_wall_on(second.shape, first.shape, WALL_TOLERANCE_M))
    if a is None or c is None:
        return a or c
    return ((a[0] + c[0]) / 2, (a[1] + c[1]) / 2)


def adjacent_floor(b: Building, floor_id: str, direction: Direction) -> str | None:
    """The next existing floor up or down (levels can have gaps), or None at the top/bottom."""
    level = b.floors[floor_id].level
    floors = b.floors_by_level()
    candidates = (
        [f for f in floors if f.level > level]
        if direction == "above"
        else [f for f in reversed(floors) if f.level < level]
    )
    return candidates[0].id if candidates else None


def stacked(b: Building, space_id: str, direction: Direction) -> str | None:
    """The space directly above/below (most overlap with this outline on the adjacent floor) (room_stacked)."""
    space = b.spaces[space_id]
    floor_id = adjacent_floor(b, space.floor_id, direction)
    if floor_id is None:
        return None
    best, best_overlap = None, 0.0
    for other in b.spaces_on(floor_id):
        if not _near(space.shape, other.shape, 0.0):
            continue
        overlap = space.shape.intersection(other.shape).area
        if overlap > best_overlap:
            best, best_overlap = other.id, overlap
    return best


def centroid_3d(b: Building, space_id: str) -> tuple[float, float, float]:
    """(x, y, elevation_m of its floor)."""
    space = b.spaces[space_id]
    return (*space.centroid, b.floors[space.floor_id].elevation_m)


def straight_line_3d(b: Building, first_id: str, second_id: str) -> float:
    """Distance between two room centres in 3D, m (straight_line_3d)."""
    return math.dist(centroid_3d(b, first_id), centroid_3d(b, second_id))


# ---- walking inside a space (Synera's route rule) -----------------------------------------------------------
#
# Synera measures every route door to door inside each space, about 15 cm off the walls, around corners and
# never through walls (docs/synera/ROUTE_SCORING.md). That is the shortest path inside the outline inset by
# 15 cm. Such a path only ever bends at the inset outline's reflex (inward) corners, so the corners that see
# each other form a small graph and Dijkstra over it gives the distance. Points the inset doesn't contain
# (doors sit on the wall) move to its nearest point. A space too narrow to inset (< 30 cm) is walked in its
# own outline; one whose inset falls apart at a neck is walked in its largest piece. Same numbers as the
# prototype in scripts/synera/route_metric_check.py (12,514 pairs, max difference 0).
#
# Cached by outline and doors, not by building: edits (a door added, a machine moved) make a new Building but
# reuse every unchanged space. All 22,725 kit spaces take 25 s once; the two largest OSM corridors (56,000 and
# 73,000 m², ~950 corners after rounding the inset) take 3 s each. Sharper (mitred) or coarser corners would
# be 10x faster but move distances by up to 1.2 m.

WALL_CLEARANCE_M = 0.15


@dataclass(frozen=True)
class _Walkable:
    area: Polygon
    cover: Any  # the area a hair bigger, prepared: "does this segment stay inside?"
    corners: tuple[XY, ...]  # reflex corners of the area
    sight: dict[int, list[tuple[int, float]]]  # corner -> (corner it sees, distance)


def _reflex_corners(area: Polygon) -> list[XY]:
    ring = list(orient(area, sign=1.0).exterior.coords)[:-1]  # counter-clockwise: reflex = right turn
    out = []
    for prev, here, nxt in zip([ring[-1], *ring[:-1]], ring, [*ring[1:], ring[0]], strict=True):
        if (here[0] - prev[0]) * (nxt[1] - here[1]) - (here[1] - prev[1]) * (nxt[0] - here[0]) < -1e-12:
            out.append(here)
    return out


@lru_cache(maxsize=8192)
def _walkable(outline: tuple[XY, ...], inset_m: float) -> _Walkable:
    shape = Polygon(outline)
    area = shape.buffer(-inset_m)
    if area.is_empty:
        area = shape
    elif area.geom_type != "Polygon":
        area = max(area.geoms, key=lambda g: g.area)
    cover = prep(area.buffer(1e-6))
    corners = _reflex_corners(area)
    sight: dict[int, list[tuple[int, float]]] = {i: [] for i in range(len(corners))}
    for i, j in itertools.combinations(range(len(corners)), 2):
        if cover.covers(LineString([corners[i], corners[j]])):
            d = math.dist(corners[i], corners[j])
            sight[i].append((j, d))
            sight[j].append((i, d))
    return _Walkable(area, cover, tuple(corners), sight)


def _inside(w: _Walkable, xy: XY) -> XY:
    point = Point(xy)
    if w.area.covers(point):
        return xy
    nearest = nearest_points(w.area, point)[0]
    return (nearest.x, nearest.y)


def _walks(w: _Walkable, points: list[XY]) -> list[list[float]]:
    """Walking distance between every pair of points (already inside the area). Points that can't reach
    each other (never happens in a connected area) fall back to the straight line, as the prototype does."""

    def visible(a: XY, z: XY) -> bool:
        return a == z or w.cover.covers(LineString([a, z]))

    seen = [
        [(c, math.dist(p, corner)) for c, corner in enumerate(w.corners) if visible(p, corner)]
        for p in points
    ]
    out = [[0.0] * len(points) for _ in points]
    for i, p in enumerate(points):
        best = [math.inf] * len(w.corners)
        heap = []
        for c, d in seen[i]:
            best[c] = d
            heap.append((d, c))
        heapq.heapify(heap)
        while heap:
            d, c = heapq.heappop(heap)
            if d > best[c]:
                continue
            for nxt, step in w.sight[c]:
                if d + step < best[nxt]:
                    best[nxt] = d + step
                    heapq.heappush(heap, (d + step, nxt))
        for j in range(i + 1, len(points)):
            q = points[j]
            via = min((best[c] + d for c, d in seen[j]), default=math.inf)
            walk = min(math.dist(p, q) if visible(p, q) else math.inf, via)
            out[i][j] = out[j][i] = walk if walk < math.inf else math.dist(p, q)
    return out


def walking_distance(
    b: Building, space_id: str, start: XY, end: XY, *, inset_m: float = WALL_CLEARANCE_M
) -> float:
    """Shortest walk between two points inside a space, keeping `inset_m` off its walls, around corners and
    never through walls (Synera's route rule). Points on or near the walls (doors) count from 15 cm in."""
    w = _walkable(b.spaces[space_id].polygon, inset_m)
    return _walks(w, [_inside(w, start), _inside(w, end)])[0][1]


def door_to_door(
    b: Building, space_id: str, *, inset_m: float = WALL_CLEARANCE_M
) -> dict[tuple[str, str], float]:
    """Walking distance inside a space between each pair of its ports, both orders: every opening touching
    it (keyed by opening id) and its start point, the representative point where routes start, end and
    enter stairs or lifts (keyed by the space id itself). Cached; treat the result as read-only."""
    space = b.spaces[space_id]
    ports = ((space_id, space.anchor), *((o.id, o.position) for o in b.openings_of(space_id)))
    return _door_to_door(space.polygon, ports, inset_m)


@lru_cache(maxsize=8192)
def _door_to_door(
    outline: tuple[XY, ...], ports: tuple[tuple[str, XY], ...], inset_m: float
) -> dict[tuple[str, str], float]:
    w = _walkable(outline, inset_m)
    walks = _walks(w, [_inside(w, xy) for _, xy in ports])
    names = [name for name, _ in ports]
    return {(a, z): walks[i][j] for i, a in enumerate(names) for j, z in enumerate(names) if i != j}
