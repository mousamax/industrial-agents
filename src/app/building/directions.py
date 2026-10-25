"""Walking directions inside the rooms of a route: turns, landmarks and obstacles a person can follow.

OWNER: Owner A, graph & state (see docs/synera/TEAM_PLAN.md). New interface; the agent's
`walk_directions` and `route` tools (agents/building_guide/tools.py) and the map are written against it.

A `Route` says which spaces and doors to pass ("H-A1, opening, H-A2, opening, Main aisle H-B2, ...").
That is right for scoring but not followable on a headset: it never says where to turn, what you walk
past, or that a machine stands between the two doors of a hall. This module fills in those gaps from the
geometry alone (no model), so neither the agent nor the map has to invent them:

- The walk itself: `walk_in_space` is the line a person walks through one room, from where they enter to
  where they leave, 15 cm off the walls (as the route model) and 30 cm clear of furniture and machines,
  round corners and obstacles. `walk_path` strings those together for a whole route, one polyline per
  floor: what the map draws. Only for drawing and speaking: the scored length stays the route graph's.
- Turns: heading changes along that walk (in the plan's XY frame) as left / right / straight / turn back,
  at the doors and at the room corners the walk bends round. Never compass words.
- Landmarks: an asset close to the walk ("past the coffee machine"), preferring machines and fixtures
  people notice over chairs and desks.
- Obstacles: furniture and machines are not obstacles for the route model, but a person can't walk
  through a 3D printer. When the wall-hugging walk would cross an asset and the real walk bends round it,
  say so and on which side it stays: "go around Printer 0.01-A, keeping it on your right".
- Floors: stairs and lifts as "take Lift 1 up to Floor 3".

Golden checks: tests/building/test_directions.py (synthetic floor with a printer in the way, plus every
dev warm-up route). Names and ids come from the building, never hard-coded.
"""

from __future__ import annotations

import heapq
import math
import threading
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Literal

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import nearest_points, substring, unary_union

from app.building.model import OUTSIDE, XY, Asset, Building
from app.building.routing import DOOR_PREFIX, Route, RouteGraph, outside_walks

__all__ = ["Action", "Direction", "directions", "walk_in_space", "walk_path"]

Action = Literal["start", "straight", "left", "right", "back", "around", "up", "down", "leave", "arrive"]


@dataclass(frozen=True)
class Direction:
    """One instruction a person can follow without looking at a screen."""

    action: Action
    text: str
    """Plain sentence, e.g. "Turn left into Corridor 0.02 and walk 7 m, past the coffee machine"."""
    space_id: str
    """The space this step happens in (one of the route's space_path)."""
    distance_m: float = 0.0
    """Walking distance of this step. The steps of a route add up to its length_m (within 1%)."""
    landmarks: list[str] = field(default_factory=list)
    """Asset ids named in `text` (to highlight on the map)."""
    around: str | None = None
    """Asset id of the obstacle this step goes around, when there is one."""
    points: list[XY] = field(default_factory=list)
    """The polyline walked in this step (plan metres, on its space's floor), to highlight the step being
    spoken. Empty for stairs, lifts and steps outside."""


# ---- tuning (judgement calls, see the module docstring) ----------------------------------------------

WALL_CLEARANCE_M = 0.15
"""The walk keeps this far off the walls, as the route model does (geometry.WALL_CLEARANCE_M)."""
OBSTACLE_CLEARANCE_M = 0.3
"""...and this far off furniture and machines it goes round."""
STRAIGHT_DEG = 30.0
"""Heading changes below this are "go straight"."""
BEAR_DEG = 60.0
"""Between STRAIGHT_DEG and this a turn is spoken as "bear left/right" (action still left/right)."""
BACK_DEG = 150.0
"""Above this the walk doubles back: "turn around"."""
MIN_HEADING_M = 0.3
"""Pieces of walk shorter than this (stepping through a door) don't set the heading."""
SIMPLIFY_M = 0.25
"""Wiggles smaller than this are ignored when reading turns off the walk."""
TURN_MIN_M = 1.5
"""A bend inside a room is spoken as a turn only with at least this much walk before and after it."""
LANDMARK_RADIUS_M = 3.0
"""An asset this close to the walk can be named as a landmark (a corridor is about 2-3 m wide)."""
LANDMARK_RANK = {"machine": 3, "equipment": 3, "fixture": 2}
"""People notice machines and equipment first, then fixtures. Furniture is only named when it is big."""
NOTABLE_FURNITURE_M2 = 2.0
"""Furniture with at least this footprint (pallet racks, workbenches, sofas) can be a landmark."""
MIN_OBSTACLE_M2 = 0.3
"""Smaller footprints (chairs, wall-mounted extinguishers) are stepped past, not walked around."""
SMALL_LANDMARK_M2 = 0.2
"""Landmarks smaller than this rank one lower than their category (a sink before a fire extinguisher)."""
MIN_SPOKEN_M = 0.5
"""Shorter walks aren't spoken as distances ("turn right into the lift", not "walk 0 m")."""
AROUND_MERGE_M = 2.0
"""Obstacles closer together than this along the walk are one "go around" (a row of desks)."""

