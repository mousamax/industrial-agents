"""Route graph for the agent's tools: Owner A's RouteGraph once it lands, a stand-in until then.

The stand-in ports the kit's route model (synera-kit/starter_agent/building_map.py `graph()`, the model
behind the golden answers) plus `min_width_m` and `closed`, behind the same API as
app.building.routing.RouteGraph. `route_graph()` uses the real one as soon as its constructor stops
raising NotImplementedError, so nothing here needs to change when Magnus's routing.py lands; delete the
stand-in then.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterable
from functools import lru_cache

import networkx as nx

from app.building.model import OUTSIDE, Building
from app.building.routing import WALKING, Constraints, Route, RouteGraph, Tour

DOOR = "door:"
STAIRS_PER_FLOOR_M = 12.0
LIFT_WAIT_M = 15.0
LIFT_PER_FLOOR_M = 6.0


class StandInGraph:
    """The kit's route model with constraints. Same methods as RouteGraph."""

    def __init__(self, building: Building, constraints: Constraints = WALKING) -> None:
        self.building = b = building
        self.constraints = c = constraints
        g = nx.MultiGraph()
        anchors = {sid: s.anchor for sid, s in b.spaces.items() if sid not in c.closed}
        g.add_nodes_from(anchors)
        g.add_node(OUTSIDE)
        for o in b.openings.values():
            if o.id in c.closed or (c.step_free and not o.step_free):
                continue
            if c.min_width_m is not None and o.width_m < c.min_width_m:
                continue
            door = DOOR + o.id
            for sid in o.space_ids:
                if sid == OUTSIDE:
                    g.add_edge(OUTSIDE, door, weight=0.0, via=o.id)
                elif sid in anchors:
                    g.add_edge(sid, door, weight=math.dist(anchors[sid], o.position), via=o.id)
        levels = {fid: f.level for fid, f in b.floors.items()}
        for k in b.connectors.values():
            if k.id in c.closed or (c.step_free and not k.step_free):
                continue
            for (low_floor, low), (high_floor, high) in itertools.pairwise(k.stops):
                if low not in anchors or high not in anchors:
                    continue
                span = abs(levels[high_floor] - levels[low_floor])
                cost = (
                    LIFT_WAIT_M + LIFT_PER_FLOOR_M * span if k.kind == "lift" else STAIRS_PER_FLOOR_M * span
                )
                g.add_edge(low, high, weight=cost, via=k.id)
        self.graph = g

    def shortest(self, start: str, goal: str) -> Route | None:
        try:
            length, nodes = nx.single_source_dijkstra(self.graph, start, goal, weight="weight")
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None
        elements = []
        for u, v in itertools.pairwise(nodes):
            edge = min(self.graph[u][v].values(), key=lambda e: e["weight"])
            elements.append(edge["via"])
        spaces = [n for n in nodes if not n.startswith(DOOR)]
        return Route(start, goal, float(length), spaces, elements, list(nodes))

    def tour(self, stops: list[str]) -> Tour | None:
        legs = [self.shortest(a, b) for a, b in itertools.pairwise(stops)]
        if any(leg is None for leg in legs):
            return None
        return Tour(stops, sum(leg.length_m for leg in legs), legs)  # type: ignore[union-attr]

    def distances_from(self, sources: Iterable[str]) -> dict[str, float]:
        sources = [s for s in sources if s in self.graph]
        if not sources:
            return {}
        found = nx.multi_source_dijkstra_path_length(self.graph, sources, weight="weight")
        return {n: d for n, d in found.items() if not n.startswith(DOOR)}

    def reachable(self, start: str) -> set[str]:
        if start not in self.graph:
            return set()
        return {n for n in nx.node_connected_component(self.graph, start) if not n.startswith(DOOR)}


@lru_cache(maxsize=64)
def route_graph(building: Building, constraints: Constraints = WALKING) -> RouteGraph | StandInGraph:
    """Owner A's RouteGraph if implemented, else the stand-in. Cached per (building, constraints)."""
    try:
        return RouteGraph(building, constraints)
    except NotImplementedError:
        return StandInGraph(building, constraints)
