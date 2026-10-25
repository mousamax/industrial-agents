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
- Production bays (PRODUCTION_BAYS) are destinations (Synera's rule): shortest, tour and distances_from
  start or end a leg in one, never pass through (no walk door to door across a bay, a bay's centre is not
  walked on from unless it's the start). reachable ignores the rule: the golden reachability counts
  (wide_delivery_count) reach rooms behind bays only through them.
- Level 3 sites (model.load_site): a walkway joins an entrance of one building to an entrance of another
  at its length, under the same constraints; it is the only way between buildings.
- Ours, off unless asked (Constraints.outside_walking, the app's chat and voice only): the outside is one more
  walkable space. Two entrances of the same ground floor are joined by the shortest walk round the
  building's outline (its footprint offset 15 cm outward, round corners), at the indoor cost per metre;
  space_path shows OUTSIDE between them. Never between two buildings of a site: walkways stay the only way.
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
from dataclasses import dataclass, field, replace
from functools import lru_cache

import numpy as np
from shapely.geometry import Point, Polygon, box
from shapely.ops import nearest_points, unary_union
from starter_agent.building_map import COVER_TOLERANCE_M, door_landing, visibility_matrix, walking_lengths

from app.building.model import OUTSIDE, XY, Building, Connector, Opening, Space, Walkway
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
    "outside_walks",
    "validate_route",
]

DOOR_PREFIX = "door:"
STAIRS_M_PER_RISE = 2.0
LIFT_FIXED_M = 15.0
LIFT_M_PER_RISE = 1.0
LENGTH_DECIMALS = 3
OUTSIDE_CLEARANCE_M = 0.15
"""outside_walking: the walk round a building keeps this far off its outline (the indoor wall clearance)."""
WALL_GAP_M = 0.5
"""Gaps between room outlines up to twice this wide are walls, not a way through the building."""


@dataclass(frozen=True)
class Constraints:
    """What a mover can't use. The default is a person walking with no closures."""

    step_free: bool = False
    """Wheelchair, pram, trolley, forklift: no stairs, no stepped entrances. Lifts are fine."""
    min_width_m: float | None = None
    """Every opening passed must be at least this wide (wide_delivery questions: 1.0 or 1.1 m)."""
    closed: frozenset[str] = frozenset()
    """Element ids (openings, connectors or spaces) that can't be used."""
    outside_walking: bool = False
    """Out one entrance, round the building, in through another, when that's shorter. Off for everything
    scored (the kit and Synera: no route round the outside); on only for the app's chat and voice sessions."""

    def with_closed(self, *element_ids: str) -> Constraints:
        return replace(self, closed=self.closed | set(element_ids))


WALKING = Constraints()
"""A person walking, nothing closed: the default everywhere."""

FORKLIFT = Constraints(step_free=True, min_width_m=3.0)
"""Synera: forklift aisles and openings 3 m or wider. Aisle-only isn't checked: the data has no aisle type."""

PRODUCTION_BAYS = frozenset({"production_zone", "assembly_zone", "additive_zone"})
"""Room types a route may end in but never pass through. Our reading of Synera's rule (open question 3 in
ROUTE_SCORING.md). RouteGraph only starts or ends a leg in one; validate_route rejects passing through."""


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
        self._bays = {sid for sid, s in open_spaces.items() if s.room_type in PRODUCTION_BAYS}
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
        for w in b.walkways.values():  # Level 3: entrance to entrance between buildings
            first, second = w.opening_ids
            if _usable(w, c) and first in open_doors and second in open_doors:
                self._add_edge(DOOR_PREFIX + first, DOOR_PREFIX + second, w.length_m, via=w.id)
        if c.outside_walking:  # round the outside, between two entrances of one ground floor
            for (first, second), (length, _) in outside_walks(b).items():
                if first in open_doors and second in open_doors:
                    self._add_edge(DOOR_PREFIX + first, DOOR_PREFIX + second, length, space=OUTSIDE)

    def _sources(self, node: str) -> list[str]:
        """Graph nodes a route starts from: the space, or every open entrance for OUTSIDE."""
        if node == OUTSIDE:
            return sorted(self._exits)
        return [node] if node in self._nodes else []

    def _dijkstra(
        self, sources: Iterable[str], goals: set[str] | None = None, *, bays_are_ends: bool = False
    ) -> tuple[dict[str, float], dict[str, tuple[str, tuple[str, float, str, str]]], str | None]:
        """Distances and predecessor edge for every node reached; stops early at the first goal reached.
        With bays_are_ends, a production bay can only start or end a leg: no walk door to door across a
        bay, and a bay's centre that isn't a source is reached but not walked on from."""
        sources = list(dict.fromkeys(sources))
        bays = self._bays if bays_are_ends else set()
        dead_ends = bays.difference(sources)
        dist: dict[str, float] = {}
        prev: dict[str, tuple[str, tuple[str, float, str, str]]] = {}
        counter = itertools.count()
        heap = [(0.0, next(counter), s) for s in sources]
        best = {s: 0.0 for _, _, s in heap}
        heapq.heapify(heap)
        while heap:
            d, _, node = heapq.heappop(heap)
            if node in dist:
                continue
            dist[node] = d
            if goals and node in goals:
                return dist, prev, node
            if node in dead_ends:
                continue
            for edge in self._edges.get(node, ()):
                other, weight = edge[0], edge[1]
                if edge[3] in bays and node != edge[3] and other != edge[3]:
                    continue  # door to door across a production bay
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
        dist, prev, reached = self._dijkstra(sources, goals, bays_are_ends=True)
        if reached is None:  # also when the only way passes through a production bay: "no route" scores
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
        dist, _, _ = self._dijkstra(starts, bays_are_ends=True)
        return {n: d for n, d in dist.items() if not n.startswith(DOOR_PREFIX)}

    def reachable(self, start: str, *, through_production_bays: bool = True) -> set[str]:
        """Space ids reachable from `start` under the constraints (wide_delivery, step_free counts).
        By default passing through production bays is allowed: Synera's reachability counts do. With
        through_production_bays=False, the route rule applies: a bay can be reached but not driven across."""
        dist, _, _ = self._dijkstra(self._sources(start), bays_are_ends=not through_production_bays)
        return {n for n in dist if not n.startswith(DOOR_PREFIX)}


def outside_walks(b: Building) -> dict[tuple[str, str], tuple[float, tuple[XY, ...]]]:
    """Walks round the outside between two entrances on the same ground floor (level 0), for
    Constraints.outside_walking: {(opening id, opening id) sorted: (metres, points from the first to the
    second)}. The line keeps OUTSIDE_CLEARANCE_M off the floor's footprint (its spaces' outlines merged,
    gaps between them closed) and goes round its corners, never through it. Entrances into a courtyard only
    reach each other. Each ground floor is walked alone, so two buildings of a site are never joined."""
    walks: dict[tuple[str, str], tuple[float, tuple[XY, ...]]] = {}
    for floor in b.floors.values():
        doors = tuple(sorted((o.id, o.position) for o in b.openings_of(OUTSIDE) if o.floor_id == floor.id))
        if floor.level == 0 and len(doors) > 1:
            walks |= _outside_walks(tuple(s.polygon for s in b.spaces_on(floor.id)), doors)
    return walks


@lru_cache(maxsize=64)
def _outside_walks(
    outlines: tuple[tuple[tuple[float, float], ...], ...], doors: tuple[tuple[str, tuple[float, float]], ...]
) -> dict[tuple[str, str], tuple[float, tuple[XY, ...]]]:
    footprint = unary_union([_outline(p) for p in outlines])
    footprint = footprint.buffer(WALL_GAP_M, join_style="mitre").buffer(-WALL_GAP_M, join_style="mitre")
    kept = footprint.buffer(OUTSIDE_CLEARANCE_M, join_style="mitre").simplify(0.02)
    outside = box(*kept.buffer(5.0).bounds).difference(kept)
    positions = dict(doors)
    walks: dict[tuple[str, str], tuple[float, tuple[XY, ...]]] = {}
    for part in getattr(outside, "geoms", [outside]):  # the ground round the building, and any courtyards
        landings = {oid: nearest_points(part, Point(xy))[0] for oid, xy in doors}
        here = [(oid, (p.x, p.y)) for oid, p in landings.items() if p.distance(Point(positions[oid])) < 1.0]
        if len(here) < 2:  # an entrance more than 1 m inside the footprint opens onto another part
            continue
        corners = [c for ring in [part.exterior, *part.interiors] for c in list(ring.coords)[:-1]]
        nodes = [xy for _, xy in here] + corners
        lengths, after = _all_shortest(visibility_matrix(part.buffer(COVER_TOLERANCE_M), nodes))
        for (i, (a, pa)), (j, (z, pz)) in itertools.combinations(enumerate(here), 2):
            if not math.isfinite(lengths[i, j]):
                continue
            path, k = [pa], i
            while k != j:
                k = int(after[k, j])
                path.append(nodes[k])
            step = math.dist(positions[a], pa) + math.dist(positions[z], pz)
            walks[(a, z)] = (float(lengths[i, j]) + step, (positions[a], *path, positions[z]))
    return walks


def _all_shortest(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Floyd-Warshall with the next node on each shortest path: (lengths, after[i, j])."""
    n = len(matrix)
    after = np.tile(np.arange(n), (n, 1))
    for middle in range(n):
        through = matrix[:, middle, None] + matrix[None, middle, :]
        better = through < matrix
        matrix = np.where(better, through, matrix)
        after = np.where(better, after[:, middle, None], after)
    return matrix, after


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
    return any(
        _usable(element, c)
        and (not isinstance(element, Walkway) or all(_usable(b.openings[o], c) for o in element.opening_ids))
        for element in _connections(b).get(frozenset((a, z)), ())
    )


def _usable(element: Opening | Connector | Walkway, c: Constraints) -> bool:
    """The one rule for which doors, stairs, lifts and walkways the constraints allow: RouteGraph and
    validate_route. A walkway without a width has no width limit."""
    if element.id in c.closed or (c.step_free and not element.step_free):
        return False
    if isinstance(element, Connector) or c.min_width_m is None or element.width_m is None:
        return True
    return element.width_m >= c.min_width_m


_CONNECTIONS: dict[int, tuple[Building, dict[frozenset[str], list[Opening | Connector | Walkway]]]] = {}


def _connections(b: Building) -> dict[frozenset[str], list[Opening | Connector | Walkway]]:
    """Openings and connectors by the pair of spaces they join, built once per building (edits make a new one)."""
    hit = _CONNECTIONS.get(id(b))
    if hit is not None and hit[0] is b:
        return hit[1]
    pairs: dict[frozenset[str], list[Opening | Connector | Walkway]] = defaultdict(list)
    for o in b.openings.values():
        pairs[frozenset(o.space_ids)].append(o)
    for k in b.connectors.values():
        for (_, x), (_, y) in itertools.combinations(k.stops, 2):
            pairs[frozenset((x, y))].append(k)
    for w in b.walkways.values():
        x, y = (b.openings[o].other_side(OUTSIDE) for o in w.opening_ids)
        pairs[frozenset((x, y))].append(w)
    if len(_CONNECTIONS) >= 32:
        _CONNECTIONS.pop(next(iter(_CONNECTIONS)))
    _CONNECTIONS[id(b)] = (b, pairs)
    return pairs


def _name(b: Building, sid: str) -> str:
    return sid if sid == OUTSIDE or sid not in b.spaces else b.label(sid)
