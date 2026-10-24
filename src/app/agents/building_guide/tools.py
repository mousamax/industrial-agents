"""Deps and tools for building_guide, on the shared domain interfaces (src/app/building/).

Tools are small building blocks the agent composes: look things up (find, room_details, list_rooms,
count_items), move through the building (route, nearest, reachable_rooms, distance_3d), derive numbers
(calculate), and change the building (what_if simulates; set_closed, add_door, move_item, undo_last_change
edit the session's BuildingState, plan edits only after the person confirms), and follow it over time
(item_history, building_changes: the Level 5 "live building", where colleagues and scanners report what
moved or arrived). Every number comes from code; the model picks tools and arguments.

Places are passed as text, the way people and questions name them ("Office 0.29 (Ground floor)", "0.29",
an id, or an item name such as "CNC mill 1.04"). Resolving that text is its own step: an unknown name is a
ModelRetry with suggestions, an ambiguous one returns the candidates so the agent can ask back. Results
carry space ids (`highlight`, `space_path`) that the UI maps onto GET /buildings/{id}/scene.
"""

from __future__ import annotations

import ast
import dataclasses
import difflib
import json
import math
import operator
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic_ai import ModelRetry, RunContext

from app.building import geometry
from app.building.directions import directions
from app.building.model import OUTSIDE, Building, Floor, Opening, Space, load_building
from app.building.routing import Constraints, Route
from app.building.state import AddAsset, AddDoor, BuildingState, Close, Edit, MoveAsset, Reopen, Stamp

from .graph import route_graph

MAX_RESULTS = 15
MIN_GAIN_M = 1.0


class Deps:
    """One conversation's view of a building: the plan plus everything changed in this session.

    Plain class rather than @dataclass so pydantic_ai's UIAdapter does not mistake `state: BuildingState`
    for a frontend AG-UI StateHandler (`__dataclass_fields__` + `state`) and overwrite it with `{}`.
    """

    def __init__(
        self,
        state: BuildingState,
        pending: dict[str, Any] | None = None,
        here: str | None = None,
        who: str = "",
    ) -> None:
        self.state: BuildingState = state
        """Shared by every colleague in the app (one per building); one per question in the benchmark."""
        self.pending: dict[str, Any] | None = pending
        """An edit proposed to the person and waiting for their yes: {"edit": Edit, "run_id": str}."""
        self.here: str | None = here
        """Space id where the person is, when the app knows it (a QR code at a door, a tap on the map, or the
        `start` a benchmark line carries). The agent gets it as context; it never overrides what they say."""
        self.who: str = who
        """Who this conversation is with (a chat id or name): stamped on the changes they report."""

    @property
    def building(self) -> Building:
        return self.state.building

    @property
    def closed(self) -> frozenset[str]:
        """Closed lifts, stairs, doors or rooms. Every route respects them."""
        return self.state.closed()

    @classmethod
    def load(cls, reference: str | Path, here: str | None = None) -> Deps:
        deps = cls(state=BuildingState(load_building(reference)))
        if here:
            deps.here = _space_id(deps.building, here)  # an id, or a name the person would use
        return deps


# ---- resolving what people say ---------------------------------------------------------------------------


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _key(text: str) -> str:
    return re.sub(r"[\s\-]+", "_", text.strip().casefold())


def floor_of(b: Building, floor: str | int) -> Floor:
    """A floor from its level (2, -1), name ("Floor 2", "the ground floor", "Basement 1") or id. On a
    Level 3 site floor names carry the building ("Fertigung Ost 27 · Ground floor"): a bare level or name
    that fits floors in several buildings is a question back, never a silent pick."""
    if str(floor) in b.floors:
        return b.floors[str(floor)]
    text = re.sub(r"^(on |at )?the ", "", str(floor).strip().casefold())
    level = re.fullmatch(r"(?:floor|level)?\s*(-?\d+)", text)
    hits = [
        f
        for f in b.floors_by_level()
        if f.name.casefold() == text
        or f.name.casefold().endswith(f" · {text}")
        or (level is not None and f.level == int(level.group(1)))
    ]
    exact = [f for f in hits if f.name.casefold() == text]
    if len(exact) == 1 or len(hits) == 1:
        return (exact or hits)[0]
    names = ", ".join(f"{f.level} = {f.name}" for f in (hits or b.floors_by_level()))
    if hits:
        raise ModelRetry(f"{floor!r} fits several floors: {names}. Pass the floor's full name.")
    raise ModelRetry(f"No floor {floor!r}. Floors: {names}.")


