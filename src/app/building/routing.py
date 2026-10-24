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

from collections.abc import Iterable
from dataclasses import dataclass, field

from app.building.model import OUTSIDE, Building

__all__ = ["OUTSIDE", "WALKING", "Constraints", "Route", "RouteGraph", "Tour"]


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
        raise NotImplementedError("Owner A: port BuildingMap.graph() + min_width_m + closed")

    def shortest(self, start: str, goal: str) -> Route | None:
        """Shortest route between two space ids (or OUTSIDE). None if unreachable under the constraints."""
        raise NotImplementedError

    def tour(self, stops: list[str]) -> Tour | None:
        """Visit stops in the given order: the sum of shortest legs. None if any leg is unreachable."""
        raise NotImplementedError

    def distances_from(self, sources: Iterable[str]) -> dict[str, float]:
        """Multi-source Dijkstra: shortest distance from the nearest source to every reachable space.
        `distances_from([OUTSIDE])` = walk to the nearest entrance (what_if_door)."""
        raise NotImplementedError

    def reachable(self, start: str) -> set[str]:
        """Space ids reachable from `start` under the constraints (wide_delivery, step_free counts)."""
        raise NotImplementedError
