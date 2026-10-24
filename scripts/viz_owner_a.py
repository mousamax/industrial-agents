"""Owner A playground: run a routing/query function and draw its inputs and outputs on the floor plan.

Writes one self-contained HTML file (inline SVG, one panel per floor) to agent_output/viz/ and opens it.

Main entry: a to-do number from notes/todo.md. Runs every case of that item's tests (the same questions
pytest samples, or --all / --case N), with expected vs actual and PASS/FAIL per case, golden drawn dashed.

    uv run python scripts/viz_owner_a.py 1.5                 # test_route on pytest's 6 sampled questions
    uv run python scripts/viz_owner_a.py 1.2 --case 3        # one case of test_route_step_free + a1_2
    uv run python scripts/viz_owner_a.py 2.2 --all --split train

Free-form modes for poking at inputs:

    uv run python scripts/viz_owner_a.py list proc-factory-dev-003
    uv run python scripts/viz_owner_a.py route proc-factory-dev-003 "Office 0.30" "Office 0.46"
    uv run python scripts/viz_owner_a.py route swiss-31 <from> <to> --step-free --closed <id>
    uv run python scripts/viz_owner_a.py tour proc-factory-dev-003 "Office 1.18" "Office 0.32" "Server room 1.13"
    uv run python scripts/viz_owner_a.py reach proc-factory-dev-003 "Warehouse zone H-C3" --step-free --min-width 1.1
    uv run python scripts/viz_owner_a.py errand proc-factory-dev-003 "Office 0.38" stove proc-factory-dev-003-F1 kitchen
    uv run python scripts/viz_owner_a.py what-if osm-way27917482 <first> <second> osm-way27917482-F3
    uv run python scripts/viz_owner_a.py question proc-factory-dev-003-H04   # ours vs golden

Spaces are accepted as ids or as text resolved with Building.resolve ("Office 0.29 (Ground floor)").
Add --no-open to only write the file. Read-only: never edits the building or the kit.
"""

from __future__ import annotations

import argparse
import dataclasses
import html
import itertools
import json
import math
import random
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.building import geometry, queries
from app.building.model import OUTSIDE, XY, Building, Opening, load_building, parse_building
from app.building.routing import DOOR_PREFIX, WALKING, Constraints, Route, RouteGraph
from app.building.state import AddDoor, BuildingState, Close, MoveAsset, Reopen
from app.settings import get_settings

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "agent_output" / "viz"
PANEL_W = 920
PANEL_MAX_H = 760
PAD = 18

TYPE_FILL = {
    "room": "#dbe7f3",
    "corridor": "#f4efd9",
    "hall": "#efe2cf",
    "stair": "#f6c9a4",
    "lift": "#d9c9ef",
    "outdoor": "#d5ecd2",
}
LEG_COLOURS = ["#d62728", "#1f77b4", "#9467bd", "#ff7f0e", "#2ca02c", "#8c564b", "#e377c2"]
HEAT_STOPS = ["#1a9850", "#91cf60", "#d9ef8b", "#fee08b", "#fc8d59", "#d73027"]
UNREACHABLE = "#e4e4e4"


# ---- overlay model ------------------------------------------------------------------------------------


@dataclass
class Marker:
    floor_id: str
    xy: XY
    colour: str
    text: str
    tip: str = ""
    shape: str = "circle"  # circle | square | diamond


@dataclass
class PathLine:
    node_path: list[str]
    colour: str
    label: str
    dashed: bool = False  # golden answers are drawn dashed under ours


@dataclass
class Overlay:
    fills: dict[str, str] = field(default_factory=dict)
    outlines: dict[str, str] = field(default_factory=dict)
    tips: dict[str, str] = field(default_factory=dict)
    paths: list[PathLine] = field(default_factory=list)
    markers: list[Marker] = field(default_factory=list)
    legend: list[tuple[str, str]] = field(default_factory=list)
    banner: str = ""  # big message, e.g. "NO ROUTE"
    floors: set[str] = field(default_factory=set)  # extra floors to show in to-do mode


@dataclass
class Report:
    title: str
    call: str
    inputs: list[tuple[str, str]]
    outputs: list[tuple[str, str]]
    compare: list[tuple[str, str, str, bool]] = field(default_factory=list)  # field, ours, golden, ok
    overlay: Overlay = field(default_factory=Overlay)

    @property
    def ok(self) -> bool:
        return all(ok for *_, ok in self.compare)


VERBOSE = [True]


def say(*parts: object) -> None:
    """Print from the free-form modes; silenced in to-do mode, which prints one line per case."""
    if VERBOSE[0]:
        print(*parts)


# ---- input resolution ---------------------------------------------------------------------------------


def space_id(b: Building, text: str) -> str:
    """A space id from an id, 'outside', or any text Building.resolve understands. Exits on 0 or >1 hits."""
    if text.strip().casefold() == OUTSIDE:
        return OUTSIDE
    hits = b.resolve(text)
    if len(hits) == 1:
        return hits[0].id
    if not hits:
        needle = text.casefold()
        close = [s for s in b.spaces.values() if needle in s.name.casefold()][:10]
        hint = "\n".join(f"  {s.id}  {b.label(s.id)}" for s in close) or "  (nothing similar)"
        sys.exit(f"no space matches {text!r}. Similar:\n{hint}")
    listing = "\n".join(f"  {s.id}  {b.label(s.id)}" for s in hits[:15])
    sys.exit(f"{text!r} is ambiguous ({len(hits)} spaces); use an id or add '(Floor name)':\n{listing}")


def element_id(b: Building, text: str) -> str:
    """A closable element: opening, connector or space id, a connector name ('Lift 1') or a space name."""
    if text in b.openings or text in b.connectors or text in b.spaces:
        return text
    named = [c.id for c in b.connectors.values() if c.name.casefold() == text.strip().casefold()]
    if len(named) == 1:
        return named[0]
    return space_id(b, text)


def floor_id(b: Building, text: str) -> str:
    """A floor id from an id, a floor name or a level number."""
    if text in b.floors:
        return text
    if re.fullmatch(r"-?\d+", text) and (f := b.floor_at_level(int(text))):
        return f.id
    for f in b.floors.values():
        if f.name.casefold() == text.strip().casefold():
            return f.id
    options = ", ".join(f"{f.id} ({f.name}, level {f.level})" for f in b.floors_by_level())
    sys.exit(f"no floor {text!r}. Floors: {options}")


def describe_element(b: Building, eid: str) -> str:
    if eid in b.connectors:
        c = b.connectors[eid]
        return f"{c.name} [{c.kind}{', step-free' if c.step_free else ''}]"
    if eid in b.openings:
        o = b.openings[eid]
        sides = " / ".join(b.label(s) for s in o.space_ids)
        return f"{o.kind} {o.width_m:.2f} m{' step-free' if o.step_free else ' STEP'} between {sides}"
    if eid in b.spaces or eid == OUTSIDE:
        return b.label(eid)
    return "?"


def lbl(b: Building, sid: str) -> str:
    return f"{b.label(sid)} [{sid}]"


def constraints_text(c: Constraints) -> str:
    parts = []
    if c.step_free:
        parts.append("step_free=True")
    if c.min_width_m is not None:
        parts.append(f"min_width_m={c.min_width_m}")
    if c.closed:
        parts.append(f"closed=frozenset({sorted(c.closed)!r})")
    return f"Constraints({', '.join(parts)})" if parts else "WALKING"


def make_constraints(b: Building, args: argparse.Namespace) -> Constraints:
    closed = frozenset(element_id(b, e) for e in (args.closed or []))
    return Constraints(step_free=args.step_free, min_width_m=args.min_width, closed=closed)


# ---- geometry helpers ---------------------------------------------------------------------------------


def node_position(b: Building, node: str) -> tuple[str, XY] | None:
    if node.startswith(DOOR_PREFIX):
        o = b.openings.get(node.removeprefix(DOOR_PREFIX))
        return (o.floor_id, o.position) if o else None
    if node in b.spaces:
        s = b.spaces[node]
        return s.floor_id, s.anchor
    return None  # outside