def _spaces_named(b: Building, text: str, floor: str | int | None) -> list[Space]:
    floor_id = floor_of(b, floor).id if floor is not None else None
    hits = b.resolve(text, floor_id)
    if not hits and (asset := _asset_named(b, text)):
        hits = [b.spaces[asset.space_id]]
    if not hits and (
        m := re.fullmatch(r"(.+?)\s+(\S+)\s*(?:\(([^()]*)\))?", text.strip())
    ):  # "Büro 1.02 (Floor 1)", "Main lobby 0.18": the labels our tools print, name + ref
        on = m[3] if m[3] is not None else floor_id
        hits = [s for s in b.resolve(m[2], on) if s.name.casefold() == m[1].casefold()]
    if not hits:  # "Room 2.03" when the ref is "2.03", "the Main lobby"
        bare = re.sub(r"^(the|room|space)\s+", "", text.strip(), flags=re.I)
        hits = b.resolve(bare, floor_id) if bare != text.strip() else []
    return hits


def _asset_named(b: Building, text: str):
    needle = text.strip().casefold()
    return next((a for a in b.assets.values() if a.id == text or a.name.casefold() == needle), None)


def _matcher(query: str):
    """A test that every word of `query` starts a word in a text ("press" finds "drill press", not
    "compressor"), plurals too."""
    words = [w for w in re.split(r"[\s_]+", query.casefold()) if w]
    patterns = [
        re.compile(
            r"(?<!\w)(?:"
            + "|".join(re.escape(v) for v in {w, w.removesuffix("s"), w.removesuffix("es")} if v)
            + ")"
        )
        for w in words
    ]

    def hit(*texts: str) -> bool:
        blob = " ".join(texts).casefold().replace("_", " ")
        return all(p.search(blob) for p in patterns)

    return hit


def _item_of(b: Building, text: str, exact_only: bool = False):
    """One asset by exact name or id, else the only one whose name and type fit the words. None if
    nothing fits; ModelRetry listing them if several do."""
    if (asset := _asset_named(b, text)) or exact_only:
        return asset
    hit = _matcher(re.sub(r"^(the|a|an|our|my)\s+", "", text.strip(), flags=re.I))
    hits = [a for a in b.assets.values() if hit(a.name, a.type)]
    if len(hits) > 1:
        options = "; ".join(f"{a.name} (in {_label(b, a.space_id)})" for a in hits[:MAX_RESULTS])
        raise ModelRetry(
            f"{text!r} fits {len(hits)} items: {options}. Pass the exact name; if the person's words "
            "don't settle it, ask them which one."
        )
    return hits[0] if hits else None


# ---- time: the live building ------------------------------------------------------------------------------


def _when(d: Deps, text: str | None) -> datetime:
    """A time the model passes (ISO, local when it has no zone), or now."""
    if not text or text.strip().casefold() == "now":
        return d.state.now()
    try:
        t = datetime.fromisoformat(text.strip())
    except ValueError:
        raise ModelRetry(
            f"Give times as ISO, e.g. {d.state.now():%Y-%m-%dT%H:%M}. It is now {_clock(d.state.now())}."
        ) from None
    return t if t.tzinfo else t.astimezone()


def _clock(at: datetime) -> str:
    return at.astimezone().strftime("%a %d %b %Y %H:%M")


def _ago(at: datetime, now: datetime) -> str:
    if at.year == 1:
        return "before the app was started"
    seconds = (now - at).total_seconds()
    span = abs(seconds)
    if span < 90:
        return "just now"
    text = (
        f"{span / 60:.0f} min"
        if span < 90 * 60
        else f"{span / 3600:.0f} h"
        if span < 48 * 3600
        else f"{span / 86400:.0f} days"
    )
    return f"{text} ago" if seconds > 0 else f"in {text}"


def _stamp(s: Stamp, now: datetime) -> dict:
    return {
        "when": "earlier" if s.at.year == 1 else _clock(s.at),
        "ago": _ago(s.at, now),
        "by": s.by or "this conversation",
        "source": s.source,
    }


def _reported(s: Stamp, now: datetime) -> str:
    when = "" if s.at.year == 1 else f" ({_clock(s.at)})"
    return f"put there {_ago(s.at, now)}{when} by {s.by or 'this conversation'} via {s.source}"


