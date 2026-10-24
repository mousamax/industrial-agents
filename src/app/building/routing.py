"""L3 route graph: shortest paths, tours, reachability and distance fields under constraints.

OWNER: Owner A, graph & state (see docs/synera/TEAM_PLAN.md). Interface frozen: change signatures only
after telling the team; tools, benchmark and UI are written against them.

The route model is the one the judges grade with (synera-kit 1.1.0, README section 3, CHANGELOG):
- Nodes: one per space at its representative point (polygon repaired with buffer(0) if invalid), one per
  opening ("door:<id>") at its position.
- Walking inside a space: every pair of the space's nodes (centre and each opening) is joined by the
  shortest walk that stays 15 cm off the walls and goes round corners (a visibility graph on the shrunken
  outline). An opening lands 30 cm inside each space, and the step to the landing is added. So a corridor
  you pass through is walked door to door. Edge lengths are rounded to 3 decimals.
- Stairs: 2 m per metre climbed, one edge per flight. Lifts: 15 m once per ride plus 1 m per metre
  climbed, an edge between every pair of stops. Height = difference of the floors' elevation_m.
- No way round the outside: an entrance leads out, not into another part of the building. OUTSIDE is
  only a start or an end (distances_from([OUTSIDE]) = walk to the nearest entrance), never passed through.
- Step-free drops stairs and openings with step_free False; closed openings, connectors and spaces are
  dropped; ours adds min_width_m (openings narrower than the load are dropped).
The geometry of walking inside one space is the kit's own reference code (starter_agent.building_map:
door_landing, walking_lengths), imported rather than copied, so the walk is graded the way it's computed.

Golden tests: tests/building/test_golden.py::test_route* and ::test_graph_* reproduce Synera's answers.
"""

from __future__ import annotations

import hashlib
import heapq
import itertools
import json
import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache

from shapely.geometry import Polygon
from starter_agent.building_map import door_landing, walking_lengths

from app.building.model import OUTSIDE, Building, Connector, Opening, Space
from app.settings import ROOT

WALK_CACHE = ROOT / ".cache" / "walks"
"""Walks inside a space, on disk by a hash of its geometry: a detailed outline costs seconds (its 15 cm
shrink can have 1,000 corners), so each space is computed once per machine. Safe to delete."""

__all__ = [
    "FORKLIFT",
    "OUTSIDE",
    "PRODUCTION_BAYS",
    "WALKING",
    "Constraints",
    "Route",
    "RouteGraph",
    "Tour",
    "validate_route",
]

DOOR_PREFIX = "door:"
STAIRS_M_PER_RISE = 2.0
LIFT_FIXED_M = 15.0
LIFT_M_PER_RISE = 1.0
LENGTH_DECIMALS = 3


@dataclass(frozen=True)
class Constraints:
    """What a mover can't use. The default is a person walking with no closures."""

    step_free: bool = False
    """Wheelchair, pram, trolley, forklift: no stairs, no stepped entrances. Lifts are fine."""
    min_width_m: float | None = None
    """Every opening passed must be at least this wide (wide_delivery questions: 1.0 or 1.1 m)."""
    closed: frozenset[str] = frozenset()
    """Element ids (openings, connectors or spaces) that can't be used."""

    def with_closed(self, *element_ids: str) -> Constraints:
        return Constraints(self.step_free, self.min_width_m, self.closed | set(element_ids))


WALKING = Constraints()
"""A person walking, nothing closed: the default everywhere."""

FORKLIFT = Constraints(step_free=True, min_width_m=3.0)
"""Synera: forklift aisles and openings 3 m or wider. Aisle-only isn't checked: the data has no aisle type."""

PRODUCTION_BAYS = frozenset({"production_zone", "assembly_zone", "additive_zone"})
"""Room types a route may end in but never pass through. Our reading of Synera's rule (open question 3 in
ROUTE_SCORING.md); checked by validate_route, not used to rank routes (the graded answers don't)."""


