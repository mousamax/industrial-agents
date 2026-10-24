"""Deps and tools for building_guide, on the shared domain interfaces (src/app/building/).

Tools are small building blocks the agent composes: look things up (find, room_details, list_rooms,
count_items), move through the building (route, nearest, reachable_rooms, distance_3d), and reason about
changes (what_if, set_closed). Every number comes from code; the model only picks tools and arguments.

Places are passed as text, the way people and questions name them ("Office 0.29 (Ground floor)", "0.29",
an id, or an item name such as "CNC mill 1.04"). Resolving that text is its own step: an unknown name is a
ModelRetry with suggestions, an ambiguous one returns the candidates so the agent can ask back. Results
carry space ids (`highlight`, `space_path`) that the UI maps onto GET /buildings/{id}/scene.
"""

from __future__ import annotations

import dataclasses
import difflib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from pydantic_ai import ModelRetry, RunContext

from app.building import geometry
from app.building.model import OUTSIDE, Building, Floor, Opening, Space, load_building
from app.building.routing import Constraints, Route

from .graph import route_graph

MAX_RESULTS = 15
MIN_GAIN_M = 1.0


@dataclass
class Deps:
    building: Building
    closed: set[str] = field(default_factory=set)
    """Elements the person said are closed (lifts, stairs, doors, rooms). Every route respects them."""

    @classmethod
    def load(cls, reference: str | Path) -> Deps:
        return cls(building=load_building(reference))


# ---- resolving what people say ---------------------------------------------------------------------------


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _key(text: str) -> str:
    return re.sub(r"[\s\-]+", "_", text.strip().casefold())


def floor_of(b: Building, floor: str | int) -> Floor:
    """A floor from its level (2, -1), name ("Floor 2", "the ground floor", "Basement 1") or id."""
    text = str(floor).strip()
    text = re.sub(r"^(on |at )?the ", "", text.casefold())
    if re.fullmatch(r"-?\d+", text) and (f := b.floor_at_level(int(text))):
        return f
    if (fid := b._floor_id(text)) or (fid := b._floor_id(str(floor))):
        return b.floors[fid]
    if m := re.fullmatch(r"(?:floor|level)\s*(-?\d+)", text):
        if f := b.floor_at_level(int(m.group(1))):
            return f
    names = ", ".join(f"{f.level} = {f.name}" for f in b.floors_by_level())
    raise ModelRetry(f"No floor {floor!r}. Floors: {names}.")


def _spaces_named(b: Building, text: str, floor: str | int | None) -> list[Space]:
    floor_id = floor_of(b, floor).id if floor is not None else None
    hits = b.resolve(text, floor_id)
    if not hits and (asset := _asset_named(b, text)):
        hits = [b.spaces[asset.space_id]]
    if not hits and (
        m := re.fullmatch(r"(.+?)\s+(\S+)\s*\(([^()]*)\)", text.strip())
    ):  # "Büro 1.02 (Floor 1)"
        hits = [s for s in b.resolve(m[2], m[3]) if s.name.casefold() == m[1].casefold()]
    if not hits:  # "Room 2.03" when the ref is "2.03", "the Main lobby"
        bare = re.sub(r"^(the|room|space)\s+", "", text.strip(), flags=re.I)
        hits = b.resolve(bare, floor_id) if bare != text.strip() else []
    return hits


def _asset_named(b: Building, text: str):
    needle = text.strip().casefold()
    return next((a for a in b.assets.values() if a.id == text or a.name.casefold() == needle), None)


