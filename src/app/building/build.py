"""Level 4: create or update a building from floor plans, by conversation. "Minutes, not days."

OWNER: Owner B, geometry & build. Scored automatically ("correct buildings built from plans"), but the
input and the comparison aren't published yet: ask Synera (docs/synera/README.md, question 2) whether
it's the SVG plans or the plain PNGs. Target: plans -> building.json in the kit format that loads with
app.building.model.parse_building and passes `validate` with no errors.

Train has 200 buildings with both the plans and the true building.json: that's the test set for this.

Measured: `validate` finds 0 errors in all 280 kit buildings (266 warnings, all OSM/Swiss doors drawn
0.5-53 m off their wall). `compare` scores every train building 1.0 against itself; without `align` a
draft shifted by 0.5 m already loses ~14% of its space matches.

`draft_from_plans` reads the kit's SVG plans. On train, compare(align=True): procedural and ResPlan
rooms, doors, stairs and lifts exact; OSM and Swiss outlines exact too, but floors sit differently from
the truth (Swiss truth floors aren't aligned either), Swiss stairs are guesses; room types within a
colour aren't in the plans (23% right). Every draft of train and dev passes `validate`.
"""

from __future__ import annotations

import itertools
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from functools import partial
from numbers import Real
from pathlib import Path
from typing import Any, Literal, get_args

from shapely import STRtree
from shapely.affinity import translate
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from app.building.model import OUTSIDE, Building, ConnectorKind, OpeningKind, SpaceType, parse_building


@dataclass(frozen=True)
class Issue:
    severity: Literal["error", "warning"]
    element_id: str | None
    message: str


REQUIRED_FIELDS = {
    "floors": ("id", "level", "name", "elevation_m", "height_m"),
    "spaces": ("id", "floor_id", "type", "room_type", "name", "ref", "polygon", "synthetic"),
    "openings": ("id", "floor_id", "space_ids", "kind", "position", "width_m", "step_free", "synthetic"),
    "vertical_connectors": ("id", "kind", "name", "floor_ids", "space_ids", "step_free", "synthetic"),
    "assets": ("id", "name", "category", "type", "space_id", "floor_id", "position", "footprint", "source"),
}
BUILDING_FIELDS = ("id", "name", "source")
DOOR_TOLERANCE_M = 0.5
"""A door's position should lie this close to the outline of each space it joins (walls are ≤0.35 m)."""


def _is_xy(value: Any) -> bool:
    return isinstance(value, list | tuple) and len(value) == 2 and all(isinstance(v, Real) for v in value)