_ACRONYMS = {"3d": "3D", "cnc": "CNC", "tv": "TV", "ups": "UPS", "atm": "ATM", "it": "IT", "pc": "PC"}


# ---- the walk through one room ---------------------------------------------------------------------------


def walk_in_space(b: Building, space_id: str, start: XY, end: XY) -> list[XY]:
    """The line a person walks inside one space from `start` to `end` (doors or its representative point):
    the shortest walk that keeps WALL_CLEARANCE_M off the walls and OBSTACLE_CLEARANCE_M off furniture and
    machines, bending round corners and obstacles. Starts at `start`, ends at `end`.

    An outline drawn with a zero-width slit out to an enclosed room is repaired first (the slit is not a
    wall). A point inside a wall margin or an obstacle's clearance steps to the nearest free spot; an asset
    under `start` or `end` (walking *to* the machine) is not an obstacle; and when the obstacles cut the
    room in two, they are ignored rather than failing. Cached on the geometry, so an edited building only
    recomputes the rooms that changed."""
    space = b.spaces[space_id]
    start, end = _key(start), _key(end)
    return list(_walk(space.polygon, _obstacles(b, space_id, start, end), start, end))


def walk_path(b: Building, route: Route) -> list[dict[str, Any]]:
    """The walk of a route for the map: `[{"floor_id": str, "points": [[x, y], ...]}]`, one segment per
    stretch on one floor (a new one where stairs, a lift or a walkway change the floor), continuous through
    doors, each room's piece from `walk_in_space`. Starts and ends at the start and end spaces'
    representative points (an entrance when the route starts or ends outside), like route.node_path.
    Points are rounded to centimetres, with collinear points dropped."""
    segments: list[tuple[str, list[XY]]] = []

    def add(floor_id: str, points: list[XY]) -> None:
        if segments and segments[-1][0] == floor_id:
            segments[-1][1].extend(points)
        else:
            segments.append((floor_id, list(points)))

    legs = _legs(b, route)
    for k, leg in enumerate(legs):
        if leg.kind == "walk" and leg.pa is not None and leg.pz is not None:
            add(_floor_of(b, leg.space, leg.a), _leg_walk(b, leg))
        elif leg.kind == "around":  # out one entrance and round the building to another
            add(_floor_of(b, OUTSIDE, leg.a), list(leg.points))
        elif leg.kind == "ride":  # one ride per connector: its first and last stop, not the floors passed
            ends = [leg.a] if k == 0 or legs[k - 1].via != leg.via else []
            ends += [leg.z] if k + 1 == len(legs) or legs[k + 1].via != leg.via else []
            for node in ends:
                position = _position(b, node)
                if position is not None:
                    add(b.spaces[node].floor_id, [position])
    if not segments and route.node_path:  # a route that doesn't move
        node = route.node_path[0]
        position = _position(b, node)
        if position is not None and node in b.spaces:
            add(b.spaces[node].floor_id, [position])
    return [{"floor_id": floor_id, "points": _compact(points)} for floor_id, points in segments]


_PREPARED = threading.Lock()
"""A prepared geometry builds its search indexes lazily on first use and GEOS does not guard that: two
threads asking the same cached `_Free.cover` at once can corrupt it and crash the process. Every predicate
on a cover takes this lock (tools run in worker threads, many conversations at once)."""


def _covers(cover: Any, lines: Any) -> Any:
    with _PREPARED:
        return shapely.covers(cover, lines)


@dataclass(frozen=True)
class _Free:
    """Where a person can walk in one room: the outline, inset off the walls, minus obstacle clearances."""

    area: Any  # Polygon | MultiPolygon
    cover: Any  # the area a hair bigger, prepared: "does this segment stay inside?"
    corners: np.ndarray  # (n, 2) reflex corners of the area, where a shortest walk can bend