def space_of(b: Building, text: str, floor: str | int | None = None) -> Space | str:
    """One space, or OUTSIDE. Raises ModelRetry with suggestions if nothing matches or several do."""
    if text.strip().casefold() in ("outside", OUTSIDE):
        return OUTSIDE
    hits = _spaces_named(b, text, floor)
    if len(hits) == 1:
        return hits[0]
    if hits:
        options = "; ".join(f"{b.label(s.id)} [{s.id}]" for s in hits[:MAX_RESULTS])
        raise ModelRetry(
            f"{text!r} matches {len(hits)} spaces: {options}. Pass the id or add the floor; if the person's "
            "words don't settle it, ask them which one."
        )
    labels = [b.label(s.id) for s in b.spaces.values()]
    close = difflib.get_close_matches(text, labels, n=5, cutoff=0.5)
    hint = f" Did you mean: {'; '.join(close)}?" if close else " Use find to search."
    raise ModelRetry(f"No room or item called {text!r}.{hint}")


def _space_id(b: Building, text: str, floor: str | int | None = None) -> str:
    s = space_of(b, text, floor)
    return s if isinstance(s, str) else s.id


def _type_of(text: str, known: set[str], what: str) -> str:
    key = _key(text)
    for candidate in (key, key.removesuffix("s"), key.removesuffix("es")):
        if candidate in known:
            return candidate
    close = difflib.get_close_matches(key, sorted(known), n=5, cutoff=0.5)
    raise ModelRetry(f"No {what} {text!r} here. Closest: {', '.join(close) or 'none'}. See the overview.")


def element_of(b: Building, text: str) -> str:
    """A lift, staircase, door or room id from its name or id."""
    if text in b.connectors or text in b.openings or text in b.spaces:
        return text
    needle = text.strip().casefold()
    named = [k.id for k in b.connectors.values() if k.name.casefold() == needle]
    if len(named) == 1:
        return named[0]
    if len(named) > 1:
        raise ModelRetry(f"{text!r} names {len(named)} connectors: {named}. Pass the id.")
    return _space_id(b, text)


def _label(b: Building, space_id: str) -> str:
    """b.label plus the room number when the name lacks it: "Büro 1.02 (Floor 1)", not "Büro (Floor 1)".
    OSM names repeat; the number is what tells rooms apart (and what the scorer accepts)."""
    if space_id == OUTSIDE:
        return OUTSIDE
    s = b.spaces[space_id]
    if s.ref and s.ref.casefold() not in s.name.casefold():
        return f"{s.name} {s.ref} ({b.floors[s.floor_id].name})"
    return b.label(space_id)


def _constraints(ctx: RunContext[Deps], step_free: bool, min_width_m: float | None, closed=()) -> Constraints:
    ids = {element_of(ctx.deps.building, c) for c in closed} | ctx.deps.closed
    return Constraints(step_free=step_free, min_width_m=min_width_m, closed=frozenset(ids))


def is_room(s: Space) -> bool:
    """What reachability counts call a room (measured on every train/dev answer): spaces of type "room",
    not halls, corridors, stairwells, lifts or balconies. Shared walls count halls too (geometry)."""
    return s.type == "room" and s.room_type not in geometry.NOT_ROOMS


# ---- context ---------------------------------------------------------------------------------------------


def overview(b: Building) -> dict:
    """The building in a few hundred tokens: floors, vocabulary of room and item types, lifts, entrances.
    Static per building on purpose: the prompt prefix stays byte-identical across turns, so it caches.
    Session state (closures) shows in tool results instead."""
    floors = []
    for f in b.floors_by_level():
        spaces = b.spaces_on(f.id)
        floors.append(
            {
                "level": f.level,
                "name": f.name,
                "elevation_m": f.elevation_m,
                "room_types": dict(Counter(s.room_type for s in spaces).most_common()),
            }
        )
    entrances = sorted({sid for o in b.openings_of(OUTSIDE) for sid in o.space_ids if sid != OUTSIDE})
    return {
        "building": b.name,
        "source": b.source,
        "floors": floors,
        "item_types": dict(sorted(Counter(a.type for a in b.assets.values()).items())),
        "lifts_and_stairs": [
            {"name": k.name, "kind": k.kind, "id": k.id, "floors": [b.floors[f].level for f, _ in k.stops]}
            for k in b.connectors.values()
        ],
        "entrances_into": [_label(b, sid) for sid in entrances],
    }