def validate(raw: dict[str, Any]) -> list[Issue]:
    """Schema and topology checks for a kit-format building.json: required fields, ids unique, every
    opening joins 2 existing spaces on its floor, connectors stop once per floor, every space reachable
    from outside, door positions on a shared wall."""
    issues: list[Issue] = []

    def error(element_id: str | None, message: str) -> None:
        issues.append(Issue("error", element_id, message))

    def warning(element_id: str | None, message: str) -> None:
        issues.append(Issue("warning", element_id, message))

    # ---- shape: required fields; malformed elements are reported once and left out of later checks ----
    info = raw.get("building")
    if not isinstance(info, dict):
        error(None, "missing 'building' object")
    else:
        for name in BUILDING_FIELDS:
            if name not in info:
                error(None, f"building is missing {name!r}")
        if info.get("units", "m") != "m":
            warning(None, f"units are {info['units']!r}, expected 'm'")
    elements: dict[str, list[dict[str, Any]]] = {}
    for key, fields in REQUIRED_FIELDS.items():
        items = raw.get(key)
        if not isinstance(items, list):
            error(None, f"missing list {key!r}")
            items = []
        elements[key] = []
        for item in items:
            missing = [f for f in fields if not isinstance(item, dict) or f not in item]
            if missing:
                error(item.get("id") if isinstance(item, dict) else None, f"{key[:-1]} is missing {missing}")
            else:
                elements[key].append(item)
    floors, spaces = elements["floors"], elements["spaces"]
    openings, connectors, assets = elements["openings"], elements["vertical_connectors"], elements["assets"]

    # ---- ids: unique across every element, since closures and edits refer to them by id alone ----------
    ids = Counter(e["id"] for items in elements.values() for e in items)
    for element_id, n in ids.items():
        if n > 1:
            error(element_id, f"id used {n} times")
        if element_id == OUTSIDE:
            error(element_id, f"{OUTSIDE!r} is reserved for the outside node")

    # ---- floors -------------------------------------------------------------------------------------------
    if not floors:
        error(None, "building has no floors")
    floor_by_id = {f["id"]: f for f in floors}
    for level, n in Counter(f["level"] for f in floors).items():
        if n > 1:
            error(None, f"level {level} used by {n} floors")
    by_level = sorted(floors, key=lambda f: f["level"])
    for lower, upper in zip(by_level, by_level[1:], strict=False):
        if upper["elevation_m"] <= lower["elevation_m"]:
            warning(upper["id"], f"elevation {upper['elevation_m']} not above level {lower['level']}")

    # ---- spaces: a known type, on a known floor, with a usable outline ---------------------------------
    space_types, shapes = set(get_args(SpaceType)), {}
    for s in spaces:
        if s["floor_id"] not in floor_by_id:
            error(s["id"], f"unknown floor {s['floor_id']!r}")
        if s["type"] not in space_types:
            error(s["id"], f"unknown space type {s['type']!r}")
        points = s["polygon"]
        if not isinstance(points, list) or len(points) < 3 or not all(_is_xy(p) for p in points):
            error(s["id"], "polygon needs at least 3 [x, y] points")
            continue
        shape = Polygon(points)
        if not shape.is_valid or shape.area <= 0:
            error(s["id"], "polygon is self-intersecting or has no area")
            continue
        shapes[s["id"]] = shape
    space_by_id = {s["id"]: s for s in spaces}

    # ---- openings: two existing spaces on the opening's floor, door on the wall between them -----------
    opening_kinds = set(get_args(OpeningKind))
    for o in openings:
        oid, ends = o["id"], o["space_ids"]
        if o["kind"] not in opening_kinds:
            error(oid, f"unknown opening kind {o['kind']!r}")
        if not isinstance(o["width_m"], Real) or o["width_m"] <= 0:
            error(oid, f"width {o['width_m']!r} m is not positive")
        if not isinstance(ends, list) or len(ends) != 2 or ends[0] == ends[1]:
            error(oid, f"must join exactly 2 different spaces, got {ends!r}")
            continue
        if (o["kind"] == "entrance") != (OUTSIDE in ends):
            warning(oid, f"kind {o['kind']!r} but {'joins' if OUTSIDE in ends else 'does not join'} outside")
        position_ok = _is_xy(o["position"])
        if not position_ok:
            error(oid, f"position {o['position']!r} is not [x, y]")
        for sid in ends:
            if sid == OUTSIDE:
                continue
            if sid not in space_by_id:
                error(oid, f"joins unknown space {sid!r}")
            elif space_by_id[sid]["floor_id"] != o["floor_id"]:
                error(oid, f"joins {sid!r} on another floor; use a vertical connector")
            elif position_ok and sid in shapes:
                gap = shapes[sid].exterior.distance(Point(o["position"]))
                if gap > DOOR_TOLERANCE_M:
                    warning(oid, f"position is {gap:.1f} m from the wall of {sid!r}")

    # ---- vertical connectors: one stop per floor, each in a space on that floor -------------------------
    connector_kinds = set(get_args(ConnectorKind))
    for c in connectors:
        cid, stop_floors, stop_spaces = c["id"], c["floor_ids"], c["space_ids"]
        if c["kind"] not in connector_kinds:
            error(cid, f"unknown connector kind {c['kind']!r}")
        elif c["step_free"] != (c["kind"] == "lift"):
            warning(cid, f"{c['kind']} with step_free={c['step_free']}")
        if not isinstance(stop_floors, list) or not isinstance(stop_spaces, list):
            error(cid, "floor_ids and space_ids must be lists")
            continue
        if len(stop_floors) != len(stop_spaces) or len(stop_floors) < 2:
            error(
                cid,
                f"needs ≥2 stops with one space each, got {len(stop_floors)} floors, {len(stop_spaces)} spaces",
            )
            continue
        for fid, n in Counter(stop_floors).items():
            if n > 1:
                error(cid, f"stops {n} times on floor {fid!r}")
        for fid, sid in zip(stop_floors, stop_spaces, strict=True):
            if sid not in space_by_id:
                error(cid, f"stops in unknown space {sid!r}")
            elif space_by_id[sid]["floor_id"] != fid:
                error(cid, f"stop {sid!r} is not on floor {fid!r}")

    # ---- assets: in an existing space, on that space's floor ---------------------------------------------
    for a in assets:
        space = space_by_id.get(a["space_id"])
        if space is None:
            error(a["id"], f"in unknown space {a['space_id']!r}")
        elif space["floor_id"] != a["floor_id"]:
            error(a["id"], f"floor {a['floor_id']!r} differs from its space's floor {space['floor_id']!r}")

    # ---- reachability on foot: every space from outside, through openings and connectors ---------------
    neighbours: dict[str, set[str]] = defaultdict(set)
    for o in openings:
        ends = o["space_ids"]
        if isinstance(ends, list) and len(ends) == 2:
            neighbours[ends[0]].add(ends[1])
            neighbours[ends[1]].add(ends[0])
    for c in connectors:
        stops = c["space_ids"] if isinstance(c["space_ids"], list) else []
        for a, b in zip(stops, stops[1:], strict=False):
            neighbours[a].add(b)
            neighbours[b].add(a)
    if spaces and not neighbours[OUTSIDE]:
        error(None, "no entrance: no opening joins a space to 'outside'")
    seen = _reachable(neighbours)
    if neighbours[OUTSIDE]:
        for s in spaces:
            if s["id"] not in seen:
                error(s["id"], "not reachable from outside")
    return issues