def _outline(points: tuple[XY, ...]) -> Polygon:
    """The room's polygon, repaired like the route model's (buffer(0) also turns a zero-width slit out to an
    enclosed room into a hole, so the slit isn't a wall)."""
    polygon = Polygon(points)
    return polygon if polygon.is_valid else polygon.buffer(0)


@lru_cache(maxsize=4096)
def _free(outline: tuple[XY, ...], obstacles: tuple[tuple[XY, ...], ...]) -> _Free | None:
    shape = _outline(outline)
    area = shape.buffer(-WALL_CLEARANCE_M, join_style="mitre", mitre_limit=2.0)
    if area.is_empty:
        area = shape
    if obstacles:
        area = area.difference(unary_union([Polygon(o) for o in obstacles]))
    parts = [g for g in getattr(area, "geoms", [area]) if g.geom_type == "Polygon" and not g.is_empty]
    if not parts:
        return None
    corners: list[XY] = []
    for part in parts:
        part = orient(part, sign=1.0)  # exterior counter-clockwise, holes clockwise: reflex = right turn
        for ring in (part.exterior, *part.interiors):
            coords = list(ring.coords)[:-1]
            for prev, here, nxt in zip(
                [coords[-1], *coords[:-1]], coords, [*coords[1:], coords[0]], strict=True
            ):
                if (here[0] - prev[0]) * (nxt[1] - here[1]) - (here[1] - prev[1]) * (
                    nxt[0] - here[0]
                ) < -1e-12:
                    corners.append((here[0], here[1]))
    cover = area.buffer(1e-6)
    with _PREPARED:
        shapely.prepare(cover)
    return _Free(area, cover, np.array(corners, dtype=float).reshape(-1, 2))


def _snap(free: _Free, p: XY) -> XY:
    point = Point(p)
    if free.area.covers(point):
        return p
    near = nearest_points(free.area, point)[0]
    return (near.x, near.y)


def _shortest(free: _Free, a: XY, z: XY) -> list[XY] | None:
    """Shortest walk from a to z (both inside the free area) over the visibility graph of its corners: A*
    that looks for visible corners only from the nodes it expands, many segments at a time."""
    if a == z or _covers(free.cover, LineString([a, z])):
        return [a, z]
    nodes = np.vstack([np.array([a, z], dtype=float), free.corners])
    goal = nodes[1]
    to_goal = np.hypot(*(nodes - goal).T)
    g = np.full(len(nodes), np.inf)
    g[0] = 0.0
    prev = np.full(len(nodes), -1)
    closed = np.zeros(len(nodes), dtype=bool)
    heap = [(to_goal[0], 0)]
    while heap:
        _, i = heapq.heappop(heap)
        if closed[i]:
            continue
        if i == 1:
            break
        closed[i] = True
        step = np.hypot(*(nodes - nodes[i]).T)
        better = ~closed & (g[i] + step < g)
        candidates = np.flatnonzero(better)
        if not len(candidates):
            continue
        lines = shapely.linestrings(
            np.stack([np.repeat(nodes[i][None, :], len(candidates), axis=0), nodes[candidates]], axis=1)
        )
        for k in candidates[_covers(free.cover, lines)]:
            g[k] = g[i] + step[k]
            prev[k] = i
            heapq.heappush(heap, (g[k] + to_goal[k], int(k)))
    if not math.isfinite(g[1]):
        return None
    path, k = [], 1
    while k != -1:
        path.append((float(nodes[k][0]), float(nodes[k][1])))
        k = int(prev[k])
    return path[::-1]


@lru_cache(maxsize=16384)
def _walk(
    outline: tuple[XY, ...], obstacles: tuple[tuple[XY, ...], ...], start: XY, end: XY
) -> tuple[XY, ...]:
    for blocked in (obstacles, ()) if obstacles else ((),):
        free = _free(outline, blocked)
        if free is None:
            continue
        path = _shortest(free, _snap(free, start), _snap(free, end))
        if path is not None:
            return tuple(_dedupe([start, *path, end]))
    return (start, end)