# ---- tools: look things up -------------------------------------------------------------------------------


def find(ctx: RunContext[Deps], query: str, floor: str | None = None) -> str:
    """Search rooms and items (furniture, machines, equipment) by name, room number or type.

    Use it to turn what a person says ("the 3D printer", "room 2.3", "coffee") into exact rooms.

    Args:
        query: Words to look for, e.g. "3d printer", "2.03", "toilet", "cnc mill", "Büchercorner".
        floor: Only this floor: level ("2", "-1"), or name ("Ground floor"). Omit for all floors.
    """
    b = ctx.deps.building
    floor_id = floor_of(b, floor).id if floor else None
    words = [w for w in re.split(r"[\s_]+", query.casefold()) if w]

    def hit(*texts: str) -> bool:
        blob = " ".join(texts).casefold().replace("_", " ")
        return all(w in blob for w in words)

    rooms = [
        {"room": _label(b, s.id), "id": s.id, "type": s.room_type}
        for s in b.spaces.values()
        if (floor_id is None or s.floor_id == floor_id) and hit(s.name, s.ref, s.room_type)
    ]
    items = [
        {"item": a.name, "type": a.type, "in": _label(b, a.space_id), "space_id": a.space_id}
        for a in b.assets.values()
        if (floor_id is None or a.floor_id == floor_id) and hit(a.name, a.type)
    ]
    if not rooms and not items:
        raise ModelRetry(
            f"Nothing matches {query!r}. Try one shorter word, or check item_types in the overview."
        )
    return _json(
        {
            "rooms": rooms[:MAX_RESULTS],
            "rooms_total": len(rooms),
            "items": items[:MAX_RESULTS],
            "items_total": len(items),
            "highlight": sorted(
                {r["id"] for r in rooms[:MAX_RESULTS]} | {i["space_id"] for i in items[:MAX_RESULTS]}
            ),
        }
    )


def room_details(ctx: RunContext[Deps], room: str) -> str:
    """Everything about one room: area, type, floor, centre, doors (width, step-free), lifts and stairs in
    it, items, the rooms it shares a wall with (≥1 m), and the spaces directly above and below.

    Args:
        room: Room name or number, "Name (Floor name)", an id, or an item name (gives the item's room).
    """
    b = ctx.deps.building
    s = space_of(b, room)
    if isinstance(s, str):
        raise ModelRetry("'outside' is not a room.")
    above, below = geometry.stacked(b, s.id, "above"), geometry.stacked(b, s.id, "below")
    walls = geometry.shared_wall_rooms(b, s.id)
    return _json(
        {
            "room": _label(b, s.id),
            "id": s.id,
            "type": s.type,
            "room_type": s.room_type,
            "area_m2": round(s.area, 2),
            "centre_xyz_m": [round(v, 2) for v in geometry.centroid_3d(b, s.id)],
            "doors": [
                {
                    "id": o.id,
                    "to": _label(b, o.other_side(s.id)),
                    "kind": o.kind,
                    "width_m": o.width_m,
                    "step_free": o.step_free,
                }
                for o in b.openings_of(s.id)
            ],
            "lifts_and_stairs": [k.name for k in b.connectors_at(s.id)],
            "items": dict(Counter(a.type for a in b.assets_in(s.id))),
            "shares_a_wall_with": {"count": len(walls), "rooms": [_label(b, r) for r in walls]},
            "directly_above": above and _label(b, above),
            "directly_below": below and _label(b, below),
            "highlight": [s.id],
        }
    )


