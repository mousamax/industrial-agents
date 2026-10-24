"""L1 plan model: the typed, read-only view of one building.json that every other module builds on.

Shared foundation, already implemented. Owner: Mousa (integration). Change field names or signatures only
after telling the team: everyone imports this.

    b = load_building("proc-factory-dev-003")          # id from synera-kit/data, or a directory path
    b.spaces[space_id].area                            # m², shapely
    b.resolve("Office 0.29 (Ground floor)")            # -> [Space], the way questions name rooms
    b.spaces_on(floor_id, type="room", room_type="office")

Facts from docs/synera/DATA_MODEL.md: openings always join exactly 2 spaces (or a space and "outside"),
stairs are never step-free and lifts always are, refs and names are not unique in OSM buildings.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from functools import cache, cached_property
from pathlib import Path
from typing import Any, Literal

from shapely.geometry import Point, Polygon

from app.settings import ROOT, get_settings

OUTSIDE = "outside"

SpaceType = Literal["room", "corridor", "hall", "stair", "lift", "outdoor"]
OpeningKind = Literal["door", "opening", "entrance"]
ConnectorKind = Literal["stairs", "lift"]
XY = tuple[float, float]


@dataclass(frozen=True)
class Floor:
    id: str
    level: int
    name: str
    elevation_m: float
    height_m: float


@dataclass(frozen=True)
class Space:
    id: str
    floor_id: str
    type: SpaceType
    room_type: str
    name: str
    ref: str
    polygon: tuple[XY, ...]
    synthetic: bool

    @cached_property
    def shape(self) -> Polygon:
        return Polygon(self.polygon)

    @cached_property
    def area(self) -> float:
        return self.shape.area

    @cached_property
    def anchor(self) -> XY:
        """The routing node position used by the ground truth: shapely's representative point."""
        p = self.shape.representative_point()
        return (p.x, p.y)

    @cached_property
    def centroid(self) -> XY:
        """Polygon centroid. Can lie outside L/U-shaped spaces (82 in the data); questions still use it."""
        c = self.shape.centroid
        return (c.x, c.y)


@dataclass(frozen=True)
class Opening:
    id: str
    floor_id: str
    space_ids: tuple[str, str]
    kind: OpeningKind
    position: XY
    width_m: float
    step_free: bool
    synthetic: bool

    @property
    def is_entrance(self) -> bool:
        return OUTSIDE in self.space_ids

    def other_side(self, space_id: str) -> str:
        a, b = self.space_ids
        return b if a == space_id else a


@dataclass(frozen=True)
class Connector:
    """Stairs or lift. `stops` are (floor_id, space_id) pairs sorted by floor level."""

    id: str
    kind: ConnectorKind
    name: str
    stops: tuple[tuple[str, str], ...]
    step_free: bool
    synthetic: bool


@dataclass(frozen=True)
class Walkway:
    """Level 3: an outdoor link between entrances of two buildings on one site (site.json). Walked like a
    corridor of `length_m`; the only way between buildings (there is no free way round the outside)."""

    id: str
    name: str
    opening_ids: tuple[str, str]  # an entrance of each building
    length_m: float
    step_free: bool = True
    width_m: float | None = None  # None: no limit


@dataclass(frozen=True)
class Asset:
    id: str
    name: str
    category: str  # furniture | fixture | machine | equipment
    type: str  # coffee_machine, cnc_mill, fire_extinguisher, ... (97 types)
    space_id: str
    floor_id: str
    position: XY
    width_m: float
    depth_m: float
    rotation_deg: float
    source: str  # dataset | generated