def _last_reports(state: BuildingState) -> dict[str, Stamp]:
    """{asset id: the latest report that holds now} for every asset someone moved or registered."""
    now, out = state.now(), {}
    for i, (e, s) in enumerate(zip(state.edits, state.stamps, strict=True)):
        if s.at <= now and isinstance(e, MoveAsset | AddAsset):
            out[e.asset_id if isinstance(e, MoveAsset) else state.asset_id(i)] = s
    return out


def report_item(
    state: BuildingState,
    item: str,
    place: str,
    *,
    at: datetime | None = None,
    new_item_type: str | None = None,
    by: str = "",
    source: str = "chat",
) -> dict:
    """Record "this is now here" (or "will be here then") for everyone sharing `state`: an item that
    moved, or with `new_item_type` a new one (a delivery). Shared by move_item and the scanner endpoint."""
    now = state.now()
    at = at or now
    b = state.view(at)
    target = _space_id(b, place)
    asset = _item_of(b, item, exact_only=bool(new_item_type))
    if asset is None and not new_item_type:
        close = difflib.get_close_matches(item, [a.name for a in b.assets.values()], n=5, cutoff=0.5)
        raise ModelRetry(
            f"No item called {item!r} in the building (closest: {', '.join(close) or 'none'}). If it is new "
            'here (a delivery, a new machine), pass new_item_type, e.g. "pallet"; else use find.'
        )
    edit = MoveAsset(asset.id, target) if asset else AddAsset(item.strip(), _key(new_item_type or ""), target)
    try:
        message = state.apply(edit, at=at, by=by, source=source)
    except ValueError as e:
        raise ModelRetry(str(e)) from None
    return {
        "item": asset.name if asset else item.strip(),
        "type": asset.type if asset else _key(new_item_type or ""),
        "from": _label(b, asset.space_id) if asset else None,
        "to": _label(b, target),
        "when": _clock(at),
        "status": "expected" if at > now else "done",
        "change": message,
        "reported_by": by or "this conversation",
        "seen_by": "everyone using this building (chats, map, scanners)",
        "highlight": [asset.space_id, target] if asset else [target],
    }


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
    if text in b.connectors or text in b.openings or text in b.spaces or text in b.walkways:
        return text
    needle = text.strip().casefold()
    walkways = [
        w.id for w in b.walkways.values() if needle in (w.name.casefold(), f"the {w.name.casefold()}")
    ]
    if len(walkways) == 1:
        return walkways[0]
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


Scope = Literal["rooms", "rooms_and_halls", "all_spaces"]
SCOPES = {
    "rooms": is_room,
    "rooms_and_halls": lambda s: s.type in ("room", "hall") and s.room_type not in geometry.NOT_ROOMS,
    "all_spaces": lambda s: True,
}


def _kinds(b: Building, floor_id: str) -> dict[str, int]:
    """How a floor's spaces split by kind, so the agent sees what a scope leaves out."""
    return dict(Counter(s.type for s in b.spaces_on(floor_id)).most_common())


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
    } | (
        {
            "site_buildings": b.info.get("buildings", []),
            "walkways": [
                {
                    "name": w.name,
                    "id": w.id,
                    "length_m": w.length_m,
                    "step_free": w.step_free,
                    "width_m": w.width_m,
                    "joins": [_label(b, b.openings[o].other_side(OUTSIDE)) for o in w.opening_ids],
                }
                for w in b.walkways.values()
            ],
        }
        if b.walkways
        else {}
    )


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
    hit = _matcher(query)
    reports, now = _last_reports(ctx.deps.state), ctx.deps.state.now()

    rooms = [
        {"room": _label(b, s.id), "id": s.id, "type": s.room_type}
        for s in b.spaces.values()
        if (floor_id is None or s.floor_id == floor_id) and hit(s.name, s.ref, s.room_type)
    ]
    items = [
        {"item": a.name, "id": a.id, "type": a.type, "in": _label(b, a.space_id), "space_id": a.space_id}
        | ({"reported": _reported(reports[a.id], now)} if a.id in reports else {})
        for a in b.assets.values()
        if (floor_id is None or a.floor_id == floor_id) and hit(a.name, a.type)
    ]
    if not rooms and not items:
        vocabulary = sorted({a.type for a in b.assets.values()} | {s.room_type for s in b.spaces.values()})
        return _json(
            {
                "found": 0,
                "note": f"Nothing in this building is named or typed {query!r}.",
                "similar_types": difflib.get_close_matches(_key(query), vocabulary, n=5, cutoff=0.6),
            }
        )
    return _json(
        {
            "rooms": rooms[:MAX_RESULTS],
            "rooms_total": len(rooms),
            "items": items[:MAX_RESULTS],
            "items_total": len(items),
            "items_by_type": dict(Counter(i["type"] for i in items).most_common()),
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
            "shares_a_wall_with": {
                "count": len(walls),
                "rooms": [_label(b, r) for r in walls],
                "counts": "rooms and halls (zones, hall access) whose outline runs >=1 m along this one; "
                "corridors, stairwells and lifts already left out: use this count as it is",
            },
            "directly_above": above and _label(b, above),
            "directly_below": below and _label(b, below),
            "directly_above_id": above,
            "directly_below_id": below,
            "highlight": [s.id],
        }
    )