def list_rooms(ctx: RunContext[Deps], floor: str, room_type: str | None = None) -> str:
    """Rooms on a floor, largest first, with areas and the total area. Use for "largest X", "total area".

    Args:
        floor: Level ("1", "0", "-1") or name ("Floor 1", "Ground floor").
        room_type: Only this type, e.g. "office", "kitchen", "generic_room" (see room_types in the overview).
    """
    b = ctx.deps.building
    f = floor_of(b, floor)
    if room_type:
        rtype = _type_of(room_type, {s.room_type for s in b.spaces.values()}, "room type")
        ranked = geometry.rooms_by_area(b, f.id, rtype)
    else:
        ranked = sorted(((s, s.area) for s in b.spaces_on(f.id) if is_room(s)), key=lambda t: -t[1])
    return _json(
        {
            "floor": f.name,
            "room_type": room_type,
            "count": len(ranked),
            "total_area_m2": round(sum(a for _, a in ranked), 2),
            "rooms": [
                {"room": _label(b, s.id), "id": s.id, "area_m2": round(a, 2)} for s, a in ranked[:MAX_RESULTS]
            ],
            "highlight": [s.id for s, _ in ranked[:MAX_RESULTS]],
        }
    )


def count_items(ctx: RunContext[Deps], item_type: str, floor: str | None = None) -> str:
    """How many items of one type there are (exact type), per floor and room.

    Args:
        item_type: An item type from the overview, e.g. "chair", "fire_extinguisher".
        floor: Level or floor name; omit for the whole building.
    """
    b = ctx.deps.building
    itype = _type_of(item_type, {a.type for a in b.assets.values()}, "item type")
    floor_id = floor_of(b, floor).id if floor else None
    items = b.assets_of_type(itype, floor_id)
    return _json(
        {
            "item_type": itype,
            "count": len(items),
            "by_room": dict(Counter(_label(b, a.space_id) for a in items).most_common(MAX_RESULTS)),
            "highlight": sorted({a.space_id for a in items}),
        }
    )


def distance_3d(ctx: RunContext[Deps], first: str, second: str) -> str:
    """Straight-line distance in metres between two room centres (floor-plan centroid at floor elevation).

    Args:
        first: Room name, number or id.
        second: Room name, number or id.
    """
    b = ctx.deps.building
    a, c = _space_id(b, first), _space_id(b, second)
    return _json(
        {
            "from": _label(b, a),
            "to": _label(b, c),
            "distance_m": round(geometry.straight_line_3d(b, a, c), 2),
            "highlight": [a, c],
        }
    )


# ---- tools: move through the building --------------------------------------------------------------------


def _steps(b: Building, route: Route) -> list[str]:
    """A skeleton of the walk for the agent to turn into directions people can follow."""
    steps: list[str] = []
    floor = None
    nodes = route.node_path or route.space_path
    for i, node in enumerate(nodes):
        if node.startswith("door:"):
            o = b.openings[node.removeprefix("door:")]
            steps.append(f"through the {o.kind} ({o.width_m} m wide)")
        elif node == OUTSIDE:
            steps.append("outside")
        else:
            s = b.spaces[node]
            if floor is not None and s.floor_id != floor:
                k = next((k for k in b.connectors_at(node) if k.id in route.element_path), None)
                how = f"{k.name} ({k.kind})" if k else "the stairs or lift"
                steps.append(f"take {how} to {b.floors[s.floor_id].name}")
            steps.append(_label(b, node) if i else f"start in {_label(b, node)}")
            floor = s.floor_id
    return steps


def _route_result(b: Building, route: Route) -> dict:
    return {
        "length_m": round(route.length_m, 2),
        "steps": _steps(b, route),
        "space_path": route.space_path,
        "element_path": list(dict.fromkeys(route.element_path)),
    }


