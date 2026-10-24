"""Walking directions inside the rooms of a route: turns, landmarks and obstacles a person can follow.

OWNER: Owner A, graph & state (see docs/synera/TEAM_PLAN.md). New interface; the agent's
`walk_directions` tool (agents/building_guide/tools.py) is written against it.

A `Route` says which spaces and doors to pass ("H-A1, opening, H-A2, opening, Main aisle H-B2, ...").
That is right for scoring but not followable on a headset: it never says where to turn, what you walk
past, or that a machine stands between the two doors of a hall. `directions` fills in those gaps from
the geometry alone (no model), so the agent never has to invent "east" or "turn left":

- Turns: the heading change between consecutive legs of the walk (node_path positions, in the plan's
  XY frame) as left / right / straight / turn back. Never compass words: people don't know where north is.
- Landmarks: an asset close to the walk ("past the coffee machine", "at the printers"), preferring
  machines and fixtures people notice over chairs and desks.
- Obstacles: furniture and machines are not obstacles for the route model, but a person can't walk
  through a 3D printer. When the straight walk from one door to the next crosses an asset's footprint,
  say so: "go around Printer 0.01-A, keeping it on your right".
- Floors: stairs and lifts as "take Lift 1 up to Floor 3".

Golden checks: tests/building/test_directions.py (synthetic floor with a printer in the way, plus every
dev warm-up route). Names and ids come from the building, never hard-coded.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

from shapely import affinity
from shapely.geometry import LineString, Point, Polygon, box

from app.building.model import OUTSIDE, Asset, Building
from app.building.routing import DOOR_PREFIX, Route, RouteGraph

__all__ = ["Action", "Direction", "directions"]

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


# ---- tuning (judgement calls, see the module docstring) ----------------------------------------------

STRAIGHT_DEG = 30.0
"""Heading changes below this are "go straight"."""
BEAR_DEG = 60.0
"""Between STRAIGHT_DEG and this a turn is spoken as "bear left/right" (action still left/right)."""
BACK_DEG = 150.0
"""Above this the walk doubles back: "turn around"."""
MIN_HEADING_M = 0.3
"""Legs shorter than this (a door straight through a thin wall) keep the previous heading."""
LANDMARK_RADIUS_M = 3.0
"""An asset this close to the straight walk can be named as a landmark (a corridor is about 2-3 m wide)."""
LANDMARK_RANK = {"machine": 3, "equipment": 3, "fixture": 2}
"""People notice machines and equipment first, then fixtures. Furniture is only named when it is big."""
NOTABLE_FURNITURE_M2 = 2.0
"""Furniture with at least this footprint (pallet racks, workbenches, sofas) can be a landmark; chairs and
desks can't."""

MIN_OBSTACLE_M2 = 0.3
"""Smaller footprints (chairs, wall-mounted extinguishers) are stepped past, not walked around."""
SMALL_LANDMARK_M2 = 0.2
"""Landmarks smaller than this rank one lower than their category (a sink before a fire extinguisher...)."""
MIN_SPOKEN_M = 0.5
"""Shorter walks aren't spoken as distances ("turn right into the lift", not "walk 0 m")."""

_ACRONYMS = {"3d": "3D", "cnc": "CNC", "tv": "TV", "ups": "UPS", "atm": "ATM", "it": "IT", "pc": "PC"}


def directions(b: Building, route: Route) -> list[Direction]:
    """Sub-room walking directions for one leg: starts with action "start" in route.from_space_id and ends
    with "arrive" in route.to_space_id, one step per turn, door, stair or lift, plus an "around" step for
    every asset whose footprint blocks the straight walk between two consecutive points of node_path."""
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
                steps.append(Direction("start", f"Start in {_name(b, space)}.", space))
                started = True
            rides = [leg]
            while i + 1 < len(legs) and legs[i + 1].kind == "ride" and legs[i + 1].via == leg.via:
                i += 1
                rides.append(legs[i])
            steps.append(_ride(b, rides))
            space, heading = rides[-1].z, None
            i += 1
            continue
        if leg.kind == "walkway":
            w = b.walkways[leg.via]
            steps.append(
                Direction(
                    "straight",
                    f"Follow {w.name or 'the walkway'} for {_m(leg.length)} to the next building.",
                    space,
                    leg.length,
                )
            )
            heading = None
            i += 1
            continue

        # A walk inside one space, from one node to the next.
        vector = _vector(leg)
        is_first = not started
        if is_first:
            action: Action = "start"
            phrase = f"Start in {_name(b, leg.space)}"
        elif heading is None or vector is None:
            action, phrase = (
                "straight",
                "Step out" if steps and steps[-1].action in ("up", "down") else "Go on",
            )
        else:
            action, phrase = _turn(heading, vector)
        into = "" if is_first or leg.space == space else f" into {_name(b, leg.space)}"

        blockers = _blockers(b, leg) if vector is not None else []
        named.update(a.id for a, _, _ in blockers)
        cuts = [0.0] + [t for _, t, _ in blockers] + [1.0]
        pieces = [leg.length * (hi - lo) for lo, hi in zip(cuts, cuts[1:], strict=False)]

        ahead = ""
        if leg.z.startswith(DOOR_PREFIX) and (is_first or _leaves(legs, i)):
            ahead = " to the exit" if _leaves(legs, i) else " to the door"
        mark = _landmark(b, leg, named) if vector is not None else None
        tail = ""
        if mark is not None:
            named.add(mark.id)
            tail = f", past the {_kind(mark)}"
        walk = _walk(pieces[0])
        text = f"{phrase}{into}{walk}{ahead if walk and not blockers else ''}{tail}."
        steps.append(
            Direction(action, text, leg.space, pieces[0], landmarks=[mark.id] if mark is not None else [])
        )
        for k, (asset, _, side) in enumerate(blockers):
            last = k == len(blockers) - 1
            rest = pieces[k + 1]
            then = f", then walk {_m(rest)}{ahead if last else ''}" if rest >= MIN_SPOKEN_M else ""
            steps.append(
                Direction(
                    "around",
                    f"Go around {_obstacle(b, asset)}, keeping it on your {side}{then}.",
                    leg.space,
                    rest,
                    around=asset.id,
                )
            )
        started = True
        space = leg.space
        if vector is not None and math.hypot(*vector) >= MIN_HEADING_M:
            heading = vector
        elif heading is None:
            heading = vector
        i += 1

    if not started:  # start == goal: nothing to walk
        steps.append(Direction("start", f"Start in {_name(b, space)}.", space))
    if route.to_space_id == OUTSIDE:
        steps.append(Direction("arrive", "Go out through the entrance: you are outside.", OUTSIDE))
    else:
        steps.append(Direction("arrive", f"Arrive at {_name(b, route.to_space_id)}.", route.to_space_id))
    return _balance(steps, route.length_m)


