"""L4 state overlay: closures, new doors, moved and delivered assets, as a timed event log over a read-only
Building.

OWNER: Owner A, graph & state. Covers the brief's EDIT ("add a door between 2.03 and 2.04") and REACT
("the east lift is closed, what changes?"), undo, before/after diffs for the UI, and the Level 5 "make things
move" add-in: every edit carries when it holds from, who reported it and how (chat, barcode, RFID), so the
building can be read at any moment ("where was it yesterday?") and a delivery can be expected in advance.
The agent keeps one BuildingState per conversation; the app shares one per building between colleagues.

    state = BuildingState(load_building("proc-campus-dev-000"))
    state.apply(Close("proc-campus-dev-000-V02"))
    graph = RouteGraph(state.building, state.constraints())
"""

from __future__ import annotations

import copy
import dataclasses
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.building import geometry
from app.building.model import OUTSIDE, XY, Asset, Building, Opening
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


@dataclass(frozen=True)
class AddAsset:
    """Something new in the building: a delivery, a new machine. Its id is given when it is applied."""

    name: str
    type: str  # pallet, crate, 3d_printer, ... free text, like the kit's asset types
    space_id: str
    category: str = "equipment"
    position: XY | None = None  # None = the space's anchor
    width_m: float = 1.2
    depth_m: float = 1.0


Edit = Close | Reopen | AddDoor | MoveAsset | AddAsset
STRUCTURAL = (AddDoor, MoveAsset, AddAsset)


def _now() -> datetime:
    return datetime.now(UTC).astimezone()


@dataclass(frozen=True)
class Stamp:
    """When an edit holds from (a future time = expected), who reported it, how, and what it said then."""

    at: datetime
    by: str = ""
    source: str = "chat"  # chat | barcode | rfid | app | ...
    said: str = ""


