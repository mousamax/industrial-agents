"""L4 state overlay: closures, new doors, moved assets, as an event log over a read-only Building.

OWNER: Owner A, graph & state. Covers the brief's EDIT ("add a door between 2.03 and 2.04") and REACT
("the east lift is closed, what changes?"), undo, before/after diffs for the UI, and the "make things
move" add-in (assets that change place). The agent keeps one BuildingState per conversation.

    state = BuildingState(load_building("proc-campus-dev-000"))
    state.apply(Close("proc-campus-dev-000-V02"))
    graph = RouteGraph(state.building, state.constraints())
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.building.model import XY, Building
from app.building.routing import WALKING, Constraints


@dataclass(frozen=True)
class Close:
    element_id: str  # opening, connector or space id


@dataclass(frozen=True)
class Reopen:
    element_id: str


@dataclass(frozen=True)
class AddDoor:
    first_space_id: str
    second_space_id: str
    width_m: float = 0.9
    step_free: bool = True
    position: XY | None = None
    """None = the middle of the wall the two spaces share (geometry.shared_wall_midpoint)."""


@dataclass(frozen=True)
class MoveAsset:
    asset_id: str
    space_id: str
    position: XY | None = None  # None = the space's anchor


Edit = Close | Reopen | AddDoor | MoveAsset


@dataclass
class BuildingState:
    base: Building
    edits: list[Edit] = field(default_factory=list)

    @property
    def building(self) -> Building:
        """The base plus every structural edit (added doors, moved assets). Cache it; edits are rare."""
        raise NotImplementedError("Owner A")

    def constraints(self, base: Constraints = WALKING) -> Constraints:
        """`base` plus every currently closed element."""
        raise NotImplementedError("Owner A")

    def apply(self, edit: Edit) -> str:
        """Validate and record an edit; return a one-line description for the agent and UI.
        Raise ValueError with a human-readable reason if invalid (unknown id, spaces don't share a wall)."""
        raise NotImplementedError("Owner A")

    def undo(self) -> Edit | None:
        raise NotImplementedError("Owner A")

    def export(self) -> dict:
        """building.json (kit format) with the edits applied: for saving and for Level 4."""
        raise NotImplementedError("Owner A")
