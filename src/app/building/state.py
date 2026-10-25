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
import functools
import itertools
import math
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import linemerge, split, substring, unary_union

from app.building import geometry
from app.building.model import OUTSIDE, XY, Asset, Building, Opening, Space
from app.building.routing import WALKING, Constraints


@dataclass(frozen=True)
class Close:
    element_id: str  # opening, connector or space id


@dataclass(frozen=True)
class Reopen:
    element_id: str


@dataclass(frozen=True)
class AddDoor:
    """A new door between two spaces on one floor, or a new entrance when one side is OUTSIDE (in the
    middle of the room's longest outside wall, on a floor that has entrances already)."""

    first_space_id: str
    second_space_id: str
    width_m: float = 0.9
    step_free: bool = True
    position: XY | None = None
    """None = the middle of the wall the two spaces share (geometry.shared_wall_midpoint)."""
    door_id: str | None = field(default=None, compare=False)
    """The new door's id, `<floor>-O-new<k>`: given by apply() and kept for the door's lifetime (undoing or
    removing other doors never renumbers it). Not part of equality: the same door is the same edit."""


@dataclass(frozen=True)
class RemoveDoor:
    """A door, opening or entrance taken out of the plan for good (Close is temporary)."""

    element_id: str


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


@dataclass(frozen=True)
class SplitRoom:
    """A new straight wall across a room from `start` to `end` (points on its outline), splitting it into two
    rooms (two new spaces; the room's id is gone). The wall has no door. `from_space_id` / `to_space_id` are
    the neighbours (or OUTSIDE) the ends were taken from, when they were named: for the message only."""

    space_id: str
    start: XY
    end: XY
    from_space_id: str | None = None
    to_space_id: str | None = None


Edit = Close | Reopen | AddDoor | RemoveDoor | MoveAsset | AddAsset | SplitRoom
STRUCTURAL = (AddDoor, RemoveDoor, MoveAsset, AddAsset, SplitRoom)


def _now() -> datetime:
    return datetime.now(UTC).astimezone()


@dataclass(frozen=True)
class Stamp:
    """When an edit holds from (a future time = expected), who reported it, how, and what it said then."""

    at: datetime
    by: str = ""
    source: str = "chat"  # chat | barcode | rfid | app | ...
    said: str = ""