@dataclass
class BuildingState:
    base: Building
    edits: list[Edit] = field(default_factory=list)
    stamps: list[Stamp] = field(default_factory=list)
    """One per edit, same order. Edits given without stamps hold since forever."""
    clock: Callable[[], datetime] = field(default=_now, repr=False, compare=False)
    _views: dict[tuple, Building] = field(default_factory=dict, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        since_ever = Stamp(datetime.min.replace(tzinfo=UTC))
        self.stamps = [since_ever] * (len(self.edits) - len(self.stamps)) + self.stamps

    # ---- reading the building at a moment ---------------------------------------------------------------

    def now(self) -> datetime:
        return self.clock()

    def _active(self, when: datetime | None = None) -> list[int]:
        """Indexes of the edits that hold at `when` (default: now)."""
        moment = when or self.now()
        return [i for i, s in enumerate(self.stamps) if s.at <= moment]

    @property
    def building(self) -> Building:
        """The base plus every structural edit that holds now (added doors, moved or delivered assets)."""
        return self.view()

    def view(self, when: datetime | None = None) -> Building:
        """The building as it was (or will be) at `when`. Cached per set of structural edits."""
        active = tuple(i for i in self._active(when) if isinstance(self.edits[i], STRUCTURAL))
        if not active:
            return self.base
        key = tuple((i, self.edits[i]) for i in active)
        if key not in self._views:
            if len(self._views) > 32:
                self._views.clear()
            self._views[key] = self._apply_structural(active)
        return self._views[key]

    def closed(self, when: datetime | None = None) -> frozenset[str]:
        """Element ids closed at `when`, default now (Close and Reopen replayed in order)."""
        out: set[str] = set()
        for i in self._active(when):
            e = self.edits[i]
            if isinstance(e, Close):
                out.add(e.element_id)
            elif isinstance(e, Reopen):
                out.discard(e.element_id)
        return frozenset(out)

    def constraints(self, base: Constraints = WALKING) -> Constraints:
        """`base` plus every currently closed element."""
        return base.with_closed(*self.closed())

    def changes(
        self, since: datetime | None = None, until: datetime | None = None
    ) -> list[tuple[int, Edit, Stamp]]:
        """(index, edit, stamp) of the edits whose time falls in [since, until), oldest first. Future ones
        are expected."""
        out = [
            (i, e, s)
            for i, (e, s) in enumerate(zip(self.edits, self.stamps, strict=True))
            if (since is None or s.at >= since) and (until is None or s.at < until)
        ]
        return sorted(out, key=lambda ies: ies[2].at)

    def asset_id(self, index: int) -> str:
        """The id an AddAsset at edits[index] gives its new asset (stable however later edits go)."""
        n = sum(isinstance(e, AddAsset) for e in self.edits[: index + 1])
        return f"{self.base.id}-A-new{n}"

    def timeline(self, asset_id: str) -> list[tuple[Stamp | None, str]]:
        """Every place an asset was put, oldest first: (None, plan space) for where the plan has it, then
        (stamp, space id) for each report, future ones included."""
        plan = [(None, self.base.assets[asset_id].space_id)] if asset_id in self.base.assets else []
        reports = [
            (s, e.space_id)
            for i, (e, s) in enumerate(zip(self.edits, self.stamps, strict=True))
            if (isinstance(e, MoveAsset) and e.asset_id == asset_id)
            or (isinstance(e, AddAsset) and self.asset_id(i) == asset_id)
        ]
        return plan + sorted(reports, key=lambda r: r[0].at)

    # ---- changing it ------------------------------------------------------------------------------------

    def apply(self, edit: Edit, *, at: datetime | None = None, by: str = "", source: str = "chat") -> str:
        """Validate and record an edit that holds from `at` (default now; later = expected); return a
        one-line description for the agent and UI. Raise ValueError with a human-readable reason if invalid
        (unknown id, spaces don't share a wall)."""
        at = at or self.now()
        b = self.view(at)
        match edit:
            case Close(element_id):
                name = self._describe(b, element_id)
                if element_id in self.closed(at):
                    raise ValueError(f"{name} is already closed")
                message = f"Closed {name}."
            case Reopen(element_id):
                name = self._describe(b, element_id)
                if element_id not in self.closed(at):
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
                if space_id not in b.spaces or space_id == OUTSIDE:
                    raise ValueError(f"unknown space {space_id!r}")
                message = f"Moved {b.assets[asset_id].name} to {b.label(space_id)}."
            case AddAsset(name=name, space_id=space_id):
                if space_id not in b.spaces or space_id == OUTSIDE:
                    raise ValueError(f"unknown space {space_id!r}")
                if not name.strip():
                    raise ValueError("a new item needs a name")
                message = f"{name} arrived in {b.label(space_id)}."
            case _:
                raise ValueError(f"unknown edit {edit!r}")
        self.edits.append(edit)
        self.stamps.append(Stamp(at, by, source, message))
        return message

    def undo(self, by: str | None = None) -> Edit | None:
        """Take back the most recent edit (only `by`'s own, when given: colleagues share a state)."""
        mine = [i for i, s in enumerate(self.stamps) if by is None or s.by == by]
        if not mine:
            return None
        self.stamps.pop(mine[-1])
        return self.edits.pop(mine[-1])

    # ---- building the view ------------------------------------------------------------------------------

    def _apply_structural(self, active: tuple[int, ...]) -> Building:
        b = self.base
        openings, assets = dict(b.openings), dict(b.assets)
        for _, e in self._placed(active):
            if isinstance(e, Opening):
                openings[e.id] = e
            else:
                assets[e.id] = e
        raw = self.export(active) if "assets" in b.raw else b.raw  # a Level 3 site keeps its site.json
        return dataclasses.replace(b, openings=openings, assets=assets, raw=raw)

    def _placed(self, active: tuple[int, ...]) -> list[tuple[int, Opening | Asset]]:
        """The doors and assets the active structural edits put in place, in edit order."""
        b, out, assets = self.base, [], dict(self.base.assets)
        doors = 0
        for i, e in enumerate(self.edits):
            doors += isinstance(e, AddDoor)
            if i not in active:
                continue
            if isinstance(e, AddDoor):
                floor_id, position = self._door_place(b, e)  # edits never change spaces
                pair = (e.first_space_id, e.second_space_id)
                door_id = f"{floor_id}-O-new{doors}"
                door = Opening(
                    door_id, floor_id, pair, "door", _rounded(position), e.width_m, e.step_free, True
                )
                out.append((i, door))
            elif isinstance(e, MoveAsset):
                space = b.spaces[e.space_id]
                old = assets.get(e.asset_id)
                if old is None:  # the delivery it moved was undone
                    continue
                moved = dataclasses.replace(
                    old,
                    space_id=space.id,
                    floor_id=space.floor_id,
                    position=_rounded(e.position or space.anchor),
                )
                assets[moved.id] = moved
                out.append((i, moved))
            elif isinstance(e, AddAsset):
                space = b.spaces[e.space_id]
                new = Asset(
                    self.asset_id(i),
                    e.name,
                    e.category,
                    e.type,
                    space.id,
                    space.floor_id,
                    _rounded(e.position or space.anchor),
                    e.width_m,
                    e.depth_m,
                    0.0,
                    "reported",
                )
                assets[new.id] = new
                out.append((i, new))
        return out

    def export(self, active: tuple[int, ...] | None = None) -> dict:
        """building.json (kit format) with the edits that hold now applied: for saving and for Level 4."""
        if active is None:
            active = tuple(i for i in self._active() if isinstance(self.edits[i], STRUCTURAL))
        raw = copy.deepcopy(self.base.raw)
        assets = {a["id"]: a for a in raw["assets"]}
        for _, e in self._placed(active):
            if isinstance(e, Opening):
                raw["openings"].append(
                    {
                        "id": e.id,
                        "floor_id": e.floor_id,
                        "space_ids": list(e.space_ids),
                        "kind": e.kind,
                        "position": list(e.position),
                        "width_m": e.width_m,
                        "step_free": e.step_free,
                        "synthetic": True,
                    }
                )
            elif e.id in assets:
                assets[e.id].update(space_id=e.space_id, floor_id=e.floor_id, position=list(e.position))
            else:
                asset = {
                    "id": e.id,
                    "name": e.name,
                    "category": e.category,
                    "type": e.type,
                    "space_id": e.space_id,
                    "floor_id": e.floor_id,
                    "position": list(e.position),
                    "footprint": {"width_m": e.width_m, "depth_m": e.depth_m, "rotation_deg": 0.0},
                    "source": e.source,
                }
                raw["assets"].append(asset)
                assets[e.id] = asset
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


def _rounded(xy: XY) -> XY:
    return (round(xy[0], 3), round(xy[1], 3))