def _footprint(a: Asset, margin: float = 0.0) -> Polygon:
    """The asset's rotated rectangle, grown by `margin` on every side."""
    w, d = max(a.width_m, 0.05) / 2 + margin, max(a.depth_m, 0.05) / 2 + margin
    x, y = a.position
    c, s = math.cos(math.radians(a.rotation_deg)), math.sin(math.radians(a.rotation_deg))
    return Polygon([(x + c * u - s * v, y + s * u + c * v) for u, v in ((-w, -d), (w, -d), (w, d), (-w, d))])


def _is_obstacle(a: Asset) -> bool:
    return a.width_m * a.depth_m >= MIN_OBSTACLE_M2


def _obstacle_assets(b: Building, space_id: str, start: XY, end: XY) -> list[Asset]:
    """The assets a walk from start to end goes round: big enough, and not the one you walk to."""
    ends = (Point(start), Point(end))
    return [
        a for a in b.assets_in(space_id) if _is_obstacle(a) and not any(_footprint(a).covers(p) for p in ends)
    ]


def _obstacles(b: Building, space_id: str, start: XY, end: XY) -> tuple[tuple[XY, ...], ...]:
    return tuple(
        tuple((round(x, 3), round(y, 3)) for x, y in _footprint(a, OBSTACLE_CLEARANCE_M).exterior.coords)
        for a in sorted(_obstacle_assets(b, space_id, start, end), key=lambda a: a.id)
    )


def _key(p: XY) -> XY:
    return (round(float(p[0]), 4), round(float(p[1]), 4))


def _dedupe(points: list[XY]) -> list[XY]:
    out: list[XY] = []
    for p in points:
        if not out or math.dist(out[-1], p) > 1e-6:
            out.append(p)
    return out


def _compact(points: list[XY]) -> list[list[float]]:
    """Round to centimetres, drop repeats and points on a straight line between their neighbours."""
    rounded = _dedupe([(round(x, 2), round(y, 2)) for x, y in points])
    out: list[tuple[float, float]] = []
    for p in rounded:
        while len(out) >= 2:
            o, q = out[-2], out[-1]
            ab, bc = (q[0] - o[0], q[1] - o[1]), (p[0] - q[0], p[1] - q[1])
            cross = ab[0] * bc[1] - ab[1] * bc[0]
            dot = ab[0] * bc[0] + ab[1] * bc[1]
            if dot > 0 and abs(cross) <= 0.01 * math.hypot(*ab) * math.hypot(*bc):
                out.pop()
            else:
                break
        out.append(p)
    return [[x, y] for x, y in out]


# ---- directions -------------------------------------------------------------------------------------------


def directions(b: Building, route: Route) -> list[Direction]:
    """Sub-room walking directions for one leg: starts with action "start" in route.from_space_id and ends
    with "arrive" in route.to_space_id, one step per turn, door, stair or lift, plus an "around" step for
    every obstacle the walk bends round. Turns and sides are read off `walk_in_space`; each step carries its
    piece of that walk in `points`, and the distances add up to route.length_m."""
    legs = _legs(b, route)
    steps: list[Direction] = []
    named: set[str] = set()  # landmarks already spoken: never twice
    heading: tuple[float, float] | None = None
    space = route.from_space_id
    started = False

    if route.from_space_id == OUTSIDE:
        steps.append(Direction("start", "Go in through the entrance.", OUTSIDE))
        started = True

    i = 0
    while i < len(legs):
        leg = legs[i]
        if leg.kind == "outside":
            i += 1
            continue
        if leg.kind == "ride":
            if not started:
                steps.append(Direction("start", f"Start in {_name(b, space)}.", space, points=_at(b, space)))
                started = True
            rides = [leg]
            while i + 1 < len(legs) and legs[i + 1].kind == "ride" and legs[i + 1].via == leg.via:
                i += 1
                rides.append(legs[i])
            steps.append(_ride(b, rides))
            space, heading = rides[-1].z, None
            i += 1
            continue
        if leg.kind == "around":
            text = (
                f"Go outside through {_entrance(b, leg.a)}, walk {_m(leg.length)} around the building to "
                f"{_entrance(b, leg.z)}."
            )
            steps.append(Direction("leave", text, OUTSIDE, leg.length, points=list(leg.points)))
            space, heading, started = OUTSIDE, None, True
            i += 1
            continue
        if leg.kind == "walkway":
            w = b.walkways[leg.via]
            text = f"Follow {w.name or 'the walkway'} for {_m(leg.length)} to the next building."
            steps.append(Direction("straight", text, space, leg.length))
            heading = None
            i += 1
            continue
        if leg.pa is None or leg.pz is None:  # no geometry (shouldn't happen): one plain step
            steps.append(Direction("straight", f"Walk{_walk_words(leg.length)}.", leg.space, leg.length))
            space, started = leg.space, True
            i += 1
            continue
        after_ride = bool(steps) and steps[-1].action in ("up", "down")
        steps.extend(_walk_steps(b, leg, legs, i, heading, space, started, named, after_ride))
        poly = _leg_walk(b, leg)
        out = _heading(_simplified(poly)[::-1])
        if out is not None:
            heading = (-out[0], -out[1])
        started = True
        space = leg.space
        i += 1

    if not started:  # start == goal: nothing to walk
        steps.append(Direction("start", f"Start in {_name(b, space)}.", space, points=_at(b, space)))
    if route.to_space_id == OUTSIDE:
        steps.append(Direction("arrive", "Go out through the entrance: you are outside.", OUTSIDE))
    else:
        end = route.to_space_id
        steps.append(Direction("arrive", f"Arrive at {_name(b, end)}.", end, points=_at(b, end)))
    return _balance(steps, route.length_m)


