"""Deps and tools: thin wrappers around the kit's BuildingMap (synera-kit/starter_agent/building_map.py).

BuildingMap implements the exact route model behind the expected answers (anchors, 12 m per stair
floor, 15 m + 6 m per floor for lifts, step-free filtering), so every number comes from code, never
from the model.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from pydantic_ai import RunContext
from starter_agent.building_map import MAX_SEARCH_RESULTS, BuildingMap, MapError
from starter_agent.viewer import draw_route

from app.settings import ROOT, get_settings

OUTPUT_DIR = ROOT / "agent_output"


def building_dir(reference: str | Path) -> Path:
    """A building directory, or a building id looked up in synera-kit/data/{train,dev,test}/."""
    path = Path(reference)
    if (path / "building.json").exists():
        return path
    matches = sorted(get_settings().synera_kit_dir.glob(f"data/*/{reference}/building.json"))
    if not matches:
        raise FileNotFoundError(f"no building {reference!r} (is the synera-kit submodule checked out?)")
    return matches[0].parent


@dataclass
class Deps:
    map: BuildingMap
    output_dir: Path = field(default=OUTPUT_DIR)

    @classmethod
    def load(cls, reference: str | Path) -> Deps:
        return cls(map=BuildingMap.load(building_dir(reference)))


def _json(value: object) -> str:
    return json.dumps(value, indent=1, ensure_ascii=False)


def _error(message: str) -> str:
    """Like the kit's ToolError: the model sees the problem and can try again, the run never crashes."""
    return _json({"error": message})


def building_overview(ctx: RunContext[Deps]) -> str:
    """Floors, stairs and lifts, entrances and current closures of the building."""
    return _json(ctx.deps.map.overview())


def search_building(ctx: RunContext[Deps], query: str, floor_level: int | None = None) -> str:
    """Find rooms and things (furniture, machines, equipment) by name, room number or type.

    Args:
        query: Words to look for, e.g. "3d printer", "2.03", "toilet", "cnc mill", "lecture hall".
        floor_level: Only search this floor (0 = ground floor, -1 = basement). Omit to search all floors.
    """
    results = ctx.deps.map.search(query, floor_level)
    if not results:
        return _error(f"Nothing matches '{query}'. Try a shorter or more general query.")
    return _json({"matches": len(results), "results": results[:MAX_SEARCH_RESULTS]})


def find_route(
    ctx: RunContext[Deps], start: str, destination: str, step_free: bool = False, via: list[str] | None = None
) -> str:
    """Shortest walking route between two places, with step-by-step directions and length in metres.

    Args:
        start: Space id, asset id, or exact room number/name of where the person is.
        destination: Space id, asset id, or exact room number/name of where they want to go.
        step_free: True to avoid stairs and steps (wheelchair, trolley, pram).
        via: Optional list of places to visit in order between start and destination (multi-stop tour).
    """
    try:
        return _json(asdict(ctx.deps.map.route(start, destination, step_free, via)))
    except MapError as error:
        return _error(str(error))


def set_closed(ctx: RunContext[Deps], element: str, closed: bool) -> str:
    """Close or reopen a lift, staircase, door or room, e.g. for maintenance. Affects all later routes.

    Args:
        element: Name or id of a lift/staircase ("Lift 1"), a door/opening id, or a room.
        closed: True to close it, False to reopen it.
    """
    try:
        element_id = ctx.deps.map.set_closed(element, closed)
    except MapError as error:
        return _error(str(error))
    return _json({"element_id": element_id, "closed": closed, "all_closed": sorted(ctx.deps.map.closed)})


def add_door(ctx: RunContext[Deps], first_space: str, second_space: str, step_free: bool = True) -> str:
    """Add a door between two neighbouring rooms on the same floor (edits the building).

    Args:
        first_space: Space id or exact room number/name.
        second_space: Space id or exact room number/name.
        step_free: Whether the new door has no step.
    """
    try:
        opening = ctx.deps.map.add_door(first_space, second_space, step_free)
    except MapError as error:
        return _error(str(error))
    path = ctx.deps.output_dir / "building_edited.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    ctx.deps.map.save(path)
    return _json({"added": opening, "saved_to": str(path)})


def show_route(ctx: RunContext[Deps]) -> str:
    """Draw the most recent route on the floor plans and save the images; returns the file paths."""
    if ctx.deps.map.last_route is None:
        return _error("No route yet. Call find_route first.")
    ctx.deps.output_dir.mkdir(parents=True, exist_ok=True)
    return _json({"images": [str(p) for p in draw_route(ctx.deps.map, ctx.deps.output_dir)]})


TOOLS = [building_overview, search_building, find_route, set_closed, add_door, show_route]