@dataclass(frozen=True)
class Route:
    """One leg, in the same shape as the golden answers (questions_basic.jsonl, kind=route)."""

    from_space_id: str
    to_space_id: str
    length_m: float
    space_path: list[str]
    """Every space the route passes through, in order, including corridors crossed door to door (and
    "outside" last when the route leaves the building)."""
    element_path: list[str]
    """The openings, stairs and lifts used, in order, each once (like the golden `element_path`)."""
    node_path: list[str] = field(default_factory=list)
    """Raw graph nodes (space ids, 'door:<opening id>', 'outside'); used to draw the route."""


@dataclass(frozen=True)
class Tour:
    stops: list[str]
    length_m: float
    legs: list[Route]


def _outline(points: tuple[tuple[float, float], ...]) -> Polygon:
    polygon = Polygon(points)
    return polygon if polygon.is_valid else polygon.buffer(0)


def _anchor(space: Space) -> tuple[float, float]:
    p = _outline(space.polygon).representative_point()
    return (p.x, p.y)


@lru_cache(maxsize=100_000)
def _space_walks(
    polygon: tuple[tuple[float, float], ...], doors: tuple[tuple[str, tuple[float, float]], ...]
) -> tuple[tuple[int, int, float], ...]:
    """Walks inside one space between its centre (index 0) and each door (1..n): (i, j, metres), i < j.
    Cached on the geometry itself (memory, then disk), so an edited building recomputes only what changed."""
    key = hashlib.sha1(repr((polygon, [position for _, position in doors])).encode()).hexdigest()
    path = WALK_CACHE / key[:2] / f"{key}.json"
    if path.exists():
        return tuple((i, j, d) for i, j, d in json.loads(path.read_text()))
    shape = _outline(polygon)
    centre = shape.representative_point()
    members = [((centre.x, centre.y), 0.0)]
    for _, position in doors:
        landing = door_landing(shape, position)
        members.append((landing, math.dist(landing, position)))
    lengths = walking_lengths(shape, [point for point, _ in members])
    walks = tuple(
        (i, j, lengths[(i, j)] + members[i][1] + members[j][1])
        for i, j in itertools.combinations(range(len(members)), 2)
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(walks))
    except OSError:
        pass  # read-only checkout: memory cache only
    return walks