def _reachable(neighbours: dict[str, set[str]]) -> set[str]:
    """Every node reachable on foot from outside."""
    seen, queue = {OUTSIDE}, deque([OUTSIDE])
    while queue:
        for nxt in neighbours.get(queue.popleft(), set()) - seen:
            seen.add(nxt)
            queue.append(nxt)
    return seen


# ---- drafting from the kit's floor_<level>.svg plans -------------------------------------------------------
#
# The kit draws every floor with matplotlib: one outline per space, in building.json order, filled by space
# type; one marker per opening, coloured by kind; each space's ref as a label (its text kept in an XML
# comment); and a title "<building> — <floor>". Every floor is padded by 2 m, which gives the scale. Not in
# the drawing: room types within a colour, door widths, steps, floor heights, and where each floor sits in
# the building's frame (floors are lined up with each other here, the origin is arbitrary).

SVG = {"svg": "http://www.w3.org/2000/svg"}
PLAN_MARGIN_M = 2.0
OUTLINE_STROKE = "#333333"
FILL_TYPES = {
    "#f3efe6": ("room", "generic_room"),
    "#dcdcdc": ("corridor", "corridor"),
    "#cfe8c4": ("outdoor", "balcony"),
    "#f4b183": ("stair", "stairwell"),
    "#b4a7d6": ("lift", "lift"),
    "#fff2b3": ("hall", "lobby"),
}
MARKER_KINDS = {"#8b4513": "door", "#d32f2f": "entrance", "#9e9e9e": "opening"}
TYPE_NAMES = {"room": "Room", "corridor": "Corridor", "hall": "Hall", "stair": "Staircase", "lift": "Lift",
              "outdoor": "Balcony"}  # fmt: skip
DOOR_SNAP_M = 0.6
"""A marker joins the spaces whose outlines pass within this distance of it; one such space = entrance."""
DEFAULT_DOOR_WIDTH_M = 0.9
DEFAULT_STOREY_M = 3.0
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?(?:e-?\d+)?")
_LABEL = re.compile(
    r'<g id="text_\d+">\s*<!-- (.*?) -->\s*<g style="[^"]*" '
    r'transform="translate\(([-\d.e]+) ([-\d.e]+)\) scale\(([\d.e]+) -[\d.e]+\)">(.*?)</g>',
    re.S,
)
_GLYPH_OFFSET = re.compile(r'<use xlink:href="#[^"]+" transform="translate\(([\d.e]+) 0\)"')


@dataclass
class _PlanFloor:
    title: str | None
    outlines: list[tuple[str | None, list[tuple[float, float]]]]
    markers: list[tuple[str | None, tuple[float, float]]]
    labels: list[tuple[str, tuple[float, float]]]


def _style(element: ET.Element) -> dict[str, str]:
    return dict(kv.split(": ", 1) for kv in (element.get("style") or "").split("; ") if ": " in kv)


def _path_points(d: str) -> list[tuple[float, float]]:
    """End points of an 'M x y L x y ... z' path. Outlines never repeat their first vertex."""
    points = []
    for _, args in re.findall(r"([MLC])([^MLCz]*)", d):
        x, y = (float(v) for v in _NUMBER.findall(args)[-2:])
        points.append((x, y))
    return points


