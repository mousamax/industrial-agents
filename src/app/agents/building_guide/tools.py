"""Deps and tools for building_guide, on the shared domain interfaces (src/app/building/).

Tools are small building blocks the agent composes: look things up (find, room_details, list_rooms,
count_items), move through the building (route, nearest, reachable_rooms, walk_distances, distance_3d), derive numbers
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
import functools
import itertools
import json
import math
import operator
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic_ai import ApprovalRequired, CallDeferred, ModelRetry, RunContext
from pydantic_ai.tools import ToolDefinition
from shapely.ops import unary_union

from app.building import geometry, queries
from app.building.directions import directions, walk_path
from app.building.model import OUTSIDE, Building, Floor, Opening, Space, load_building
from app.building.routing import PRODUCTION_BAYS, Constraints, Route, RouteGraph
from app.building.state import (
    AddAsset,
    AddDoor,
    BuildingState,
    Close,
    Edit,
    MoveAsset,
    RemoveDoor,
    Reopen,
    SplitRoom,
    Stamp,
    split_between,
    split_room,
    with_split,
)

from .graph import route_graph

MAX_RESULTS = 15
MIN_GAIN_M = 1.0


Detail = Literal["rooms", "walk"]


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
        detail: Detail = "rooms",
        confirm_edits: bool = False,
        outside_walking: bool = False,
    ) -> None:
        self.state: BuildingState = state
        """Shared by every colleague in the app (one per building); one per question in the benchmark."""
        self.pending: dict[str, Any] | None = pending
        """Edits proposed to the person in one turn, waiting for one yes for all: {"edits": [Edit], "run_id": str}."""
        self.here: str | None = here
        """Space id where the person is, when the app knows it (a QR code at a door, a tap on the map, or the
        `start` a benchmark line carries). The agent gets it as context; it never overrides what they say."""
        self.who: str = who
        """Who this conversation is with (a chat id or name): stamped on the changes they report."""
        self.detail: Detail = detail
        """How fine directions may be. "rooms" (the default: benchmarks, the Sunday run, evals, anything
        that doesn't say) gives routes room by room, with no walk_directions tool and no in-room `path`.
        "walk" (set by the interactive channels: the app chat, voice, `ia chat`, tricky-user scenarios)
        adds walk_directions and the drawn walk round obstacles."""
        self.confirm_edits: bool = confirm_edits
        """Whether plan edits (add_door, add_wall) wait for a yes. False (the default: the text chat, benchmarks,
        the Sunday run, evals) applies them at once, undo_last_change takes them back. True only for the
        voice call (the app sends `confirmEdits`): the first call proposes, the person's yes applies."""
        self.outside_walking: bool = outside_walking
        """Whether routes may go out one entrance, round the building and in through another when that's
        shorter (Constraints.outside_walking). False (the default: benchmarks, the Sunday run, evals: the scored
        route model has no way round the outside). True only for the app's chat and voice sessions."""
        self.turn_context: tuple[str, str] | None = None
        """(run id, the clock and recorded changes as that turn started): agent._live_context."""

    @property
    def building(self) -> Building:
        return self.state.building

    @property
    def closed(self) -> frozenset[str]:
        """Closed lifts, stairs, doors or rooms. Every route respects them."""
        return self.state.closed()

    @classmethod
    def load(cls, reference: str | Path, here: str | None = None, detail: Detail = "rooms") -> Deps:
        deps = cls(state=BuildingState(load_building(reference)), detail=detail)
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


def _number_forms(text: str) -> list[str]:
    """How a room number said with one decimal is written on plans: "1.3" -> ["1.03", "1.30"]."""
    m = re.fullmatch(
        r"(?:(?:the\s+)?(?:room|space|no\.?|number)\s+)?([A-Za-z]*-?\d+)\.(\d)", text.strip(), re.I
    )
    return [f"{m[1]}.0{m[2]}", f"{m[1]}.{m[2]}0"] if m else []


def _missing_room(asset) -> ModelRetry:
    return ModelRetry(
        f"{asset.name} ({asset.type}) is recorded in room {asset.space_id}, but that room is not on the floor "
        "plan (no such space, no door to it), so there is no route to it and no area. Tell the person the "
        "building data is inconsistent: the item's room is missing from the plan."
    )


def _spaces_named(b: Building, text: str, floor: str | int | None) -> list[Space]:
    floor_id = floor_of(b, floor).id if floor is not None else None
    hits = b.resolve(text, floor_id)
    if not hits and (asset := _asset_named(b, text)):
        if asset.space_id not in b.spaces:
            raise _missing_room(asset)
        hits = [b.spaces[asset.space_id]]
    if not hits and (
        m := re.fullmatch(r"(.+?)\s+(\S+)\s*(?:\(([^()]*)\))?", text.strip())
    ):  # "Büro 1.02 (Floor 1)", "Main lobby 0.18": the labels our tools print, name + ref
        on = m[3] if m[3] is not None else floor_id
        hits = [s for s in b.resolve(m[2], on) if s.name.casefold() == m[1].casefold()]
    if not hits:  # "Room 2.03" when the ref is "2.03", "the Main lobby"
        bare = re.sub(r"^(the|room|space)\s+", "", text.strip(), flags=re.I)
        hits = b.resolve(bare, floor_id) if bare != text.strip() else []
    if not hits:  # "room 1.3": plans write 1.03 or 1.30; both existing means the person must say which
        hits = [s for form in _number_forms(text) for s in b.resolve(form, floor_id)]
    if not hits:  # "2.03" or "Office 2.03" after a wall split it: both halves (2.03-1, 2.03-2), so ask which
        said = text.strip().casefold()
        hits = [
            s
            for s in b.spaces.values()
            if (floor_id is None or s.floor_id == floor_id) and said in _before_split(s)
        ]
    return hits


def _before_split(s: Space) -> set[str]:
    """What people may still call a split half: "2.03", "Office 2.03" (both halves match) and "Office 2.03
    (west half)" for "Office 2.03-1 (west half)". Empty for rooms that were never split."""
    m = re.fullmatch(r"(.+)-\d", s.ref)
    if not m or not s.name.endswith(" half)"):
        return set()
    old = s.name.replace(s.ref, m[1])  # "Office 2.03 (west half)" names just this half
    return {m[1].casefold(), re.sub(r"\s*\([^()]*half\)$", "", old).casefold(), old.casefold()}


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


OUTSIDE_WORDS = re.compile(
    r"^(?:the\s+)?(?:outside|outdoors|exterior|street|(?:any|an|every|all|the\s+building'?s?)\s+entrances?|entrances)\b"
)
"""What people call the outside of the building: a route from there starts at the nearest open entrance."""


def space_of(b: Building, text: str, floor: str | int | None = None) -> Space | str:
    """One space, or OUTSIDE. Raises ModelRetry with suggestions if nothing matches or several do."""
    said = text.strip().casefold()
    if said in ("outside", OUTSIDE):
        return OUTSIDE
    hits = _one_per_room(_spaces_named(b, text, floor))
    if not hits and OUTSIDE_WORDS.match(said):  # "outside (any step-free entrance)", "the street"
        return OUTSIDE
    if len(hits) == 1:
        return hits[0]
    if hits:
        options = "; ".join(f"{_label(b, s.id)} [{s.id}]" for s in hits[:MAX_RESULTS])
        raise ModelRetry(
            f"Ambiguous: {text!r} matches {len(hits)} spaces: {options}. Pass the id or add the floor; if the "
            "person's words don't settle it, don't pick one: ask which, or give the result for each."
        )
    labels = [b.label(s.id) for s in b.spaces.values()]
    close = difflib.get_close_matches(text, labels, n=5, cutoff=0.5)
    hint = f" Did you mean: {'; '.join(close)}?" if close else " Use find to search."
    raise ModelRetry(
        f"No room or item called {text!r}.{hint} For the outside of the building (from or to any entrance) "
        'pass "outside".'
    )


def _space_id(b: Building, text: str, floor: str | int | None = None) -> str:
    s = space_of(b, text, floor)
    return s if isinstance(s, str) else s.id


SAME_ROOM = 0.5
"""Two same-named spaces on one floor are one room drawn twice (an OSM duplicate) when they overlap by more
than this share of the smaller outline. Different rooms sharing a name don't overlap at all."""


def _one_per_room(hits: list[Space]) -> list[Space]:
    """The matches with each room drawn several times kept once (its lowest id): the copies are the same
    place (a route may differ by copy: each has its own doors, and the kit picks one at random)."""
    kept: list[Space] = []
    for s in sorted(hits, key=lambda s: s.id):
        if not any(k.floor_id == s.floor_id and _overlap_share(k, s) > SAME_ROOM for k in kept):
            kept.append(s)
    return kept if len(kept) < len(hits) else hits


def _overlap_share(a: Space, c: Space) -> float:
    smaller = min(a.area, c.area)
    if smaller <= 0:
        return 0.0
    try:
        return a.shape.intersection(c.shape).area / smaller
    except Exception:  # an invalid OSM outline: not a duplicate we can prove
        return 0.0


def _scored(ctx: RunContext[Deps]) -> bool:
    """A one-shot benchmark question (it asks for an 'Answer:' line): there is no one to ask which."""
    prompt = getattr(ctx, "prompt", None)
    return isinstance(prompt, str) and "Answer:" in prompt


MAX_COMBINATIONS = 30


def _stops_committed(
    ctx: RunContext[Deps], places: list[str], constraints: Constraints
) -> tuple[list[str], dict | None]:
    """Route stops as space ids. With a person, a name several different rooms share raises 'Ambiguous' (they
    say which). In a one-shot scored question it can't be asked: the kit's questions pick one of the
    same-named rooms at random, so every option is routed and the one whose length is closest to all the
    others is used (the least wrong whichever room was meant); `shared_name` lists them all."""
    b = ctx.deps.building
    try:
        return [_space_id(b, p) for p in places], None
    except ModelRetry:
        if not _scored(ctx):
            raise
    options: list[list[str]] = []
    for p in places:
        try:
            options.append([_space_id(b, p)])
        except ModelRetry as e:
            hits = _one_per_room(_spaces_named(b, p, None))
            if len(hits) < 2:
                raise e
            options.append([s.id for s in hits])
    combos = list(itertools.islice(itertools.product(*options), MAX_COMBINATIONS))
    graph = route_graph(b, constraints)
    lengths = {c: t.length_m for c in combos if (t := graph.tour(list(c))) is not None}
    if not lengths:
        return list(combos[0]), None
    pick = min(lengths, key=lambda c: (sum(abs(lengths[c] - v) for v in lengths.values()), c))
    shared = [p for p, o in zip(places, options, strict=True) if len(o) > 1]
    return list(pick), {
        "names": shared,
        "used": [_label(b, s) + f" [{s}]" for s, o in zip(pick, options, strict=True) if len(o) > 1],
        "why": "Several different rooms carry this name. This one-shot answer uses the option whose length is "
        "closest to all the others; commit to total_length_m as the single number. Mention the others above "
        "the Answer line, not in it.",
        "options": [
            {
                "rooms": [s for s, o in zip(c, options, strict=True) if len(o) > 1],
                "total_length_m": round(v, 2),
            }
            for c, v in sorted(lengths.items(), key=lambda kv: kv[1])
        ],
    }


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
    if space_id not in b.spaces:  # an item recorded in a room the plan doesn't have
        return f"{space_id} (not on the floor plan)"
    s = b.spaces[space_id]
    if s.ref and s.ref.casefold() not in s.name.casefold():
        return f"{s.name} {s.ref} ({b.floors[s.floor_id].name})"
    return b.label(space_id)


def _constraints(ctx: RunContext[Deps], step_free: bool, min_width_m: float | None, closed=()) -> Constraints:
    ids = {element_of(ctx.deps.building, c) for c in closed} | ctx.deps.closed
    return Constraints(step_free, min_width_m, frozenset(ids), outside_walking=ctx.deps.outside_walking)


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

    on_floor = [s for s in b.spaces.values() if floor_id is None or s.floor_id == floor_id]
    said = query.strip().casefold()
    notes = []
    named = [s for s in on_floor if said in (s.name.casefold(), (s.ref or "").casefold())]
    forms = [] if named else _number_forms(query)
    variants = [s for s in on_floor if (s.ref or "").casefold() in forms]
    if variants:  # "1.3" is no room number here: plans write it 1.03 or 1.30
        named = variants
        notes.append(
            f"No room is numbered {query!r}; it could be written {' or '.join(forms)}: these are the "
            "rooms with those numbers."
        )
    if len(named) > 1:
        notes.append(
            f"Ambiguous: {len(named)} rooms are called {query!r}. Unless the person's words settle it, don't "
            "pick one: ask which, or give the result for each."
        )
    first = {s.id for s in named}
    rest = [] if variants else [s for s in on_floor if s.id not in first and hit(s.name, s.ref, s.room_type)]
    rooms = [{"room": _label(b, s.id), "id": s.id, "type": s.room_type} for s in [*named, *rest]]
    items = [
        {"item": a.name, "id": a.id, "type": a.type, "in": _label(b, a.space_id), "space_id": a.space_id}
        | ({"reported": _reported(reports[a.id], now)} if a.id in reports else {})
        | ({"room_missing": True} if a.space_id not in b.spaces else {})
        for a in b.assets.values()
        if (floor_id is None or a.floor_id == floor_id) and hit(a.name, a.type)
    ]
    if any(i.get("room_missing") for i in items):
        notes.append(
            "An item marked room_missing is recorded in a room that is not on the floor plan (no such space, no "
            "door): say so plainly; there is no route to it."
        )
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
            **({"notes": notes} if notes else {}),
            "highlight": sorted(
                {r["id"] for r in rooms[:MAX_RESULTS]}
                | {i["space_id"] for i in items[:MAX_RESULTS] if i["space_id"] in b.spaces}
            ),
        }
    )


