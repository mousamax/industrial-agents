"""L4 state overlay: closures, new doors, moved assets, as an event log over a read-only Building.

OWNER: Owner A, graph & state. Covers the brief's EDIT ("add a door between 2.03 and 2.04") and REACT
("the east lift is closed, what changes?"), undo, before/after diffs for the UI, and the "make things
move" add-in (assets that change place). The agent keeps one BuildingState per conversation.

    state = BuildingState(load_building("proc-campus-dev-000"))
    state.apply(Close("proc-campus-dev-000-V02"))
    graph = RouteGraph(state.building, state.constraints())
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from app.building import geometry
from app.building.model import OUTSIDE, XY, Building, parse_building
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
    _cache: tuple[tuple[Edit, ...], Building] | None = field(
        default=None, init=False, repr=False, compare=False
    )

    @property
    def building(self) -> Building:
        """The base plus every structural edit (added doors, moved assets). Cache it; edits are rare."""
        structural = tuple(e for e in self.edits if isinstance(e, AddDoor | MoveAsset))
        if not structural:
            return self.base
        if self._cache is None or self._cache[0] != structural:
            self._cache = (structural, parse_building(self.export()))
        return self._cache[1]

    def closed(self) -> frozenset[str]:
        """Element ids closed right now (Close and Reopen replayed in order)."""
        out: set[str] = set()
        for e in self.edits:
            if isinstance(e, Close):
                out.add(e.element_id)
            elif isinstance(e, Reopen):
                out.discard(e.element_id)
        return frozenset(out)

    def constraints(self, base: Constraints = WALKING) -> Constraints:
        """`base` plus every currently closed element."""
        return base.with_closed(*self.closed())

    def apply(self, edit: Edit) -> str:
        """Validate and record an edit; return a one-line description for the agent and UI.
        Raise ValueError with a human-readable reason if invalid (unknown id, spaces don't share a wall)."""
        b = self.building
        match edit:
            case Close(element_id):
                name = self._describe(b, element_id)
                if element_id in self.closed():
                    raise ValueError(f"{name} is already closed")
                message = f"Closed {name}."
            case Reopen(element_id):
                name = self._describe(b, element_id)
                if element_id not in self.closed():
                    raise ValueError(f"{name} isn't closed")
                message = f"Reopened {name}."
            case AddDoor():
                _, position = self._door_place(b, edit)
                message = (
                    f"Added a {edit.width_m:g} m door between {b.label(edit.first_space_id)} and "
                    f"{b.label(edit.second_space_id)} at ({position[0]:.1f}, {position[1]:.1f})."
                )
            case MoveAsset(asset_id, space_id):
                if asset_id not in b.assets:
                    raise ValueError(f"unknown asset {asset_id!r}")
                if space_id not in b.spaces:
                    raise ValueError(f"unknown space {space_id!r}")
                message = f"Moved {b.assets[asset_id].name} to {b.label(space_id)}."
            case _:
                raise ValueError(f"unknown edit {edit!r}")
        self.edits.append(edit)
        return message

    def undo(self) -> Edit | None:
        return self.edits.pop() if self.edits else None

    def export(self) -> dict:
        """building.json (kit format) with the edits applied: for saving and for Level 4."""
        raw = copy.deepcopy(self.base.raw)
        assets = {a["id"]: a for a in raw["assets"]}
        doors = 0
        for e in self.edits:
            if isinstance(e, AddDoor):
                doors += 1
                floor_id, (x, y) = self._door_place(self.base, e)  # edits never change spaces
                raw["openings"].append(
                    {
                        "id": f"{floor_id}-O-new{doors}",
                        "floor_id": floor_id,
                        "space_ids": [e.first_space_id, e.second_space_id],
                        "kind": "door",
                        "position": [round(x, 3), round(y, 3)],
                        "width_m": e.width_m,
                        "step_free": e.step_free,
                        "synthetic": True,
                    }
                )
            elif isinstance(e, MoveAsset):
                space = self.base.spaces[e.space_id]
                x, y = e.position or space.anchor
                assets[e.asset_id].update(
                    space_id=e.space_id, floor_id=space.floor_id, position=[round(x, 3), round(y, 3)]
                )
        return raw

    @staticmethod
    def _describe(b: Building, element_id: str) -> str:
        if element_id in b.connectors:
            c = b.connectors[element_id]
            return f"{c.name} ({c.kind})"
        if element_id in b.openings:
            o = b.openings[element_id]
            first, second = (b.label(sid) for sid in o.space_ids)
            return f"the {o.kind} between {first} and {second}"
        if element_id in b.spaces:
            return b.label(element_id)
        if element_id in b.walkways:
            return f"the {b.walkways[element_id].name} (walkway)"
        raise ValueError(f"unknown element {element_id!r}: give an opening, stair/lift, walkway or space id")

    @staticmethod
    def _door_place(b: Building, edit: AddDoor) -> tuple[str, XY]:
        """(floor id, position) of a new door, or ValueError if it can't go there."""
        first, second = edit.first_space_id, edit.second_space_id
        for sid in (first, second):
            if sid not in b.spaces or sid == OUTSIDE:
                raise ValueError(f"unknown space {sid!r}")
        if first == second:
            raise ValueError("a door needs two different spaces")
        floor_id = b.spaces[first].floor_id
        if b.spaces[second].floor_id != floor_id:
            raise ValueError("a door can only join two spaces on the same floor")
        position = edit.position or geometry.shared_wall_midpoint(b, first, second)
        if position is None:
            raise ValueError(f"{b.label(first)} and {b.label(second)} don't share a wall")
        return floor_id, position