@dataclass(frozen=True, eq=False)
class Building:
    id: str
    name: str
    source: str  # procedural | osm_indoor | swiss_dwellings | resplan
    info: dict[str, Any]
    floors: dict[str, Floor]
    spaces: dict[str, Space]
    openings: dict[str, Opening]
    connectors: dict[str, Connector]
    assets: dict[str, Asset]
    raw: dict[str, Any] = field(repr=False)
    """The original building.json, untouched. Export edits through app.building.state, not by mutating this."""
    walkways: dict[str, Walkway] = field(default_factory=dict)
    """Level 3 sites only (load_building on a folder with site.json); empty for a single building."""

    # ---- indexes -------------------------------------------------------------------------------------

    @cached_property
    def _spaces_by_floor(self) -> dict[str, list[Space]]:
        out: dict[str, list[Space]] = defaultdict(list)
        for s in self.spaces.values():
            out[s.floor_id].append(s)
        return out

    @cached_property
    def _openings_by_space(self) -> dict[str, list[Opening]]:
        out: dict[str, list[Opening]] = defaultdict(list)
        for o in self.openings.values():
            for sid in o.space_ids:
                out[sid].append(o)
        return out

    @cached_property
    def _assets_by_space(self) -> dict[str, list[Asset]]:
        out: dict[str, list[Asset]] = defaultdict(list)
        for a in self.assets.values():
            out[a.space_id].append(a)
        return out

    def floors_by_level(self) -> list[Floor]:
        return sorted(self.floors.values(), key=lambda f: f.level)

    def floor_at_level(self, level: int) -> Floor | None:
        return next((f for f in self.floors.values() if f.level == level), None)

    def spaces_on(
        self, floor_id: str, *, type: str | None = None, room_type: str | None = None
    ) -> list[Space]:
        return [
            s
            for s in self._spaces_by_floor.get(floor_id, [])
            if (type is None or s.type == type) and (room_type is None or s.room_type == room_type)
        ]

    def openings_of(self, space_id: str) -> list[Opening]:
        """Every opening touching a space. Use space_id=OUTSIDE for all entrances."""
        return self._openings_by_space.get(space_id, [])

    def connectors_at(self, space_id: str) -> list[Connector]:
        return [c for c in self.connectors.values() if any(sid == space_id for _, sid in c.stops)]

    def assets_in(self, space_id: str) -> list[Asset]:
        return self._assets_by_space.get(space_id, [])

    def assets_of_type(self, asset_type: str, floor_id: str | None = None) -> list[Asset]:
        return [
            a
            for a in self.assets.values()
            if a.type == asset_type and (floor_id is None or a.floor_id == floor_id)
        ]

    def space_at(self, floor_id: str, xy: XY) -> Space | None:
        """The space containing a point (smallest one if OSM spaces overlap)."""
        p = Point(xy)
        hits = [s for s in self.spaces_on(floor_id) if s.shape.covers(p)]
        return min(hits, key=lambda s: s.area) if hits else None

    def label(self, space_id: str) -> str:
        """'Office 0.29 (Ground floor)': unambiguous for people, the same form the questions use."""
        if space_id == OUTSIDE:
            return "outside"
        s = self.spaces[space_id]
        return f"{s.name} ({self.floors[s.floor_id].name})"

    # ---- name resolution -----------------------------------------------------------------------------

    def resolve(self, reference: str, floor: str | int | None = None) -> list[Space]:
        """Spaces matching an id, a ref ('0.21'), a name, or 'Name (Floor name)'. Empty list if none.

        Several matches mean the reference is ambiguous (OSM duplicates, 'Staircase 1' on every floor):
        the caller should ask, or narrow with `floor` (a floor id, floor name or level).
        """
        ref = reference.strip()
        if ref in self.spaces:
            return [self.spaces[ref]]
        floor_id = self._floor_id(floor) if floor is not None else None
        if floor_id is None and (m := re.fullmatch(r"(.*\S)\s*\(([^()]*)\)", ref)):
            if (fid := self._floor_id(m.group(2))) is not None:
                ref, floor_id = m.group(1), fid
        needle = ref.casefold()
        candidates = self.spaces.values() if floor_id is None else self._spaces_by_floor.get(floor_id, [])
        for key in (lambda s: s.name.casefold(), lambda s: s.ref.casefold()):
            hits = [s for s in candidates if key(s) == needle]
            if hits:
                return hits
        return []

    def _floor_id(self, floor: str | int) -> str | None:
        if isinstance(floor, int):
            f = self.floor_at_level(floor)
            return f.id if f else None
        if floor in self.floors:
            return floor
        return next(
            (f.id for f in self.floors.values() if f.name.casefold() == floor.strip().casefold()), None
        )


# ---- loading ---------------------------------------------------------------------------------------------


def building_dir(reference: str | Path) -> Path:
    """A building (or site) directory, or a building id looked up in synera-kit/data/{train,dev,test}/."""
    for path in (Path(reference), ROOT / str(reference)):  # a folder, also relative to the repo root
        if (path / "building.json").exists() or (path / "site.json").exists():
            return path
    matches = sorted(get_settings().synera_kit_dir.glob(f"data/*/{reference}/building.json"))
    if not matches:
        raise FileNotFoundError(f"no building {reference!r} (is the synera-kit submodule checked out?)")
    return matches[0].parent