NEAR_TIE = 0.9
"""Two rooms overlapping a room's outline this nearly equally both sit "directly" above (or below) it."""


def _stack_tie(b: Building, s: Space, direction: Literal["above", "below"]) -> dict | None:
    """When two spaces on the next floor cover this one almost equally, both with their overlap."""
    floor_id = geometry.adjacent_floor(b, s.floor_id, direction)
    if floor_id is None:
        return None
    overlaps = sorted(
        ((s.shape.intersection(o.shape).area, o.id) for o in b.spaces_on(floor_id)), reverse=True
    )[:2]
    if len(overlaps) < 2 or overlaps[0][0] <= 0 or overlaps[1][0] < NEAR_TIE * overlaps[0][0]:
        return None
    return {
        "rooms": [{"room": _label(b, sid), "overlap_m2": round(a, 2)} for a, sid in overlaps],
        "note": f"Ambiguous: these two cover this room almost equally (of its {s.area:.2f} m²). Don't name "
        "just one as directly " + direction + ": name both (the first one first), or ask which part.",
    }


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
    ties = {d: _stack_tie(b, s, d) for d in ("above", "below")}
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
            **{f"directly_{d}_ambiguous": t for d, t in ties.items() if t},
            **_stack(b, s),
            "highlight": [s.id],
        }
    )


def highlight_rooms(ctx: RunContext[Deps], rooms: list[str], label: str | None = None) -> str:
    """Light up exactly these rooms on the person's map, replacing whatever is lit now. Call it whenever
    the answer is a set of rooms they should see: "highlight / show me / mark ..." and results you worked
    out over several lookups (the rooms next to these, the neighbours of a set, rooms matching a rule).
    Pass the final set only (e.g. the adjacent rooms, not the rooms you started from, unless asked for
    both). The last call in a turn is what stays on screen.

    Args:
        rooms: Every room to light up: names, numbers, "Name (Floor name)", ids, or item names (their room).
        label: A few words for the map caption, e.g. "rooms next to 0.29 and 0.31".
    """
    b = ctx.deps.building
    ids: list[str] = []
    missing: dict[str, str] = {}
    for text in rooms:
        try:
            s = space_of(b, text)
        except ModelRetry as e:
            missing[text] = e.message
            continue
        if isinstance(s, str):
            missing[text] = "'outside' is not a room."
        elif s.id not in ids:
            ids.append(s.id)
    if not ids:
        raise ModelRetry(f"None of these is one room: {missing}. Pass ids from find or room_details.")
    return _json(
        {
            "highlighted": [_label(b, i) for i in ids],
            **({"not_found": missing} if missing else {}),
            "view": [{"kind": "highlight", "ids": ids, "label": label}],
        }
    )