# ---- the legs of the walk --------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Leg:
    a: str
    z: str
    length: float
    kind: Literal["walk", "ride", "walkway", "outside"]
    space: str = ""
    via: str = ""
    pa: tuple[float, float] | None = None
    pz: tuple[float, float] | None = None


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


def _position(b: Building, node: str) -> tuple[float, float] | None:
    if node.startswith(DOOR_PREFIX):
        o = b.openings.get(node.removeprefix(DOOR_PREFIX))
        return (float(o.position[0]), float(o.position[1])) if o else None
    s = b.spaces.get(node)
    if s is None:
        return None
    shape = Polygon(s.polygon)
    p = (shape if shape.is_valid else shape.buffer(0)).representative_point()
    return (p.x, p.y)


def _vector(leg: _Leg) -> tuple[float, float] | None:
    if leg.pa is None or leg.pz is None:
        return None
    v = (leg.pz[0] - leg.pa[0], leg.pz[1] - leg.pa[1])
    return v if math.hypot(*v) > 1e-9 else None


def _leaves(legs: list[_Leg], i: int) -> bool:
    """The walk after leg i goes out of the building."""
    return i + 1 < len(legs) and legs[i + 1].kind == "outside"


# ---- turns, rides, obstacles, landmarks ------------------------------------------------------------------


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


def _footprint(a: Asset) -> Polygon:
    w, d = max(a.width_m, 0.05), max(a.depth_m, 0.05)
    x, y = a.position
    rect = box(x - w / 2, y - d / 2, x + w / 2, y + d / 2)
    return affinity.rotate(rect, a.rotation_deg, origin=(x, y)) if a.rotation_deg else rect


def _blockers(b: Building, leg: _Leg) -> list[tuple[Asset, float, str]]:
    """Assets in the space whose footprint crosses the straight walk of this leg: (asset, where along the
    leg 0..1, which side to keep it on). An asset at either end (you walk *to* it) doesn't block. The side
    is the side its centre lies on: the walk bends round the far side, so the asset stays on that side."""
    assert leg.pa is not None and leg.pz is not None
    line = LineString([leg.pa, leg.pz])
    ends = (Point(leg.pa), Point(leg.pz))
    out = []
    for a in b.assets_in(leg.space):
        if a.width_m * a.depth_m < MIN_OBSTACLE_M2:
            continue
        shape = _footprint(a)
        if not shape.intersects(line) or any(shape.covers(p) for p in ends):
            continue
        t = line.project(Point(a.position), normalized=True)
        if not 0.0 < t < 1.0:
            continue
        dx, dy = leg.pz[0] - leg.pa[0], leg.pz[1] - leg.pa[1]
        cross = dx * (a.position[1] - leg.pa[1]) - dy * (a.position[0] - leg.pa[0])
        out.append((a, t, "left" if cross > 0 else "right"))
    return sorted(out, key=lambda hit: hit[1])


def _landmark(b: Building, leg: _Leg, named: set[str]) -> Asset | None:
    """The most noticeable asset near the walk of this leg (machines and equipment over fixtures over big
    furniture; nearest first), or None."""
    assert leg.pa is not None and leg.pz is not None
    line = LineString([leg.pa, leg.pz])
    best: tuple[int, float, str] | None = None
    pick = None
    for a in b.assets_in(leg.space):
        if a.id in named:
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


def _walk(metres: float) -> str:
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
        Direction(s.action, s.text, s.space_id, d, list(s.landmarks), s.around)
        for s, d in zip(steps, rounded, strict=True)
    ]