def list_rooms(
    ctx: RunContext[Deps], floor: str, room_type: str | None = None, scope: Scope = "rooms"
) -> str:
    """Spaces on a floor, largest first, with areas, how many there are and their total area.

    Args:
        floor: Level ("1", "0", "-1") or name ("Floor 1", "Ground floor").
        room_type: Only this type, e.g. "office", "kitchen", "generic_room" (see room_types in the
            overview). With a room_type every space of that type counts, whatever the scope.
        scope: Without a room_type, which spaces count: "rooms" (rooms only), "rooms_and_halls" (also halls
            such as factory zones), or "all_spaces" (also corridors, stairwells, lifts, balconies).
    """
    b = ctx.deps.building
    f = floor_of(b, floor)
    if room_type:
        rtype = _type_of(room_type, {s.room_type for s in b.spaces.values()}, "room type")
        ranked = geometry.rooms_by_area(b, f.id, rtype)
    else:
        keep = SCOPES[scope]
        ranked = sorted(((s, s.area) for s in b.spaces_on(f.id) if keep(s)), key=lambda t: -t[1])
    return _json(
        {
            "floor": f.name,
            "room_type": room_type,
            "scope": None if room_type else scope,
            "floor_spaces_by_kind": _kinds(b, f.id),
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
    """A skeleton of the walk for the agent to turn into directions people can follow: one line per
    space entered, saying how (which door, stairs or lift). Each step between two consecutive spaces of
    `space_path` uses the next element of `element_path`."""
    path, elements = route.space_path, route.element_path
    if not path:
        return []
    steps = [f"start in {_label(b, path[0])}"]
    for here, element in zip(path[1:], elements, strict=False):
        if here == OUTSIDE:
            steps.append(
                "leave the building through the entrance" if element in b.openings else "leave the building"
            )
        elif element in b.walkways:
            w = b.walkways[element]
            steps.append(
                f"leave the building and follow the {w.name} ({w.length_m:g} m outside) into {_label(b, here)}"
            )
        elif element in b.connectors:
            k = b.connectors[element]
            steps.append(f"take {k.name} ({k.kind}) to {_label(b, here)}")
        elif element in b.openings:
            o = b.openings[element]
            steps.append(f"through the {o.kind} ({o.width_m:g} m wide) into {_label(b, here)}")
        else:
            steps.append(f"into {_label(b, here)}")
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


def walk_directions(
    ctx: RunContext[Deps],
    start: str,
    destination: str,
    via: list[str] | None = None,
    step_free: bool = False,
    min_width_m: float | None = None,
) -> str:
    """Turn-by-turn walking directions a person can follow without looking at a screen: where to turn
    left or right, what they walk past, and what stands in the way ("go around the printer, keeping it on
    your left"). Use it when the person wants to be guided ("how exactly", "keep it short", on a headset)
    after or instead of route. Same arguments as route; closures they reported are avoided.

    Args:
        start: Where the person is: room name/number, id, item name, or "outside".
        destination: Where they go (room, item name, "outside").
        via: Stops to visit in order on the way.
        step_free: No stairs or steps. Lifts are fine.
        min_width_m: Every door passed must be at least this wide.
    """
    b = ctx.deps.building
    stops = [_space_id(b, p) for p in [start, *(via or []), destination]]
    tour = route_graph(b, _constraints(ctx, step_free, min_width_m)).tour(stops)
    if tour is None:
        return _json({"reachable": False, "stops": [_label(b, s) for s in stops]})
    try:
        legs = [[dataclasses.asdict(step) for step in directions(b, leg)] for leg in tour.legs]
    except NotImplementedError:  # scaffold: app.building.directions isn't built yet
        return _json(
            {
                "available": False,
                "note": "Turn-by-turn directions aren't available yet. Use route and describe only the "
                "spaces and doors it lists; never invent turns or compass directions.",
            }
        )
    return _json(
        {
            "reachable": True,
            "total_length_m": round(tour.length_m, 2),
            "legs": legs,
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
    scope: Scope = "rooms",
) -> str:
    """Rooms on a floor that can be reached from a start (deliveries, wheelchair access), and how many.

    Args:
        start: Where the person or load comes in, e.g. the room an entrance leads into.
        floor: Level or floor name to count rooms on.
        step_free: No stairs or steps (wheelchair, trolley, pallet). Lifts are fine.
        min_width_m: Every door passed must be at least this wide.
        closed: Extra lifts, stairs, doors or rooms out of service for this question only.
        scope: Which spaces count: "rooms" (rooms only, what a count of rooms means), "rooms_and_halls"
            (also halls such as factory zones), or "all_spaces".
    """
    b = ctx.deps.building
    origin, f = _space_id(b, start), floor_of(b, floor)
    reached = route_graph(b, _constraints(ctx, step_free, min_width_m, closed or [])).reachable(origin)
    rooms = [s for s in b.spaces_on(f.id) if SCOPES[scope](s)]
    yes = [s.id for s in rooms if s.id in reached]
    return _json(
        {
            "floor": f.name,
            "scope": scope,
            "floor_spaces_by_kind": _kinds(b, f.id),
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


def _exit_changes(
    before_b: Building,
    after_b: Building,
    before_c: Constraints,
    after_c: Constraints,
    floor_id: str,
    scope: Scope = "rooms",
) -> dict:
    """How each space's walk out of the building (to its nearest entrance) changes between two states."""
    before = route_graph(before_b, before_c).distances_from([OUTSIDE])
    after = route_graph(after_b, after_c).distances_from([OUTSIDE])
    rows = [
        (s.id, before.get(s.id), after.get(s.id)) for s in before_b.spaces_on(floor_id) if SCOPES[scope](s)
    ]
    both = [(sid, d0, d1) for sid, d0, d1 in rows if d0 is not None and d1 is not None]
    shorter = sorted(((sid, d0 - d1) for sid, d0, d1 in both if d0 - d1 > MIN_GAIN_M), key=lambda t: -t[1])
    longer = sorted(((sid, d1 - d0) for sid, d0, d1 in both if d1 - d0 > MIN_GAIN_M), key=lambda t: -t[1])
    cut_off = [sid for sid, d0, d1 in rows if d0 is not None and d1 is None]
    b = before_b
    return {
        "floor": b.floors[floor_id].name,
        "scope": scope,
        "rooms_compared": len(rows),
        f"shorter_by_more_than_{MIN_GAIN_M:g}_m": len(shorter),
        f"longer_by_more_than_{MIN_GAIN_M:g}_m": len(longer),
        "no_longer_reachable": len(cut_off),
        "shorter": [{"room": _label(b, sid), "saves_m": round(g, 1)} for sid, g in shorter[:MAX_RESULTS]],
        "longer": [{"room": _label(b, sid), "adds_m": round(g, 1)} for sid, g in longer[:MAX_RESULTS]],
        "cut_off": [_label(b, sid) for sid in cut_off[:MAX_RESULTS]],
        "highlight": [sid for sid, _ in shorter] + cut_off,
    }


def what_if(
    ctx: RunContext[Deps],
    floor: str,
    add_door_between: list[str] | None = None,
    close: list[str] | None = None,
    step_free: bool = False,
    scope: Scope = "rooms",
) -> str:
    """Simulate a change without making it: how each room's walk out of the building (to its nearest
    entrance) changes on a floor if a door is added in the middle of a shared wall, or things close.

    Args:
        floor: Level or floor name whose rooms to compare (may differ from the door's floor).
        add_door_between: Two spaces that share a wall, e.g. ["Corridor 0.01", "Lecture hall 0.21"].
        close: Lifts, stairs, doors or rooms to imagine closed.
        step_free: Compare step-free walks instead.
        scope: Which spaces to compare: "rooms" (default), "rooms_and_halls" or "all_spaces".
    """
    b = ctx.deps.building
    f = floor_of(b, floor)
    after_b = b
    if add_door_between:
        if len(add_door_between) != 2:
            raise ModelRetry("add_door_between needs exactly two spaces.")
        after_b = with_door(b, *(_space_id(b, p) for p in add_door_between))
    before_c = _constraints(ctx, step_free, None)
    after_c = _constraints(ctx, step_free, None, close or [])
    return _json(_exit_changes(b, after_b, before_c, after_c, f.id, scope))


# ---- tools: arithmetic -----------------------------------------------------------------------------------

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}
_FUNCS = {"abs": abs, "round": round, "min": min, "max": max, "sum": lambda *a: sum(a), "sqrt": math.sqrt}


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS:
        return _FUNCS[node.func.id](*(_eval(a) for a in node.args))
    raise ValueError(f"not allowed: {ast.dump(node)[:60]}")


def calculate(ctx: RunContext[Deps], expression: str) -> str:
    """Arithmetic on numbers from earlier tool results: differences, sums, ratios, percentages, unit
    changes. Use it for every number you derive yourself, so the answer stays exact and checkable.

    Args:
        expression: e.g. "41.37 - 37.57", "(12.5 + 30.1) / 2", "round(3145.0 / 12, 1)", "max(3, 7)".
    """
    try:
        result = _eval(ast.parse(expression, mode="eval"))
    except (ValueError, SyntaxError, TypeError, ZeroDivisionError) as e:
        raise ModelRetry(
            f"Can't calculate {expression!r}: {e}. Use numbers, + - * / ** % ( ) and "
            "abs round min max sum sqrt."
        ) from None
    return _json({"expression": expression, "result": round(result, 4)})


# ---- tools: changes to the building (session state) ----------------------------------------------------


def _propose_or_apply(ctx: RunContext[Deps], edit: Edit, preview: dict, confirmed: bool) -> str:
    """Real edits happen in two turns: propose (nothing changes) -> the person says yes -> apply."""
    d = ctx.deps
    if not confirmed:
        d.pending = {"edit": edit, "run_id": ctx.run_id}
        return _json(
            preview
            | {
                "status": "proposed: nothing changed yet",
                "next": "Say what you understood and what it changes, then ask the person to confirm. Only after "
                "they say yes, call this tool again with the same arguments and confirmed=true.",
            }
        )
    if not d.pending or d.pending["edit"] != edit:
        raise ModelRetry("Propose this change first (confirmed=false) and get the person's yes.")
    if d.pending["run_id"] == ctx.run_id:
        raise ModelRetry("The person hasn't answered yet: ask them, and apply it after they say yes.")
    try:
        message = d.state.apply(edit, by=d.who)
    except ValueError as e:
        raise ModelRetry(str(e)) from None
    d.pending = None
    return _json(preview | {"status": "done", "change": message, "undo": "undo_last_change reverts it"})


def set_closed(ctx: RunContext[Deps], element: str, closed: bool = True) -> str:
    """Record that a lift, staircase, door or room is closed (or open again), as the person reports it.
    All later routes respect it. (To ask "what if X closed?" without recording it, use what_if.)

    Args:
        element: Name or id, e.g. "Lift 1", "East stairs", a door id, or a room.
        closed: True when it is closed, False when it reopens.
    """
    d = ctx.deps
    eid = element_of(d.building, element)
    try:
        message = d.state.apply(Close(eid) if closed else Reopen(eid), by=d.who)
    except ValueError as e:
        message = str(e)  # already closed / not closed: nothing to do, tell the agent
    return _json({"element": eid, "closed": closed, "change": message, "all_closed": sorted(d.closed)})


def add_door(
    ctx: RunContext[Deps], first: str, second: str, width_m: float = 0.9, confirmed: bool = False
) -> str:
    """Add a real door to the plan, in the middle of the wall two spaces share. First call proposes it and
    shows what it would change (nothing is edited); after the person says yes, call again with confirmed=true.

    Args:
        first: A room, hall or corridor.
        second: A space that shares a wall with `first`, on the same floor.
        width_m: Door width in metres.
        confirmed: True only after the person agreed to this exact change.
    """
    d = ctx.deps
    b = d.building
    a, c = _space_id(b, first), _space_id(b, second)
    after = with_door(b, a, c, width_m)  # ModelRetry if they don't share a wall
    effect = _exit_changes(
        b, after, _constraints(ctx, False, None), _constraints(ctx, False, None), b.spaces[a].floor_id
    )
    preview = {
        "door_between": [_label(b, a), _label(b, c)],
        "width_m": width_m,
        "effect_on_walks_out_on_that_floor": effect,
        "highlight": [a, c],
    }
    return _propose_or_apply(ctx, AddDoor(a, c, width_m=width_m), preview, confirmed)


def move_item(
    ctx: RunContext[Deps],
    item: str,
    to_room: str,
    when: str | None = None,
    new_item_type: str | None = None,
) -> str:
    """Record "this is now here", as the person reports it: a machine or tool that moved, or a delivery
    that arrived. Recorded at once (no confirmation needed) for everyone using this building, so their
    searches, routes and the map find it there; undo_last_change takes it back.

    Args:
        item: The item's exact name or id from find, e.g. "CNC mill H-A1-B". For something new to the
            building, the name people will search for, e.g. "Pallet of aluminium sheets".
        to_room: The room, hall or zone where it is now.
        when: ISO time it got there (or will), e.g. "2026-10-27T08:00"; omit for now. A future time records
            an expected delivery: it is listed by building_changes but not found until then.
        new_item_type: Only for an item the building doesn't have yet (a delivery, a new machine): its type,
            e.g. "pallet", "crate", "3d_printer".
    """
    d = ctx.deps
    return _json(
        report_item(d.state, item, to_room, at=_when(d, when), new_item_type=new_item_type, by=d.who)
    )


def item_history(ctx: RunContext[Deps], item: str) -> str:
    """Where an item is now, where it was before (the plan, then every reported move, with when and by
    whom) and any expected arrival. For "where was it yesterday?", "who moved it?", "when did it come?".

    Args:
        item: The item's name or id, e.g. "Drill press H-A1" or "Pallet of aluminium sheets".
    """
    d, now = ctx.deps, ctx.deps.state.now()
    everything = d.state.view(datetime.max.replace(tzinfo=UTC))  # includes items still expected
    asset = _item_of(d.building, item) or _item_of(everything, item)
    if asset is None:
        raise ModelRetry(f"No item called {item!r}, now or expected. Use find or building_changes.")
    history = [
        {"in": _label(d.building, space_id)}
        | ({"when": "in the building plan"} if stamp is None else _stamp(stamp, now))
        | ({"status": "expected"} if stamp and stamp.at > now else {})
        for stamp, space_id in d.state.timeline(asset.id)
    ]
    current = d.building.assets.get(asset.id)
    return _json(
        {
            "item": asset.name,
            "type": asset.type,
            "now_in": _label(d.building, current.space_id) if current else "not here yet (expected)",
            "history": history,
            "highlight": [current.space_id] if current else [],
        }
    )


KINDS = {Close: "closed", Reopen: "reopened", AddDoor: "door added", MoveAsset: "moved", AddAsset: "arrived"}


def building_changes(ctx: RunContext[Deps], since: str | None = None, until: str | None = None) -> str:
    """What colleagues, scanners and this conversation recorded in a time window, oldest first: deliveries
    that arrived, items that moved, closures, new doors, each with when, who and where items are now. For
    "what was delivered yesterday?", "what changed since this morning?", "anything arriving this week?"
    (entries after now are expected, not there yet).

    Args:
        since: ISO start, e.g. "2026-10-23T00:00"; omit for everything recorded.
        until: ISO end (not included), e.g. "2026-10-24T00:00"; omit for no end, expected ones included.
    """
    d = ctx.deps
    state, now, b = d.state, d.state.now(), d.building
    lo = _when(d, since) if since else None
    hi = _when(d, until) if until else None
    entries = []
    for i, edit, stamp in state.changes(lo, hi):
        entry = {"kind": KINDS[type(edit)], "change": stamp.said} | _stamp(stamp, now)
        entry["status"] = "expected" if stamp.at > now else "done"
        if isinstance(edit, MoveAsset | AddAsset):
            asset_id = edit.asset_id if isinstance(edit, MoveAsset) else state.asset_id(i)
            current = b.assets.get(asset_id)
            name = current.name if current else getattr(edit, "name", asset_id)
            entry |= {"item": name, "to": _label(b, edit.space_id)}
            entry["now_in"] = _label(b, current.space_id) if current else "not here yet"
        entries.append(entry)
    return _json(
        {
            "now": _clock(now),
            "window": [_clock(lo) if lo else "start", _clock(hi) if hi else "open end"],
            "total": len(entries),
            "changes": entries[-30:],
            "note": "Only the newest 30 are listed." if len(entries) > 30 else "",
        }
    )


def undo_last_change(ctx: RunContext[Deps]) -> str:
    """Undo the most recent change made in this conversation (a closure, reopening, new door, moved or
    delivered item). Colleagues' changes stay."""
    d = ctx.deps
    edit = d.state.undo(by=d.who) if d.who else d.state.undo()
    return _json(
        {
            "undone": repr(edit) if edit else None,
            "all_closed": sorted(d.closed),
            "changes_left": len(d.state.edits),
        }
    )


def load_floor_plans(ctx: RunContext[Deps], plans: list[str], name: str, confirmed: bool = False) -> str:
    """Set up a new building from its floor plans (Level 4: "here are the plans, learn the building").
    First call drafts it and shows what was understood (floors, rooms per floor, doors, stairs and lifts,
    and every guess to check) without switching; after the person says yes, call again with
    confirmed=true and every later question is about the new building.

    Args:
        plans: Paths to the floor plans, one per floor: floor_<level>.svg or .png.
        name: What people call the building.
        confirmed: True only after the person agreed to switch to this draft.
    """
    from app.building.build import draft_from_plans, validate
    from app.building.model import parse_building

    d = ctx.deps
    key = ("load_floor_plans", tuple(str(Path(p).expanduser().resolve()) for p in plans), name)
    if confirmed:
        pending = d.pending or {}
        if pending.get("edit") != key:
            raise ModelRetry("Draft these plans first (confirmed=false), show the person, and get their yes.")
        if pending["run_id"] == ctx.run_id:
            raise ModelRetry("The person hasn't answered yet: ask them, and switch after they say yes.")
        b = pending["building"]
        d.state, d.pending, d.here = BuildingState(b), None, None
        return _json(pending["summary"] | {"status": "done: every question is now about " + b.name})

    if not plans:
        raise ModelRetry("Pass the floor plan files, one per floor: floor_<level>.svg or .png.")
    paths = [Path(p).expanduser() for p in plans]
    missing = [str(p) for p in paths if not p.is_file()]
    if missing:
        raise ModelRetry(
            f"No such file: {', '.join(missing)}. Ask the person for the paths of the floor plans."
        )
    try:
        raw = draft_from_plans(paths, name=name)
        b = parse_building(raw)
    except Exception as e:  # unreadable or unsupported plan: let the agent ask for better input
        raise ModelRetry(
            f"Couldn't read these plans ({type(e).__name__}: {e}). Plans must be one file per floor, "
            "named floor_<level>.svg or floor_<level>.png, drawn like the kit's plans."
        ) from None
    issues = validate(raw)
    guesses = Counter((g["field"], g["source"]) for g in raw.get("guesses", []))
    unsure = sorted(
        (g for g in raw.get("guesses", []) if g.get("confidence") is not None and g["confidence"] < 0.6),
        key=lambda g: g["confidence"],
    )
    summary = {
        "building": b.name,
        "id": b.id,
        "floors": [
            {
                "level": f.level,
                "name": f.name,
                "spaces": _kinds(b, f.id),
                "doors": sum(1 for o in b.openings.values() if o.floor_id == f.id),
            }
            for f in b.floors_by_level()
        ],
        "entrances": len(b.openings_of(OUTSIDE)),
        "stairs_and_lifts": [
            {"kind": k.kind, "floors": [b.floors[f].level for f, _ in k.stops], "guessed": k.synthetic}
            for k in b.connectors.values()
        ],
        "items": len(b.assets),
    }
    preview = (
        summary
        | {
            "guesses": [{"field": f, "source": s, "count": n} for (f, s), n in guesses.most_common()],
            "least_sure": [
                {
                    "what": _label(b, g["element_id"]) if g["element_id"] in b.spaces else g["element_id"],
                    "field": g["field"],
                    "guess": g["value"],
                    "confidence": round(g["confidence"], 2),
                }
                for g in unsure[:5]
            ],  # fmt: skip
            "issues": [f"{i.severity}: {i.message}" for i in issues[:8]]
            + ([f"... {len(issues) - 8} more"] if len(issues) > 8 else []),
        }
    )
    d.pending = {"edit": key, "run_id": ctx.run_id, "building": b, "summary": summary}
    return _json(
        preview
        | {
            "status": "proposed: still on " + d.building.name + ", nothing switched yet",
            "next": "Say briefly what you understood (floors, rooms, stairs/lifts) and which guesses to check, "
            "then ask the person to confirm. Only after they say yes, call again with the same arguments and "
            "confirmed=true.",
        }
    )


TOOLS = [
    find,
    room_details,
    list_rooms,
    count_items,
    distance_3d,
    route,
    walk_directions,
    nearest,
    reachable_rooms,
    what_if,
    calculate,
    set_closed,
    add_door,
    move_item,
    item_history,
    building_changes,
    undo_last_change,
    load_floor_plans,
]