def _stack(b: Building, s: Space) -> dict:
    """Every room under (and over) this one, one per floor down to the lowest (up to the highest): on each
    floor the space overlapping this room's own outline most. Measured against this outline, not hop by hop,
    so it doesn't drift sideways. Only when there are two or more floors that way."""
    level = b.floors[s.floor_id].level
    out = {}
    for name, floors in (
        ("all_below_down_to_lowest_floor", [f for f in reversed(b.floors_by_level()) if f.level < level]),
        ("all_above_up_to_highest_floor", [f for f in b.floors_by_level() if f.level > level]),
    ):
        if len(floors) < 2:
            continue
        chain = []
        for f in floors:
            overlaps = [
                (s.shape.intersection(o.shape).area, o)
                for o in b.spaces_on(f.id)
                if s.shape.intersects(o.shape)
            ]
            area, best = max(overlaps, key=lambda t: t[0], default=(0.0, None))
            if best is not None and area > 0.01:
                chain.append(_label(b, best.id))
        out[name] = {"count": len(chain), "rooms": chain}
    return out


def list_rooms(
    ctx: RunContext[Deps], floor: str | None = None, room_type: str | None = None, scope: Scope = "rooms"
) -> str:
    """Spaces on a floor, or in the whole building, largest first, with areas, how many there are and their
    total area.

    Args:
        floor: Level ("1", "0", "-1") or name ("Floor 1", "Ground floor"). Omit it for the whole building
            ("the largest kitchen in the building"): every floor is ranked together, ties across floors too.
        room_type: Only this type, e.g. "office", "kitchen", "generic_room" (see room_types in the
            overview). With a room_type every space of that type counts, whatever the scope.
        scope: Without a room_type, which spaces count: "rooms" (rooms only), "rooms_and_halls" (also halls
            such as factory zones), or "all_spaces" (also corridors, stairwells, lifts, balconies).
    """
    b = ctx.deps.building
    floor_ids = [floor_of(b, floor).id] if floor else [f.id for f in b.floors_by_level()]
    if room_type:
        rtype = _type_of(room_type, {s.room_type for s in b.spaces.values()}, "room type")
        ranked = [r for fid in floor_ids for r in geometry.rooms_by_area(b, fid, rtype)]
    else:
        keep = SCOPES[scope]
        ranked = [(s, s.area) for fid in floor_ids for s in b.spaces_on(fid) if keep(s)]
    ranked.sort(key=lambda t: -t[1])
    kinds: Counter[str] = Counter()
    for fid in floor_ids:
        kinds.update(_kinds(b, fid))
    return _json(
        {
            "floor": b.floors[floor_ids[0]].name if floor else "whole building",
            "room_type": room_type,
            "scope": None if room_type else scope,
            "floor_spaces_by_kind": dict(kinds.most_common()),
            "count": len(ranked),
            "total_area_m2": round(sum(a for _, a in ranked), 2),
            "rooms": [
                {"room": _label(b, s.id), "id": s.id, "area_m2": round(a, 2)} for s, a in ranked[:MAX_RESULTS]
            ],
            **_area_tie(b, ranked),
            **_overlap(ranked),
            "highlight": [s.id for s, _ in ranked[:MAX_RESULTS]],
        }
    )


def _overlap(ranked: list[tuple[Space, float]]) -> dict:
    """Spaces drawn inside or over others (OSM concourses around shops): the area they cover, counting each
    square metre once, differs from the sum. Only reported when it does, by more than 0.5 %."""
    total = sum(a for _, a in ranked)
    if len(ranked) < 2 or total <= 0:
        return {}
    by_floor: dict[str, list] = defaultdict(list)
    for s, _ in ranked:  # rooms stacked on different floors don't overlap: union each floor on its own
        by_floor[s.floor_id].append(s.shape if s.shape.is_valid else s.shape.buffer(0))
    covered = sum(unary_union(shapes).area for shapes in by_floor.values())
    if total - covered <= 0.005 * total:
        return {}
    return {
        "covered_area_m2": round(covered, 2),
        "overlap_note": f"These spaces overlap (some are drawn inside others): together they cover {covered:.2f} "
        f"m², counting overlaps once; total_area_m2 counts {total - covered:.2f} m² twice. For the ground or "
        "floor area covered, use covered_area_m2.",
    }


def _area_tie(b: Building, ranked: list[tuple[Space, float]]) -> dict:
    """Rooms of the same size (equal to the hundredth of a m²) at the top: no single room is largest. Rooms
    that differ but are within 1 % keep their order: the exact areas decide, the note only says how close."""
    if len(ranked) < 2:
        return {}
    top = ranked[0][1]
    tied = [s for s, a in ranked if round(a, 2) == round(top, 2)]
    if len(tied) >= 2:
        return {
            "largest_is_a_tie": [_label(b, s.id) for s in tied],
            "note": f"Ambiguous: {len(tied)} rooms are exactly the same size. Don't name just one. Say it once, "
            "the way a person would: that they are the same size, the size, and which rooms (the first listed "
            "first), e.g. 'two kitchens are the same size, 29.7 m²: Kitchen 1.13 and Kitchen 2.13'. Don't list "
            "each room with its area again.",
        }
    close = [(s, a) for s, a in ranked[1:] if a >= 0.99 * top]
    if not close:
        return {}
    return {
        "close_in_size": [
            {"room": _label(b, s.id), "area_m2": round(a, 2)} for s, a in ranked[: len(close) + 1]
        ],
        "note": "These rooms differ by less than 1 %, but they are not the same size: rank them by the exact "
        "areas above (largest first, then second, ...) and say how close they are.",
    }


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
    for k, (here, element) in enumerate(zip(path[1:], elements, strict=False), 2):
        if here == OUTSIDE and k < len(path):  # out and back in (Deps.outside_walking)
            steps.append("go outside through the entrance and walk around the building")
        elif here == OUTSIDE:
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


def _passes(b: Building, elements: list[str]) -> dict[str, int]:
    """What a walk goes through, counted: doorways (doors and open archways between two spaces), building
    entrances, stairs and lifts taken (each once, however many floors), outdoor walkways."""
    counts: Counter[str] = Counter()
    for e in elements:
        if e in b.openings:
            o = b.openings[e]
            counts["entrances" if OUTSIDE in o.space_ids else "doorways"] += 1
            if OUTSIDE not in o.space_ids:
                counts[f"doorways_of_kind_{o.kind}"] += 1
        elif e in b.connectors:
            counts["lifts_taken" if b.connectors[e].kind == "lift" else "stairs_taken"] += 1
        elif e in b.walkways:
            counts["walkways"] += 1
    return {"doorways": 0, **counts}


def _route_result(b: Building, route: Route, walk: bool = False) -> dict:
    """One leg for the agent; `walk` (interactive channels) adds the drawn walk round walls and obstacles."""
    return {
        "length_m": round(route.length_m, 2),
        "passes": _passes(b, route.element_path),
        "steps": _steps(b, route),
        "space_path": route.space_path,
        "element_path": list(dict.fromkeys(route.element_path)),
    } | ({"path": walk_path(b, route)} if walk else {})  # the walk for the map: [{floor_id, points}]


class _BaysPassable(RouteGraph):
    """RouteGraph with the production-bay rule off. Only used to explain a missing route, never to give one."""

    def _dijkstra(self, sources, goals=None, *, bays_are_ends=False):
        return super()._dijkstra(sources, goals, bays_are_ends=False)


def _up_to_the_bay(b: Building, c: Constraints, stops: list[str]) -> dict[str, Any] | None:
    """When only the production-bay rule blocks a route: how far the person can go by the rules (into the
    first bay the way crosses, since a route may end in one) and what to tell them, so a no-route answer in a
    conversation still leaves them a next step. None when something else (width, steps, closures) blocks it."""
    crossing = _BaysPassable(b, c).tour(stops)
    if crossing is None:
        return None
    for leg in crossing.legs:
        inner = leg.space_path[1:-1]
        bay = next((s for s in inner if s in b.spaces and b.spaces[s].room_type in PRODUCTION_BAYS), None)
        if bay is None:
            continue
        legal = route_graph(b, c).shortest(leg.space_path[0], bay)
        beyond = inner[inner.index(bay) + 1] if inner.index(bay) + 1 < len(inner) else leg.space_path[-1]
        return {
            "closest_by_the_rules": _label(b, bay),
            "walk_m": legal.length_m if legal else None,
            "bay_borders": _label(b, beyond),
            "tell_them": (
                f"Say plainly there is no route by the rules (a production bay may be a destination, never a "
                f"shortcut), then give a next step: they can walk as far as {_label(b, bay)}, the production "
                f"bay on the way, which opens onto {_label(b, beyond)}; to go on through it to "
                f"{_label(b, leg.space_path[-1])} they should ask the people working in that bay, a supervisor "
                "or facility management whether they may cross, or whether there is another way in."
            ),
        }
    return None