def floors_touched(b: Building, node_path: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for n in node_path:
        if (p := node_position(b, n)) is not None:
            seen[p[0]] = None
    return [b.floors[f].name for f in seen]


def heat(t: float) -> str:
    t = min(max(t, 0.0), 1.0) * (len(HEAT_STOPS) - 1)
    i = min(int(t), len(HEAT_STOPS) - 2)
    f = t - i
    a, c = HEAT_STOPS[i], HEAT_STOPS[i + 1]
    rgb = [round(int(a[k : k + 2], 16) * (1 - f) + int(c[k : k + 2], 16) * f) for k in (1, 3, 5)]
    return "#" + "".join(f"{v:02x}" for v in rgb)


def route_rows(b: Building, route: Route, prefix: str = "") -> list[tuple[str, str]]:
    connectors = [e for e in dict.fromkeys(route.element_path) if e in b.connectors]
    return [
        (f"{prefix}length_m", f"{route.length_m}"),
        (f"{prefix}space_path ({len(route.space_path)})", " → ".join(b.label(s) for s in route.space_path)),
        (f"{prefix}floors", " → ".join(floors_touched(b, route.node_path)) or "-"),
        (f"{prefix}connectors", ", ".join(describe_element(b, c) for c in connectors) or "none"),
        (f"{prefix}element_path ({len(route.element_path)})", " ".join(route.element_path)),
    ]


def endpoint_markers(b: Building, start: str, goal: str) -> list[Marker]:
    out = []
    for sid, colour, text in ((start, "#2ca02c", "A"), (goal, "#d62728", "B")):
        if sid in b.spaces:
            s = b.spaces[sid]
            out.append(Marker(s.floor_id, s.anchor, colour, text, b.label(sid), "square"))
    return out


# ---- modes --------------------------------------------------------------------------------------------


def do_route(b: Building, start: str, goal: str, c: Constraints, title: str = "route") -> Report:
    route = RouteGraph(b, c).shortest(start, goal)
    ov = Overlay(markers=endpoint_markers(b, start, goal))
    for sid in (start, goal):
        ov.outlines[sid] = "#111"
    inputs = [("from", lbl(b, start)), ("to", lbl(b, goal)), ("constraints", constraints_text(c))]
    inputs += [("closed", f"{e}: {describe_element(b, e)}") for e in sorted(c.closed)]
    if route is None:
        ov.banner = "NO ROUTE under these constraints"
        outputs = [("result", "None (unreachable)")]
    else:
        ov.paths.append(PathLine(route.node_path, LEG_COLOURS[0], f"{route.length_m} m"))
        outputs = route_rows(b, route)
    say(f"{title}: {b.label(start)} -> {b.label(goal)} [{constraints_text(c)}]")
    say(f"  length: {route.length_m if route else 'NO ROUTE'}")
    if route:
        say("  path: " + " -> ".join(b.label(s) for s in route.space_path))
    return Report(
        f"{title} · {b.id}",
        f"RouteGraph(b, {constraints_text(c)}).shortest({start!r}, {goal!r})",
        inputs,
        outputs,
        overlay=ov,
    )


def do_tour(b: Building, stops: list[str], c: Constraints = WALKING) -> Report:
    tour = RouteGraph(b, c).tour(stops)
    ov = Overlay()
    for k, sid in enumerate(stops):
        if sid in b.spaces:
            s = b.spaces[sid]
            ov.markers.append(Marker(s.floor_id, s.anchor, "#111", str(k + 1), b.label(sid), "square"))
            ov.outlines[sid] = "#111"
    inputs = [(f"stop {k + 1}", lbl(b, s)) for k, s in enumerate(stops)]
    inputs.append(("constraints", constraints_text(c)))
    if tour is None:
        ov.banner = "NO TOUR: at least one leg is unreachable"
        outputs = [("result", "None")]
    else:
        outputs = [("total length_m", f"{tour.length_m}")]
        for k, leg in enumerate(tour.legs):
            colour = LEG_COLOURS[k % len(LEG_COLOURS)]
            ov.paths.append(PathLine(leg.node_path, colour, f"leg {k + 1}: {leg.length_m} m"))
            ov.legend.append(
                (colour, f"leg {k + 1}: {b.label(leg.from_space_id)} → {b.label(leg.to_space_id)}")
            )
            outputs += route_rows(b, leg, prefix=f"leg {k + 1} ")[:3]
    say(f"tour over {len(stops)} stops: {tour.length_m if tour else 'NO TOUR'}")
    if tour:
        for k, leg in enumerate(tour.legs):
            say(f"  leg {k + 1}: {leg.length_m} m  " + " -> ".join(b.label(s) for s in leg.space_path))
    return Report(
        f"tour · {b.id}",
        f"RouteGraph(b, {constraints_text(c)}).tour({stops!r})",
        inputs,
        outputs,
        overlay=ov,
    )


def do_reach(
    b: Building, start: str, c: Constraints, count_floor: str | None = None, counted: list[str] | None = None
) -> Report:
    graph = RouteGraph(b, c)
    dist = graph.distances_from([start])
    reach = graph.reachable(start)
    ov = Overlay(markers=endpoint_markers(b, start, start)[:1])
    finite = [d for s, d in dist.items() if s in b.spaces]
    top = max(finite) if finite else 1.0
    for sid in b.spaces:
        if sid in dist:
            ov.fills[sid] = heat(dist[sid] / top if top else 0.0)
            ov.tips[sid] = f"{dist[sid]:.2f} m from start"
        else:
            ov.fills[sid] = UNREACHABLE
            ov.tips[sid] = "unreachable"
    for sid in counted or []:
        ov.outlines[sid] = "#111"
    ov.legend = [(heat(0), "0 m"), (heat(0.5), f"{top / 2:.0f} m"), (heat(1), f"{top:.0f} m (max)")]
    ov.legend.append((UNREACHABLE, "unreachable"))
    if counted is not None:
        ov.legend.append(("#ffffff", "black outline = counted room"))
    per_floor = Counter(b.spaces[s].floor_id for s in reach)
    rooms_per_floor = Counter(b.spaces[s].floor_id for s in reach if b.spaces[s].type == "room")
    inputs = [("start", lbl(b, start)), ("constraints", constraints_text(c))]
    inputs += [("closed", f"{e}: {describe_element(b, e)}") for e in sorted(c.closed)]
    outputs = [
        ("reachable spaces", f"{len(reach)} of {len(b.spaces)}"),
        ("max distance", f"{top:.2f} m"),
        ("outside reachable", str(OUTSIDE in dist)),
    ]
    for f in b.floors_by_level():
        n_rooms = len(b.spaces_on(f.id, type="room"))
        outputs.append(
            (f"{f.name}", f"{per_floor[f.id]} spaces, {rooms_per_floor[f.id]}/{n_rooms} rooms (type=room)")
        )
    if count_floor is not None and counted is not None:
        outputs.insert(0, (f"counted rooms on {b.floors[count_floor].name}", str(len(counted))))
    say(f"reach from {b.label(start)} [{constraints_text(c)}]: {len(reach)} spaces, max {top:.2f} m")
    for f in b.floors_by_level():
        say(f"  {f.name}: {per_floor[f.id]} spaces, {rooms_per_floor[f.id]} rooms")
    return Report(
        f"reach · {b.id}",
        f"RouteGraph(b, {constraints_text(c)}).distances_from([{start!r}])  # + .reachable()",
        inputs,
        outputs,
        overlay=ov,
    )


def do_errand(b: Building, start: str, asset_type: str, fid: str, room_type: str) -> Report:
    if not b.assets_of_type(asset_type):
        types = ", ".join(sorted({a.type for a in b.assets.values()}))
        sys.exit(f"no asset of type {asset_type!r}. Types here: {types}")
    result = queries.errand(b, start, asset_type, fid, room_type)
    graph = RouteGraph(b)
    ov = Overlay(markers=endpoint_markers(b, start, start)[:1])
    ov.outlines[start] = "#111"
    for a in b.assets_of_type(asset_type):
        ov.markers.append(
            Marker(a.floor_id, a.position, "#ff7f0e", "", f"{a.name} in {b.label(a.space_id)}", "diamond")
        )
    for s, _area in geometry.rooms_by_area(b, fid, room_type):
        ov.outlines.setdefault(s.id, "#9467bd")
    inputs = [
        ("start", lbl(b, start)),
        ("asset_type", f"{asset_type} ({len(b.assets_of_type(asset_type))} in building)"),
        ("floor_id", f"{fid} ({b.floors[fid].name})"),
        ("room_type", room_type),
    ]
    call = f"queries.errand(b, {start!r}, {asset_type!r}, {fid!r}, {room_type!r})"
    if result is None:
        ov.banner = "NO ERRAND (no reachable item, no such room, or second leg unreachable)"
        say("errand: None")
        return Report(f"errand · {b.id}", call, inputs, [("result", "None")], overlay=ov)
    leg1 = graph.shortest(start, result.pickup_space_id)
    leg2 = graph.shortest(result.pickup_space_id, result.target_space_id)
    for k, leg in enumerate((leg1, leg2)):
        if leg is not None:
            ov.paths.append(PathLine(leg.node_path, LEG_COLOURS[k], f"leg {k + 1}: {leg.length_m} m"))
    ov.outlines[result.pickup_space_id] = "#ff7f0e"
    ov.outlines[result.target_space_id] = "#d62728"
    for sid, colour, text in (
        (result.pickup_space_id, "#ff7f0e", "P"),
        (result.target_space_id, "#d62728", "T"),
    ):
        s = b.spaces[sid]
        ov.markers.append(Marker(s.floor_id, s.anchor, colour, text, b.label(sid), "square"))
    ov.legend = [
        ("#ff7f0e", f"{asset_type} (diamond) / pickup P"),
        ("#9467bd", f"{room_type} rooms on floor"),
        (LEG_COLOURS[0], f"leg 1 {leg1.length_m if leg1 else '?'} m"),
        (LEG_COLOURS[1], f"leg 2 {leg2.length_m if leg2 else '?'} m"),
    ]
    area = dict((s.id, a) for s, a in geometry.rooms_by_area(b, fid, room_type)).get(result.target_space_id)
    outputs = [
        ("pickup_space_id", lbl(b, result.pickup_space_id)),
        (
            "target_space_id",
            f"{lbl(b, result.target_space_id)}, {area:.2f} m²" if area else lbl(b, result.target_space_id),
        ),
        (
            "length_m",
            f"{result.length_m}  (= {leg1.length_m if leg1 else '?'} + {leg2.length_m if leg2 else '?'})",
        ),
    ]
    say(
        f"errand: pickup {b.label(result.pickup_space_id)}, target {b.label(result.target_space_id)}, {result.length_m} m"
    )
    return Report(f"errand · {b.id}", call, inputs, outputs, overlay=ov)


def do_what_if(b: Building, first: str, second: str, fid: str) -> Report:
    call = f"queries.what_if_door(b, {first!r}, {second!r}, {fid!r})"
    inputs = [
        ("first", lbl(b, first)),
        ("second", lbl(b, second)),
        ("floor_id", f"{fid} ({b.floors[fid].name})"),
    ]
    ov = Overlay()
    for sid in (first, second):
        ov.outlines[sid] = "#1f77b4"
    midpoint = geometry.shared_wall_midpoint(b, first, second)
    if midpoint is None:
        ov.banner = "NO SHARED WALL: what_if_door raises ValueError"
        say("what-if: no shared wall")
        return Report(
            f"what-if · {b.id}", call, inputs, [("result", "ValueError: no shared wall")], overlay=ov
        )
    gained = queries.what_if_door(b, first, second, fid)
    # Recompute before/after for tooltips (same model as the query, only for display).
    door_floor = b.spaces[first].floor_id
    door = Opening("viz-whatif", door_floor, (first, second), "door", midpoint, 0.9, True, True)
    with_door = dataclasses.replace(b, openings={**b.openings, door.id: door})
    before = RouteGraph(b).distances_from([OUTSIDE])
    after = RouteGraph(with_door).distances_from([OUTSIDE])
    for sid in b.spaces:
        old, new = before.get(sid, math.inf), after.get(sid, math.inf)
        ov.tips[sid] = f"to nearest entrance: {old:.2f} → {new:.2f} m (gain {old - new:.2f})"
        if sid in gained:
            ov.fills[sid] = "#7fd17f"
            ov.outlines[sid] = "#1a7f1a"
        elif old - new > 1e-9:
            ov.fills[sid] = "#d8f0d8"
    ov.markers.append(
        Marker(door_floor, midpoint, "#e31a1c", "NEW", f"new 0.9 m step-free door {midpoint}", "diamond")
    )
    ov.legend = [
        ("#7fd17f", f"counted: room on {b.floors[fid].name} gaining > 1 m"),
        ("#d8f0d8", "any other space that gains"),
        ("#e31a1c", "new door"),
    ]
    outputs = [
        ("door position", f"({midpoint[0]:.2f}, {midpoint[1]:.2f}) on {b.floors[door_floor].name}"),
        ("rooms gaining > 1 m", str(len(gained))),
        ("rooms", ", ".join(b.label(s) for s in gained) or "none"),
    ]
    say(
        f"what-if door {b.label(first)} | {b.label(second)}: {len(gained)} rooms gain on {b.floors[fid].name}"
    )
    return Report(f"what-if · {b.id}", call, inputs, outputs, overlay=ov)


def do_list(b: Building) -> None:
    print(f"{b.id}  {b.name}  source={b.source}  spaces={len(b.spaces)} openings={len(b.openings)}")
    print("floors:")
    for f in b.floors_by_level():
        n = Counter(s.type for s in b.spaces_on(f.id))
        print(f"  {f.id:<28} level {f.level:>2}  {f.name:<14} {dict(n)}")
    print("sample spaces (up to 6 per floor):")
    for f in b.floors_by_level():
        for s in b.spaces_on(f.id)[:6]:
            print(f"  {s.id:<32} {s.type:<9} {b.label(s.id)}")
    print("connectors:")
    for c in b.connectors.values():
        floors = ",".join(str(b.floors[fl].level) for fl, _ in c.stops)
        print(f"  {c.id:<24} {c.kind:<6} step_free={c.step_free!s:<5} levels {floors}  {c.name}")
    entrances = [o for o in b.openings.values() if o.is_entrance]
    print(f"entrances: {len(entrances)} ({sum(o.step_free for o in entrances)} step-free)")
    for o in entrances[:8]:
        inside = o.other_side(OUTSIDE)
        print(f"  {o.id:<28} {o.width_m:.2f} m step_free={o.step_free!s:<5} into {b.label(inside)}")
    print(
        "asset types:",
        ", ".join(f"{t}({n})" for t, n in sorted(Counter(a.type for a in b.assets.values()).items())),
    )
    rooms = Counter(s.room_type for s in b.spaces.values() if s.type == "room")
    print("room types (type=room):", ", ".join(f"{t}({n})" for t, n in sorted(rooms.items())))


# ---- golden questions ---------------------------------------------------------------------------------


def find_question(qid: str, splits: list[str]) -> dict[str, Any]:
    data = get_settings().synera_kit_dir / "data"
    for split in splits:
        for path in sorted((data / split).glob("*/questions*.jsonl")):
            if qid.rsplit("-", 1)[0] != path.parent.name:
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip() and (q := json.loads(line))["id"] == qid:
                    return q
    sys.exit(f"question {qid!r} not found in {splits}")


def length_ok(ours: float, golden: float) -> bool:
    return abs(ours - golden) <= max(0.01 * golden, 0.05)


def close_to(value: float, answer: dict[str, Any]) -> bool:
    tol = max(
        abs(float(answer["value"])) * float(answer["relative_tolerance"]), float(answer["absolute_tolerance"])
    )
    return abs(value - float(answer["value"])) <= tol


def do_question(q: dict[str, Any]) -> tuple[Building, Report]:
    b, kind, a, i = load_building(q["building_id"]), q["kind"], q["answer"], q.get("inputs", {})
    say(f"{q['id']} [{kind}] {q['question']}")
    if kind in ("route", "route_step_free", "route_with_closure"):
        c = Constraints(step_free=kind == "route_step_free")
        if kind == "route_with_closure":
            c = c.with_closed(a["closed_element_id"])
        rep = do_route(b, a["from_space_id"], a["to_space_id"], c, title=kind)
        route = RouteGraph(b, c).shortest(a["from_space_id"], a["to_space_id"])
        if a.get("reachable") is False:
            rep.compare.append(("reachable", str(route is not None), "False", route is None))
        else:
            ours = route.length_m if route else None
            ok = ours is not None and length_ok(ours, a["optimal_length_m"])
            rep.compare.append(("length_m (±1%)", str(ours), str(a["optimal_length_m"]), ok))
            same = route is not None and route.space_path == a["space_path"]
            rep.compare.append(("space_path identical", str(same), "(info only)", True))
    elif kind == "tour":
        rep = do_tour(b, a["stops"])
        tour = RouteGraph(b).tour(a["stops"])
        ours = tour.length_m if tour else None
        ok = ours is not None and length_ok(ours, a["optimal_length_m"])
        rep.compare.append(("length_m (±1%)", str(ours), str(a["optimal_length_m"]), ok))
    elif kind == "errand":
        rep = do_errand(b, i["start"], i["asset_type"], i["floor_id"], i["room_type"])
        r = queries.errand(b, i["start"], i["asset_type"], i["floor_id"], i["room_type"])
        rep.compare += [
            (
                "via (pickup)",
                r and b.label(r.pickup_space_id) or "None",
                b.label(a["via"]),
                bool(r and r.pickup_space_id == a["via"]),
            ),
            (
                "target",
                r and b.label(r.target_space_id) or "None",
                b.label(a["target"]),
                bool(r and r.target_space_id == a["target"]),
            ),
            ("length_m", str(r and r.length_m), str(a["value"]), bool(r and close_to(r.length_m, a))),
        ]
    elif kind == "wide_delivery_count":
        rooms = queries.deliverable_rooms(b, i["start"], i["floor_id"], i["min_width"])
        c = Constraints(step_free=True, min_width_m=i["min_width"])
        rep = do_reach(b, i["start"], c, i["floor_id"], rooms)
        rep.call = f"queries.deliverable_rooms(b, {i['start']!r}, {i['floor_id']!r}, {i['min_width']})"
        rep.compare.append(("room count", str(len(rooms)), str(a["value"]), len(rooms) == a["value"]))
    elif kind == "step_free_reachable_count":
        rooms = queries.step_free_reachable_rooms(b, i["start"], i["floor_id"], [i["closed"]])
        c = Constraints(step_free=True, closed=frozenset([i["closed"]]))
        rep = do_reach(b, i["start"], c, i["floor_id"], rooms)
        rep.call = (
            f"queries.step_free_reachable_rooms(b, {i['start']!r}, {i['floor_id']!r}, [{i['closed']!r}])"
        )
        rep.compare.append(("room count", str(len(rooms)), str(a["value"]), len(rooms) == a["value"]))
    elif kind == "what_if_door":
        rep = do_what_if(b, i["first"], i["second"], i["floor_id"])
        try:
            n = len(queries.what_if_door(b, i["first"], i["second"], i["floor_id"]))
        except ValueError:
            n = -1
        rep.compare.append(("room count", str(n), str(a["value"]), n == a["value"]))
    else:
        sys.exit(
            f"{kind!r} is not an Owner A kind (route*, tour, errand, wide_delivery_count, "
            "step_free_reachable_count, what_if_door)"
        )
    rep.title = f"{q['id']} · {kind}"
    rep.inputs.insert(0, ("question", q["question"]))
    for name, ours, golden, ok in rep.compare:
        say(f"  {'PASS' if ok else 'FAIL'}  {name}: ours {ours} | golden {golden}")
    return b, rep


# ---- to-do mode: notes/todo.md item -> its tests -> one case per test parameter -----------------------

TODO_FILE = ROOT / "notes" / "todo.md"
TODO_ITEM = re.compile(
    r"^\s*- \[[ xX]\] \*\*(?P<id>[\d.]+)\*\* (?P<text>.+)$"
)  # same as tests/building/conftest.py
TODO_TEST = re.compile(r"`(test_\w+)`")
GOLDEN_SEED = 7  # tests/building/test_golden.py samples 6 questions per kind with Random(7)
GOLDEN_SAMPLE = 6
PRODUCTION_BAY = "production_zone"  # tests/building/test_owner_a.py constants
FORKLIFT_MIN_WIDTH_M = 3.0
LIFT_WAIT_M, LIFT_PER_M, STAIRS_PER_M = 15.0, 1.0, 2.0
GOLD = "#b8860b"


@dataclass(frozen=True)
class TodoItem:
    id: str
    text: str
    tests: list[str]


def todo_items() -> dict[str, TodoItem]:
    items = {}
    for line in TODO_FILE.read_text(encoding="utf-8").splitlines():
        if m := TODO_ITEM.match(line):
            text, _, tests = m["text"].partition(" · tests:")
            items[m["id"]] = TodoItem(m["id"], re.sub(r"[`*]", "", text).strip(), TODO_TEST.findall(tests))
    return items


@dataclass
class Golden:
    """The question sets test_golden.py runs on: same files, same order, same seeded sample."""

    benchmark: dict[str, list[dict[str, Any]]]
    warmup: dict[str, list[dict[str, Any]]]

    @classmethod
    def load(cls, split: str, sample_all: bool) -> Golden:
        data = get_settings().synera_kit_dir / "data" / split

        def questions(filename: str) -> dict[str, list[dict[str, Any]]]:
            by_kind: dict[str, list[dict[str, Any]]] = {}
            for path in sorted(data.glob(f"*/{filename}")):
                for line in path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        q = json.loads(line)
                        by_kind.setdefault(q["kind"], []).append(q)
            if not sample_all:
                rng = random.Random(GOLDEN_SEED)
                by_kind = {k: rng.sample(v, min(GOLDEN_SAMPLE, len(v))) for k, v in by_kind.items()}
            return by_kind

        return cls(questions("questions.jsonl"), questions("questions_basic.jsonl"))

    def reachable_routes(self, kind: str = "route") -> list[dict[str, Any]]:
        return [q for q in self.warmup.get(kind, []) if q["answer"].get("reachable") is not False]

    def route_buildings(self) -> list[str]:
        return sorted({q["building_id"] for q in self.warmup.get("route", [])})


def golden_node_path(b: Building, a: dict[str, Any]) -> list[str]:
    """Rebuild graph nodes from a golden space_path + element_path (openings twice, connectors once)."""
    spaces, elements = a.get("space_path", []), a.get("element_path", [])
    nodes, k, si = spaces[:1], 0, 1
    while k < len(elements) and si < len(spaces):
        e = elements[k]
        if e in b.openings:
            nodes.append(DOOR_PREFIX + e)
            k += 2 if k + 1 < len(elements) and elements[k + 1] == e else 1
        else:
            k += 1
        nodes.append(spaces[si])
        si += 1
    return nodes if si == len(spaces) else list(spaces)


def mark_golden_route(b: Building, ov: Overlay, a: dict[str, Any], label: str = "golden route") -> None:
    if a.get("space_path"):
        length = a.get("optimal_length_m", "?")
        ov.paths.append(PathLine(golden_node_path(b, a), GOLD, f"{label}: {length} m", dashed=True))
        ov.legend.append((GOLD, f"{label} (dashed, {length} m)"))


def mark_space(
    b: Building, ov: Overlay, sid: str, text: str, colour: str = GOLD, shape: str = "ring"
) -> None:
    if sid in b.spaces:
        s = b.spaces[sid]
        ov.markers.append(Marker(s.floor_id, s.anchor, colour, text, f"{text}: {b.label(sid)}", shape))


def mark_element(b: Building, ov: Overlay, eid: str, text: str, colour: str = "#111") -> None:
    """An X on a closed/offending opening, connector (every stop) or space."""
    tip = f"{text}: {eid} {describe_element(b, eid)}"
    if eid in b.openings:
        o = b.openings[eid]
        ov.markers.append(Marker(o.floor_id, o.position, colour, text, tip, "x"))
    elif eid in b.connectors:
        for fid, sid in b.connectors[eid].stops:
            if sid in b.spaces:
                ov.markers.append(Marker(fid, b.spaces[sid].anchor, colour, text, tip, "x"))
    elif eid in b.spaces:
        ov.outlines[eid] = colour
        mark_space(b, ov, eid, text, colour, "x")


def golden_route_checks(route: Route | None, a: dict[str, Any]) -> list[tuple[str, str, str, bool]]:
    """test_golden._assert_route as rows."""
    if a.get("reachable") is False:
        return [
            (
                "route is None (unreachable)",
                "None" if route is None else f"{route.length_m} m",
                "None",
                route is None,
            )
        ]
    if route is None:
        return [("a route exists", "None", f"{a['optimal_length_m']} m", False)]
    return [
        (
            "length_m within 1% (≥0.05 m)",
            str(route.length_m),
            str(a["optimal_length_m"]),
            length_ok(route.length_m, a["optimal_length_m"]),
        ),
        (
            "starts at from_space_id",
            b_id(route.space_path[0]),
            b_id(a["from_space_id"]),
            route.space_path[0] == a["from_space_id"],
        ),
        (
            "ends at to_space_id",
            b_id(route.space_path[-1]),
            b_id(a["to_space_id"]),
            route.space_path[-1] == a["to_space_id"],
        ),
    ]


def b_id(sid: str) -> str:
    return sid.rsplit("-", 1)[-1] if sid != OUTSIDE else sid


def warmup_route_case(
    q: dict[str, Any], c: Constraints, *, golden_checks: bool, golden_label: str = "golden route"
) -> tuple[Building, Report, Route | None]:
    """Shared by every warm-up-route test: our route under c, the golden route dashed, A/B endpoints."""
    b, a = load_building(q["building_id"]), q["answer"]
    rep = do_route(b, a["from_space_id"], a["to_space_id"], c)
    route = RouteGraph(b, c).shortest(a["from_space_id"], a["to_space_id"])
    mark_golden_route(b, rep.overlay, a, golden_label)
    if rep.overlay.paths and not rep.overlay.paths[0].dashed:
        rep.overlay.legend.insert(0, (LEG_COLOURS[0], "our route (solid)"))
    rep.inputs.insert(0, ("question", q["question"]))
    golden = (
        f"{a.get('optimal_length_m', 'unreachable')} m" if a.get("reachable") is not False else "unreachable"
    )
    rep.inputs.append(("golden answer", golden))
    if golden_checks:
        rep.compare += golden_route_checks(route, a)
    return b, rep, route


# One handler per test name. Each returns (building, report) for one parametrized case.
Handler = Any
CaseFn = tuple[str, Handler]  # (pytest-style case id, zero-arg callable)


def t_route(q: dict[str, Any]) -> tuple[Building, Report]:
    b, rep, _ = warmup_route_case(q, WALKING, golden_checks=True)
    return b, rep


def t_route_step_free(q: dict[str, Any]) -> tuple[Building, Report]:
    b, rep, _ = warmup_route_case(q, Constraints(step_free=True), golden_checks=True)
    return b, rep


def t_route_with_closure(q: dict[str, Any]) -> tuple[Building, Report]:
    closed = q["answer"]["closed_element_id"]
    b, rep, _ = warmup_route_case(q, WALKING.with_closed(closed), golden_checks=True)
    mark_element(b, rep.overlay, closed, "closed")
    return b, rep


def t_route_tour(q: dict[str, Any]) -> tuple[Building, Report]:
    b, a = load_building(q["building_id"]), q["answer"]
    rep = do_tour(b, a["stops"])
    tour = RouteGraph(b).tour(a["stops"])
    for k, leg in enumerate(a.get("legs", [])):
        mark_golden_route(b, rep.overlay, leg, f"golden leg {k + 1}")
    rep.inputs.insert(0, ("question", q["question"]))
    rep.compare += [
        ("tour is not None", str(tour is not None), "True", tour is not None),
        (
            "length_m within 1%",
            str(tour and tour.length_m),
            str(a["optimal_length_m"]),
            bool(tour and length_ok(tour.length_m, a["optimal_length_m"])),
        ),
    ]
    return b, rep


def t_a1_1(building_id: str) -> tuple[Building, Report]:
    b = load_building(building_id)
    rep = do_reach(b, OUTSIDE, WALKING)
    rep.title = "distances from outside (walking)"
    rep.call = (
        "RouteGraph(b, c) for c in (WALKING, Constraints(step_free=True), Constraints(min_width_m=1.0))"
    )
    rep.inputs = [("building", building_id)]
    for c in (WALKING, Constraints(step_free=True), Constraints(min_width_m=1.0)):
        try:
            g = RouteGraph(b, c)
            n = len(g.reachable(OUTSIDE))
            rep.compare.append(
                (
                    f"RouteGraph(b, {constraints_text(c)}) builds",
                    f"ok, {n} spaces reachable from outside",
                    "no exception",
                    True,
                )
            )
        except Exception as e:  # noqa: BLE001 - the test only asserts that it builds
            rep.compare.append(
                (f"RouteGraph(b, {constraints_text(c)}) builds", repr(e), "no exception", False)
            )
    return b, rep


def t_a1_2(q: dict[str, Any]) -> tuple[Building, Report]:
    b, rep, route = warmup_route_case(
        q, Constraints(step_free=True), golden_checks=False, golden_label="golden step-free route"
    )
    rep.compare.append(
        ("a route exists", "None" if route is None else f"{route.length_m} m", "a route", route is not None)
    )
    if route is not None:
        bad = [
            e
            for e in route.element_path
            if not getattr(b.openings.get(e) or b.connectors.get(e), "step_free", False)
        ]
        for e in dict.fromkeys(bad):
            mark_element(b, rep.overlay, e, "step", "#d62728")
        rep.compare.append(
            (
                "every element step-free",
                ", ".join(dict.fromkeys(bad)) or "all step-free",
                "all step-free",
                not bad,
            )
        )
    return b, rep


def t_a1_3(q: dict[str, Any]) -> tuple[Building, Report]:
    closed = q["answer"]["closed_element_id"]
    b, rep, route = warmup_route_case(q, WALKING.with_closed(closed), golden_checks=False)
    mark_element(b, rep.overlay, closed, "closed")
    used = route is not None and (closed in route.element_path or closed in route.space_path)
    rep.compare.append(
        (
            f"{closed} not on the route",
            "USED" if used else ("no route" if route is None else "not used"),
            "not used",
            not used,
        )
    )
    return b, rep


def t_a1_4(q: dict[str, Any]) -> tuple[Building, Report]:
    b, rep, route = warmup_route_case(
        q,
        Constraints(min_width_m=1.1),
        golden_checks=False,
        golden_label="golden walking route (no width limit)",
    )
    narrow = (
        []
        if route is None
        else [e for e in dict.fromkeys(route.element_path) if e in b.openings and b.openings[e].width_m < 1.1]
    )
    for e in narrow:
        mark_element(b, rep.overlay, e, f"{b.openings[e].width_m:.2f} m", "#d62728")
    shown = "no route (allowed)" if route is None else (", ".join(narrow) or "none")
    rep.compare.append(("openings narrower than 1.1 m used", shown, "none", not narrow))
    return b, rep


def t_a1_7(q: dict[str, Any]) -> tuple[Building, Report]:
    b, rep, route = warmup_route_case(q, WALKING, golden_checks=False)
    a = q["answer"]
    dist = RouteGraph(b).distances_from([a["from_space_id"]])
    d = dist.get(a["to_space_id"])
    rep.call += f"\nRouteGraph(b, WALKING).distances_from([{a['from_space_id']!r}])[{a['to_space_id']!r}]"
    rep.compare += [
        ("to_space_id in distances", str(d is not None), "True", d is not None),
        (
            "distances_from == shortest (1%)",
            f"{d}",
            f"{route.length_m if route else None}",
            bool(d is not None and route and length_ok(d, route.length_m)),
        ),
    ]
    return b, rep


def t_a1_8(q: dict[str, Any]) -> tuple[Building, Report]:
    b, a = load_building(q["building_id"]), q["answer"]
    c = Constraints(step_free=True)
    rep = do_reach(b, a["from_space_id"], c)
    reached = RouteGraph(b, c).reachable(a["from_space_id"])
    expected = a.get("reachable") is not False
    rep.overlay.outlines[a["to_space_id"]] = "#111"
    mark_space(b, rep.overlay, a["to_space_id"], "goal")
    mark_golden_route(b, rep.overlay, a, "golden step-free route")
    rep.inputs = [
        ("question", q["question"]),
        ("from", lbl(b, a["from_space_id"])),
        ("to", lbl(b, a["to_space_id"])),
        ("constraints", "step_free=True"),
    ]
    rep.call = f"RouteGraph(b, Constraints(step_free=True)).reachable({a['from_space_id']!r})"
    got = a["to_space_id"] in reached
    rep.compare.append(("to_space_id in reachable", str(got), str(expected), got == expected))
    return b, rep


def t_a2_1(q: dict[str, Any]) -> tuple[Building, Report]:
    b, i, a = load_building(q["building_id"]), q["inputs"], q["answer"]
    found = queries.nearest_with_asset(b, i["start"], i["asset_type"])
    ov = Overlay(markers=endpoint_markers(b, i["start"], i["start"])[:1])
    for asset in b.assets_of_type(i["asset_type"]):
        ov.markers.append(
            Marker(
                asset.floor_id,
                asset.position,
                "#ff7f0e",
                "",
                f"{asset.name} in {b.label(asset.space_id)}",
                "diamond",
            )
        )
    if found:
        leg = RouteGraph(b).shortest(i["start"], found[0])
        if leg:
            ov.paths.append(PathLine(leg.node_path, LEG_COLOURS[0], f"start → our pickup {leg.length_m} m"))
        mark_space(b, ov, found[0], "ours", "#ff7f0e", "square")
        ov.outlines[found[0]] = "#ff7f0e"
    mark_space(b, ov, a["via"], "golden via")
    ov.legend = [("#ff7f0e", f"{i['asset_type']} (diamond) / our pickup"), (GOLD, "golden via (ring)")]
    rep = Report(
        "nearest_with_asset",
        f"queries.nearest_with_asset(b, {i['start']!r}, {i['asset_type']!r})",
        [("question", q["question"]), ("start", lbl(b, i["start"])), ("asset_type", i["asset_type"])],
        [("result", f"({lbl(b, found[0])}, {found[1]} m)" if found else "None")],
        overlay=ov,
    )
    rep.compare += [
        ("found is not None", str(found is not None), "True", found is not None),
        (
            "space_id == via",
            b.label(found[0]) if found else "None",
            b.label(a["via"]),
            bool(found and found[0] == a["via"]),
        ),
        ("metres >= 0", str(found and found[1]), ">= 0", bool(found and found[1] >= 0)),
    ]
    return b, rep


def t_errand(q: dict[str, Any]) -> tuple[Building, Report]:
    b, rep = do_question(q)
    a = q["answer"]
    mark_space(b, rep.overlay, a["via"], "golden via")
    mark_space(b, rep.overlay, a["target"], "golden target")
    rep.overlay.legend.append((GOLD, "golden via / target (rings)"))
    return b, rep


def t_question(q: dict[str, Any]) -> tuple[Building, Report]:
    return do_question(q)


def state_case(g: Golden) -> tuple[Building, Opening, Any, str]:
    """test_owner_a._state_case: first sampled route building, first inside door, first asset."""
    b = load_building(g.route_buildings()[0])
    door = next(o for o in b.openings.values() if OUTSIDE not in o.space_ids)
    asset = next(iter(b.assets.values()))
    elsewhere = next(s.id for s in b.spaces_on(asset.floor_id) if s.id != asset.space_id)
    return b, door, asset, elsewhere


def state_report(
    b: Building, title: str, call: str, door: Opening, routes: list[tuple[str, Route | None]]
) -> Report:
    ov = Overlay()
    first, second = door.space_ids
    for k, (name, route) in enumerate(routes):
        colour = LEG_COLOURS[k % len(LEG_COLOURS)]
        if route is not None:
            ov.paths.append(
                PathLine(
                    route.node_path, colour, f"{name}: {route.length_m} m", dashed=k == 0 and len(routes) > 1
                )
            )
        ov.legend.append((colour, f"{name}: {route.length_m if route else 'no route'} m"))
    mark_space(b, ov, first, "A", "#2ca02c", "square")
    mark_space(b, ov, second, "B", "#d62728", "square")
    inputs = [
        ("building", b.id),
        ("door (first inside opening)", f"{door.id}: {describe_element(b, door.id)}"),
    ]
    outputs = [
        (
            name,
            f"{route.length_m} m via {' → '.join(b.label(s) for s in route.space_path)}"
            if route
            else "no route",
        )
        for name, route in routes
    ]
    return Report(title, call, inputs, outputs, overlay=ov)


def t_a3_1(g: Golden) -> tuple[Building, Report]:
    b, door, _, _ = state_case(g)
    first, second = door.space_ids
    state = BuildingState(b)
    before = RouteGraph(b).shortest(first, second)
    message = state.apply(Close(door.id))
    after = RouteGraph(state.building, state.constraints()).shortest(first, second)
    closed_after_close = door.id in state.constraints().closed
    state.apply(Reopen(door.id))
    closed_after_reopen = door.id in state.constraints().closed
    try:
        state.apply(Close("no-such-element"))
        raised = "no error"
    except ValueError as e:
        raised = f"ValueError: {e}"
    rep = state_report(
        b,
        "Close / Reopen",
        f"state.apply(Close({door.id!r})); state.apply(Reopen({door.id!r}))",
        door,
        [("before (open)", before), ("after Close", after)],
    )
    mark_element(b, rep.overlay, door.id, "closed")
    rep.outputs.insert(0, ("apply(Close) message", message))
    rep.compare += [
        ("apply returns a str", type(message).__name__, "str", isinstance(message, str)),
        ("door in constraints().closed after Close", str(closed_after_close), "True", closed_after_close),
        ("door not closed after Reopen", str(not closed_after_reopen), "True", not closed_after_reopen),
        ("Close('no-such-element') raises", raised, "ValueError", raised.startswith("ValueError")),
    ]
    return b, rep


def t_a3_2(g: Golden) -> tuple[Building, Report]:
    b, door, asset, elsewhere = state_case(g)
    first, second = door.space_ids
    state = BuildingState(b)
    m1 = state.apply(AddDoor(first, second, position=door.position))
    m2 = state.apply(MoveAsset(asset.id, elsewhere))
    edited = state.building
    route = RouteGraph(edited).shortest(first, second)
    rep = state_report(
        edited,
        "AddDoor + MoveAsset (drawn on the edited building)",
        f"state.apply(AddDoor({first!r}, {second!r}, position={door.position})); state.apply(MoveAsset({asset.id!r}, {elsewhere!r}))",
        door,
        [("route A→B on edited building", route)],
    )
    new_doors = [o for o in edited.openings.values() if o.id not in b.openings]
    for o in new_doors:
        rep.overlay.markers.append(
            Marker(o.floor_id, o.position, "#e31a1c", "NEW", f"{o.id} {o.width_m} m", "diamond")
        )
    moved = edited.assets[asset.id]
    rep.overlay.markers.append(
        Marker(asset.floor_id, asset.position, "#999", "asset before", asset.name, "diamond")
    )
    rep.overlay.markers.append(
        Marker(moved.floor_id, moved.position, "#ff7f0e", "asset after", moved.name, "diamond")
    )
    rep.overlay.outlines[elsewhere] = "#ff7f0e"
    rep.outputs = [
        ("messages", f"{m1} / {m2}"),
        ("new openings", ", ".join(o.id for o in new_doors)),
    ] + rep.outputs
    rep.compare += [
        (
            "len(edited.openings)",
            str(len(edited.openings)),
            f"{len(b.openings) + 1} (base + 1)",
            len(edited.openings) == len(b.openings) + 1,
        ),
        ("edited asset space", b.label(moved.space_id), b.label(elsewhere), moved.space_id == elsewhere),
        (
            "base asset untouched",
            b.label(b.assets[asset.id].space_id),
            b.label(asset.space_id),
            b.assets[asset.id].space_id == asset.space_id,
        ),
    ]
    return edited, rep


def t_a3_3(q: dict[str, Any]) -> tuple[Building, Report]:
    b, a = load_building(q["building_id"]), q["answer"]
    state = BuildingState(b)
    state.apply(Close(a["closed_element_id"]))
    c = state.constraints()
    graph = RouteGraph(state.building, c)
    route = graph.shortest(a["from_space_id"], a["to_space_id"])
    rep = do_route(b, a["from_space_id"], a["to_space_id"], c)
    rep.call = f"state.apply(Close({a['closed_element_id']!r})); RouteGraph(state.building, state.constraints()).shortest(...)"
    mark_golden_route(b, rep.overlay, a)
    mark_element(b, rep.overlay, a["closed_element_id"], "closed")
    rep.inputs.insert(0, ("question", q["question"]))
    rep.compare += golden_route_checks(route, a)[:1]
    return b, rep


def t_a3_4(g: Golden) -> tuple[Building, Report]:
    b, door, _, _ = state_case(g)
    first, second = door.space_ids
    state = BuildingState(b)
    empty = state.undo()
    edit = Close(door.id)
    state.apply(edit)
    closed_route = RouteGraph(b, state.constraints()).shortest(first, second)
    undone = state.undo()
    after = RouteGraph(b, state.constraints()).shortest(first, second)
    rep = state_report(
        b,
        "undo",
        f"state.undo(); state.apply(Close({door.id!r})); state.undo()",
        door,
        [("while closed", closed_route), ("after undo", after)],
    )
    mark_element(b, rep.overlay, door.id, "closed, then undone")
    rep.compare += [
        ("undo() on empty state", repr(empty), "None", empty is None),
        ("undo() returns the edit", repr(undone), repr(edit), undone == edit),
        (
            "door open again",
            str(door.id not in state.constraints().closed),
            "True",
            door.id not in state.constraints().closed,
        ),
    ]
    return b, rep


def t_a3_5(g: Golden) -> tuple[Building, Report]:
    b, door, _, _ = state_case(g)
    first, second = door.space_ids
    state = BuildingState(b)
    state.apply(AddDoor(first, second, position=door.position))
    exported = parse_building(state.export())
    route = RouteGraph(exported).shortest(first, second)
    rep = state_report(
        exported,
        "export round trip (drawn from the exported json)",
        "parse_building(state.export())",
        door,
        [("route A→B on exported building", route)],
    )
    for o in exported.openings.values():
        if o.id not in b.openings:
            rep.overlay.markers.append(Marker(o.floor_id, o.position, "#e31a1c", "NEW", o.id, "diamond"))
    rep.compare += [
        (
            "len(exported.openings)",
            str(len(exported.openings)),
            f"{len(b.openings) + 1}",
            len(exported.openings) == len(b.openings) + 1,
        ),
        (
            "same space ids",
            str(exported.spaces.keys() == b.spaces.keys()),
            "True",
            exported.spaces.keys() == b.spaces.keys(),
        ),
    ]
    return exported, rep


def t_a4_1(q: dict[str, Any]) -> tuple[Building, Report]:
    b, rep, route = warmup_route_case(q, WALKING, golden_checks=False)
    for s in b.spaces.values():
        if s.room_type == PRODUCTION_BAY:
            rep.overlay.fills[s.id] = "#f7b6b6"
    rep.overlay.legend.append(("#f7b6b6", "production bay (destination only)"))
    through = (
        []
        if route is None
        else [s for s in route.space_path[1:-1] if s in b.spaces and b.spaces[s].room_type == PRODUCTION_BAY]
    )
    for s in through:
        mark_element(b, rep.overlay, s, "through bay", "#d62728")
    rep.compare += [
        ("a route exists", str(route is not None), "True", route is not None),
        (
            "passes through production bays",
            ", ".join(b.label(s) for s in through) or "none",
            "none",
            not through,
        ),
    ]
    return b, rep


def t_a4_2(building_id: str) -> tuple[Building, Report]:
    b = load_building(building_id)
    c = Constraints(step_free=True, min_width_m=FORKLIFT_MIN_WIDTH_M)
    rep = do_reach(b, OUTSIDE, c)
    graph = RouteGraph(b, c)
    narrow: dict[str, str] = {}
    for target in graph.reachable(OUTSIDE):
        route = graph.shortest(OUTSIDE, target)
        for e in route.element_path if route else []:
            if e in b.openings and b.openings[e].width_m < FORKLIFT_MIN_WIDTH_M:
                narrow.setdefault(e, target)
    for e in narrow:
        mark_element(b, rep.overlay, e, f"{b.openings[e].width_m:.2f} m", "#d62728")
    rep.title = "forklift reach from outside"
    rep.compare.append(
        (
            f"openings < {FORKLIFT_MIN_WIDTH_M} m on any forklift route",
            ", ".join(narrow) or "none",
            "none",
            not narrow,
        )
    )
    return b, rep


def t_a4_3(building_id: str) -> tuple[Building, Report]:
    b = load_building(building_id)
    graph = RouteGraph(b)
    ov = Overlay()
    rep = Report(
        "connector cost per metre (xfail until adopted)",
        "RouteGraph(b, WALKING).shortest(low, high) for each connector leg",
        [("building", building_id)],
        [],
        overlay=ov,
    )
    for connector in b.connectors.values():
        for (low_floor, low), (high_floor, high) in itertools.pairwise(connector.stops):
            route = graph.shortest(low, high)
            if route is None or route.element_path != [connector.id]:
                continue
            height = abs(b.floors[high_floor].elevation_m - b.floors[low_floor].elevation_m)
            expected = (
                LIFT_WAIT_M + LIFT_PER_M * height if connector.kind == "lift" else STAIRS_PER_M * height
            )
            name = f"{connector.name} {b.floors[low_floor].name}→{b.floors[high_floor].name} ({height:g} m)"
            ok = length_ok(route.length_m, expected)
            rep.compare.append((name, f"{route.length_m}", f"{expected:g}", ok))
            mark_space(b, ov, low, f"{route.length_m} vs {expected:g}", "#2ca02c" if ok else "#d62728", "x")
    if not rep.compare:
        ov.banner = "skipped: no direct stair or lift leg in this building"
    return b, rep


XFAIL = {"test_a4_3_stairs_and_lift_cost_per_metre": "per-metre stair/lift cost not adopted yet"}

HANDLERS: dict[str, tuple[str, Handler]] = {
    # test name -> (case source, handler). Sources mirror the parametrize of each test.
    "test_route": ("warmup:route", t_route),
    "test_route_step_free": ("warmup:route_step_free", t_route_step_free),
    "test_route_with_closure": ("warmup:route_with_closure", t_route_with_closure),
    "test_route_tour": ("warmup:tour", t_route_tour),
    "test_graph_errand": ("benchmark:errand", t_errand),
    "test_graph_wide_delivery_count": ("benchmark:wide_delivery_count", t_question),
    "test_graph_step_free_reachable_count": ("benchmark:step_free_reachable_count", t_question),
    "test_graph_what_if_door": ("benchmark:what_if_door", t_question),
    "test_a1_1_graph_builds": ("buildings", t_a1_1),
    "test_a1_2_step_free_never_uses_steps": ("reachable:route_step_free", t_a1_2),
    "test_a1_3_closed_element_never_used": ("warmup:route_with_closure", t_a1_3),
    "test_a1_4_min_width_respected": ("reachable:route", t_a1_4),
    "test_a1_7_distances_from_matches_shortest": ("reachable:route", t_a1_7),
    "test_a1_8_reachable_matches_shortest": ("warmup:route_step_free", t_a1_8),
    "test_a2_1_nearest_with_asset": ("benchmark:errand", t_a2_1),
    "test_a3_1_apply_close_reopen": ("single", t_a3_1),
    "test_a3_2_building_has_structural_edits": ("single", t_a3_2),
    "test_a3_3_constraints_drive_routing": ("warmup:route_with_closure", t_a3_3),
    "test_a3_4_undo": ("single", t_a3_4),
    "test_a3_5_export_round_trips": ("single", t_a3_5),
    "test_a4_1_no_shortcut_through_production_bay": ("reachable:route", t_a4_1),
    "test_a4_2_forklift_uses_wide_openings_only": ("buildings", t_a4_2),
    "test_a4_3_stairs_and_lift_cost_per_metre": ("buildings", t_a4_3),
}


def test_cases(test: str, g: Golden) -> list[tuple[str, Any]]:
    """(case id as pytest shows it, handler argument) for every parametrized case of a test."""
    source, _ = HANDLERS[test]
    kind = source.partition(":")[2]
    if source.startswith("warmup:"):
        return [(q["id"], q) for q in g.warmup.get(kind, [])]
    if source.startswith("benchmark:"):
        return [(q["id"], q) for q in g.benchmark.get(kind, [])]
    if source.startswith("reachable:"):
        return [(q["id"], q) for q in g.reachable_routes(kind)]
    if source == "buildings":
        return [(bid, bid) for bid in g.route_buildings()]
    return [("single", g)]


def run_todo(number: str, case: int | None, sample_all: bool, split: str, open_it: bool) -> None:
    items = todo_items()
    if number not in items:
        sys.exit(f"no to-do item {number!r} in {TODO_FILE}. Items: {', '.join(items)}")
    item = items[number]
    print(f"{item.id}  {item.text}")
    if not item.tests:
        print("  no automatic test for this item: nothing to visualise.")
        print("  For hand checks use the free-form modes, e.g. `route <building> <from> <to>`.")
        return
    unknown = [t for t in item.tests if t not in HANDLERS]
    if unknown:
        sys.exit(f"no visualiser for {unknown}; add one to HANDLERS in {Path(__file__).name}")
    VERBOSE[0] = False
    g = Golden.load(split, sample_all)
    sections, summary, passed, total = [], [], 0, 0
    for test in item.tests:
        cases_ = test_cases(test, g)
        print(
            f"  {test}: {len(cases_)} case(s) on {split}{' (all)' if sample_all else f' (pytest sample of {GOLDEN_SAMPLE})'}"
        )
        if case is not None:
            if not 1 <= case <= len(cases_):
                print(f"    --case {case} out of range 1..{len(cases_)}")
                continue
            cases_ = [cases_[case - 1]]
            numbers = [case]
        else:
            numbers = list(range(1, len(cases_) + 1))
        _, handler = HANDLERS[test]
        if test in XFAIL:
            print(f"    (marked xfail in the test file: {XFAIL[test]}; FAIL here is expected)")
        for n, (case_id, arg) in zip(numbers, cases_, strict=True):
            anchor = f"{test}-{n}"
            try:
                b, rep = handler(arg)
            except Exception as e:  # noqa: BLE001 - a crash is a visible FAIL, not a stop
                print(f"    FAIL  #{n} {case_id}: {type(e).__name__}: {e}")
                summary.append((anchor, test, n, case_id, "", False, f"{type(e).__name__}: {e}"))
                total += 1
                continue
            rep.title = f"{item.id} · {test}[{case_id}] · case {n}" + (
                " · xfail expected" if test in XFAIL else ""
            )
            rep.inputs.insert(0, ("to-do", f"{item.id} {item.text}"))
            ok = rep.ok
            passed += ok
            total += 1
            first_fail = next(
                (f"{name}: expected {exp}, got {got}" for name, got, exp, k in rep.compare if not k), ""
            )
            print(
                f"    {'PASS' if ok else 'FAIL'}  #{n} {case_id}"
                + (f"  ({first_fail})" if first_fail else "")
            )
            summary.append((anchor, test, n, case_id, b.id, ok, first_fail))
            sections.append(case_html(b, rep, anchor, only_relevant=True))
    rows = "".join(
        f'<tr class="{"ok" if ok else "bad"}"><td><a href="#{esc(anchor)}">{esc(test)} #{n}</a></td>'
        f"<td>{esc(cid)}</td><td>{esc(bid)}</td><td>{'PASS' if ok else 'FAIL'}</td><td>{esc(why)}</td></tr>"
        for anchor, test, n, cid, bid, ok, why in summary
    )
    head = (
        f'<div class="todo"><h1>To-do {esc(item.id)}: {passed}/{total} cases pass</h1><p>{esc(item.text)}</p>'
        f"<p class=muted>split {esc(split)} · {'every case' if sample_all else 'the cases pytest samples (seed 7, 6 per kind)'}"
        f" · tests: {esc(', '.join(item.tests))} · golden = gold rings / dashed lines, ours = solid</p>"
        f"<table><tr><th>test / case</th><th>case id</th><th>building</th><th></th><th>first failing check</th></tr>{rows}</table></div>"
    )
    print(f"  {passed}/{total} cases pass")
    suffix = f"-case{case}" if case else ("-all" if sample_all else "")
    write_html(f"todo-{item.id}-{split}{suffix}", page(f"To-do {item.id}", head + "".join(sections)), open_it)


# ---- rendering ----------------------------------------------------------------------------------------


def esc(text: object) -> str:
    return html.escape(str(text), quote=True)


class Frame:
    """World (metres, y up) to panel pixels (y down), fitted to the floor's bounding box."""

    def __init__(self, xs: list[float], ys: list[float]) -> None:
        self.x0, self.x1, self.y0, self.y1 = min(xs), max(xs), min(ys), max(ys)
        w, h = max(self.x1 - self.x0, 1e-6), max(self.y1 - self.y0, 1e-6)
        self.scale = min((PANEL_W - 2 * PAD) / w, (PANEL_MAX_H - 2 * PAD) / h)
        self.width = w * self.scale + 2 * PAD
        self.height = h * self.scale + 2 * PAD

    def __call__(self, xy: XY) -> tuple[float, float]:
        return (PAD + (xy[0] - self.x0) * self.scale, PAD + (self.y1 - xy[1]) * self.scale)

    def pts(self, coords: list[XY] | tuple[XY, ...]) -> str:
        return " ".join(f"{x:.1f},{y:.1f}" for x, y in map(self, coords))


def marker_svg(px: float, py: float, m: Marker) -> str:
    tip = f"<title>{esc(m.tip or m.text)}</title>"
    if m.shape == "square":
        shape = f'<rect x="{px - 8:.1f}" y="{py - 8:.1f}" width="16" height="16" rx="3" fill="{m.colour}" stroke="#fff"/>'
    elif m.shape == "diamond":
        r = 9 if m.text else 5
        shape = f'<polygon points="{px},{py - r} {px + r},{py} {px},{py + r} {px - r},{py}" fill="{m.colour}" stroke="#fff"/>'
    elif m.shape == "ring":  # golden: hollow, so ours shows through
        shape = (
            f'<circle cx="{px:.1f}" cy="{py:.1f}" r="12" fill="none" stroke="{m.colour}" stroke-width="3"/>'
        )
    elif m.shape == "x":
        shape = (
            f'<path d="M{px - 7:.1f},{py - 7:.1f}L{px + 7:.1f},{py + 7:.1f}M{px - 7:.1f},{py + 7:.1f}'
            f'L{px + 7:.1f},{py - 7:.1f}" stroke="{m.colour}" stroke-width="4" stroke-linecap="round"/>'
        )
    else:
        shape = f'<circle cx="{px:.1f}" cy="{py:.1f}" r="7" fill="{m.colour}" stroke="#fff"/>'
    text = ""
    if m.text:
        dy = {"diamond": -12, "ring": -15, "x": -10}.get(m.shape, 4)
        fill = "#fff" if m.shape in ("square", "circle") else m.colour
        text = (
            f'<text x="{px:.1f}" y="{py + dy:.1f}" text-anchor="middle" class="mk" '
            f'fill="{fill}">{esc(m.text)}</text>'
        )
    return f"<g>{tip}{shape}{text}</g>"


def path_runs(b: Building, node_path: list[str]) -> list[tuple[str, list[XY]]]:
    """Split a node path into same-floor runs; floor changes and 'outside' break the line."""
    runs: list[tuple[str, list[XY]]] = []
    current: tuple[str, list[XY]] | None = None
    for node in node_path:
        pos = node_position(b, node)
        if pos is None:
            current = None
            continue
        fid, xy = pos
        if current is None or current[0] != fid:
            current = (fid, [xy])
            runs.append(current)
        else:
            current[1].append(xy)
    return runs


def floor_svg(b: Building, fid: str, ov: Overlay) -> str:
    spaces = b.spaces_on(fid)
    openings = [o for o in b.openings.values() if o.floor_id == fid]
    xs = [x for s in spaces for x, _ in s.polygon] + [o.position[0] for o in openings]
    ys = [y for s in spaces for _, y in s.polygon] + [o.position[1] for o in openings]
    if not xs:
        return "<p>(no spaces)</p>"
    fr = Frame(xs, ys)
    out = [
        f'<svg viewBox="0 0 {fr.width:.0f} {fr.height:.0f}" width="{fr.width:.0f}" height="{fr.height:.0f}">'
    ]
    # spaces, big first so small ones stay clickable
    for s in sorted(spaces, key=lambda s: -s.area):
        fill = ov.fills.get(s.id, TYPE_FILL.get(s.type, "#eee"))
        stroke, sw = (ov.outlines[s.id], 2.2) if s.id in ov.outlines else ("#8a8f98", 0.6)
        tip = f"{b.label(s.id)}\n{s.id}\ntype={s.type} room_type={s.room_type} area={s.area:.1f} m²"
        if s.id in ov.tips:
            tip += "\n" + ov.tips[s.id]
        out.append(
            f'<polygon points="{fr.pts(s.polygon)}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}">'
            f"<title>{esc(tip)}</title></polygon>"
        )
    # labels and stair/lift symbols
    for s in spaces:
        minx, miny, maxx, maxy = s.shape.bounds
        wpx, hpx = (maxx - minx) * fr.scale, (maxy - miny) * fr.scale
        px, py = fr(s.anchor)
        if s.type in ("stair", "lift"):
            sym = "S" if s.type == "stair" else "L"
            colour = "#b5530b" if s.type == "stair" else "#6a3fb5"
            out.append(
                f'<text x="{px:.1f}" y="{py + 4:.1f}" text-anchor="middle" class="sym" fill="{colour}">{sym}</text>'
            )
        elif wpx > 34 and hpx > 12:
            short = s.ref or s.name
            size = max(7.0, min(10.0, wpx / max(len(short), 1) * 1.6))
            if len(short) * size * 0.55 <= wpx:
                out.append(
                    f'<text x="{px:.1f}" y="{py + 3:.1f}" text-anchor="middle" class="lb" '
                    f'font-size="{size:.1f}">{esc(short)}</text>'
                )
    # openings: radius ~ width, green = step-free, red = step; entrances get a dark ring
    for o in openings:
        px, py = fr(o.position)
        r = max(1.8, min(o.width_m * fr.scale / 2, 6.0))
        fill = "#2e9d4b" if o.step_free else "#d23b3b"
        ring = ' stroke="#111" stroke-width="1.4"' if o.is_entrance else ""
        sides = " | ".join(b.label(s) for s in o.space_ids)
        tip = f"{o.id} {o.kind} width {o.width_m:.2f} m {'step-free' if o.step_free else 'STEP'}\n{sides}"
        out.append(
            f'<circle cx="{px:.1f}" cy="{py:.1f}" r="{r:.1f}" fill="{fill}"{ring}><title>{esc(tip)}</title></circle>'
        )
    # route lines
    for line in sorted(ov.paths, key=lambda pl: not pl.dashed):
        for run_fid, coords in path_runs(b, line.node_path):
            if run_fid != fid:
                continue
            style = (
                'stroke-width="6" stroke-dasharray="2 7" opacity="0.6"'
                if line.dashed
                else ('stroke-width="3.2" opacity="0.85"')
            )
            if len(coords) > 1:
                out.append(
                    f'<polyline points="{fr.pts(coords)}" fill="none" stroke="{line.colour}" {style} '
                    f'stroke-linejoin="round" stroke-linecap="round"><title>{esc(line.label)}</title></polyline>'
                )
            if line.dashed:
                continue
            for xy in (coords[0], coords[-1]):
                px, py = fr(xy)
                out.append(
                    f'<circle cx="{px:.1f}" cy="{py:.1f}" r="3.5" fill="#fff" stroke="{line.colour}" stroke-width="2"/>'
                )
    for m in ov.markers:
        if m.floor_id == fid:
            out.append(marker_svg(*fr(m.xy), m))
    out.append("</svg>")
    return "".join(out)


def relevant_floors(b: Building, ov: Overlay) -> set[str]:
    """Floors the overlay touches (paths, markers, outlined spaces); used to keep to-do pages small."""
    floors = {f for line in ov.paths for f, _ in path_runs(b, line.node_path)}
    floors |= {m.floor_id for m in ov.markers}
    floors |= {b.spaces[s].floor_id for s in ov.outlines if s in b.spaces}
    return floors | ov.floors


def case_html(b: Building, rep: Report, anchor: str = "", only_relevant: bool = False) -> str:
    """One case: title, call, verdict, expected vs actual, inputs, outputs, then one SVG per floor."""
    ov = rep.overlay
    path_floors = {f for line in ov.paths for f, _ in path_runs(b, line.node_path)}
    marker_floors = {m.floor_id for m in ov.markers}

    def rows(items: list[tuple[str, str]]) -> str:
        return "".join(f"<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>" for k, v in items)

    compare = ""
    if rep.compare:
        verdict = rep.ok
        body = "".join(
            f'<tr class="{"ok" if ok else "bad"}"><th>{esc(n)}</th><td>{esc(g)}</td><td>{esc(o)}</td>'
            f"<td>{'PASS' if ok else 'FAIL'}</td></tr>"
            for n, o, g, ok in rep.compare
        )
        compare = (
            f'<h2 class="{"ok" if verdict else "bad"}">{"PASS" if verdict else "FAIL"}</h2>'
            f"<table><tr><th>check</th><th>expected</th><th>actual (ours)</th><th></th></tr>{body}</table>"
        )
    legend = [(TYPE_FILL[t], t) for t in TYPE_FILL] + ov.legend
    legend_html = "".join(f'<span><i style="background:{c}"></i>{esc(t)}</span>' for c, t in legend)
    legend_html += (
        '<span><i class="dot" style="background:#2e9d4b"></i>step-free opening</span>'
        '<span><i class="dot" style="background:#d23b3b"></i>stepped opening</span>'
        '<span><i class="dot" style="background:#fff;border:2px solid #111"></i>entrance (dot size = width)</span>'
        "<span>S stair · L lift · hover anything for ids</span>"
    )
    shown = relevant_floors(b, ov) if only_relevant else set(b.floors)
    if not shown:
        shown = set(b.floors)
    panels = []
    for f in b.floors_by_level():
        if f.id not in shown:
            continue
        tags = []
        if f.id in path_floors:
            tags.append("route")
        if f.id in marker_floors:
            tags.append("markers")
        tag = f" <em>{' · '.join(tags)}</em>" if tags else ""
        panels.append(
            f"<section><h3>{esc(f.name)} <small>{esc(f.id)} · level {f.level}</small>{tag}</h3>"
            f"{floor_svg(b, f.id, ov)}</section>"
        )
    hidden = len(b.floors) - len(panels)
    note = f"<p class=muted>{hidden} floor(s) the case doesn't touch are hidden.</p>" if hidden else ""
    banner = f'<div class="banner">{esc(ov.banner)}</div>' if ov.banner else ""
    return f"""<article id="{esc(anchor)}">
<h1>{esc(rep.title)}</h1>
<div>{esc(b.id)} · {esc(b.name)} · {esc(b.source)} · {len(b.spaces)} spaces · {len(b.openings)} openings · {len(b.floors)} floors</div>
<h2>Call</h2><code>{esc(rep.call)}</code>
{banner}{compare}
<h2>Inputs</h2><table>{rows(rep.inputs)}</table>
<h2>Outputs</h2><table>{rows(rep.outputs)}</table>
<div class="legend">{legend_html}</div>
{note}{"".join(panels)}
</article>"""


CSS = """
body{font:13px/1.4 -apple-system,system-ui,sans-serif;margin:16px;background:#fafafa;color:#222}
h1{font-size:18px;margin:0 0 6px} h2{font-size:15px;margin:14px 0 4px} h3{font-size:14px;margin:18px 0 4px}
h3 small{color:#888;font-weight:normal} h3 em{color:#d62728;font-style:normal;font-size:12px}
code{display:block;background:#272822;color:#f8f8f2;padding:8px 10px;border-radius:6px;white-space:pre-wrap}
table{border-collapse:collapse;margin:4px 0;max-width:100%}
th,td{text-align:left;vertical-align:top;padding:2px 10px 2px 0;border-bottom:1px solid #eee}
th{color:#555;font-weight:600;white-space:nowrap} td{word-break:break-word}
.ok{color:#1a7f1a} .bad{color:#c0392b} tr.ok td:last-child,tr.bad td:last-child{font-weight:700}
.muted{color:#888}
.banner{background:#c0392b;color:#fff;font-size:18px;font-weight:700;padding:10px 14px;border-radius:6px;margin:10px 0}
.legend span{display:inline-flex;align-items:center;margin:2px 12px 2px 0}
.legend i{display:inline-block;width:14px;height:14px;margin-right:4px;border:1px solid #999}
.legend i.dot{border-radius:50%;width:10px;height:10px}
svg{background:#fff;border:1px solid #ddd;max-width:100%;height:auto}
.lb{fill:#555;pointer-events:none} .sym{font-weight:700;font-size:12px;pointer-events:none}
.mk{font-weight:700;font-size:10px;pointer-events:none}
polygon:hover{opacity:.75}
article{border-top:3px solid #333;margin-top:28px;padding-top:10px}
.todo{background:#fff;border:1px solid #ddd;border-radius:6px;padding:10px 14px}
"""


def page(title: str, body: str) -> str:
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{esc(title)}</title><style>{CSS}</style></head><body>{body}</body></html>"
    )


