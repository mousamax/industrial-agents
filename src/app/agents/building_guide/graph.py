"""Route graphs for the agent's tools: app.building.routing.RouteGraph, cached per (building, constraints).

RouteGraph follows the judges' route model (synera-kit 1.1.0). The walks inside each space are cached on
their geometry, so a graph for new constraints, or for an edited building, is cheap to build.
"""

from __future__ import annotations

from functools import lru_cache

from app.building.model import Building
from app.building.routing import WALKING, Constraints, RouteGraph


@lru_cache(maxsize=64)
def route_graph(building: Building, constraints: Constraints = WALKING) -> RouteGraph:
    return RouteGraph(building, constraints)