def _read_plan(path: Path, metres_per_pt: float | None) -> _PlanFloor:
    """One floor plan SVG in metres, with the lower-left corner of its outlines at (0, 0)."""
    text = path.read_text(encoding="utf-8")
    root = ET.fromstring(text)
    axes, clip = root.find(".//svg:g[@id='axes_1']", SVG), root.find(".//svg:clipPath/svg:rect", SVG)
    if axes is None or clip is None:
        raise ValueError(f"{path.name}: not a kit floor plan (no matplotlib axes)")
    outlines, markers = [], []
    for group in axes:
        gid, path_el = group.get("id", ""), group.find("svg:path", SVG)
        if path_el is None:
            continue
        style = _style(path_el)
        if gid.startswith("patch_") and style.get("stroke") == OUTLINE_STROKE:
            outlines.append((style.get("fill"), _path_points(path_el.get("d", ""))))
        elif gid.startswith("line2d_"):
            markers.append((style.get("stroke"), _path_points(path_el.get("d", ""))[0]))
    if not outlines:
        raise ValueError(f"{path.name}: no space outlines found")
    xs = [x for _, pts in outlines for x, _ in pts]
    ys = [y for _, pts in outlines for _, y in pts]
    cx, cy, cw, ch = (float(clip.get(k, 0)) for k in ("x", "y", "width", "height"))
    pt_per_m = (
        1 / metres_per_pt
        if metres_per_pt
        else ((min(xs) - cx) + (cx + cw - max(xs)) + (min(ys) - cy) + (cy + ch - max(ys)))
        / (4 * PLAN_MARGIN_M)
    )
    x0, y0 = min(xs), max(ys)  # SVG y grows downwards

    def metres(p: tuple[float, float]) -> tuple[float, float]:
        return (round((p[0] - x0) / pt_per_m, 4), round((y0 - p[1]) / pt_per_m, 4))

    title, labels = None, []
    for m in _LABEL.finditer(text):
        label, tx, ty, scale = m.group(1), float(m.group(2)), float(m.group(3)), float(m.group(4))
        if " — " in label:
            title = label
            continue
        # Labels are centred on the space: last glyph offset + one glyph's advance (~63.6) is the width.
        width = (max([0.0, *(float(v) for v in _GLYPH_OFFSET.findall(m.group(5)))]) + 63.6) * scale
        labels.append((label, metres((tx + width / 2, ty - 36 * scale))))
    return _PlanFloor(
        title=title,
        outlines=[(fill, [metres(p) for p in pts]) for fill, pts in outlines],
        markers=[(stroke, metres(p)) for stroke, p in markers],
        labels=labels,
    )


def _outline_union(plan: _PlanFloor, types: set[str] | None = None) -> Any:
    shapes = [
        Polygon(pts)
        for fill, pts in plan.outlines
        if types is None or FILL_TYPES.get(fill, ("room",))[0] in types
    ]
    return unary_union(shapes) if shapes else Polygon()


LINE_UP_FINALISTS = 12
"""Offsets with the most stair/lift overlap that also get the (slower) whole-footprint check."""


def _line_up(plans: dict[int, _PlanFloor]) -> dict[int, tuple[float, float]]:
    """Level -> (dx, dy) that stacks each floor on the one below: try offsets that put a stair or lift
    on a stair or lift, or line up the footprints' corners; keep the one that overlaps stairs and lifts
    most, then the whole footprint."""
    vertical = {"stair", "lift"}
    stairs = {level: _outline_union(plan, vertical) for level, plan in plans.items()}
    footprint = {level: _outline_union(plan) for level, plan in plans.items()}
    levels = sorted(plans)
    shifts = {levels[0]: (0.0, 0.0)}
    for below, level in itertools.pairwise(levels):
        base = shifts[below]
        below_stairs, below_all = translate(stairs[below], *base), translate(footprint[below], *base)
        candidates = {base}
        for b in getattr(below_stairs, "geoms", [below_stairs]):
            for h in getattr(stairs[level], "geoms", [stairs[level]]):
                if not b.is_empty and not h.is_empty:
                    candidates.add(
                        (round(b.centroid.x - h.centroid.x, 3), round(b.centroid.y - h.centroid.y, 3))
                    )
        (bx0, by0, bx1, by1), (hx0, hy0, hx1, hy1) = below_all.bounds, footprint[level].bounds
        corners = {
            (bx0 - hx0, by0 - hy0),
            (bx1 - hx1, by1 - hy1),
            (bx0 - hx0, by1 - hy1),
            (bx1 - hx1, by0 - hy0),
        }
        stair_fit = partial(_overlap_after, stairs[level], below_stairs, digits=1)
        finalists = sorted(sorted(candidates), key=stair_fit, reverse=True)[:LINE_UP_FINALISTS]
        footprint_fit = partial(_overlap_after, footprint[level], below_all)
        shifts[level] = max(sorted({*finalists, *corners}), key=lambda s: (stair_fit(s), footprint_fit(s)))
    return shifts


def _overlap_after(shape: Any, other: Any, shift: tuple[float, float], digits: int | None = None) -> float:
    """Area shared by `shape` moved by `shift` and `other`."""
    area = translate(shape, *shift).intersection(other).area
    return round(area, digits) if digits is not None else area