def route(
    ctx: RunContext[Deps],
    start: str,
    destination: str,
    via: list[str] | None = None,
    step_free: bool = False,
    min_width_m: float | None = None,
) -> str:
    """Shortest route between places, in order through any `via` stops, with its length in metres and a
    skeleton of steps (rooms, doors, lifts, stairs). Closures the person reported are always avoided.

    Args:
        start: Where the person is: room name/number, id, item name, or "outside".
        destination: Where they go (room, item name, "outside").
        via: Stops to visit in order on the way, e.g. a room to pick something up from.
        step_free: No stairs or steps (wheelchair, pram, trolley, crutches, "no stairs"). Lifts are fine.
        min_width_m: Every door passed must be at least this wide (moving a wide load).
    """
    b = ctx.deps.building
    stops = [_space_id(b, p) for p in [start, *(via or []), destination]]
    graph = route_graph(b, _constraints(ctx, step_free, min_width_m))
    tour = graph.tour(stops)
    if tour is None:
        return _json(
            {
                "reachable": False,
                "stops": [_label(b, s) for s in stops],
                "note": "No route under these constraints.",
            }
        )
    legs = [_route_result(b, leg) for leg in tour.legs]
    return _json(
        {
            "reachable": True,
            "total_length_m": round(tour.length_m, 2),
            "stops": [_label(b, s) for s in stops],
            "legs": legs,
            "closed_avoided": sorted(ctx.deps.closed),
            "highlight": stops,
        }
    )


def nearest(
    ctx: RunContext[Deps],
    start: str,
    item_type: str | None = None,
    room_type: str | None = None,
    step_free: bool = False,
) -> str:
    """The closest rooms by walking distance that hold an item type, or are of a room type.

    Args:
        start: Where the person is.
        item_type: An item type from the overview, e.g. "coffee_machine", "office_chair".
        room_type: Or a room type, e.g. "toilet", "kitchen".
        step_free: Walk without stairs or steps.
    """
    b = ctx.deps.building
    if not item_type and not room_type:
        raise ModelRetry("Give item_type or room_type.")
    origin = _space_id(b, start)
    if item_type:
        itype = _type_of(item_type, {a.type for a in b.assets.values()}, "item type")
        targets = {a.space_id for a in b.assets.values() if a.type == itype}
    else:
        rtype = _type_of(room_type or "", {s.room_type for s in b.spaces.values()}, "room type")
        targets = {s.id for s in b.spaces.values() if s.room_type == rtype}
    distances = route_graph(b, _constraints(ctx, step_free, None)).distances_from([origin])
    ranked = sorted((distances[t], t) for t in targets if t in distances)
    return _json(
        {
            "from": _label(b, origin),
            "nearest": [{"room": _label(b, t), "id": t, "walk_m": round(d, 2)} for d, t in ranked[:5]],
            "unreachable": len(targets) - len(ranked),
            "highlight": [t for _, t in ranked[:5]],
        }
    )


def reachable_rooms(
    ctx: RunContext[Deps],
    start: str,
    floor: str,
    step_free: bool = False,
    min_width_m: float | None = None,
    closed: list[str] | None = None,
) -> str:
    """Rooms on a floor that can be reached from a start (deliveries, wheelchair access). Counts rooms only:
    halls (factory zones), corridors, stairwells, lifts and balconies are not rooms.

    Args:
        start: Where the person or load comes in, e.g. the room an entrance leads into.
        floor: Level or floor name to count rooms on.
        step_free: No stairs or steps (wheelchair, trolley, pallet). Lifts are fine.
        min_width_m: Every door passed must be at least this wide.
        closed: Extra lifts, stairs, doors or rooms out of service for this question only.
    """
    b = ctx.deps.building
    origin, f = _space_id(b, start), floor_of(b, floor)
    reached = route_graph(b, _constraints(ctx, step_free, min_width_m, closed or [])).reachable(origin)
    rooms = [s for s in b.spaces_on(f.id) if is_room(s)]
    yes = [s.id for s in rooms if s.id in reached]
    return _json(
        {
            "floor": f.name,
            "reachable_count": len(yes),
            "unreachable_count": len(rooms) - len(yes),
            "reachable": [_label(b, s) for s in yes[:MAX_RESULTS]],
            "unreachable": [_label(b, s.id) for s in rooms if s.id not in reached][:MAX_RESULTS],
            "highlight": yes,
        }
    )


# ---- tools: changes --------------------------------------------------------------------------------------