class RouteGraph:
    """Built once per (building, constraints). Walks inside spaces are cached, so rebuilding is cheap."""

    def __init__(self, building: Building, constraints: Constraints = WALKING) -> None:
        self.building = building
        self.constraints = constraints
        # Multigraph as adjacency lists: node -> [(neighbour, metres, element id or "", space walked or "")].
        self._edges: dict[str, list[tuple[str, float, str, str]]] = defaultdict(list)
        self._build()

    def _add_edge(self, a: str, b: str, weight: float, via: str = "", space: str = "") -> None:
        weight = round(weight, LENGTH_DECIMALS)
        self._edges[a].append((b, weight, via, space))
        self._edges[b].append((a, weight, via, space))

    def _build(self) -> None:
        b, c = self.building, self.constraints
        open_spaces = {sid: s for sid, s in b.spaces.items() if sid not in c.closed}
        self._nodes = set(open_spaces)
        open_doors = {o.id for o in b.openings.values() if _usable(o, c)}
        self._exits = {
            DOOR_PREFIX + o.id for o in b.openings.values() if o.is_entrance and o.id in open_doors
        }
        for sid, space in open_spaces.items():
            doors = sorted(b.openings_of(sid), key=lambda o: o.id)
            node = [sid] + [DOOR_PREFIX + o.id for o in doors]  # index 0 = centre, k = doors[k - 1]
            for i, j, length in _space_walks(space.polygon, tuple((o.id, o.position) for o in doors)):
                if (i == 0 or doors[i - 1].id in open_doors) and doors[j - 1].id in open_doors:
                    self._add_edge(node[i], node[j], length, space=sid)
        elevations = {fid: f.elevation_m for fid, f in b.floors.items()}
        for k in b.connectors.values():
            if not _usable(k, c):
                continue
            is_lift = k.kind == "lift"
            pairs = itertools.combinations(k.stops, 2) if is_lift else itertools.pairwise(k.stops)
            for (low_floor, low), (high_floor, high) in pairs:
                if low not in open_spaces or high not in open_spaces:
                    continue
                rise = abs(elevations[high_floor] - elevations[low_floor])
                cost = LIFT_FIXED_M + LIFT_M_PER_RISE * rise if is_lift else STAIRS_M_PER_RISE * rise
                self._add_edge(low, high, cost, via=k.id)

    def _sources(self, node: str) -> list[str]:
        """Graph nodes a route starts from: the space, or every open entrance for OUTSIDE."""
        if node == OUTSIDE:
            return sorted(self._exits)
        return [node] if node in self._nodes else []

    def _dijkstra(
        self, sources: Iterable[str], goals: set[str] | None = None
    ) -> tuple[dict[str, float], dict[str, tuple[str, tuple[str, float, str, str]]], str | None]:
        """Distances and predecessor edge for every node reached; stops early at the first goal reached."""
        dist: dict[str, float] = {}
        prev: dict[str, tuple[str, tuple[str, float, str, str]]] = {}
        counter = itertools.count()
        heap = [(0.0, next(counter), s) for s in dict.fromkeys(sources)]
        best = {s: 0.0 for _, _, s in heap}
        heapq.heapify(heap)
        while heap:
            d, _, node = heapq.heappop(heap)
            if node in dist:
                continue
            dist[node] = d
            if goals and node in goals:
                return dist, prev, node
            for edge in self._edges.get(node, ()):
                other, weight = edge[0], edge[1]
                nd = d + weight
                if other not in dist and nd < best.get(other, math.inf):
                    best[other] = nd
                    prev[other] = (node, edge)
                    heapq.heappush(heap, (nd, next(counter), other))
        return dist, prev, None

    def shortest(self, start: str, goal: str) -> Route | None:
        """Shortest route between two space ids (or OUTSIDE). None if unreachable under the constraints."""
        sources, goals = self._sources(start), set(self._sources(goal))
        if not sources or not goals or start == goal == OUTSIDE:
            return None
        dist, prev, reached = self._dijkstra(sources, goals)
        if reached is None:
            return None
        nodes, edges = [reached], []
        while nodes[-1] in prev:
            node, edge = prev[nodes[-1]]
            nodes.append(node)
            edges.append(edge)
        nodes.reverse()
        edges.reverse()
        if start == OUTSIDE:
            nodes.insert(0, OUTSIDE)
        if goal == OUTSIDE:
            nodes.append(OUTSIDE)
        return Route(
            from_space_id=start,
            to_space_id=goal,
            length_m=round(dist[reached], 2),
            space_path=_spaces_passed(nodes, edges, start == OUTSIDE),
            element_path=_elements_used(nodes, edges),
            node_path=nodes,
        )

    def tour(self, stops: list[str]) -> Tour | None:
        """Visit stops in the given order: the sum of shortest legs. None if any leg is unreachable."""
        legs = []
        for a, b in itertools.pairwise(stops):
            leg = self.shortest(a, b)
            if leg is None:
                return None
            legs.append(leg)
        return Tour(stops=list(stops), length_m=round(sum(leg.length_m for leg in legs), 2), legs=legs)

    def distances_from(self, sources: Iterable[str]) -> dict[str, float]:
        """Multi-source Dijkstra: shortest distance from the nearest source to every reachable space.
        `distances_from([OUTSIDE])` = walk to the nearest entrance (what_if_door)."""
        starts = [n for s in sources for n in self._sources(s)]
        dist, _, _ = self._dijkstra(starts)
        return {n: d for n, d in dist.items() if not n.startswith(DOOR_PREFIX)}

    def reachable(self, start: str) -> set[str]:
        """Space ids reachable from `start` under the constraints (wide_delivery, step_free counts)."""
        return set(self.distances_from([start]))