def _walk_steps(
    b: Building,
    leg: _Leg,
    legs: list[_Leg],
    i: int,
    heading: tuple[float, float] | None,
    space: str,
    started: bool,
    named: set[str],
    after_ride: bool = False,
) -> list[Direction]:
    """The steps of one walk inside a space: the turn into it, turns at corners it bends round, and a
    "go around" for each obstacle on the way. Distances split leg.length in proportion to the walk."""
    poly = _leg_walk(b, leg)
    line = LineString(poly) if len(poly) > 1 else None
    total = line.length if line is not None else 0.0
    simple = _simplified(poly)
    entry = _heading(simple)

    is_first = not started
    if is_first:
        action: Action = "start"
        phrase = f"Start in {_name(b, leg.space)}"
    elif heading is None or entry is None:
        action, phrase = "straight", "Step out" if after_ride else "Go"
    else:
        action, phrase = _turn(heading, entry)
    into = "" if is_first or leg.space == space else f" into {_name(b, leg.space)}"
    ahead = ""
    if leg.z.startswith(DOOR_PREFIX) and (is_first or _leaves(legs, i)):
        ahead = " to the exit" if _leaves(legs, i) else " to the door"

    # Where the walk is cut into steps: (distance along, kind, payload).
    cuts: list[tuple[float, str, Any]] = []
    if line is not None and total > 0:
        arounds = _arounds(b, leg, poly)
        named.update(a.id for a, _, _ in arounds)
        for asset, along, side in arounds:
            reach = max(asset.width_m, asset.depth_m) / 2 + OBSTACLE_CLEARANCE_M
            cuts.append((max(0.0, along - reach), "around", (asset, side)))
        outline = _outline(b.spaces[leg.space].polygon).boundary
        hazards = [_footprint(a, OBSTACLE_CLEARANCE_M + 0.1) for a, _, _ in arounds]
        for k in range(1, len(simple) - 1):
            before, here, after = simple[k - 1], simple[k], simple[k + 1]
            if math.dist(before, here) < TURN_MIN_M or math.dist(here, after) < TURN_MIN_M:
                continue
            if outline.distance(Point(here)) > 2 * WALL_CLEARANCE_M + 0.05:  # round furniture, not a corner
                continue
            if any(h.covers(Point(here)) for h in hazards):
                continue
            turn, words = _turn(
                (here[0] - before[0], here[1] - before[1]), (after[0] - here[0], after[1] - here[1])
            )
            if turn != "straight":
                cuts.append((line.project(Point(here)), "turn", (turn, words)))
    cuts.sort(key=lambda c: c[0])
    kept: list[tuple[float, str, Any]] = []
    for cut in cuts:
        if kept and cut[0] - kept[-1][0] < MIN_HEADING_M:
            if cut[1] == "around" and kept[-1][1] == "turn":
                kept[-1] = cut
            continue
        if cut[1] == "turn" and (cut[0] < MIN_HEADING_M or total - cut[0] < MIN_HEADING_M):
            continue
        kept.append(cut)
    bounds = [0.0] + [c[0] for c in kept] + [total]
    shares = [(hi - lo) / total if total > 0 else 0.0 for lo, hi in zip(bounds, bounds[1:], strict=False)]
    if total <= 0:
        shares[0] = 1.0
    pieces = [leg.length * s for s in shares]
    lines = (
        [
            _compact(list(substring(line, lo, hi).coords)) if line is not None and hi > lo else []
            for lo, hi in zip(bounds, bounds[1:], strict=False)
        ]
        if line is not None
        else [_compact(poly)]
    )

    avoid = {payload[0].type for _, kind, payload in kept if kind == "around"}
    mark = _landmark(b, leg, poly, named, avoid)
    mark_at = -1
    if mark is not None:
        named.add(mark.id)
        shape = _footprint(mark)
        mark_at = min(
            range(len(lines)),
            key=lambda k: shape.distance(LineString(lines[k])) if len(lines[k]) > 1 else math.inf,
        )

    out: list[Direction] = []
    for k, metres in enumerate(pieces):
        last = k == len(pieces) - 1
        tail = f", past the {_kind(mark)}" if k == mark_at and mark is not None else ""
        to = ahead if last and metres >= MIN_SPOKEN_M else ""
        marks = [mark.id] if k == mark_at and mark is not None else []
        points = [tuple(p) for p in lines[k]] if k < len(lines) else []
        if k == 0:
            text = f"{phrase}{into}{_walk_words(metres)}{to}{tail}."
            out.append(Direction(action, text, leg.space, metres, marks, None, points))
            continue
        _, kind, payload = kept[k - 1]
        if kind == "turn":
            turn, words = payload
            out.append(
                Direction(
                    turn, f"{words}{_walk_words(metres)}{to}{tail}.", leg.space, metres, marks, None, points
                )
            )
        else:
            asset, side = payload
            then = f", then walk {_m(metres)}{to}" if metres >= MIN_SPOKEN_M else ""
            text = f"Go around {_obstacle(b, asset)}, keeping it on your {side}{then}{tail}."
            out.append(Direction("around", text, leg.space, metres, marks, asset.id, points))
    return out