def _locked(method):
    """Run a BuildingState method holding the state's lock (re-entrant: methods call each other)."""

    @functools.wraps(method)
    def locked(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return locked


@dataclass
class BuildingState:
    base: Building
    edits: list[Edit] = field(default_factory=list)
    stamps: list[Stamp] = field(default_factory=list)
    """One per edit, same order. Edits given without stamps hold since forever."""
    clock: Callable[[], datetime] = field(default=_now, repr=False, compare=False)
    _views: dict[tuple, Building] = field(default_factory=dict, init=False, repr=False, compare=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False, compare=False)
    """Colleagues share one state (the API keeps one per building) and tools run in worker threads: every
    read and write of edits/stamps holds this, so a write is atomic and a reader never sees half of one."""

    _doors_made: int = field(default=0, init=False, repr=False, compare=False)
    """The highest k of a `-O-new<k>` door id ever given out: never reused, even after an undo."""

    def __post_init__(self) -> None:
        since_ever = Stamp(datetime.min.replace(tzinfo=UTC))
        self.stamps = [since_ever] * (len(self.edits) - len(self.stamps)) + self.stamps
        for e in self.edits:  # ids a replayed record carries are taken; edits without one number on
            self._doors_made += isinstance(e, AddDoor)
            if isinstance(e, AddDoor) and e.door_id and (m := re.search(r"-O-new(\d+)$", e.door_id)):
                self._doors_made = max(self._doors_made, int(m.group(1)))

    def _with_door_id(self, edit: AddDoor, floor_id: str) -> AddDoor:
        """The AddDoor with its lifetime id: a new number, or the one it carries (a replayed record)."""
        if edit.door_id is None:
            self._doors_made += 1
            return dataclasses.replace(edit, door_id=f"{floor_id}-O-new{self._doors_made}")
        if (m := re.search(r"-O-new(\d+)$", edit.door_id)) is not None:
            self._doors_made = max(self._doors_made, int(m.group(1)))
        return edit

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

    @_locked
    def view(self, when: datetime | None = None) -> Building:
        """The building as it was (or will be) at `when`. Cached per set of structural edits."""
        active = tuple(i for i in self._active(when) if isinstance(self.edits[i], STRUCTURAL))
        if not active:
            return self.base
        key = tuple((i, self.edits[i], getattr(self.edits[i], "door_id", None)) for i in active)
        if key not in self._views:
            if len(self._views) > 32:
                self._views.clear()
            self._views[key] = self._apply_structural(active)
        return self._views[key]

    @_locked
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

    @_locked
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

    @_locked
    def asset_id(self, index: int) -> str:
        """The id an AddAsset at edits[index] gives its new asset (stable however later edits go)."""
        n = sum(isinstance(e, AddAsset) for e in self.edits[: index + 1])
        return f"{self.base.id}-A-new{n}"

    @_locked
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

    @_locked
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
                floor_id, position = self._door_place(b, edit)
                if edit.door_id is not None and edit.door_id in b.openings:
                    raise ValueError(f"door id {edit.door_id!r} is already in use")
                ends = f"at ({position[0]:.1f}, {position[1]:.1f})"
                if OUTSIDE in (edit.first_space_id, edit.second_space_id):
                    room = edit.second_space_id if edit.first_space_id == OUTSIDE else edit.first_space_id
                    message = f"Added a {edit.width_m:g} m entrance from outside into {b.label(room)} {ends}."
                else:
                    message = (
                        f"Added a {edit.width_m:g} m door between {b.label(edit.first_space_id)} and "
                        f"{b.label(edit.second_space_id)} {ends}."
                    )
                edit = self._with_door_id(edit, floor_id)
                message = message[:-1] + f" (id {edit.door_id})."
            case RemoveDoor(element_id):
                if element_id not in b.openings:
                    raise ValueError(
                        f"no door {element_id!r} in the plan (stairs, lifts and rooms can only be closed)"
                    )
                o = b.openings[element_id]
                message = (
                    f"Removed {self._describe(b, element_id)} ({o.width_m:g} m wide, at "
                    f"({o.position[0]:.1f}, {o.position[1]:.1f}))."
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
            case SplitRoom():
                split = split_room(b, edit)
                first, second = split.halves
                message = (
                    f"Split {b.label(edit.space_id)} with a {split.length_m:.1f} m wall from "
                    f"{_end_label(b, edit.from_space_id, edit.start)} to "
                    f"{_end_label(b, edit.to_space_id, edit.end)}: {first.name} ({first.area:.1f} m²) and "
                    f"{second.name} ({second.area:.1f} m²). The new wall has no door."
                )
            case _:
                raise ValueError(f"unknown edit {edit!r}")
        self.edits.append(edit)
        self.stamps.append(Stamp(at, by, source, message))
        return message

    @_locked
    def undo(self, by: str | None = None) -> Edit | None:
        """Take back the most recent edit (only `by`'s own, when given: colleagues share a state)."""
        mine = [i for i, s in enumerate(self.stamps) if by is None or s.by == by]
        if not mine:
            return None
        self.stamps.pop(mine[-1])
        return self.edits.pop(mine[-1])

    # ---- building the view ------------------------------------------------------------------------------

    def _apply_structural(self, active: tuple[int, ...]) -> Building:
        b = self._replay(active)
        raw = (
            self._export(b) if "assets" in self.base.raw else self.base.raw
        )  # a Level 3 site keeps site.json
        return dataclasses.replace(b, raw=raw)

    def _replay(self, active: tuple[int, ...]) -> Building:
        """The base with the active structural edits applied in edit order: split rooms, added doors, moved
        and delivered assets (raw untouched). An edit whose room an undone edit made is skipped."""
        b, doors = self.base, 0
        for i, e in enumerate(self.edits):
            doors += isinstance(e, AddDoor)  # the old numbering, for an AddDoor put in edits by hand
            if i not in active:
                continue
            if isinstance(e, SplitRoom):
                try:
                    b = with_split(b, e)
                except ValueError:
                    continue
            elif isinstance(e, AddDoor):
                try:
                    floor_id, position = self._door_place(b, e)
                except ValueError:
                    continue
                pair = (e.first_space_id, e.second_space_id)
                kind = "door"
                if OUTSIDE in pair:  # an entrance, the kit's way: [room, "outside"]
                    pair, kind = (pair[1], OUTSIDE) if pair[0] == OUTSIDE else pair, "entrance"
                door_id = e.door_id or f"{floor_id}-O-new{doors}"
                if door_id in b.openings:
                    continue
                door = Opening(
                    door_id, floor_id, pair, kind, _rounded(position), e.width_m, e.step_free, True
                )
                b = dataclasses.replace(b, openings={**b.openings, door.id: door})
            elif isinstance(e, RemoveDoor):
                if e.element_id in b.openings:
                    b = dataclasses.replace(
                        b, openings={k: o for k, o in b.openings.items() if k != e.element_id}
                    )
            elif isinstance(e, MoveAsset):
                space = b.spaces.get(e.space_id)
                old = b.assets.get(e.asset_id)
                if old is None or space is None:  # the delivery it moved, or its room, was undone
                    continue
                moved = dataclasses.replace(
                    old,
                    space_id=space.id,
                    floor_id=space.floor_id,
                    position=_rounded(e.position or space.anchor),
                )
                b = dataclasses.replace(b, assets={**b.assets, moved.id: moved})
            elif isinstance(e, AddAsset):
                space = b.spaces.get(e.space_id)
                if space is None:
                    continue
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
                b = dataclasses.replace(b, assets={**b.assets, new.id: new})
        return b

    @_locked
    def export(self, active: tuple[int, ...] | None = None) -> dict:
        """building.json (kit format) with the edits that hold now applied: for saving and for Level 4."""
        if active is None:
            active = tuple(i for i in self._active() if isinstance(self.edits[i], STRUCTURAL))
        return self._export(self._replay(active))

    def _export(self, b: Building) -> dict:
        """The base's building.json brought in line with `b` (the replayed building)."""
        raw = copy.deepcopy(self.base.raw)
        spaces = {s["id"]: s for s in raw["spaces"]}
        if spaces.keys() != b.spaces.keys():  # a room was split: its halves replace it
            raw["spaces"] = [spaces.get(s.id) or _space_json(s) for s in b.spaces.values()]
        raw["openings"] = [o for o in raw["openings"] if o["id"] in b.openings]  # removed doors go
        known = set()
        for o in raw["openings"]:
            known.add(o["id"])
            if o["id"] in b.openings:
                o["space_ids"] = list(b.openings[o["id"]].space_ids)
        for e in b.openings.values():
            if e.id not in known:
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
        for c in raw["vertical_connectors"]:
            if c["id"] in b.connectors:
                stops = dict(b.connectors[c["id"]].stops)
                c["space_ids"] = [
                    stops.get(f, s) for f, s in zip(c["floor_ids"], c["space_ids"], strict=True)
                ]
        assets = {a["id"]: a for a in raw["assets"]}
        for e in b.assets.values():
            if e.id in assets:
                if e != self.base.assets.get(e.id):
                    assets[e.id].update(space_id=e.space_id, floor_id=e.floor_id, position=list(e.position))
            else:
                raw["assets"].append(
                    {
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
        if OUTSIDE in (first, second):
            return BuildingState._entrance_place(b, edit)
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
        stretches = _straight_shared(b.spaces[first], b.spaces[second])
        where = f"the wall between {b.label(first)} and {b.label(second)}"
        return floor_id, _fit_door(stretches, position, edit.width_m, where)

    @staticmethod
    def _entrance_place(b: Building, edit: AddDoor) -> tuple[str, XY]:
        """A new entrance: the middle of the room's longest outside wall (outline shared with no space on
        its floor). Only on a floor that already has an entrance, as the kit's data does (the ground floor;
        an upper one on a slope): elsewhere a door to the outside would open into the air."""
        room_id = edit.second_space_id if edit.first_space_id == OUTSIDE else edit.first_space_id
        if room_id == OUTSIDE or room_id not in b.spaces:
            raise ValueError("a door to the outside needs a room on the other side")
        room = b.spaces[room_id]
        floor = b.floors[room.floor_id]
        if floor.level != 0 and not any(o.floor_id == floor.id for o in b.openings_of(OUTSIDE)):
            raise ValueError(
                f"{floor.name} has no entrance: a door to the outside there would open into the air. Add it "
                "on the ground floor (or a floor that has an entrance)"
            )
        walls = [
            (a, c)
            for piece in _shared_stretches(b, room, OUTSIDE)
            for a, c in itertools.pairwise(piece.coords)
            if math.dist(a, c) > 1e-6
        ]  # straight pieces: a door never wraps round a corner
        a, c = max(walls, key=lambda w: math.dist(*w))
        position = edit.position or ((a[0] + c[0]) / 2, (a[1] + c[1]) / 2)
        where = f"the outside wall of {b.label(room_id)}"
        return room.floor_id, _fit_door(walls, position, edit.width_m, where)


DOOR_MARGIN_M = 0.1
"""A new door keeps this far from the ends of its wall where the wall is long enough."""


def _straight_shared(first: Space, second: Space) -> list[tuple[XY, XY]]:
    """The straight stretches of `first`'s outline that run along `second`'s (parallel edges at most
    WALL_TOLERANCE_M apart, overlapping), each as its two ends on `first`'s outline."""
    tolerance = geometry.WALL_TOLERANCE_M
    ring_a = list(_outline(first).exterior.coords)
    ring_b = list(_outline(second).exterior.coords)
    out = []
    for a0, a1 in itertools.pairwise(ring_a):
        length = math.dist(a0, a1)
        if length < 1e-6:
            continue
        ux, uy = (a1[0] - a0[0]) / length, (a1[1] - a0[1]) / length
        cuts = []
        for b0, b1 in itertools.pairwise(ring_b):
            # both ends of b's edge within tolerance of a's line, and b parallel to a
            off = [(q[0] - a0[0]) * -uy + (q[1] - a0[1]) * ux for q in (b0, b1)]
            if max(abs(o) for o in off) > tolerance or math.dist(b0, b1) < 1e-6:
                continue
            along = sorted((q[0] - a0[0]) * ux + (q[1] - a0[1]) * uy for q in (b0, b1))
            lo, hi = max(0.0, along[0]), min(length, along[1])
            if hi - lo > 1e-3:
                cuts.append((lo, hi))
        merged: list[list[float]] = []
        for lo, hi in sorted(cuts):
            if merged and lo <= merged[-1][1] + 1e-3:
                merged[-1][1] = max(merged[-1][1], hi)
            else:
                merged.append([lo, hi])
        out += [((a0[0] + ux * lo, a0[1] + uy * lo), (a0[0] + ux * hi, a0[1] + uy * hi)) for lo, hi in merged]
    return out


def _fit_door(walls: list[tuple[XY, XY]], position: XY, width_m: float, where: str) -> XY:
    """`position` moved along the nearest straight wall just enough that the whole door width lies on it
    (DOOR_MARGIN_M from its ends where there is room). ValueError when the wall is shorter than the door.
    The offset across the wall is kept (thick walls: a door stands between the two outlines)."""
    if not walls:
        raise ValueError(f"there is no straight stretch of {where} for a door")

    def gap(w: tuple[XY, XY]) -> float:
        return LineString(w).distance(Point(position))

    a, c = min(walls, key=lambda w: (gap(w), -math.dist(*w)))
    length = math.dist(a, c)
    ux, uy = (c[0] - a[0]) / length, (c[1] - a[1]) / length
    t = (position[0] - a[0]) * ux + (position[1] - a[1]) * uy
    if width_m > length + 1e-6:
        raise ValueError(
            f"{where} is only {length:.2f} m long, so a door there can be at most {math.floor(length * 10) / 10:g} m "
            f"wide (asked for {width_m:g} m)"
        )
    margin = min(DOOR_MARGIN_M, (length - width_m) / 2)
    lo, hi = width_m / 2 + margin, length - width_m / 2 - margin
    fitted = min(max(t, lo), hi)
    across = (position[0] - a[0]) * -uy + (position[1] - a[1]) * ux
    if abs(across) > geometry.WALL_TOLERANCE_M:  # a point off the wall: onto it
        across = 0.0
    if abs(fitted - t) < 1e-9 and across == (position[0] - a[0]) * -uy + (position[1] - a[1]) * ux:
        return position
    return (a[0] + ux * fitted - uy * across, a[1] + uy * fitted + ux * across)


def _rounded(xy: XY) -> XY:
    return (round(xy[0], 3), round(xy[1], 3))


def _space_json(s: Space) -> dict:
    return {
        "id": s.id,
        "floor_id": s.floor_id,
        "type": s.type,
        "room_type": s.room_type,
        "name": s.name,
        "ref": s.ref,
        "polygon": [list(p) for p in s.polygon],
        "synthetic": s.synthetic,
    }


# ---- splitting a room with a new wall ---------------------------------------------------------------------


@dataclass(frozen=True)
class Split:
    halves: tuple[Space, Space]
    wall: tuple[XY, XY]
    """The new wall's two ends, on the room's outline."""

    @property
    def length_m(self) -> float:
        (x0, y0), (x1, y1) = self.wall
        return math.hypot(x1 - x0, y1 - y0)


@dataclass(frozen=True)
class Anchor:
    """Where a new wall meets the room's outline: the middle of the stretch shared with a neighbour (or the
    outside), and that stretch as a polyline."""

    point: XY
    stretch: tuple[XY, ...]

    @property
    def length_m(self) -> float:
        return LineString(self.stretch).length if len(self.stretch) > 1 else 0.0


SNAP_M = 0.5
"""A wall end given as coordinates is moved onto the room's outline when it is at most this far from it."""


def _outline(room: Space) -> Polygon:
    shape = room.shape if room.shape.is_valid else room.shape.buffer(0)
    if not isinstance(shape, Polygon):  # an invalid OSM outline that fell apart: its biggest piece
        shape = max(getattr(shape, "geoms", []), key=lambda g: g.area)
    return shape


def _pieces(g) -> list[LineString]:
    """The connected stretches of a linear geometry."""
    if g.is_empty:
        return []
    if not isinstance(g, LineString):
        lines = [p for p in getattr(g, "geoms", []) if isinstance(p, LineString) and p.length > 0]
        if not lines:
            return []
        g = linemerge(lines) if len(lines) > 1 else lines[0]
    return [p for p in getattr(g, "geoms", [g]) if p.length > 0]


def _middle(piece: LineString) -> XY:
    p = piece.interpolate(0.5, normalized=True)
    return (p.x, p.y)


def _anchor(piece: LineString) -> Anchor:
    return Anchor(_rounded(_middle(piece)), tuple(_rounded(xy) for xy in piece.coords))


def _shared_stretches(b: Building, room: Space, neighbour_id: str) -> list[LineString]:
    """The stretches of the room's outline along a neighbour, or along no space at all for OUTSIDE."""
    shape, label = _outline(room), b.label(room.id)
    tolerance = geometry.WALL_TOLERANCE_M
    if neighbour_id == OUTSIDE:
        near = [
            s.shape.exterior.buffer(tolerance)
            for s in b.spaces_on(room.floor_id)
            if s.id != room.id and geometry._near(shape, s.shape, tolerance)  # noqa: SLF001
        ]
        free = shape.exterior.difference(unary_union(near)) if near else shape.exterior
        pieces = [p for p in _pieces(free) if p.length >= geometry.MIN_WALL_M]
        if not pieces:
            raise ValueError(f"{label} has no outside wall: every wall is shared with another space")
        return pieces
    if neighbour_id not in b.spaces:
        raise ValueError(f"unknown space {neighbour_id!r}")
    if neighbour_id == room.id:
        raise ValueError(f"name a neighbour of {label}, not the room itself")
    other = b.spaces[neighbour_id]
    if other.floor_id != room.floor_id or geometry.shared_wall_midpoint(b, room.id, neighbour_id) is None:
        raise ValueError(f"{b.label(neighbour_id)} doesn't share a wall with {label}")
    pieces = _pieces(geometry._wall_on(shape, other.shape, tolerance))  # noqa: SLF001
    if not pieces:
        raise ValueError(f"{b.label(neighbour_id)} doesn't share a wall with {label}")
    return pieces


def wall_anchors(b: Building, room_id: str, first_id: str, second_id: str) -> tuple[Anchor, Anchor]:
    """The two ends of a new wall across a room named by its neighbours (space ids, or OUTSIDE).

    Each end is the middle of the stretch of the room's outline shared with that neighbour (the longest one
    if there are several). For OUTSIDE it is the middle of an outside wall (outline shared with no space):
    of several, the one farthest from the other end, roughly the opposite wall. ValueError with a reason."""
    if room_id not in b.spaces or room_id == OUTSIDE:
        raise ValueError(f"unknown space {room_id!r}")
    room = b.spaces[room_id]
    if first_id == second_id and first_id != OUTSIDE:
        raise ValueError(
            f"name two different neighbours of {b.label(room_id)}: the wall runs from one to the other"
        )
    stretches = [_shared_stretches(b, room, sid) for sid in (first_id, second_id)]
    found: list[Anchor | None] = [None, None]
    for k, sid in enumerate((first_id, second_id)):
        if sid != OUTSIDE:
            found[k] = _anchor(max(stretches[k], key=lambda p: p.length))
    if (
        found[0] is None and found[1] is None
    ):  # both outside: the longest outside wall and the farthest other one
        longest = max(stretches[0], key=lambda p: p.length)
        rest = [p for p in stretches[1] if not p.equals(longest)]
        if not rest:
            raise ValueError(
                f"{b.label(room_id)} has only one outside wall: name a neighbour for the other end"
            )
        found[0] = _anchor(longest)
        stretches[1] = rest
    for k in (0, 1):
        if found[k] is None:
            other = Point(found[1 - k].point)  # type: ignore[union-attr]
            found[k] = _anchor(max(stretches[k], key=lambda p: Point(_middle(p)).distance(other)))
    return found[0], found[1]  # type: ignore[return-value]


def split_between(b: Building, room_id: str, first_id: str, second_id: str) -> SplitRoom:
    """The SplitRoom for a wall across a room from the wall it shares with `first_id` to the one it shares
    with `second_id` (space ids, or OUTSIDE): its ends are wall_anchors. ValueError with a reason."""
    start, end = wall_anchors(b, room_id, first_id, second_id)
    return SplitRoom(room_id, start.point, end.point, first_id, second_id)


def snap_to_outline(b: Building, room_id: str, xy: XY) -> XY:
    """A point moved onto the room's outline, or ValueError when it is more than SNAP_M away from it."""
    shape = _outline(b.spaces[room_id])
    ring = shape.exterior
    p = ring.interpolate(ring.project(Point(xy)))
    gap = p.distance(Point(xy))
    if gap > SNAP_M:
        raise ValueError(
            f"({xy[0]:.2f}, {xy[1]:.2f}) is {gap:.1f} m from the walls of {b.label(room_id)}: a wall end must be "
            f"on its outline (within {SNAP_M:g} m)"
        )
    return _rounded((p.x, p.y))


def _compass(dx: float, dy: float) -> str:
    """The compass word for the direction (dx, dy) on the plan (y points north): one of the eight winds."""
    ns = "north" if dy > 0 else "south"
    ew = "east" if dx > 0 else "west"
    if abs(dy) < abs(dx) * 0.414:  # within 22.5° of east-west
        return ew
    if abs(dx) < abs(dy) * 0.414:
        return ns
    return f"{ns}-{ew}"


def _end_label(b: Building, sid: str | None, xy: XY) -> str:
    if sid == OUTSIDE:
        return "the outside wall"
    if sid:
        return f"the wall with {b.label(sid)}"
    return f"({xy[0]:.2f}, {xy[1]:.2f})"


def split_room(b: Building, edit: SplitRoom) -> Split:
    """The two parts the straight wall from edit.start to edit.end cuts a room into, or ValueError (with a
    reason for a person) when it doesn't cut it in two: it runs outside the room, along one of its walls,
    or cuts it into more pieces. The halves are whatever the wall cuts: not necessarily equal."""
    if edit.space_id not in b.spaces or edit.space_id == OUTSIDE:
        raise ValueError(f"unknown space {edit.space_id!r}")
    room = b.spaces[edit.space_id]
    label = b.label(room.id)
    shape = _outline(room)
    p, q = edit.start, edit.end
    seg = LineString([p, q])
    if seg.length < 0.1:
        raise ValueError(
            f"the two ends of the wall are the same point: pick ends on two different walls of {label}"
        )
    span = f"from {_end_label(b, edit.from_space_id, p)} to {_end_label(b, edit.to_space_id, q)}"
    inner = substring(seg, 0.001, seg.length - 0.001)
    if not shape.contains(inner):
        if shape.exterior.buffer(0.01).contains(inner):
            raise ValueError(
                f"a wall {span} would run along a wall of {label}, not across it: both ends are on the same side of the room"
            )
        raise ValueError(f"a straight wall {span} would leave {label}: its shape is in the way")
    d = ((q[0] - p[0]) / seg.length, (q[1] - p[1]) / seg.length)
    reach = 0.01
    longer = LineString(
        [(p[0] - d[0] * reach, p[1] - d[1] * reach), (q[0] + d[0] * reach, q[1] + d[1] * reach)]
    )
    parts = [g for g in split(shape, longer).geoms if isinstance(g, Polygon) and g.area > 1e-4]
    if len(parts) != 2:
        raise ValueError(f"a straight wall {span} would cut {label} into {len(parts)} parts, not two")
    first, second = parts
    dx, dy = first.centroid.x - second.centroid.x, first.centroid.y - second.centroid.y
    if (dx > 0) if abs(dx) >= abs(dy) else (dy < 0):  # number in reading order: west, then north, first
        first, second = second, first
    halves = []
    for number, (half, other) in enumerate(((first, second), (second, first)), start=1):
        word = _compass(half.centroid.x - other.centroid.x, half.centroid.y - other.centroid.y)
        tag = "".join(w[0].upper() for w in word.split("-"))
        corners = [_rounded(xy) for xy in list(half.exterior.coords)[:-1]]
        polygon = tuple(xy for i, xy in enumerate(corners) if xy != corners[i - 1])  # no repeated corners
        # Each half gets its own number: 0.33 -> 0.33-1 and 0.33-2, in the name too where it shows the ref.
        ref = f"{room.ref}-{number}" if room.ref else str(number)
        name = (
            room.name.replace(room.ref, ref, 1)
            if room.ref and room.ref in room.name
            else f"{room.name} {ref}"
        )
        halves.append(
            Space(
                f"{room.id}-{tag}",
                room.floor_id,
                room.type,
                room.room_type,
                f"{name} ({word} half)",
                ref,
                polygon,
                True,
            )
        )
    if halves[0].id == halves[1].id or any(h.id in b.spaces for h in halves):
        raise ValueError(f"can't name the two halves of {label}")
    return Split((halves[0], halves[1]), (_rounded(p), _rounded(q)))


def with_split(b: Building, edit: SplitRoom) -> Building:
    """`b` with a room replaced by its two halves. Doors and items go to the half they are in (or nearest),
    stairs and lifts to the half holding the room's anchor."""
    split = split_room(b, edit)
    old = b.spaces[edit.space_id]

    def half_at(xy: XY) -> str:
        p = Point(xy)
        return min(split.halves, key=lambda h: h.shape.distance(p)).id

    spaces: dict[str, Space] = {}
    for sid, s in b.spaces.items():
        if sid == old.id:
            spaces |= {h.id: h for h in split.halves}
        else:
            spaces[sid] = s
    openings = dict(b.openings)
    for o in b.openings_of(old.id):
        half = half_at(o.position)
        openings[o.id] = dataclasses.replace(
            o, space_ids=tuple(half if sid == old.id else sid for sid in o.space_ids)
        )
    assets = dict(b.assets)
    for a in b.assets_in(old.id):
        assets[a.id] = dataclasses.replace(a, space_id=half_at(a.position))
    connectors = dict(b.connectors)
    anchor_half = half_at(old.anchor)
    for c in b.connectors_at(old.id):
        connectors[c.id] = dataclasses.replace(
            c, stops=tuple((f, anchor_half if sid == old.id else sid) for f, sid in c.stops)
        )
    return dataclasses.replace(b, spaces=spaces, openings=openings, assets=assets, connectors=connectors)