def write_html(name: str, content: str, open_it: bool) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{re.sub(r'[^A-Za-z0-9._-]+', '_', name)[:150]}.html"
    path.write_text(content, encoding="utf-8")
    print(f"wrote {path}  ({path.stat().st_size // 1024} KB)")
    if open_it and sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    return path


def write(b: Building, rep: Report, name: str, open_it: bool) -> Path:
    slug = f"{b.id}-" + name.replace(f"{b.id}-", "")
    return write_html(slug, page(rep.title, case_html(b, rep)), open_it)


# ---- CLI ----------------------------------------------------------------------------------------------


def todo_main(argv: list[str]) -> None:
    p = argparse.ArgumentParser(
        prog="viz_owner_a.py", description="Visualise the test cases of a notes/todo.md item."
    )
    p.add_argument("number", help="to-do item, e.g. 1.5 or 2.2")
    pick = p.add_mutually_exclusive_group()
    pick.add_argument("--case", type=int, help="only the Nth case (1-based, in pytest order)")
    pick.add_argument(
        "--all", action="store_true", help="every question of the split, not pytest's sample of 6"
    )
    p.add_argument("--split", choices=["dev", "train"], default="dev")
    p.add_argument("--no-open", action="store_true")
    args = p.parse_args(argv)
    run_todo(args.number, args.case, args.all, args.split, not args.no_open)