def _stacked_chains(
    spaces_by_level: dict[int, list[dict[str, Any]]], space_type: str
) -> list[list[dict[str, Any]]]:
    """Spaces of one type that sit on top of each other (>50% overlap) on consecutive levels: one chain
    per staircase or lift shaft."""
    levels, used, chains = sorted(spaces_by_level), set(), []
    for i, level in enumerate(levels):
        for start in spaces_by_level[level]:
            if start["type"] != space_type or start["id"] in used:
                continue
            chain = [start]
            for upper in levels[i + 1 :]:
                below = Polygon(chain[-1]["polygon"])
                options = [
                    s for s in spaces_by_level[upper] if s["type"] == space_type and s["id"] not in used
                ]
                best = max(
                    options, key=lambda s: Polygon(s["polygon"]).intersection(below).area, default=None
                )
                if best is None or Polygon(best["polygon"]).intersection(below).area <= 0.5 * below.area:
                    break
                chain.append(best)
            if len(chain) > 1:
                used.update(s["id"] for s in chain)
                chains.append(chain)
    return chains


def _shared_wall(a: Polygon, b: Polygon) -> Any:
    """The stretch of a's outline within wall thickness (0.35 m) of b's."""
    return a.exterior.intersection(b.exterior.buffer(0.35))


def _openings(
    plan: _PlanFloor, floor_id: str, spaces: list[dict[str, Any]], shift: tuple[float, float]
) -> list[dict[str, Any]]:
    """Door markers -> openings joining the spaces whose outlines pass closest to each marker."""
    shapes = [Polygon(s["polygon"]) for s in spaces]
    tree = STRtree(shapes)
    out = []
    for j, (stroke, (mx, my)) in enumerate(plan.markers):
        point = Point(mx + shift[0], my + shift[1])
        near = sorted(
            (int(i) for i in tree.query(point, predicate="dwithin", distance=DOOR_SNAP_M)),
            key=lambda i: shapes[i].exterior.distance(point),
        )
        if not near:  # drawn off every wall (OSM): attach it to the nearest space
            near = [int(tree.nearest(point))]
        kind = MARKER_KINDS.get(stroke or "", "door")
        if kind == "entrance" or len(near) == 1:
            kind, ends = "entrance", [spaces[near[0]]["id"], OUTSIDE]
        else:  # the two nearest outlines (wrong at a few corners where three spaces meet)
            ends = [spaces[near[0]]["id"], spaces[near[1]]["id"]]
        width = DEFAULT_DOOR_WIDTH_M
        if kind == "opening":  # open passages (hall zones) span the whole shared edge
            width = round(_shared_wall(shapes[near[0]], shapes[near[1]]).length, 2) or DEFAULT_DOOR_WIDTH_M
        out.append({
            "id": f"{floor_id}-O{j:04d}", "floor_id": floor_id, "space_ids": ends, "kind": kind,
            "position": [round(point.x, 4), round(point.y, 4)], "width_m": width, "step_free": True,
            "synthetic": False,
        })  # fmt: skip
    return out


STOP_RANK = {"stair": 2, "lift": 2, "corridor": 1, "hall": 1}
"""Where a guessed staircase should stop: stair spaces first, then corridors and halls, rooms last."""


def _guessed_stairs(
    spaces_by_level: dict[int, list[dict[str, Any]]], neighbours: dict[str, set[str]]
) -> list[list[dict[str, Any]]]:
    """Staircases (lower, upper) that make every space reachable: while some aren't, join the unreachable
    space and the reachable one on the floor above or below that stack best (stair/corridor first, then
    most overlap, then closest). Swiss stairwells are drawn as corridors and don't always stack."""
    levels = sorted(spaces_by_level)
    level_of = {s["id"]: lv for lv, spaces in spaces_by_level.items() for s in spaces}
    by_id = {s["id"]: s for spaces in spaces_by_level.values() for s in spaces}
    shapes = {sid: Polygon(s["polygon"]) for sid, s in by_id.items()}

    def fit(a: str, b: str) -> tuple[int, float, float]:
        pa, pb = shapes[a], shapes[b]
        overlap = pa.intersection(pb).area
        iou = overlap / (pa.area + pb.area - overlap) if overlap else 0.0
        rank = min(STOP_RANK.get(by_id[a]["type"], 0), STOP_RANK.get(by_id[b]["type"], 0))
        return (rank, iou, -pa.centroid.distance(pb.centroid))

    links = []
    while True:
        reached = _reachable(neighbours)
        missing = [sid for sid in by_id if sid not in reached]
        if not missing or len(reached) == 1:  # all reachable, or no entrance to start from
            return links
        best_rank = max(STOP_RANK.get(by_id[sid]["type"], 0) for sid in missing)
        options = []
        for a in (sid for sid in missing if STOP_RANK.get(by_id[sid]["type"], 0) == best_rank):
            i = levels.index(level_of[a])
            for lv in levels[max(i - 1, 0) : i + 2]:
                options += [
                    (a, b["id"]) for b in spaces_by_level[lv] if lv != level_of[a] and b["id"] in reached
                ]
        if not options:
            return links
        a, b = max(options, key=lambda pair: fit(*pair))
        neighbours[a].add(b)
        neighbours[b].add(a)
        links.append(sorted((by_id[a], by_id[b]), key=lambda s: level_of[s["id"]]))