def _why_no_route(b: Building, c: Constraints, stops: list[str]) -> list[str]:
    """Which restrictions block a route, found by lifting each one alone (and all of them together)."""

    def found(graph: RouteGraph):
        return graph.tour(stops)

    causes = []
    if crossing := found(_BaysPassable(b, c)):
        bays = {
            s
            for leg in crossing.legs
            for s in leg.space_path[1:-1]
            if s in b.spaces and b.spaces[s].room_type in PRODUCTION_BAYS
        }
        names = ", ".join(sorted(_label(b, s) for s in bays))
        causes.append(
            f"production bays: a route meeting every other restriction exists only by passing through {names}; "
            "a route may end in a production bay but never cross one"
        )
    if c.min_width_m and (wider := found(route_graph(b, dataclasses.replace(c, min_width_m=None)))):
        narrow = [
            f"{b.openings[e].width_m} m opening between {' and '.join(_label(b, s) for s in b.openings[e].space_ids)}"
            for leg in wider.legs
            for e in dict.fromkeys(leg.element_path)
            if e in b.openings and b.openings[e].width_m < c.min_width_m
        ]
        causes.append(
            f"width: without the {c.min_width_m} m limit a route exists, through "
            + (", ".join(narrow) or "narrow lifts or stairs")
        )
    if c.step_free and found(route_graph(b, dataclasses.replace(c, step_free=False))):
        causes.append("steps: a route exists only with stairs or stepped doors")
    if c.closed and found(route_graph(b, dataclasses.replace(c, closed=frozenset()))):
        causes.append(f"closures: the reported closures block it ({', '.join(sorted(c.closed))})")
    if not causes:
        if found(_BaysPassable(b, Constraints())):
            causes.append(
                "a combination: no single restriction is the cause; only lifting several at once "
                "(width, steps, closures, production bays) gives a route"
            )
        else:
            causes.append(_not_connected(b, stops))
    return causes


def _not_connected(b: Building, stops: list[str]) -> str:
    """What the plan is missing between stops no rule can join: a room with no door at all, a floor no stairs
    or lift stops at, or else how small the cut-off part is. Facts from the plan, so nobody has to guess."""
    free = _BaysPassable(b, Constraints())
    for a, z in itertools.pairwise(stops):
        if z in free.reachable(a):
            continue
        for sid in (z, a):
            if sid != OUTSIDE and not b.openings_of(sid) and not b.connectors_at(sid):
                return (f"not connected: {_label(b, sid)} has no door, opening, stairs or lift at all on the plan, "
                        "so nothing leads into it (a gap in the plan)")  # fmt: skip
        part = free.reachable(z) if z != OUTSIDE else free.reachable(a)
        stopped = {fid for k in b.connectors.values() for fid, _ in k.stops}
        floors = {b.spaces[s].floor_id for s in part if s in b.spaces}
        if len(floors) == 1 and (fid := next(iter(floors))) not in stopped and len(b.floors) > 1:
            levels = sorted(b.floors[f].level for f in stopped) or [None]
            return (f"not connected: no stairs or lift stops at {b.floors[fid].name} (they reach level "
                    f"{levels[0]} to {levels[-1]}), so nothing leads up or down to it (a gap in the plan)")  # fmt: skip
        return (f"not connected: {_label(b, z)} is in a part of the plan of {len(part)} spaces that no door, "
                f"stairs or lift joins to {_label(b, a)} (a gap in the plan)")  # fmt: skip
    return "not connected: no door, stairs or lift joins these places on the plan, under any rule"


LENGTH_INCLUDES = (
    "everything: walking, every lift ride (15 m waiting plus 1 m per metre of height) and every flight of "
    "stairs (2 m per metre climbed). Don't add lift or stair costs again."
)


def route(
    ctx: RunContext[Deps],
    start: str,
    destination: str,
    via: list[str] | None = None,
    step_free: bool = False,
    min_width_m: float | None = None,
) -> str:
    """Shortest route between places, in order through any `via` stops, with its length in metres, a
    skeleton of steps (rooms, doors, lifts, stairs) and `passes`: how many doorways (doors and open
    archways between two spaces), entrances, stairs and lifts it goes through, per leg and in total.
    Closures the person reported are always avoided. reachable=false means there is no route: going out
    one entrance and back in through another is not a route, so never combine legs to and from "outside".

    No route rarely has a single obvious cause. Several restrictions apply at once: openings narrower than
    min_width_m, stairs and stepped doors when step_free, reported closures, and production bays (production,
    assembly and additive zones), which a route may start or end in but never pass through, even when the
    bay is wide and step-free. So a path that is wide enough can still be ruled out. When reachable=false,
    `blocked_by` says which restrictions block it, found by lifting each one in turn. Explain the trip
    with those causes. Don't name a single narrow opening, step or closure as the reason unless
    `blocked_by` shows it alone is enough, and don't call a place unreachable when it's only these rules.

    Args:
        start: Where the person is: room name/number, id, item name, or "outside" (from the street: the
            route starts at the best open entrance).
        destination: The last place visited (room, item name, "outside").
        via: Stops visited in order before the destination, e.g. a room to pick something up from. For
            "visit A, then B": via=["A"], destination="B".
        step_free: No stairs or steps (wheelchair, pram, trolley, crutches, "no stairs"). Lifts are fine.
        min_width_m: Every door, lift and opening passed must be at least this wide (a wide load). A
            forklift or other vehicle: always min_width_m=3 (unless they give its width) and step_free=true,
            so lifts with narrow doors are out.
    """
    b = ctx.deps.building
    constraints = _constraints(ctx, step_free, min_width_m)
    stops, shared_name = _stops_committed(ctx, [start, *(via or []), destination], constraints)
    tour = route_graph(b, constraints).tour(stops)
    if tour is None:
        blocked = {
            "reachable": False,
            "stops": [_label(b, s) for s in stops],
            "note": "No route under these constraints. See blocked_by for why.",
            "blocked_by": _why_no_route(b, constraints, stops),
        }
        if ctx.deps.detail == "walk" and (way_on := _up_to_the_bay(b, constraints, stops)):
            blocked["next_step"] = way_on  # a person being guided, never a scored run
            blocked["note"] += " Then give them next_step, so they aren't left without a way forward."
        return _json(blocked)
    legs = [_route_result(b, leg, ctx.deps.detail == "walk") for leg in tour.legs]
    total: Counter[str] = Counter()
    for leg in legs:
        total.update(leg["passes"])
    return _json(
        {
            "reachable": True,
            "total_length_m": round(tour.length_m, 2),
            "length_includes": LENGTH_INCLUDES,
            "passes_in_total": dict(total),
            "stops": [_label(b, s) for s in stops],
            "legs": legs,
            **({"shared_name": shared_name} if shared_name else {}),
            "closed_avoided": sorted(ctx.deps.closed),
            "highlight": stops,
        }
    )


_WALK_M = re.compile(r"walk (\d+) m")


def _short_walk(steps: list) -> list[str]:
    """The leg in as few sentences as it can be told without losing a turn: each step that goes straight on
    joins the one before it, its metres added in code; every left, right, stair, lift and obstacle stays."""
    out: list[list] = []  # [text, metres]
    for step in steps:
        if step.action == "straight" and out and _WALK_M.search(out[-1][0]):
            out[-1][1] += step.distance_m
            continue
        out.append([step.text, step.distance_m])
    return [
        _WALK_M.sub(f"walk about {max(1, round(m))} m", t, count=1) if _WALK_M.search(t) else t
        for t, m in out
    ]


def _directions_leg(b: Building, route: Route) -> dict:
    """One walk_directions leg: the spoken steps (each with its floor and its own polyline) and the walk."""
    steps = []
    walk = directions(b, route)
    for step in walk:
        d = dataclasses.asdict(step)
        space = b.spaces.get(step.space_id)
        d["floor_id"] = space.floor_id if space is not None else None
        d["points"] = [[round(x, 2), round(y, 2)] for x, y in step.points]
        steps.append(d)
    return {
        "length_m": round(route.length_m, 2),
        "space_path": route.space_path,
        "short_walk": _short_walk(walk),
        "steps": steps,
        "path": walk_path(b, route),
    }


async def walk_mode_only(ctx: RunContext[Deps], tool_def: ToolDefinition) -> ToolDefinition | None:
    """Tool `prepare` hook: the tool exists only in conversations with detail="walk" (people being guided),
    never in benchmark or Sunday runs, where answers stay room by room."""
    return tool_def if ctx.deps.detail == "walk" else None