# ---- the legs of the walk --------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Leg:
    a: str
    z: str
    length: float
    kind: Literal["walk", "ride", "walkway", "outside", "around"]
    space: str = ""
    via: str = ""
    pa: XY | None = None
    pz: XY | None = None
    points: tuple[XY, ...] = ()
    """An "around" leg (Constraints.outside_walking): the walk round the building, entrance to entrance."""


_GRAPHS: dict[int, tuple[Building, RouteGraph]] = {}


def _graph(b: Building) -> RouteGraph:
    """The unconstrained route graph of a building, built once (edits make a new Building object)."""
    hit = _GRAPHS.get(id(b))
    if hit is not None and hit[0] is b:
        return hit[1]
    graph = RouteGraph(b)
    if len(_GRAPHS) >= 8:
        _GRAPHS.pop(next(iter(_GRAPHS)))
    _GRAPHS[id(b)] = (b, graph)
    return graph


def _legs(b: Building, route: Route) -> list[_Leg]:
    """One leg per edge of node_path, with the edge's own length from the route graph (so the walk is
    measured exactly as the route was: door landings, wall clearance, stair and lift costs)."""
    nodes = route.node_path
    if len(nodes) < 2:
        return []
    edges = _graph(b)._edges
    used = set(route.element_path)
    spaces = set(route.space_path)
    legs = []
    for a, z in zip(nodes, nodes[1:], strict=False):
        if OUTSIDE in (a, z):
            legs.append(_Leg(a, z, 0.0, "outside", OUTSIDE))
            continue
        options = [e for e in edges.get(a, ()) if e[0] == z]
        fitting = [e for e in options if (not e[2] or e[2] in used) and (not e[3] or e[3] in spaces)]
        around = _around(b, a, z) if OUTSIDE in route.space_path[1:-1] else None
        if around is not None and all(around.length < e[1] for e in fitting):
            legs.append(around)
            continue
        if not (fitting or options):  # not in the unconstrained graph (shouldn't happen): straight line
            pa, pz = _position(b, a), _position(b, z)
            length = math.dist(pa, pz) if pa and pz else 0.0
            space = a if a in b.spaces else z if z in b.spaces else route.from_space_id
            legs.append(_Leg(a, z, length, "walk", space, "", pa, pz))
            continue
        _, length, via, space = min(fitting or options, key=lambda e: e[1])
        if via in b.connectors:
            legs.append(_Leg(a, z, length, "ride", a, via))
        elif via in b.walkways:
            legs.append(_Leg(a, z, length, "walkway", "", via))
        else:
            legs.append(_Leg(a, z, length, "walk", space, "", _position(b, a), _position(b, z)))
    return legs