def _connectors(
    building_id: str, spaces_by_level: dict[int, list[dict[str, Any]]], openings: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Stairs and lifts from stair/lift spaces stacked on consecutive floors, plus guessed staircases
    (synthetic) wherever spaces would otherwise be unreachable."""
    chains = [("stairs", c, False) for c in _stacked_chains(spaces_by_level, "stair")]
    chains += [("lift", c, False) for c in _stacked_chains(spaces_by_level, "lift")]
    neighbours: dict[str, set[str]] = defaultdict(set)
    for o in openings:
        a, b = o["space_ids"]
        neighbours[a].add(b)
        neighbours[b].add(a)
    for _, chain, _ in chains:
        for a, b in itertools.pairwise(chain):
            neighbours[a["id"]].add(b["id"])
            neighbours[b["id"]].add(a["id"])
    # Chain guessed links that continue each other (one stairwell through several floors).
    links = _guessed_stairs(spaces_by_level, neighbours)
    upper_of = {}
    for lower, upper in links:
        if lower["id"] in upper_of:
            chains.append(("stairs", [lower, upper], True))
        else:
            upper_of[lower["id"]] = upper
    uppers = {s["id"] for s in upper_of.values()}
    by_id = {s["id"]: s for lower, upper in links for s in (lower, upper)}
    for start in upper_of:
        if start in uppers:
            continue  # the middle of a chain that starts lower down
        chain = [by_id[start]]
        while chain[-1]["id"] in upper_of and len(chain) <= len(upper_of):
            chain.append(upper_of[chain[-1]["id"]])
        chains.append(("stairs", chain, True))
    return [
        {
            "id": f"{building_id}-V{n:02d}",
            "kind": kind,
            "name": f"{'Lift' if kind == 'lift' else 'Stairs'} {n}",
            "floor_ids": [s["floor_id"] for s in chain],
            "space_ids": [s["id"] for s in chain],
            "step_free": kind == "lift",
            "synthetic": guessed,
        }  # fmt: skip
        for n, (kind, chain, guessed) in enumerate(chains, start=1)
    ]


def draft_from_plans(
    images: list[Path], *, name: str, levels: list[int] | None = None, metres_per_pixel: float | None = None
) -> dict[str, Any]:
    """Floor plans -> kit-format building.json. Reads the kit's vector plans (floor_<level>.svg) exactly:
    outlines, space types, refs, doors and entrances; stairs and lifts from spaces stacked between floors.
    `levels` default to the number in each file name (floor_-1.svg), else 0, 1, 2, ...; the scale comes
    from the plan's 2 m margin unless `metres_per_pixel` (metres per SVG pt) is given.

    Guessed, because plans don't show them: room types inside a colour (rooms -> generic_room, halls ->
    lobby), door widths (0.9 m), steps (none), floor heights (3 m per level) and the origin of the frame.
    PNG plans aren't supported yet."""
    if not images:
        raise ValueError("no floor plans given")
    paths = [Path(p) for p in images]
    if any(p.suffix.lower() != ".svg" for p in paths):
        raise NotImplementedError("only the kit's vector plans (floor_<level>.svg) can be read so far")
    if levels is None:
        numbers = [re.search(r"floor_(-?\d+)", p.stem) for p in paths]
        levels = [int(m.group(1)) for m in numbers] if all(numbers) else list(range(len(paths)))  # type: ignore[union-attr]
    if len(levels) != len(paths) or len(set(levels)) != len(levels):
        raise ValueError(f"need one distinct level per plan, got {levels} for {len(paths)} plans")
    plans = {level: _read_plan(p, metres_per_pixel) for level, p in zip(levels, paths, strict=True)}
    shifts = _line_up(plans)

    building_id = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-") or "drafted"
    raw: dict[str, Any] = {
        "building": {"id": building_id, "name": name, "source": "floor_plans", "units": "m"},
        "floors": [], "spaces": [], "openings": [], "vertical_connectors": [], "assets": [],
    }  # fmt: skip
    spaces_by_level: dict[int, list[dict[str, Any]]] = {}
    for level in sorted(plans):
        plan, (dx, dy) = plans[level], shifts[level]
        floor_id = f"{building_id}-F{level}"
        floor_name = (
            plan.title.rsplit(" — ", 1)[1]
            if plan.title
            else ("Ground floor" if level == 0 else f"Floor {level}")
        )
        raw["floors"].append({
            "id": floor_id, "level": level, "name": floor_name,
            "elevation_m": DEFAULT_STOREY_M * level, "height_m": DEFAULT_STOREY_M,
        })  # fmt: skip
        spaces = []
        for i, (fill, pts) in enumerate(plan.outlines):
            space_type, room_type = FILL_TYPES.get(fill or "", ("room", "generic_room"))
            spaces.append({
                "id": f"{floor_id}-S{i:03d}", "floor_id": floor_id, "type": space_type, "room_type": room_type,
                "name": "", "ref": "", "polygon": [[x + dx, y + dy] for x, y in pts], "synthetic": False,
            })  # fmt: skip
        shapes = [Polygon(s["polygon"]) for s in spaces]
        for label, (lx, ly) in plan.labels:
            point = Point(lx + dx, ly + dy)
            inside = [i for i, shape in enumerate(shapes) if shape.covers(point)]
            target = (
                min(inside, key=lambda i: shapes[i].area)
                if inside
                else min(range(len(shapes)), key=lambda i: shapes[i].distance(point))
            )
            spaces[target]["ref"] = label
        for i, s in enumerate(spaces):
            s["name"] = f"{TYPE_NAMES[s['type']]} {s['ref'] or f'{level}-{i:03d}'}"
        raw["spaces"] += spaces
        raw["openings"] += _openings(plan, floor_id, spaces, (dx, dy))
        spaces_by_level[level] = spaces
    raw["vertical_connectors"] = _connectors(building_id, spaces_by_level, raw["openings"])
    return raw