def parse_building(raw: dict[str, Any]) -> Building:
    info = raw["building"]
    floors = {
        f["id"]: Floor(f["id"], f["level"], f["name"], f["elevation_m"], f["height_m"]) for f in raw["floors"]
    }
    levels = {fid: f.level for fid, f in floors.items()}
    return Building(
        id=info["id"],
        name=info["name"],
        source=info["source"],
        info=info,
        floors=floors,
        spaces={
            s["id"]: Space(
                s["id"],
                s["floor_id"],
                s["type"],
                s["room_type"],
                s["name"],
                s["ref"],
                tuple((float(x), float(y)) for x, y in s["polygon"]),
                s["synthetic"],
            )
            for s in raw["spaces"]
        },
        openings={
            o["id"]: Opening(
                o["id"],
                o["floor_id"],
                tuple(o["space_ids"]),
                o["kind"],
                tuple(o["position"]),  # type: ignore[arg-type]
                o["width_m"],
                o["step_free"],
                o["synthetic"],
            )
            for o in raw["openings"]
        },
        connectors={
            c["id"]: Connector(
                c["id"],
                c["kind"],
                c["name"],
                tuple(sorted(zip(c["floor_ids"], c["space_ids"], strict=True), key=lambda st: levels[st[0]])),
                c["step_free"],
                c["synthetic"],
            )
            for c in raw["vertical_connectors"]
        },
        assets={
            a["id"]: Asset(
                a["id"],
                a["name"],
                a["category"],
                a["type"],
                a["space_id"],
                a["floor_id"],
                tuple(a["position"]),  # type: ignore[arg-type]
                a["footprint"]["width_m"],
                a["footprint"]["depth_m"],
                a["footprint"]["rotation_deg"],
                a["source"],
            )
            for a in raw["assets"]
        },
        raw=raw,
    )


@cache
def _load(path: str) -> Building:
    if path.endswith("site.json"):
        return load_site(Path(path))
    return parse_building(json.loads(Path(path).read_text(encoding="utf-8")))


def load_building(reference: str | Path) -> Building:
    """Load (cached) a building by kit id or directory, or a Level 3 site (a directory with site.json).
    The result is shared: treat it as read-only."""
    folder = building_dir(reference)
    name = "site.json" if (folder / "site.json").exists() else "building.json"
    return _load(str((folder / name).resolve()))


def load_site(path: Path) -> Building:
    """Level 3: several buildings and the walkways between them, as one Building.

    site.json = {"site": {"id", "name"}, "buildings": [kit id or folder, ...], "walkways": [{"id", "name",
    "from": entrance opening id, "to": entrance opening id, "length_m", "step_free", "width_m"}]}. Ids stay
    as they are (kit ids already start with the building id); floor names get the building's name, so
    "Ground floor" isn't ambiguous. Without length_m a walkway is the straight line between its two doors,
    which is only right when the buildings share one coordinate frame: give the length when you know it.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    parts = [
        load_building(path.parent / ref if (path.parent / ref).exists() else ref) for ref in raw["buildings"]
    ]
    merged: dict[str, dict] = {"floors": {}, "spaces": {}, "openings": {}, "connectors": {}, "assets": {}}
    for b in parts:
        merged["floors"] |= {
            fid: Floor(f.id, f.level, f"{b.name} · {f.name}", f.elevation_m, f.height_m)
            for fid, f in b.floors.items()
        }
        for key in ("spaces", "openings", "connectors", "assets"):
            clash = merged[key].keys() & getattr(b, key).keys()
            if clash:
                raise ValueError(f"{b.id}: {key} ids also used by another building: {sorted(clash)[:3]}")
            merged[key] |= getattr(b, key)
    walkways = {}
    for w in raw.get("walkways", []):
        ends = (w["from"], w["to"])
        for end in ends:
            if end not in merged["openings"] or not merged["openings"][end].is_entrance:
                raise ValueError(f"walkway {w['id']}: {end!r} is not an entrance of a site building")
        a, c = (merged["openings"][e].position for e in ends)
        length = (
            float(w["length_m"])
            if w.get("length_m") is not None
            else ((a[0] - c[0]) ** 2 + (a[1] - c[1]) ** 2) ** 0.5
        )
        walkways[w["id"]] = Walkway(
            w["id"], w.get("name", w["id"]), ends, length, w.get("step_free", True), w.get("width_m")
        )
    info = raw.get("site", {})
    return Building(
        id=info.get("id", path.parent.name),
        name=info.get("name", path.parent.name),
        source="site",
        info=info | {"buildings": [b.id for b in parts]},
        raw=raw,
        walkways=walkways,
        **merged,
    )