def _around(b: Building, a: str, z: str) -> _Leg | None:
    """The walk round the outside from entrance node a to entrance node z, if the two are joined so."""
    first, second = a.removeprefix(DOOR_PREFIX), z.removeprefix(DOOR_PREFIX)
    walk = outside_walks(b).get((min(first, second), max(first, second)))
    if walk is None or not (a.startswith(DOOR_PREFIX) and z.startswith(DOOR_PREFIX)):
        return None
    length, points = walk
    return _Leg(a, z, round(length, 3), "around", OUTSIDE, points=points if first < second else points[::-1])


def _entrance(b: Building, node: str) -> str:
    """'the entrance of Office 0.01': entrances have no names of their own."""
    o = b.openings[node.removeprefix(DOOR_PREFIX)]
    return f"the entrance of {_name(b, o.other_side(OUTSIDE))}"


def _leg_walk(b: Building, leg: _Leg) -> list[XY]:
    assert leg.pa is not None and leg.pz is not None
    if leg.space not in b.spaces:
        return [leg.pa, leg.pz]
    return walk_in_space(b, leg.space, leg.pa, leg.pz)


def _position(b: Building, node: str) -> XY | None:
    if node.startswith(DOOR_PREFIX):
        o = b.openings.get(node.removeprefix(DOOR_PREFIX))
        return (float(o.position[0]), float(o.position[1])) if o else None
    s = b.spaces.get(node)
    if s is None:
        return None
    p = _outline(s.polygon).representative_point()
    return (p.x, p.y)


def _floor_of(b: Building, space_id: str, node: str) -> str:
    if space_id in b.spaces:
        return b.spaces[space_id].floor_id
    o = b.openings.get(node.removeprefix(DOOR_PREFIX))
    return o.floor_id if o else ""


def _at(b: Building, space_id: str) -> list[XY]:
    p = _position(b, space_id)
    return [(round(p[0], 2), round(p[1], 2))] if p is not None else []


def _leaves(legs: list[_Leg], i: int) -> bool:
    """The walk after leg i goes out of the building."""
    return i + 1 < len(legs) and legs[i + 1].kind in ("outside", "around")


# ---- turns, rides, obstacles, landmarks ------------------------------------------------------------------


def _simplified(poly: list[XY]) -> list[XY]:
    if len(poly) < 3:
        return list(poly)
    return [(x, y) for x, y in LineString(poly).simplify(SIMPLIFY_M, preserve_topology=False).coords]


def _heading(points: list[XY]) -> tuple[float, float] | None:
    """Direction of the first piece of the walk that is long enough to have one (else of the whole walk)."""
    for p, q in zip(points, points[1:], strict=False):
        if math.dist(p, q) >= MIN_HEADING_M:
            return (q[0] - p[0], q[1] - p[1])
    if len(points) > 1 and math.dist(points[0], points[-1]) > 1e-9:
        return (points[-1][0] - points[0][0], points[-1][1] - points[0][1])
    return None


def _turn(before: tuple[float, float], after: tuple[float, float]) -> tuple[Action, str]:
    """Left / right / straight / back from the signed heading change (counter-clockwise = left, plan XY)."""
    cross = before[0] * after[1] - before[1] * after[0]
    dot = before[0] * after[0] + before[1] * after[1]
    angle = math.degrees(math.atan2(cross, dot))
    if abs(angle) < STRAIGHT_DEG:
        return "straight", "Go straight"
    if abs(angle) > BACK_DEG:
        return "back", "Turn around"
    side: Action = "left" if angle > 0 else "right"
    return side, f"{'Bear' if abs(angle) < BEAR_DEG else 'Turn'} {side}"


def _ride(b: Building, rides: list[_Leg]) -> Direction:
    k = b.connectors[rides[0].via]
    start, end = b.spaces[rides[0].a], b.spaces[rides[-1].z]
    low, high = b.floors[start.floor_id], b.floors[end.floor_id]
    up = (high.level, high.elevation_m) >= (low.level, low.elevation_m)
    name = k.name or ("the lift" if k.kind == "lift" else "the stairs")
    text = f"Take {name} {'up' if up else 'down'} to {high.name}."
    return Direction("up" if up else "down", text, start.id, sum(r.length for r in rides))