MATCH_IOU = 0.5
"""A drafted space counts as a true space when their outlines overlap by at least this IoU."""


def _ratio(hits: float, total: float) -> float:
    """hits / total, and 1.0 when there was nothing to find (a building without lifts misses none)."""
    return hits / total if total else 1.0


def _f1(precision: float, recall: float) -> float:
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def _match_spaces(predicted: Building, truth: Building) -> dict[str, tuple[str, float]]:
    """True space id -> (drafted space id, IoU), one to one, best IoU first, floors paired by level."""
    pairs = []
    for floor in truth.floors.values():
        drafted_floor = predicted.floor_at_level(floor.level)
        if drafted_floor is None:
            continue
        drafted = predicted.spaces_on(drafted_floor.id)
        tree = STRtree([s.shape for s in drafted])
        for t in truth.spaces_on(floor.id):
            for index in tree.query(t.shape):
                p = drafted[int(index)]
                overlap = t.shape.intersection(p.shape).area
                iou = overlap / (t.area + p.area - overlap) if overlap else 0.0
                if iou >= MATCH_IOU:
                    pairs.append((iou, t.id, p.id))
    matched: dict[str, tuple[str, float]] = {}
    used: set[str] = set()
    for iou, true_id, drafted_id in sorted(pairs, reverse=True):
        if true_id not in matched and drafted_id not in used:
            matched[true_id] = (drafted_id, iou)
            used.add(drafted_id)
    return matched


def _floor_area_iou(predicted: Building, truth: Building) -> float:
    """Area IoU of the union of all space outlines, summed over floors paired by level."""
    overlap = union = 0.0
    for level in {f.level for f in truth.floors.values()} | {f.level for f in predicted.floors.values()}:
        shapes = []
        for b in (predicted, truth):
            floor = b.floor_at_level(level)
            spaces = b.spaces_on(floor.id) if floor else []
            shapes.append(unary_union([s.shape for s in spaces]) if spaces else Polygon())
        overlap += shapes[0].intersection(shapes[1]).area
        union += shapes[0].union(shapes[1]).area
    return _ratio(overlap, union)


def _found(drafted: Counter, true: Counter) -> tuple[float, float]:
    """(precision, recall) of two multisets of keys."""
    hits = sum((drafted & true).values())
    return _ratio(hits, sum(drafted.values())), _ratio(hits, sum(true.values()))


def _footprints(b: Building) -> dict[int, Any]:
    """Level -> union of every space outline on that floor."""
    return {f.level: unary_union([s.shape for s in b.spaces_on(f.id)]) for f in b.floors.values()}