def main() -> None:
    positional = [a for a in sys.argv[1:] if not a.startswith("-")]
    if positional and re.fullmatch(r"\d+\.\d+", positional[0]):
        todo_main(sys.argv[1:])
        return
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--no-open", action="store_true", help="write the HTML but don't open it")
    sub = p.add_subparsers(dest="mode", required=True)

    def constrained(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--step-free", action="store_true")
        sp.add_argument("--min-width", type=float, default=None, metavar="M")
        sp.add_argument(
            "--closed", nargs="+", default=[], metavar="ID", help="opening/connector/space id or name"
        )

    sp = sub.add_parser("route", help="shortest route between two spaces")
    sp.add_argument("building")
    sp.add_argument("start")
    sp.add_argument("goal")
    constrained(sp)
    sp = sub.add_parser("tour", help="visit stops in order")
    sp.add_argument("building")
    sp.add_argument("stops", nargs="+")
    constrained(sp)
    sp = sub.add_parser("reach", help="distance heat map from a start")
    sp.add_argument("building")
    sp.add_argument("start")
    constrained(sp)
    sp = sub.add_parser("errand", help="pickup nearest asset, carry to largest room_type on floor")
    for name in ("building", "start", "asset_type", "floor_id", "room_type"):
        sp.add_argument(name)
    sp = sub.add_parser("what-if", help="add a door between two spaces, see who gains")
    for name in ("building", "first", "second", "floor_id"):
        sp.add_argument(name)
    sp = sub.add_parser("question", help="run a golden question and compare")
    sp.add_argument("question_id")
    sp.add_argument("--split", choices=["dev", "train"], default="dev", help="train also searches dev")
    sp = sub.add_parser("list", help="print valid inputs for a building")
    sp.add_argument("building")
    for sp in sub.choices.values():
        sp.add_argument("--no-open", action="store_true", default=argparse.SUPPRESS)
    args = p.parse_args()
    open_it = not args.no_open

    if args.mode == "question":
        b, rep = do_question(
            find_question(args.question_id, ["dev", "train"] if args.split == "train" else ["dev"])
        )
        write(b, rep, f"question-{args.question_id.rsplit('-', 1)[-1]}", open_it)
        return
    b = load_building(args.building)
    if args.mode == "list":
        do_list(b)
        return
    if args.mode == "route":
        start, goal = space_id(b, args.start), space_id(b, args.goal)
        rep = do_route(b, start, goal, make_constraints(b, args))
        write(b, rep, f"route-{start}-{goal}", open_it)
    elif args.mode == "tour":
        stops = [space_id(b, s) for s in args.stops]
        write(b, do_tour(b, stops, make_constraints(b, args)), f"tour-{'-'.join(stops)}", open_it)
    elif args.mode == "reach":
        start = space_id(b, args.start)
        write(b, do_reach(b, start, make_constraints(b, args)), f"reach-{start}", open_it)
    elif args.mode == "errand":
        start, fid = space_id(b, args.start), floor_id(b, args.floor_id)
        rep = do_errand(b, start, args.asset_type, fid, args.room_type)
        write(b, rep, f"errand-{start}-{args.asset_type}-{args.room_type}", open_it)
    elif args.mode == "what-if":
        first, second, fid = space_id(b, args.first), space_id(b, args.second), floor_id(b, args.floor_id)
        write(b, do_what_if(b, first, second, fid), f"whatif-{first}-{second}", open_it)


if __name__ == "__main__":
    main()