def _spaces_passed(
    nodes: list[str], edges: list[tuple[str, float, str, str]], from_outside: bool
) -> list[str]:
    """Every space the route goes through, in order, including those crossed from door to door."""
    inner = [n for n in nodes if n != OUTSIDE]
    spaces: list[str] = [OUTSIDE] if from_outside else []
    for i, node in enumerate(inner):
        walked = edges[i - 1][3] if i else ""
        for sid in (walked, "" if node.startswith(DOOR_PREFIX) else node):
            if sid and spaces[-1:] != [sid]:
                spaces.append(sid)
    if nodes[-1] == OUTSIDE:
        spaces.append(OUTSIDE)
    return spaces


def _elements_used(nodes: list[str], edges: list[tuple[str, float, str, str]]) -> list[str]:
    """The openings passed and the stairs/lifts ridden, in order, each once."""
    inner = [n for n in nodes if n != OUTSIDE]
    used: list[str] = []
    for i, node in enumerate(inner):
        if i and edges[i - 1][2]:
            used.append(edges[i - 1][2])
        if node.startswith(DOOR_PREFIX):
            used.append(node.removeprefix(DOOR_PREFIX))
    return list(dict.fromkeys(used))


def validate_route(b: Building, space_path: list[str], constraints: Constraints = WALKING) -> list[str]:
    """Why Synera would score this space sequence 0 (docs/synera/ROUTE_SCORING.md); empty if valid.

    Each step must cross a real door, opening, stair or lift that the constraints allow (not closed, step-free
    if asked, wide enough), no space on the way may be closed, and no production bay may be passed through
    (ending in one is fine). A lift or stair may skip floors: any two of its stops count as one step.
    """
    c = constraints
    problems = []
    if not space_path:
        return ["empty route"]
    for sid in space_path:
        if sid != OUTSIDE and sid not in b.spaces:
            problems.append(f"unknown space {sid}")
        elif sid in c.closed:
            problems.append(f"closed space {b.label(sid)}")
    for sid in space_path[1:-1]:
        if sid in b.spaces and b.spaces[sid].room_type in PRODUCTION_BAYS:
            problems.append(f"passes through production bay {b.label(sid)}")
    for a, z in itertools.pairwise(space_path):
        if not _step_allowed(b, a, z, c):
            problems.append(f"no usable connection {_name(b, a)} -> {_name(b, z)}")
    return problems


def _step_allowed(b: Building, a: str, z: str, c: Constraints) -> bool:
    if a == z:
        return False
    return any(_usable(element, c) for element in _connections(b).get(frozenset((a, z)), ()))


def _usable(element: Opening | Connector, c: Constraints) -> bool:
    """The one rule for which doors, stairs and lifts the constraints allow: RouteGraph and validate_route."""
    if element.id in c.closed or (c.step_free and not element.step_free):
        return False
    return isinstance(element, Connector) or c.min_width_m is None or element.width_m >= c.min_width_m


_CONNECTIONS: dict[int, tuple[Building, dict[frozenset[str], list[Opening | Connector]]]] = {}


def _connections(b: Building) -> dict[frozenset[str], list[Opening | Connector]]:
    """Openings and connectors by the pair of spaces they join, built once per building (edits make a new one)."""
    hit = _CONNECTIONS.get(id(b))
    if hit is not None and hit[0] is b:
        return hit[1]
    pairs: dict[frozenset[str], list[Opening | Connector]] = defaultdict(list)
    for o in b.openings.values():
        pairs[frozenset(o.space_ids)].append(o)
    for k in b.connectors.values():
        for (_, x), (_, y) in itertools.combinations(k.stops, 2):
            pairs[frozenset((x, y))].append(k)
    if len(_CONNECTIONS) >= 32:
        _CONNECTIONS.pop(next(iter(_CONNECTIONS)))
    _CONNECTIONS[id(b)] = (b, pairs)
    return pairs


def _name(b: Building, sid: str) -> str:
    return sid if sid == OUTSIDE or sid not in b.spaces else b.label(sid)
