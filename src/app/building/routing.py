"""L3 route graph: shortest paths, tours, reachability and distance fields under constraints.

OWNER: Owner A, graph & state (see docs/synera/TEAM_PLAN.md). Interface frozen: change signatures only
after telling the team; tools, benchmark and UI are written against them.

Must reproduce the ground-truth model exactly (synera-kit/starter_agent/building_map.py, `graph()`):
nodes = space anchors (representative point) + one node per opening at its position + "outside";
edges = space <-> each of its openings (straight-line), entrance <-> outside (0 m), stairs 12 m per floor
spanned, lift 15 m + 6 m per floor; MultiGraph (a stair and a lift may join the same two spaces);
step-free drops every element with step_free False. New here: `min_width_m` (drop openings narrower than
the load) and `closed`. The kit's code is the reference: port it, don't reinvent it.

Scoring: docs/synera/ROUTE_SCORING.md is Synera's announced route scoring (door-to-door distance, stairs
2 m per metre climbed, lift 15 m + 1 m per metre, forklift >= 3 m, no passing through production bays).
It differs from the kit's cost model above; read it before changing how routes are ranked.

Golden tests: tests/building/test_golden.py::test_route* (warm-up routes, step-free, closures, tours).
"""

from __future__ import annotations

import heapq
import itertools
import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from app.building.model import OUTSIDE, Building

__all__ = ["OUTSIDE", "WALKING", "Constraints", "Route", "RouteGraph", "Tour"]

DOOR_PREFIX = "door:"
STAIRS_COST_PER_FLOOR_M = 12.0
LIFT_WAIT_COST_M = 15.0
LIFT_COST_PER_FLOOR_M = 6.0


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


@dataclass(frozen=True)
class Route:
    """One leg, in the same shape as the golden answers (questions_basic.jsonl, kind=route)."""

    from_space_id: str
    to_space_id: str
    length_m: float
    space_path: list[str]
    """Spaces visited in order, including "outside" when the route leaves and re-enters."""
    element_path: list[str]
    """Opening and connector ids in order, exactly like the golden `element_path` (an opening appears
    twice: once arriving at its door node, once leaving it; a connector appears once)."""
    node_path: list[str] = field(default_factory=list)
    """Raw graph nodes (space ids, 'door:<opening id>', 'outside'); used to draw the route."""


@dataclass(frozen=True)
class Tour:
    stops: list[str]
    length_m: float
    legs: list[Route]


class RouteGraph:
    """Built once per (building, constraints). Cheap enough to rebuild after every edit."""

    def __init__(self, building: Building, constraints: Constraints = WALKING) -> None:
        self.building = building
        self.constraints = constraints
        # Multigraph as adjacency lists: node -> [(neighbour, metres, element id)]. Parallel edges are kept.
        self._edges: dict[str, list[tuple[str, float, str]]] = defaultdict(list)
        self._build()

    def _add_edge(self, a: str, b: str, weight: float, via: str) -> None:
        self._edges[a].append((b, weight, via))
        self._edges[b].append((a, weight, via))

    def _build(self) -> None:
        b, c = self.building, self.constraints
        anchors = {sid: s.anchor for sid, s in b.spaces.items() if sid not in c.closed}
        self._nodes = set(anchors)
        for opening in b.openings.values():
            if opening.id in c.closed or (c.step_free and not opening.step_free):
                continue
            if c.min_width_m is not None and opening.width_m < c.min_width_m:
                continue
            door = DOOR_PREFIX + opening.id
            for sid in opening.space_ids:
                if sid == OUTSIDE:
                    self._add_edge(OUTSIDE, door, 0.0, opening.id)
                elif sid in anchors:
                    self._add_edge(sid, door, math.dist(anchors[sid], opening.position), opening.id)
        levels = {fid: f.level for fid, f in b.floors.items()}
        for connector in b.connectors.values():
            if connector.id in c.closed or (c.step_free and not connector.step_free):
                continue
            for (low_floor, low), (high_floor, high) in itertools.pairwise(connector.stops):
                if low not in anchors or high not in anchors:
                    continue
                span = abs(levels[high_floor] - levels[low_floor])
                cost = (
                    LIFT_WAIT_COST_M + LIFT_COST_PER_FLOOR_M * span
                    if connector.kind == "lift"
                    else STAIRS_COST_PER_FLOOR_M * span
                )
                self._add_edge(low, high, cost, connector.id)

    def _has(self, node: str) -> bool:
        return node in self._nodes or (node == OUTSIDE and OUTSIDE in self._edges)

    def _dijkstra(
        self, sources: Iterable[str], goal: str | None = None
    ) -> tuple[dict[str, float], dict[str, tuple[str, str]]]:
        """Distances and predecessor (node, element) for every node reached; stops early at `goal`."""
        dist: dict[str, float] = {}
        prev: dict[str, tuple[str, str]] = {}
        counter = itertools.count()
        heap = [(0.0, next(counter), s) for s in dict.fromkeys(sources) if self._has(s)]
        best = {s: 0.0 for _, _, s in heap}
        heapq.heapify(heap)
        while heap:
            d, _, node = heapq.heappop(heap)
            if node in dist:
                continue
            dist[node] = d
            if node == goal:
                break
            for other, weight, via in self._edges.get(node, ()):
                nd = d + weight
                if other not in dist and nd < best.get(other, math.inf):
                    best[other] = nd
                    prev[other] = (node, via)
                    heapq.heappush(heap, (nd, next(counter), other))
        return dist, prev

    def shortest(self, start: str, goal: str) -> Route | None:
        """Shortest route between two space ids (or OUTSIDE). None if unreachable under the constraints."""
        if not self._has(start) or not self._has(goal):
            return None
        dist, prev = self._dijkstra([start], goal)
        if goal not in dist:
            return None
        nodes, elements = [goal], []
        while nodes[-1] != start:
            node, via = prev[nodes[-1]]
            nodes.append(node)
            elements.append(via)
        nodes.reverse()
        elements.reverse()
        return Route(
            from_space_id=start,
            to_space_id=goal,
            length_m=round(dist[goal], 2),
            space_path=[n for n in nodes if not n.startswith(DOOR_PREFIX)],
            element_path=elements,
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
        dist, _ = self._dijkstra(sources)
        return {n: d for n, d in dist.items() if not n.startswith(DOOR_PREFIX)}

    def reachable(self, start: str) -> set[str]:
        """Space ids reachable from `start` under the constraints (wide_delivery, step_free counts)."""
        return {n for n in self.distances_from([start]) if n != OUTSIDE}