def _arounds(b: Building, leg: _Leg, poly: list[XY]) -> list[tuple[Asset, float, str]]:
    """Obstacles the walk bends round: the wall-hugging walk (no obstacles) would cross the asset and the
    real walk doesn't. (asset, distance along the real walk, side it stays on), merging obstacles closer
    together than AROUND_MERGE_M into the biggest of them. The side is where the asset's centre lies
    relative to the direction of walking where the walk passes it."""
    assert leg.pa is not None and leg.pz is not None
    space = b.spaces[leg.space]
    start, end = _key(leg.pa), _key(leg.pz)
    bare = LineString(_walk(space.polygon, (), start, end))
    real = LineString(poly)
    hits = []
    for a in _obstacle_assets(b, leg.space, start, end):
        shape = _footprint(a)
        if not shape.intersects(bare) or shape.intersects(real):
            continue
        along = real.project(Point(a.position))
        p = real.interpolate(max(0.0, along - 0.5))
        q = real.interpolate(min(real.length, along + 0.5))
        dx, dy = q.x - p.x, q.y - p.y
        cross = dx * (a.position[1] - p.y) - dy * (a.position[0] - p.x)
        hits.append((a, along, "left" if cross > 0 else "right"))
    hits.sort(key=lambda h: h[1])
    merged: list[tuple[Asset, float, str]] = []
    for hit in hits:
        if merged and hit[1] - merged[-1][1] < AROUND_MERGE_M and hit[2] == merged[-1][2]:
            if hit[0].width_m * hit[0].depth_m > merged[-1][0].width_m * merged[-1][0].depth_m:
                merged[-1] = (hit[0], merged[-1][1], hit[2])
            continue
        merged.append(hit)
    return merged


def _landmark(
    b: Building, leg: _Leg, poly: list[XY], named: set[str], avoid: set[str] = frozenset()
) -> Asset | None:
    """The most noticeable asset near the walk (machines and equipment over fixtures over big furniture;
    nearest first), or None. Not one of the `avoid` types (what the step already goes around)."""
    line = LineString(poly) if len(poly) > 1 else Point(poly[0])
    best: tuple[int, float, str] | None = None
    pick = None
    for a in b.assets_in(leg.space):
        if a.id in named or a.type in avoid:
            continue
        rank = LANDMARK_RANK.get(a.category, 0)
        area = a.width_m * a.depth_m
        if rank == 0 and area >= NOTABLE_FURNITURE_M2:
            rank = 1
        elif rank and area < SMALL_LANDMARK_M2:
            rank -= 1
        if rank == 0:
            continue
        distance = _footprint(a).distance(line)
        if distance > LANDMARK_RADIUS_M:
            continue
        key = (-rank, round(distance, 1), a.id)
        if best is None or key < best:
            best, pick = key, a
    return pick


# ---- words ------------------------------------------------------------------------------------------------


def _name(b: Building, space_id: str) -> str:
    if space_id == OUTSIDE:
        return "outside"
    s = b.spaces[space_id]
    return s.name or s.room_type.replace("_", " ") or s.type


def _kind(a: Asset) -> str:
    """'coffee_machine' -> 'coffee machine', '3d_printer' -> '3D printer'."""
    words = (a.type or a.name).replace("_", " ").split()
    return " ".join(_ACRONYMS.get(w.lower(), w) for w in words)


def _obstacle(b: Building, a: Asset) -> str:
    """'the printer' when it is the only one of its kind in the room, else its name ('Printer 0.01-A')."""
    same = [x for x in b.assets_in(a.space_id) if x.type == a.type]
    return a.name if len(same) > 1 or not a.type else f"the {_kind(a)}"


def _walk_words(metres: float) -> str:
    return f" and walk {_m(metres)}" if metres >= MIN_SPOKEN_M else ""


def _m(metres: float) -> str:
    return f"{max(1, round(metres))} m"


def _balance(steps: list[Direction], length_m: float) -> list[Direction]:
    """Round step distances and put the rounding remainder on the longest step, so they add up exactly."""
    rounded = [round(s.distance_m, 3) for s in steps]
    rest = round(length_m - sum(rounded), 3)
    if rest and any(rounded):
        k = max(range(len(rounded)), key=rounded.__getitem__)
        rounded[k] = round(rounded[k] + rest, 3)
    return [
        Direction(s.action, s.text, s.space_id, d, list(s.landmarks), s.around, list(s.points))
        for s, d in zip(steps, rounded, strict=True)
    ]