def walk_directions(
    ctx: RunContext[Deps],
    start: str,
    destination: str,
    via: list[str] | None = None,
    step_free: bool = False,
    min_width_m: float | None = None,
) -> str:
    """The walk inside the rooms of a route: where to turn left or right, what they walk past, and what
    stands in the way ("go around the printer, keeping it on your left"), plus the drawn path for the map.
    Call it together with route whenever someone asks how to get somewhere, unless they clearly know the
    building. Its steps are raw material: tell them as a short walk built on turns and landmarks, not read
    out one by one. Each leg's `short_walk` is the same walk with straight stretches already joined and
    their metres added: build short or spoken answers from it, keeping every turn it has.
    Same arguments as route; closures they reported are avoided.

    Args:
        start: Where the person is: room name/number, id, item name, or "outside" (from any entrance).
        destination: The last place visited (room, item name, "outside").
        via: Stops visited in order before the destination.
        step_free: No stairs or steps. Lifts are fine.
        min_width_m: Every door passed must be at least this wide (a forklift or vehicle: 3, step_free).
    """
    b = ctx.deps.building
    stops = [_space_id(b, p) for p in [start, *(via or []), destination]]
    tour = route_graph(b, _constraints(ctx, step_free, min_width_m)).tour(stops)
    if tour is None:
        return _json({"reachable": False, "stops": [_label(b, s) for s in stops]})
    try:
        legs = [_directions_leg(b, leg) for leg in tour.legs]
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
            "length_includes": LENGTH_INCLUDES,
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
    min_width_m: float | None = None,
    then_to: str | None = None,
) -> str:
    """The closest rooms by walking distance that hold an item type, or are of a room type, nearest first,
    and how many such rooms there are in all.

    Args:
        start: Where the person is, or "outside" (from any entrance).
        item_type: An item type from the overview, e.g. "coffee_machine", "office_chair".
        room_type: Or a room type, e.g. "toilet", "kitchen".
        step_free: Walk without stairs or steps.
        min_width_m: Every door passed must be at least this wide (a forklift or vehicle: 3, step_free).
        then_to: Where they carry it afterwards, when they may pick it up in whichever room is best ("any
            printer on the way", "whichever is best for them"): rooms are then ranked by the whole walk,
            start to room to then_to. Leave it out when they want the one closest to them.
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
    graph = route_graph(b, _constraints(ctx, step_free, min_width_m))
    distances = graph.distances_from([origin])
    if then_to:  # the whole errand: start -> room, then room -> destination (walks are symmetric)
        goal = _space_id(b, then_to)
        onward = graph.distances_from([goal])
        ranked = sorted((distances[t] + onward[t], t) for t in targets if t in distances and t in onward)
        rows = [
            {"room": _label(b, t), "id": t, "walk_m": round(distances[t], 2), "then_to_m": round(onward[t], 2),
             "total_m": round(d, 2)}
            for d, t in ranked[:5]
        ]  # fmt: skip
    else:
        ranked = sorted((distances[t], t) for t in targets if t in distances)
        rows = [{"room": _label(b, t), "id": t, "walk_m": round(d, 2)} for d, t in ranked[:5]]
    return _json(
        {
            "from": _label(b, origin),
            **({"then_to": _label(b, goal), "ranked_by": "total_m, the whole walk"} if then_to else {}),
            "nearest": rows,
            "rooms_in_all": len(targets),
            "unreachable": len(targets) - len(ranked),
            "highlight": [t for _, t in ranked[:5]],
        }
    )


def plan_visits(
    ctx: RunContext[Deps],
    start: str,
    rooms: list[str] | None = None,
    item_types: list[str] | None = None,
    room_types: list[str] | None = None,
    end: str | None = None,
    return_to_start: bool = False,
    step_free: bool = False,
    min_width_m: float | None = None,
) -> str:
    """The shortest trip through several stops when the ORDER is free, and, for "a room with X", which room
    to use: tries every order and every choice of room, so never compare orders or rooms yourself. Use it
    for "visit A, B and C in any order", "in whatever order is best", "pick up X, Y and Z", "I need a room
    with a printer, then a kitchen...", with or without coming back. A room holding several of the items
    counts for each. For stops in a GIVEN order ("first A, then B"), use route with via instead.

    Returns the order, what each stop is for, the total and per-leg walks (steps, spaces, doors, lifts, like
    route). exact=true means proven shortest. greedy_total_m is the naive nearest-next-stop trip, for comparison.

    Args:
        start: Where the person is: room name/number, id, or "outside".
        rooms: Specific rooms that must all be visited, e.g. ["Office 1.02", "Lab 2.17"].
        item_types: Item types from the overview; one room holding each is visited, whichever is best,
            e.g. ["coffee_machine", "3d_printer"].
        room_types: Room types; one room of each is visited, whichever is best, e.g. ["toilet"].
        end: Where the trip must finish, if somewhere other than the last stop.
        return_to_start: Come back to start at the end (a round trip).
        step_free: No stairs or steps. Lifts are fine.
        min_width_m: Every door passed must be at least this wide (a forklift or vehicle: 3, step_free).
    """
    b = ctx.deps.building
    origin = _space_id(b, start)
    finish = _space_id(b, end) if end else None
    if return_to_start and finish not in (None, origin):
        raise ModelRetry("Give end or return_to_start=true, not both.")
    groups: list[list[str]] = []
    names: list[str] = []
    for room in rooms or []:
        groups.append([_space_id(b, room)])
        names.append(_label(b, groups[-1][0]))
    known_items = {a.type for a in b.assets.values()}
    for item in item_types or []:
        itype = _type_of(item, known_items, "item type")
        groups.append(
            sorted({a.space_id for a in b.assets.values() if a.type == itype and a.space_id in b.spaces})
        )
        names.append(f"a room with a {itype}")
    known_rooms = {s.room_type for s in b.spaces.values()}
    for rt in room_types or []:
        rtype = _type_of(rt, known_rooms, "room type")
        groups.append(sorted(s.id for s in b.spaces.values() if s.room_type == rtype))
        names.append(f"a {rtype}")
    if not groups:
        raise ModelRetry("Give the stops: rooms, item_types or room_types.")
    graph = route_graph(b, _constraints(ctx, step_free, min_width_m))
    plan = queries.plan_visits(b, origin, groups, end=finish, return_to_start=return_to_start, graph=graph)
    if plan is None or plan.unreachable_groups:
        missing = [names[g] for g in plan.unreachable_groups] if plan else [_label(b, finish or "")]
        return _json(
            {
                "reachable": False,
                "unreachable": missing,
                "note": "No trip under these constraints reaches these.",
            }
        )
    stops = [origin, *plan.stops] + ([plan.end] if plan.end is not None else [])
    tour = graph.tour(stops)
    if tour is None:  # can't happen: the plan's legs are shortest routes on the same graph
        return _json({"reachable": False, "note": "No route under these constraints."})
    return _json(
        {
            "reachable": True,
            "total_length_m": round(tour.length_m, 2),
            "length_includes": LENGTH_INCLUDES,
            "exact": plan.exact,
            "order": [_label(b, s) for s in stops],
            "stops": [
                {"room": _label(b, s), "id": s, "for": [names[g] for g in covers]}
                for s, covers in zip(plan.stops, plan.covers, strict=True)
            ],
            **(
                {"settled_at_start_or_end": [names[g] for g in plan.start_covers]}
                if plan.start_covers
                else {}
            ),
            "greedy_total_m": plan.greedy_total_m,
            **(
                {
                    "note": "Many rooms qualified; only those nearest the start, end and fixed stops were compared "
                    f"for: {', '.join(names[g] for g in plan.pruned_groups)}."
                }
                if plan.pruned_groups
                else {}
            ),
            "legs": [_route_result(b, leg, ctx.deps.detail == "walk") for leg in tour.legs],
            "closed_avoided": sorted(ctx.deps.closed),
            "highlight": stops,
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
    room_type: str | None = None,
    through_production_bays: bool = True,
) -> str:
    """Rooms on a floor that can be reached from a start (deliveries, wheelchair access, a forklift's
    reach), and how many.

    Args:
        start: Where the person or load comes in: a room, or "outside" for in from the street through any
            open entrance (the union of all entrances; step_free keeps only step-free ones).
        floor: Level or floor name to count rooms on.
        step_free: No stairs or steps (wheelchair, trolley, pallet). Lifts are fine.
        min_width_m: Every door passed must be at least this wide (a forklift or vehicle: 3, step_free).
        closed: Extra lifts, stairs, doors or rooms out of service for this question only.
        scope: Which spaces count: "rooms" (rooms only, what a count of rooms means), "rooms_and_halls"
            (also halls such as factory zones), or "all_spaces".
        room_type: Only spaces of this type, whatever the scope, e.g. "warehouse_zone", "office".
        through_production_bays: Keep the default (true) for delivery and access counts ("how many rooms
            can it be delivered to / reach"): those may pass through production bays. Set false when the
            person says a vehicle or forklift may not drive through production bays, or asks where a route
            can actually go: then a production bay can be a destination but is never crossed, and
            `dropped_by_production_bay_rule` lists what only a way through a bay would reach.
    """
    b = ctx.deps.building
    origin, f = _space_id(b, start), floor_of(b, floor)
    graph = route_graph(b, _constraints(ctx, step_free, min_width_m, closed or []))
    via_bays = graph.reachable(origin)
    reached = via_bays if through_production_bays else graph.reachable(origin, through_production_bays=False)
    if room_type:
        rtype = _type_of(room_type, {s.room_type for s in b.spaces.values()}, "room type")
        rooms = [s for s in b.spaces_on(f.id) if s.room_type == rtype]
    else:
        rooms = [s for s in b.spaces_on(f.id) if SCOPES[scope](s)]
    yes = [s.id for s in rooms if s.id in reached]
    dropped = [s.id for s in rooms if s.id in via_bays and s.id not in reached]
    return _json(
        {
            "floor": f.name,
            "scope": None if room_type else scope,
            "room_type": room_type,
            "floor_spaces_by_kind": _kinds(b, f.id),
            "through_production_bays": through_production_bays,
            "reachable_count": len(yes),
            "unreachable_count": len(rooms) - len(yes),
            "reachable": [_label(b, s) for s in yes[:MAX_RESULTS]],
            "unreachable": [_label(b, s.id) for s in rooms if s.id not in reached][:MAX_RESULTS],
            **(
                {"dropped_by_production_bay_rule": [_label(b, s) for s in dropped[:MAX_RESULTS]]}
                if not through_production_bays
                else {}
            ),
            "highlight": yes,
        }
    )


def walk_distances(
    ctx: RunContext[Deps],
    floor: str,
    start: str = "outside",
    step_free: bool = False,
    min_width_m: float | None = None,
    closed: list[str] | None = None,
    scope: Scope = "rooms",
    room_type: str | None = None,
    within_m: float | None = None,
) -> str:
    """Every room on a floor with its walking distance from a start, LONGEST FIRST. With the default start
    "outside" that is each room's walk out of the building to its nearest entrance: use it for fire drills
    and evacuation, "which room is farthest from / closest to an exit", worst or average walk out, or
    "which room on floor X is farthest from Y". One call ranks every room; never probe rooms one by one.
    For "how many (other) rooms can I reach within X m" pass within_m=X: the answer is `count_within`,
    counted in code (the start itself excluded, walks of exactly X m included); report it as is.
    It lists at most 30 rooms. For ONE named room's walk out ("how far from X to get outside"), call
    route(start=X, destination="outside") instead; with a room as start, `start_walk_out_m` is that walk.

    Args:
        floor: Level or floor name whose rooms to rank (for "on that floor", the start's floor).
        start: "outside" (nearest entrance, the default) or a room or place to measure from.
        step_free: No stairs or steps. Lifts are fine.
        min_width_m: Every door passed must be at least this wide (a forklift or vehicle: 3, step_free).
        closed: Extra lifts, stairs, doors or rooms out of service for this question only.
        scope: Which spaces count: "rooms" (default), "rooms_and_halls" or "all_spaces".
        room_type: Only spaces of this type, whatever the scope, e.g. "office".
        within_m: Count and list, shortest first, the rooms reachable within this many metres of walking.
    """
    b = ctx.deps.building
    origin, f = _space_id(b, start), floor_of(b, floor)
    graph = route_graph(b, _constraints(ctx, step_free, min_width_m, closed or []))
    distances = graph.distances_from([origin])
    walk_out = graph.shortest(origin, OUTSIDE) if origin != OUTSIDE else None
    start_out = {"start_walk_out_m": round(walk_out.length_m, 2)} if walk_out is not None else {}
    if room_type:
        rtype = _type_of(room_type, {s.room_type for s in b.spaces.values()}, "room type")
        rooms = [s for s in b.spaces_on(f.id) if s.room_type == rtype]
    else:
        rooms = [s for s in b.spaces_on(f.id) if SCOPES[scope](s)]
    if within_m is not None:
        others = sorted((distances[s.id], s.id) for s in rooms if s.id in distances and s.id != origin)
        near = [(d, sid) for d, sid in others if d <= within_m]
        return _json(
            {
                "from": "the nearest entrance (walk out of the building)"
                if origin == OUTSIDE
                else _label(b, origin),
                "floor": f.name,
                "scope": None if room_type else scope,
                "room_type": room_type,
                "within_m": within_m,
                "count_within": len(near),
                "note": "Other rooms only: the start itself is not counted. Walks of exactly "
                f"{within_m} m count. The answer is count_within.",
                "within": [{"room": _label(b, sid), "walk_m": round(d, 2)} for d, sid in near],
                "count_farther": len(others) - len(near),
                "nearest_farther": [
                    {"room": _label(b, sid), "walk_m": round(d, 2)} for d, sid in others[len(near) :][:3]
                ],
                "unreachable_count": sum(1 for s in rooms if s.id not in distances and s.id != origin),
                **start_out,
                "highlight": [sid for _, sid in near],
            }
        )
    ranked = sorted(((distances[s.id], s.id) for s in rooms if s.id in distances), key=lambda t: -t[0])
    keep = 2 * MAX_RESULTS
    rows = [{"room": _label(b, sid), "id": sid, "walk_m": round(d, 2)} for d, sid in ranked]
    return _json(
        {
            "from": "the nearest entrance (walk out of the building)"
            if origin == OUTSIDE
            else _label(b, origin),
            "floor": f.name,
            "scope": None if room_type else scope,
            "room_type": room_type,
            "rooms_compared": len(rooms),
            "longest_first": rows[:keep],
            **({"shortest": rows[-1]} if rows else {}),
            **(
                {
                    "note": f"{len(rows) - keep} more rooms with shorter walks not listed. For one room's own "
                    'walk, call route(start=<room>, destination=...), "outside" for its walk out.'
                }
                if len(rows) > keep
                else {}
            ),
            "unreachable_count": len(rooms) - len(ranked),
            "unreachable": [_label(b, s.id) for s in rooms if s.id not in distances][:MAX_RESULTS],
            **start_out,
            "highlight": [sid for _, sid in ranked[:1]],
        }
    )


# ---- tools: changes --------------------------------------------------------------------------------------


def with_door(
    b: Building,
    first: str,
    second: str,
    width_m: float = 0.9,
    step_free: bool = True,
    position: tuple[float, float] | None = None,
) -> Building:
    """A copy of the building with a new door in the middle of the wall two spaces share (or at
    `position`). With OUTSIDE as one side: a new entrance in the middle of the room's longest outside wall."""
    if OUTSIDE not in (first, second) and position is None:
        if geometry.shared_wall_midpoint(b, first, second) is None:
            raise ModelRetry(
                f"{_label(b, first)} and {_label(b, second)} don't share a wall (≥1 m) on one floor."
            )
    try:
        floor_id, where = BuildingState._door_place(b, AddDoor(first, second, position=position))  # noqa: SLF001
    except ValueError as e:
        raise ModelRetry(f"Can't put a door there: {e}.") from None
    pair, kind = (first, second), "door"
    if OUTSIDE in pair:
        pair, kind = (second, OUTSIDE) if first == OUTSIDE else pair, "entrance"
    door = Opening(
        f"{b.id}-NEW-{first[-4:]}-{second[-4:]}", floor_id, pair, kind, where, width_m, step_free, True
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
    newly = [sid for sid, d0, d1 in rows if d0 is None and d1 is not None]  # no way out before: gains most
    shorter = [(sid, math.inf) for sid in newly] + sorted(
        ((sid, d0 - d1) for sid, d0, d1 in both if d0 - d1 > MIN_GAIN_M), key=lambda t: -t[1]
    )
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
        "shorter": [
            {"room": _label(b, sid)}
            | ({"saves_m": round(g, 1)} if g < math.inf else {"before": "no way out at all, now has one"})
            for sid, g in shorter[:MAX_RESULTS]
        ],
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
    Lists only what changes; for each room's walk out as it is now (farthest room from an exit), use
    walk_distances.

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


def _proposed(pending: dict[str, Any] | None, edit: Edit) -> Edit | None:
    """The proposed edit this confirming call means: the same edit, or the same door named the other way
    round or without the width. The person said yes to what was proposed, so that is what gets applied."""
    for e in pending["edits"] if pending else []:
        if e == edit:
            return e
        if isinstance(e, AddDoor) and isinstance(edit, AddDoor):
            if {e.first_space_id, e.second_space_id} == {edit.first_space_id, edit.second_space_id}:
                return e
        if isinstance(e, SplitRoom) and isinstance(edit, SplitRoom) and e.space_id == edit.space_id:
            if {e.from_space_id, e.to_space_id} == {edit.from_space_id, edit.to_space_id}:
                return e
    return None


def _propose_or_apply(
    ctx: RunContext[Deps], edit: Edit, preview: dict, confirmed: bool, apply_now: bool = False
) -> str:
    """Edits apply at once, unless the conversation asks for a yes first (Deps.confirm_edits, the voice
    call): then they happen in two turns, propose (nothing changes) -> the person says yes -> apply.
    Everything proposed in one turn is one batch: a single yes applies all of it, without asking again per
    change. `apply_now`: the person said to make the change without being asked; it is applied at once."""
    d = ctx.deps
    if apply_now or not d.confirm_edits:
        if edit in d.state.edits:  # the same edit asked for again (a "yes" after it was made): once only
            return _json(preview | {"status": "done", "change": "Already in the plan; nothing added twice."})
        try:
            message = d.state.apply(edit, by=d.who)
        except ValueError as e:
            raise ModelRetry(str(e)) from None
        return _json(preview | {"status": "done", "change": message, "undo": "undo_last_change reverts it"})
    if not confirmed:
        if not d.pending or d.pending["run_id"] != ctx.run_id:  # a new turn starts a new batch
            d.pending = {"edits": [], "run_id": ctx.run_id}
        if edit not in d.pending["edits"]:
            d.pending["edits"].append(edit)
        n = len(d.pending["edits"])
        return _json(
            preview
            | {
                "status": "proposed: nothing changed yet",
                "proposed_this_turn": n,
                "next": "Propose every change they asked for now, one call each. Then say in one reply what you "
                "understood and what it changes, and ask once to confirm them all. After their yes, call this "
                "tool again for each with the same arguments and confirmed=true, all in that turn.",
            }
        )
    match = _proposed(d.pending, edit)
    if match is None:
        raise ModelRetry(
            "Nothing like this was proposed in this conversation, so nothing was changed. Propose it now "
            "(confirmed=false) and ask again; don't tell them it was added. If they said to do it without "
            "asking, call it with apply_now=true instead."
        )
    if match != edit and isinstance(match, AddDoor):
        preview = preview | {"width_m": match.width_m}
    edit = match
    assert d.pending is not None
    if d.pending["run_id"] == ctx.run_id:
        raise ModelRetry("The person hasn't answered yet: ask them, and apply it after they say yes.")
    try:
        message = d.state.apply(edit, by=d.who)
    except ValueError as e:
        raise ModelRetry(str(e)) from None
    d.pending["edits"].remove(edit)
    left = len(d.pending["edits"])
    if not left:
        d.pending = None
    return _json(
        preview
        | {"status": "done", "change": message, "undo": "undo_last_change reverts it"}
        | ({"confirmed_still_to_apply": left} if left else {})
    )


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


def _door_already_there(d: Deps, a: str, c: str) -> str | None:
    """The answer to add_door when the two spaces already have a door: one this session added (asked for
    again, e.g. a "yes" after it was made) is "done", one in the plan "already there". Each adds nothing."""
    b = d.building
    hits = [o for o in b.openings_of(a) if o.other_side(a) == c]
    if not hits:
        return None
    o = hits[0]
    names = f"{_label(b, a)} and {_label(b, c)}" if c != OUTSIDE else f"outside and {_label(b, a)}"
    door = f"a {o.width_m:g} m {o.kind} ({o.id})"
    if o.id not in d.state.base.openings:
        return _json(
            {
                "status": "done",
                "change": f"This door is already in the plan: {door} between {names}, added in this session; "
                "nothing added again.",
                "undo": "undo_last_change reverts it",
                "highlight": [a] if c == OUTSIDE else [a, c],
            }
        )
    return _json(
        {
            "status": "already there",
            "change": f"Nothing added: {names} already have {door}. For a second door, call again with "
            "another=true; for a different width, remove_door it and add the new one at its position.",
            "highlight": [a] if c == OUTSIDE else [a, c],
        }
    )


def add_door(
    ctx: RunContext[Deps],
    first: str,
    second: str,
    width_m: float = 0.9,
    step_free: bool = True,
    position: list[float] | None = None,
    another: bool = False,
    confirmed: bool = False,
    apply_now: bool = False,
) -> str:
    """Add a real door to the plan, in the middle of the wall two spaces share. With "outside" as `second`
    it is a new entrance, in the middle of the room's longest outside wall (only on the ground floor or a
    floor that already has an entrance). It is added at once and later routes use it (undo_last_change takes
    it back); only where you are told edits need a yes first, the first call proposes it (nothing is edited)
    and a call with confirmed=true after their yes adds it. Only a result with "status": "done" means the
    door is in the plan.

    To change an existing door's width or steps: remove_door it, then add_door between the same two spaces
    with the new width_m (and step_free) and `position` set to the removed door's position, which
    remove_door returns as "re_add_at". That is one change for the person: say both are done and that
    undo_last_change twice takes it back. A wider door must fit on the wall the two spaces share: near a
    corner it moves along the wall just enough to fit ("position" in the result is where it ended up; say
    so if it moved), and a door wider than the wall is refused with the most that fits.

    Args:
        first: A room, hall or corridor.
        second: A space that shares a wall with `first`, on the same floor, or "outside" for an entrance.
        width_m: Door width in metres.
        step_free: False for a door with steps (a stepped entrance).
        position: [x, y] in metres, only to put it back exactly where a removed door was; omit otherwise.
        another: True only when the person wants a second door where the two spaces already have one.
            Otherwise nothing is added and the result names the door that is there (asking again for a
            door that was just added is safe: it is added once).
        confirmed: Only where edits need a yes: True after the person agreed to this exact change.
        apply_now: Only where edits need a yes: True when the person explicitly said to make the change
            without asking them first ("just add it", "no need to confirm"): it is added at once.
    """
    d = ctx.deps
    b = d.building
    a, c = _space_id(b, first), _space_id(b, second)
    if a == OUTSIDE:
        a, c = c, a
    where = (float(position[0]), float(position[1])) if position else None
    if not another and where is None and (there := _door_already_there(d, a, c)):  # a re-add has a position
        return there
    after = with_door(b, a, c, width_m, step_free, where)  # ModelRetry if it can't go there
    floor_id = b.spaces[a].floor_id if a in b.spaces else next(iter(b.floors))
    effect = _exit_changes(b, after, _constraints(ctx, False, None), _constraints(ctx, False, None), floor_id)
    (placed,) = [o for o in after.openings.values() if o.id not in b.openings]
    preview = {
        ("entrance_into" if c == OUTSIDE else "door_between"): [_label(b, a)]
        if c == OUTSIDE
        else [_label(b, a), _label(b, c)],
        "width_m": width_m,
        "step_free": step_free,
        "position": [round(v, 3) for v in placed.position],  # where it stands: the map draws it there
        "effect_on_walks_out_on_that_floor": effect,
        "highlight": [a] if c == OUTSIDE else [a, c],
    }
    edit = AddDoor(a, c, width_m=width_m, step_free=step_free, position=where)
    return _propose_or_apply(ctx, edit, preview, confirmed, apply_now)


def door_of(b: Building, door: str | None, between: list[str] | None) -> Opening:
    """One door, entrance or opening: by id, or as the one between two spaces ("outside" for an entrance).
    ModelRetry listing the ids when several join the same two spaces."""
    if door and door.strip() in b.openings:
        return b.openings[door.strip()]
    if not between or len(between) != 2:
        raise ModelRetry(
            f"No door with id {door!r}. Name the two spaces it joins in `between`, e.g. "
            '["Office 0.02", "Corridor 0.01"] (or "outside" for an entrance); room_details lists door ids.'
        )
    a, c = (_space_id(b, x) for x in between)
    hits = [o for o in b.openings_of(a) if o.other_side(a) == c and a in o.space_ids]
    if not hits:
        raise ModelRetry(f"There is no door between {_label(b, a)} and {_label(b, c)}.")
    if len(hits) > 1:
        options = "; ".join(
            f"{o.id} ({o.width_m:g} m at {o.position[0]:.1f}, {o.position[1]:.1f})" for o in hits
        )
        raise ModelRetry(
            f"{len(hits)} doors join them: {options}. Pass the id of the one meant, or ask which."
        )
    return hits[0]


def remove_door(
    ctx: RunContext[Deps],
    door: str | None = None,
    between: list[str] | None = None,
    confirmed: bool = False,
    apply_now: bool = False,
) -> str:
    """Take a door, opening or entrance out of the plan for good ("brick up the door between X and Y",
    "remove that entrance"). For a door that is only shut for now, use set_closed. It is removed at once and
    later routes no longer use it (undo_last_change puts it back); only where you are told edits need a yes
    first, the first call proposes it and a call with confirmed=true after their yes removes it. The result
    says which rooms it leaves with no way in at all: tell the person.

    To change a door's width or steps ("make the door between X and Y 1.5 m wide"), remove it, then call
    add_door with the two spaces, the new width_m, step_free and position from "re_add_at" in this result.

    Args:
        door: The door's id (from room_details or a route), if known.
        between: Otherwise the two spaces it joins, e.g. ["Office 0.02", "Corridor 0.01"]; "outside" for an
            entrance.
        confirmed: Only where edits need a yes: True after the person agreed to this exact change.
        apply_now: Only where edits need a yes: True when the person explicitly said to make the change
            without asking them first: it is removed at once.
    """
    d = ctx.deps
    b = d.building
    if door and door.strip() not in b.openings and RemoveDoor(door.strip()) in d.state.edits:
        return _json(  # asked for again (a "yes" after it was made): removed once only
            {
                "door": door.strip(),
                "status": "done",
                "change": f"{door.strip()} is already removed from the "
                "plan in this session; nothing changed again.",
                "undo": "undo_last_change puts it back",
            }
        )
    o = door_of(b, door, between)
    after = dataclasses.replace(b, openings={k: v for k, v in b.openings.items() if k != o.id})
    c = _constraints(ctx, False, None)
    before_out, after_out = (
        route_graph(b, c).distances_from([OUTSIDE]),
        route_graph(after, c).distances_from([OUTSIDE]),
    )
    cut_off = [sid for sid in before_out if sid in b.spaces and sid not in after_out]
    rooms = [sid for sid in o.space_ids if sid != OUTSIDE]
    first, second = o.space_ids
    preview = {
        "door": o.id,
        ("entrance_into" if OUTSIDE in o.space_ids else "door_between"): [_label(b, sid) for sid in rooms],
        "width_m": o.width_m,
        "step_free": o.step_free,
        "re_add_at": {"first": first, "second": second, "position": list(o.position)},
        "effect_on_walks_out_on_that_floor": _exit_changes(b, after, c, c, o.floor_id),
        "no_way_in_any_more": [_label(b, sid) for sid in cut_off[:MAX_RESULTS]],
        "no_way_in_count": len(cut_off),
        "highlight": rooms + [sid for sid in cut_off if sid not in rooms],
    }
    return _propose_or_apply(ctx, RemoveDoor(o.id), preview, confirmed, apply_now)


def _neighbour_id(b: Building, text: str, room_id: str) -> str:
    """A space named next to a room: on the room's floor first ("Staircase 1" is on every floor)."""
    try:
        return _space_id(b, text, b.spaces[room_id].floor_id)
    except ModelRetry:
        return _space_id(b, text)


def add_wall(
    ctx: RunContext[Deps],
    room: str,
    from_room: str,
    to_room: str,
    confirmed: bool = False,
    apply_now: bool = False,
) -> str:
    """Add a new wall to the plan that splits a room into two halves of equal area ("put a wall across
    Office 2.03", "split this room in two"). The wall runs from the wall the room shares with `from_room` to
    the wall it shares with `to_room`: neighbours on opposite sides give a wall straight across (north and
    south neighbours give a north-south wall, leaving a west and an east half); neighbours on two adjacent
    sides give a diagonal one. The room becomes two rooms with their own numbers, "Office 2.03-1 (west half)" and
    "Office 2.03-2 (east half)"; its doors and
    items go to the half they are in. The new wall has no door: if a half is left without one, say so and
    offer add_door (between the two halves, or to a neighbour). It is built at once (undo_last_change takes it
    back); only where you are told edits need a yes first, the first call proposes it and shows the halves
    (nothing is edited) and a call with confirmed=true after their yes builds it. Only a result with
    "status": "done" means the wall is in the plan.

    Args:
        room: The room to split.
        from_room: A space sharing the wall where the new wall starts (same floor).
        to_room: A space sharing the wall where it ends, on a different side of the room.
        confirmed: Only where edits need a yes: True after the person agreed to this exact change.
        apply_now: Only where edits need a yes: True when the person explicitly said to make the change
            without asking them first ("just do it", "no need to confirm"): it is built at once.
    """
    d = ctx.deps
    b = d.building
    s = _space_id(b, room)
    try:
        edit = split_between(b, s, _neighbour_id(b, from_room, s), _neighbour_id(b, to_room, s))
        split = split_room(b, edit)
        after = with_split(b, edit)
    except ValueError as e:
        raise ModelRetry(f"Can't split it that way: {e}.") from None
    halves = []
    for h in split.halves:
        doors = sorted({_label(b, o.other_side(h.id)) for o in after.openings_of(h.id)})
        halves.append(
            {"name": _label(after, h.id), "area_m2": round(h.area, 1), "doors_to": doors}
            | ({"note": "no door: no way in until one is added"} if not doors else {})
        )
    preview = {
        "split": _label(b, s),
        "wall_from": _label(b, edit.from_space_id),
        "wall_to": _label(b, edit.to_space_id),
        "wall_length_m": round(split.length_m, 1),
        "halves": halves,
        "highlight": [s],
        "wall": {"floor_id": b.spaces[s].floor_id, "ends": [[round(v, 3) for v in xy] for xy in split.wall]},
    }
    return _propose_or_apply(ctx, edit, preview, confirmed, apply_now)


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


KINDS = {
    Close: "closed",
    Reopen: "reopened",
    AddDoor: "door added",
    RemoveDoor: "door removed",
    MoveAsset: "moved",
    AddAsset: "arrived",
    SplitRoom: "wall added",
}


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
    """Undo the most recent change made in this conversation (a closure, reopening, new or removed door,
    wall, moved or delivered item). Colleagues' changes stay. A width change (remove_door + add_door) is two
    changes: undo twice."""
    d = ctx.deps
    edit = d.state.undo(by=d.who) if d.who else d.state.undo()
    return _json(
        {
            "undone": repr(edit) if edit else None,
            "all_closed": sorted(d.closed),
            "changes_left": len(d.state.edits),
        }
    )


def never_crash(tool):
    """A tool that never ends the run: a ModelRetry on its last allowed attempt, or any bug or data error
    (a room the plan lacks), comes back to the model as a result it can explain, not as a crash."""

    @functools.wraps(tool)
    def safe(ctx: RunContext[Deps], *args, **kwargs):
        try:  # sync tools run in worker threads, concurrently: what they share must be thread-safe
            return tool(ctx, *args, **kwargs)
        except (ApprovalRequired, CallDeferred):
            raise
        except ModelRetry as e:
            if not ctx.last_attempt:
                raise
            return _json(
                {"error": e.message, "note": "Stop retrying this; answer with what you know or ask."}
            )
        except Exception as e:  # noqa: BLE001 (any data error is reported, never a crash)
            return _json(
                {
                    "error": f"{type(e).__name__}: {e}",
                    "note": "The building data doesn't support this lookup. Tell the person what couldn't be "
                    "checked and why; don't guess.",
                }
            )

    return safe


TOOLS = [
    find,
    room_details,
    highlight_rooms,
    list_rooms,
    count_items,
    distance_3d,
    route,
    walk_directions,
    nearest,
    plan_visits,
    reachable_rooms,
    walk_distances,
    what_if,
    calculate,
    set_closed,
    add_door,
    remove_door,
    add_wall,
    move_item,
    item_history,
    building_changes,
    undo_last_change,
]