def with_door(b: Building, first: str, second: str, width_m: float = 0.9) -> Building:
    """A copy of the building with a new door in the middle of the wall two spaces share."""
    where = geometry.shared_wall_midpoint(b, first, second)
    if where is None:
        raise ModelRetry(
            f"{_label(b, first)} and {_label(b, second)} don't share a wall (≥1 m) on one floor."
        )
    door = Opening(
        f"{b.id}-NEW-{first[-4:]}-{second[-4:]}",
        b.spaces[first].floor_id,
        (first, second),
        "door",
        where,
        width_m,
        True,
        True,
    )
    return dataclasses.replace(b, openings={**b.openings, door.id: door})


def what_if(
    ctx: RunContext[Deps],
    floor: str,
    add_door_between: list[str] | None = None,
    close: list[str] | None = None,
    step_free: bool = False,
) -> str:
    """Simulate a change without making it: how each room's walk out of the building (to its nearest
    entrance) changes on a floor if a door is added in the middle of a shared wall, or things close.

    Args:
        floor: Level or floor name whose rooms to compare (may differ from the door's floor).
        add_door_between: Two spaces that share a wall, e.g. ["Corridor 0.01", "Lecture hall 0.21"].
        close: Lifts, stairs, doors or rooms to imagine closed.
        step_free: Compare step-free walks instead.
    """
    b = ctx.deps.building
    f = floor_of(b, floor)
    before_c = _constraints(ctx, step_free, None)
    after_c = _constraints(ctx, step_free, None, close or [])
    after_b = b
    if add_door_between:
        if len(add_door_between) != 2:
            raise ModelRetry("add_door_between needs exactly two spaces.")
        after_b = with_door(b, *(_space_id(b, p) for p in add_door_between))
    before = route_graph(b, before_c).distances_from([OUTSIDE])
    after = route_graph(after_b, after_c).distances_from([OUTSIDE])
    rows = []
    for s in b.spaces_on(f.id):
        if not is_room(s):
            continue
        d0, d1 = before.get(s.id), after.get(s.id)
        rows.append((s.id, d0, d1))
    shorter = [
        (sid, d0 - d1) for sid, d0, d1 in rows if d0 is not None and d1 is not None and d0 - d1 > MIN_GAIN_M
    ]
    longer = [
        (sid, d1 - d0) for sid, d0, d1 in rows if d0 is not None and d1 is not None and d1 - d0 > MIN_GAIN_M
    ]
    cut_off = [sid for sid, d0, d1 in rows if d0 is not None and d1 is None]
    return _json(
        {
            "floor": f.name,
            "rooms_compared": len(rows),
            f"shorter_by_more_than_{MIN_GAIN_M:g}_m": len(shorter),
            f"longer_by_more_than_{MIN_GAIN_M:g}_m": len(longer),
            "no_longer_reachable": len(cut_off),
            "shorter": [
                {"room": _label(b, sid), "saves_m": round(g, 1)}
                for sid, g in sorted(shorter, key=lambda t: -t[1])[:MAX_RESULTS]
            ],
            "longer": [{"room": _label(b, sid), "adds_m": round(g, 1)} for sid, g in longer[:MAX_RESULTS]],
            "cut_off": [_label(b, sid) for sid in cut_off[:MAX_RESULTS]],
            "highlight": [sid for sid, _ in shorter] + cut_off,
        }
    )


def set_closed(ctx: RunContext[Deps], element: str, closed: bool = True) -> str:
    """Record that a lift, staircase, door or room is closed (or open again). All later routes respect it.

    Args:
        element: Name or id, e.g. "Lift 1", "East stairs", a door id, or a room.
        closed: True when it is closed, False when it reopens.
    """
    b = ctx.deps.building
    eid = element_of(b, element)
    (ctx.deps.closed.add if closed else ctx.deps.closed.discard)(eid)
    return _json({"element": eid, "closed": closed, "all_closed": sorted(ctx.deps.closed)})


TOOLS = [
    find,
    room_details,
    list_rooms,
    count_items,
    distance_3d,
    route,
    nearest,
    reachable_rooms,
    what_if,
    set_closed,
]