def _best_shift(predicted: Building, truth: Building) -> tuple[float, float]:
    """The one translation that best lays the draft over the truth, floors keeping their relative places.
    Candidates line up the corners and centres of each shared floor's footprint; the winner overlaps most."""
    drafted, true = _footprints(predicted), _footprints(truth)
    levels = [lv for lv in true if lv in drafted and not drafted[lv].is_empty and not true[lv].is_empty]
    candidates = {(0.0, 0.0)}
    for lv in levels:
        (px0, py0, px1, py1), (tx0, ty0, tx1, ty1) = drafted[lv].bounds, true[lv].bounds
        for dx in (tx0 - px0, tx1 - px1, (tx0 + tx1 - px0 - px1) / 2):
            for dy in (ty0 - py0, ty1 - py1, (ty0 + ty1 - py0 - py1) / 2):
                candidates.add((dx, dy))

    def overlap(shift: tuple[float, float]) -> float:
        return sum(translate(drafted[lv], *shift).intersection(true[lv]).area for lv in levels)

    return max(sorted(candidates), key=overlap)


def _shifted(raw: dict[str, Any], dx: float, dy: float) -> dict[str, Any]:
    """A copy of a building.json with every coordinate moved by (dx, dy)."""
    out = dict(raw)
    out["spaces"] = [{**s, "polygon": [[x + dx, y + dy] for x, y in s["polygon"]]} for s in raw["spaces"]]
    for key in ("openings", "assets"):
        out[key] = [{**e, "position": [e["position"][0] + dx, e["position"][1] + dy]} for e in raw[key]]
    return out


def compare(predicted: dict[str, Any], truth: dict[str, Any], *, align: bool = False) -> dict[str, float]:
    """How close a drafted building is to the true one (room count, area IoU, door/adjacency recall):
    our own score for Level 4 until Synera tells us theirs.

    `align=True` ignores where the draft sits in the frame: it first moves the whole draft by the one
    translation that overlays it best on the truth. Use it for drafts from plans, whose origin is arbitrary."""
    p, t = parse_building(predicted), parse_building(truth)
    if align and (shift := _best_shift(p, t)) != (0.0, 0.0):
        p = parse_building(_shifted(predicted, *shift))
    matched = _match_spaces(p, t)
    to_truth = {drafted_id: true_id for true_id, (drafted_id, _) in matched.items()}
    to_truth[OUTSIDE] = OUTSIDE

    space_precision, space_recall = _ratio(len(matched), len(p.spaces)), _ratio(len(matched), len(t.spaces))
    pairs = [(t.spaces[true_id], p.spaces[drafted_id]) for true_id, (drafted_id, _) in matched.items()]

    # Doors: an opening joining the same two (matched) spaces. Drafted ones joining an unmatched space miss.
    def door_keys(b: Building, rename: dict[str, str] | None) -> Counter:
        keys: Counter = Counter()
        for o in b.openings.values():
            ends = [rename.get(sid) for sid in o.space_ids] if rename is not None else list(o.space_ids)
            keys[frozenset(ends) if None not in ends else ("unmatched", o.id)] += 1
        return keys

    door_precision, door_recall = _found(door_keys(p, to_truth), door_keys(t, None))

    # Stairs and lifts: same kind, stopping in the same (matched) spaces.
    def connector_keys(b: Building, rename: dict[str, str] | None) -> Counter:
        keys: Counter = Counter()
        for c in b.connectors.values():
            stops = (
                [rename.get(sid) for _, sid in c.stops] if rename is not None else [sid for _, sid in c.stops]
            )
            keys[(c.kind, frozenset(stops)) if None not in stops else ("unmatched", c.id)] += 1
        return keys

    connector_precision, connector_recall = _found(connector_keys(p, to_truth), connector_keys(t, None))

    scores = {
        "floor_recall": _ratio(
            sum(p.floor_at_level(f.level) is not None for f in t.floors.values()), len(t.floors)
        ),
        "space_precision": space_precision,
        "space_recall": space_recall,
        "mean_iou": sum(iou for _, iou in matched.values()) / len(matched) if matched else 0.0,
        "area_iou": _floor_area_iou(p, t),
        "room_type_accuracy": _ratio(sum(a.room_type == b.room_type for a, b in pairs), len(pairs)),
        "ref_accuracy": _ratio(sum(a.ref == b.ref for a, b in pairs), len(pairs)),
        "door_precision": door_precision,
        "door_recall": door_recall,
        "connector_precision": connector_precision,
        "connector_recall": connector_recall,
    }
    headline = (
        scores["area_iou"],
        _f1(space_precision, space_recall),
        scores["room_type_accuracy"],
        _f1(door_precision, door_recall),
        _f1(connector_precision, connector_recall),
    )
    scores["score"] = sum(headline) / len(headline)
    return scores
