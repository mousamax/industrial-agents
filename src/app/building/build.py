"""Level 4: create or update a building from floor plans, by conversation. "Minutes, not days."

OWNER: Owner B, geometry & build. Scored automatically ("correct buildings built from plans"), but the
input and the comparison aren't published yet: ask Synera (docs/synera/README.md, question 2) whether
it's the SVG plans or the plain PNGs. Target: plans -> building.json in the kit format that loads with
app.building.model.parse_building and passes `validate` with no errors.

Train has 200 buildings with both the plans and the true building.json: that's the test set for this.

Measured: `validate` finds 0 errors in all 280 kit buildings (266 warnings, all OSM/Swiss doors drawn
0.5-53 m off their wall). `compare` scores every train building 1.0 against itself; without `align` a
draft shifted by 0.5 m already loses ~14% of its space matches.

`draft_from_plans` reads the kit's SVG plans. On dev and test (held out), compare(align=True) scores
0.90: procedural and ResPlan rooms, doors, stairs and lifts exact; OSM and Swiss outlines exact too, but
floors sit differently from the truth (Swiss truth floors aren't aligned either) and Swiss stairs are
guesses. Room types (95-97%) and asset types (94-95%) are learned from the furniture in train; every
such value is in the draft's "guesses" with its confidence. Every draft of train and dev passes `validate`.

`diff(before, after)` (Level 5, keep the map current) lists what changed between two versions: spaces
added, removed, split, merged, reshaped, retyped or renamed; doors added, removed or resized; assets new,
gone or moved. No false changes on any of the 280 kit buildings against itself, shifted or not; on every
dev building, a machine moved and a door added through BuildingState come back as exactly those two.
"""

from __future__ import annotations

import itertools
import json
import math
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from functools import cache, partial
from numbers import Real
from pathlib import Path
from typing import Any, Literal, get_args

import contourpy
import numpy as np
from PIL import Image
from shapely import STRtree, make_valid
from shapely.affinity import translate
from shapely.geometry import LinearRing, Point, Polygon
from shapely.ops import unary_union

from app.building.model import OUTSIDE, Building, ConnectorKind, OpeningKind, SpaceType, parse_building
from app.settings import get_settings


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
ASSET_CATEGORIES = {
    "#7f6a4f": "furniture",
    "#2e7d32": "equipment",
    "#4f81bd": "fixture",
    "#c0504d": "machine",
}
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
    assets: list[tuple[str, list[tuple[float, float]]]]
    """(category, rectangle corners) for every drawn asset; floor_<level>_plain plans have none."""


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
    outlines, markers, assets = [], [], []
    for group in axes:
        gid, path_el = group.get("id", ""), group.find("svg:path", SVG)
        if path_el is None:
            continue
        style = _style(path_el)
        if gid.startswith("patch_") and style.get("stroke") == OUTLINE_STROKE:
            outlines.append((style.get("fill"), _path_points(path_el.get("d", ""))))
        elif gid.startswith("patch_") and style.get("fill") in ASSET_CATEGORIES:
            assets.append((ASSET_CATEGORIES[style["fill"]], _path_points(path_el.get("d", ""))))
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
        assets=[(category, [metres(p) for p in pts]) for category, pts in assets],
    )


# ---- the same floors from the kit's floor_<level>_plain.png ----------------------------------------------
#
# The PNGs are the same drawing rasterised at 100 dpi, without furniture. Spaces are flat fills in the six
# space colours, separated by ~1 px dark outlines; doors are small coloured markers. Each fill colour's
# regions are traced (contourpy at half a pixel), grown back over half the outline, and simplified. The
# scale follows the renderer's rule, recovered from all 797 SVG floors: 9.9792 pt per metre, unless the
# plan's longer side (plus the 2 m margins) would be under 332.6 pt or over 2217.6 pt, then it's clamped.
# Not read yet: labels (refs) and titles, which would need OCR.

PNG_PX_PER_PT = 100 / 72
PLAN_PT_PER_M = 9.9792
PLAN_SIDE_PT = (332.6, 2217.6)
PNG_FILL_TOLERANCE = 20  # max RGB channel difference to a fill colour
PNG_MARKER_TOLERANCE = 40
PNG_GROW_PX = 0.8  # half the outline: from the fill's edge to the wall's middle
PNG_MIN_THICKNESS_PX = 1.5  # thinner "regions" are antialiasing along lines and text
PNG_MARKER_CORE_TOLERANCE = 8
PNG_MARKER_MIN_CORE_PX = 12  # door dots have ~21 px of their exact colour, entrances ~58; text edges 0-3
PNG_MARKER_MIN_SOLIDITY = 0.7  # markers are solid: most of the blob is that exact colour


def _rgb(colour: str) -> tuple[int, int, int]:
    return (int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16))


def _regions(mask: Any, min_area: float, thickness: float) -> list[Polygon]:
    """Outer outlines (px) of a boolean mask's regions. contourpy traces outer edges counter-clockwise
    and holes (label text) clockwise, so holes are dropped by direction. Regions thinner than `thickness`
    everywhere are antialiasing along lines and text, not drawn shapes."""
    rings = contourpy.contour_generator(z=mask.astype(float), line_type=contourpy.LineType.Separate).lines(
        0.5
    )
    shapes = []
    for ring in rings:
        if len(ring) < 4 or not LinearRing(ring).is_ccw:
            continue
        shape = Polygon(ring)
        if not shape.is_valid:
            shape = max(getattr(fixed := shape.buffer(0), "geoms", [fixed]), key=lambda g: g.area)
        if shape.area >= min_area and not shape.buffer(-thickness).is_empty:
            shapes.append(shape)
    return shapes


def _valid_ring(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """The outline itself, or the largest valid piece of it when tracing left a self-crossing spike."""
    shape = Polygon(points)
    if shape.is_valid:
        return points
    fixed = make_valid(shape)
    pieces = [g for g in getattr(fixed, "geoms", [fixed]) if isinstance(g, Polygon)]
    return [(round(x, 4), round(y, 4)) for x, y in max(pieces, key=lambda g: g.area).exterior.coords[:-1]]


def _read_png_plan(path: Path, metres_per_px: float | None) -> _PlanFloor:
    """One floor plan PNG in metres, with the lower-left corner of its outlines at (0, 0)."""
    rgb = np.asarray(Image.open(path).convert("RGB")).astype(int)

    def near(colour: str, tolerance: int) -> Any:
        return np.abs(rgb - np.array(_rgb(colour))).max(axis=2) <= tolerance

    outlines = [
        (fill, shape.buffer(PNG_GROW_PX, join_style="mitre").simplify(0.5))
        for fill in FILL_TYPES
        for shape in _regions(near(fill, PNG_FILL_TOLERANCE), min_area=6.0, thickness=PNG_MIN_THICKNESS_PX)
    ]
    if not outlines:
        raise ValueError(f"{path.name}: no space fills found")
    markers = []
    for stroke in MARKER_KINDS:
        core = near(stroke, PNG_MARKER_CORE_TOLERANCE)
        for blob in _regions(near(stroke, PNG_MARKER_TOLERANCE), min_area=4.0, thickness=0.0):
            bx0, by0, bx1, by1 = blob.bounds
            solid = core[int(by0) : int(by1) + 2, int(bx0) : int(bx1) + 2].sum()
            if solid >= max(PNG_MARKER_MIN_CORE_PX, PNG_MARKER_MIN_SOLIDITY * blob.area):
                markers.append((stroke, (blob.centroid.x, blob.centroid.y)))
    x0, y0, x1, y1 = unary_union([shape for _, shape in outlines]).bounds
    if metres_per_px:
        px_per_m = 1 / metres_per_px
    else:  # the longer side plus a 2 m margin at each end is clamped into PLAN_SIDE_PT
        px_per_m, longest = PLAN_PT_PER_M * PNG_PX_PER_PT, max(x1 - x0, y1 - y0)
        low, high = (side * PNG_PX_PER_PT for side in PLAN_SIDE_PT)
        if not low <= longest + 2 * PLAN_MARGIN_M * px_per_m <= high:
            side = low if longest + 2 * PLAN_MARGIN_M * px_per_m < low else high
            px_per_m = (side - longest) / (2 * PLAN_MARGIN_M)

    def metres(p: tuple[float, float]) -> tuple[float, float]:
        return (round((p[0] - x0) / px_per_m, 4), round((y1 - p[1]) / px_per_m, 4))  # image y grows down

    return _PlanFloor(
        title=None,
        outlines=[
            (fill, _valid_ring([metres(p) for p in list(shape.exterior.coords)[:-1]]))
            for fill, shape in outlines
        ],
        markers=[(stroke, metres(p)) for stroke, p in markers],
        labels=[],
        assets=[],
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
"""Where a guessed staircase should stop: stair spaces (and stairwells) first, then corridors and halls."""


def _stop_rank(space: dict[str, Any]) -> int:
    return 2 if space["room_type"] == "stairwell" else STOP_RANK.get(space["type"], 0)


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
        rank = min(_stop_rank(by_id[a]), _stop_rank(by_id[b]))
        return (rank, iou, -pa.centroid.distance(pb.centroid))

    links = []
    while True:
        reached = _reachable(neighbours)
        missing = [sid for sid in by_id if sid not in reached]
        if not missing or len(reached) == 1:  # all reachable, or no entrance to start from
            return links
        options = []
        for rank in sorted({_stop_rank(by_id[sid]) for sid in missing}, reverse=True):
            for a in (sid for sid in missing if _stop_rank(by_id[sid]) == rank):
                i = levels.index(level_of[a])
                for lv in levels[max(i - 1, 0) : i + 2]:
                    options += [
                        (a, b["id"]) for b in spaces_by_level[lv] if lv != level_of[a] and b["id"] in reached
                    ]
            if options:  # best-ranked unreachable spaces that have a reachable floor next to them
                break
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


# ---- types from furniture, learned from the kit's train buildings -------------------------------------------
#
# The kit furnishes each room by its type (beds in bedrooms, desks in offices, CNC mills in production zones)
# and the plans draw every piece: category by colour, exact footprint. So a room's furniture tells its type,
# and a piece's size and room tell what it is. Held out (dev, test): room types 98-99% right, asset types
# 96%. That leans on the kit's generator, which a real architect's plan won't follow, so every such value
# is listed in the draft's "guesses" with its confidence.

TYPES_LEARNED_FROM = "train"
SMOOTHING = 0.1
AMBIGUOUS_TYPES = frozenset({"room", "hall", "corridor"})
"""Space types whose colour leaves the room type open (corridor: corridor or stairwell)."""


@dataclass
class _TypeModel:
    rooms: Counter  # (space type, room type) -> spaces seen
    room_tokens: dict[tuple[str, str], Counter]  # -> furniture and area tokens seen in them
    token_totals: dict[tuple[str, str], int]
    vocabulary: int
    asset_types: dict[tuple[str, ...], Counter]  # (category, size, room type), (category, size), (category,)
    room_names: dict[str, str]  # room type -> name prefix: "Quality lab"
    asset_names: dict[str, str]  # asset type -> name prefix: "CNC mill"


def _size(width: float, depth: float, digits: int) -> str:
    short, long = sorted((width, depth))
    return f"{short:.{digits}f}x{long:.{digits}f}"


def _room_tokens(area: float, furniture: list[tuple[str, float, float]]) -> list[str]:
    """What a space looks like on the plan: each piece's category and size, plus its area (counted twice)."""
    area_token = f"area:{int(math.log2(area + 1))}"
    return [f"{c}:{_size(w, d, 1)}" for c, w, d in furniture] + [area_token, area_token]


def _name_prefix(name: str, ref: str) -> str | None:
    """'Quality lab 0.21' (ref 0.21) -> 'Quality lab'; 'Desk 2.03-B' (in room 2.03) -> 'Desk'."""
    head, _, tail = name.rpartition(" ")
    return head if head and ref and tail.startswith(ref) else None


@cache
def _type_model() -> _TypeModel | None:
    """Room and asset types as the kit's train buildings use them; None without the kit's data."""
    files = sorted((get_settings().synera_kit_dir / "data" / TYPES_LEARNED_FROM).glob("*/building.json"))
    if not files:
        return None
    rooms: Counter = Counter()
    room_tokens: dict[tuple[str, str], Counter] = defaultdict(Counter)
    asset_types: dict[tuple[str, ...], Counter] = defaultdict(Counter)
    room_names: dict[str, Counter] = defaultdict(Counter)
    asset_names: dict[str, Counter] = defaultdict(Counter)
    for path in files:
        raw = json.loads(path.read_text(encoding="utf-8"))
        spaces = {s["id"]: s for s in raw["spaces"]}
        furniture = defaultdict(list)
        for a in raw["assets"]:
            width, depth, space = a["footprint"]["width_m"], a["footprint"]["depth_m"], spaces[a["space_id"]]
            furniture[space["id"]].append((a["category"], width, depth))
            size = _size(width, depth, 2)
            for key in ((a["category"], size, space["room_type"]), (a["category"], size), (a["category"],)):
                asset_types[key][a["type"]] += 1
            if prefix := _name_prefix(a["name"], space["ref"]):
                asset_names[a["type"]][prefix] += 1
        for s in raw["spaces"]:
            rooms[(s["type"], s["room_type"])] += 1
            room_tokens[(s["type"], s["room_type"])].update(
                _room_tokens(Polygon(s["polygon"]).area, furniture[s["id"]])
            )
            if prefix := _name_prefix(s["name"], s["ref"]):
                room_names[s["room_type"]][prefix] += 1
    return _TypeModel(
        rooms=rooms,
        room_tokens=dict(room_tokens),
        token_totals={k: sum(v.values()) for k, v in room_tokens.items()},
        vocabulary=len({t for tokens in room_tokens.values() for t in tokens}),
        asset_types=dict(asset_types),
        room_names={k: v.most_common(1)[0][0] for k, v in room_names.items()},
        asset_names={k: v.most_common(1)[0][0] for k, v in asset_names.items()},
    )


def _guess_room_type(model: _TypeModel, space_type: str, tokens: list[str]) -> tuple[str, float] | None:
    """Most likely room type for a space of this type and its probability (naive Bayes over the tokens),
    or None when the type has only one room type."""
    scores = {}
    for (kind, room_type), n in model.rooms.items():
        if kind != space_type:
            continue
        seen, total = model.room_tokens[(kind, room_type)], model.token_totals[(kind, room_type)]
        scores[room_type] = math.log(n) + sum(
            math.log((seen[t] + SMOOTHING) / (total + SMOOTHING * model.vocabulary)) for t in tokens
        )
    if len(scores) < 2:
        return None
    top = max(scores.values())
    weights = {room_type: math.exp(score - top) for room_type, score in scores.items()}
    best = max(weights, key=lambda room_type: weights[room_type])
    return best, weights[best] / sum(weights.values())


def _guess_asset_type(
    model: _TypeModel, category: str, width: float, depth: float, room_type: str
) -> tuple[str, float]:
    """Most common asset type for this category and size (in this room type if seen), and its share."""
    size = _size(width, depth, 2)
    for key in ((category, size, room_type), (category, size), (category,)):
        if seen := model.asset_types.get(key):
            asset_type, n = seen.most_common(1)[0]
            return asset_type, n / sum(seen.values())
    return category, 0.0


def _guess(
    element_id: str | None, field: str, value: Any, source: str, confidence: float | None
) -> dict[str, Any]:
    """One value the plan didn't state. source: furniture | footprint | reachability | default."""
    rounded = None if confidence is None else round(confidence, 4)
    return {"element_id": element_id, "field": field, "value": value, "source": source, "confidence": rounded}


def _furniture(
    plan: _PlanFloor, spaces: list[dict[str, Any]], shift: tuple[float, float]
) -> list[dict[str, Any]]:
    """Drawn assets in the building frame, each in the (smallest) space containing its centre."""
    shapes = [Polygon(s["polygon"]) for s in spaces]
    tree = STRtree(shapes)
    out = []
    for category, corners in plan.assets:
        if len(corners) < 4:
            continue
        (x0, y0), (x1, y1), (x2, y2) = corners[:3]
        centre = Point(
            sum(x for x, _ in corners[:4]) / 4 + shift[0], sum(y for _, y in corners[:4]) / 4 + shift[1]
        )
        inside = [int(i) for i in tree.query(centre, predicate="within")]
        out.append({
            "space": min(inside, key=lambda i: shapes[i].area) if inside else int(tree.nearest(centre)),
            "category": category, "position": [round(centre.x, 4), round(centre.y, 4)],
            "width_m": round(math.hypot(x1 - x0, y1 - y0), 3), "depth_m": round(math.hypot(x2 - x1, y2 - y1), 3),
            "rotation_deg": round(math.degrees(math.atan2(y1 - y0, x1 - x0)) % 360, 1),
        })  # fmt: skip
    return out


def _type_and_name(
    level: int, floor_id: str, spaces: list[dict[str, Any]], furniture: list[dict[str, Any]], guesses: list
) -> list[dict[str, Any]]:
    """Room types from each space's furniture, names like the kit's ('Office 0.21'), and the assets typed
    from their size and room ('Desk 0.21-B'). Every guessed value goes into `guesses`."""
    model = _type_model()
    pieces = defaultdict(list)
    for f in furniture:
        pieces[f["space"]].append((f["category"], f["width_m"], f["depth_m"]))
    for i, s in enumerate(spaces):
        if s["type"] in AMBIGUOUS_TYPES and model is None:
            guesses.append(_guess(s["id"], "room_type", s["room_type"], "default", None))
        elif s["type"] in AMBIGUOUS_TYPES and model is not None:
            tokens = _room_tokens(Polygon(s["polygon"]).area, pieces[i])
            if guess := _guess_room_type(model, s["type"], tokens):
                s["room_type"] = guess[0]
                guesses.append(_guess(s["id"], "room_type", guess[0], "furniture", guess[1]))
        prefix = (model.room_names.get(s["room_type"]) if model else None) or TYPE_NAMES[s["type"]]
        s["name"] = f"{prefix} {s['ref'] or f'{level}-{i:03d}'}"

    typed = []
    for f in furniture:
        if model is None:
            typed.append((f, f["category"], "default", None))
        else:
            asset_type, confidence = _guess_asset_type(
                model, f["category"], f["width_m"], f["depth_m"], spaces[f["space"]]["room_type"]
            )
            typed.append((f, asset_type, "footprint", confidence))
    per_room, numbered = Counter((f["space"], t) for f, t, _, _ in typed), Counter()
    assets = []
    for k, (f, asset_type, source, confidence) in enumerate(typed):
        space = spaces[f["space"]]
        prefix = (model.asset_names.get(asset_type) if model else None) or asset_type.replace(
            "_", " "
        ).capitalize()
        numbered[(f["space"], asset_type)] += 1
        n = numbered[(f["space"], asset_type)]
        suffix = "" if per_room[(f["space"], asset_type)] == 1 else f"-{chr(64 + n) if n <= 26 else n}"
        asset_id = f"{floor_id}-A{k:04d}"
        assets.append({
            "id": asset_id, "name": f"{prefix} {space['ref'] or space['name']}{suffix}",
            "category": f["category"], "type": asset_type, "space_id": space["id"], "floor_id": floor_id,
            "position": f["position"],
            "footprint": {"width_m": f["width_m"], "depth_m": f["depth_m"], "rotation_deg": f["rotation_deg"]},
            "source": "plan",
        })  # fmt: skip
        guesses.append(_guess(asset_id, "type", asset_type, source, confidence))
    return assets


def draft_from_plans(
    images: list[Path], *, name: str, levels: list[int] | None = None, metres_per_pixel: float | None = None
) -> dict[str, Any]:
    """Floor plans -> kit-format building.json. Reads the kit's vector plans (floor_<level>.svg) exactly:
    outlines, space types, refs, doors and entrances, furniture; stairs and lifts from spaces stacked
    between floors. `levels` default to the number in each file name (floor_-1.svg), else 0, 1, 2, ...;
    the scale comes from the plan's 2 m margin unless `metres_per_pixel` (metres per SVG pt) is given.

    Everything the plans don't state is listed in an extra top-level "guesses" key (the rest of the code
    ignores it): {element_id, field, value, source, confidence}, element_id None for the whole building.
    Sources: furniture (room types, with their probability), footprint (asset types, with the share of
    same-sized pieces that type had in train), reachability (staircases added so every space can be
    reached), default (floor heights 3 m, door widths 0.9 m, no steps). The frame's origin is arbitrary.
    Use floor_<level>.svg, not the _plain ones, which have no furniture.

    PNG plans (floor_<level>_plain.png, or any PNG drawn the same way) work too, less exactly: outlines
    traced from the fill colours (to about a pixel), doors from the markers, scale from the renderer's
    rule (or `metres_per_pixel`); no refs, titles or furniture, so room types are guessed from area alone."""
    if not images:
        raise ValueError("no floor plans given")
    paths = [Path(p) for p in images]
    if any(p.suffix.lower() not in (".svg", ".png") for p in paths):
        raise NotImplementedError("only the kit's plans can be read: floor_<level>.svg or .png")
    if levels is None:
        numbers = [re.search(r"floor_(-?\d+)", p.stem) for p in paths]
        levels = [int(m.group(1)) for m in numbers] if all(numbers) else list(range(len(paths)))  # type: ignore[union-attr]
    if len(levels) != len(paths) or len(set(levels)) != len(levels):
        raise ValueError(f"need one distinct level per plan, got {levels} for {len(paths)} plans")
    plans = {
        level: (_read_png_plan if p.suffix.lower() == ".png" else _read_plan)(p, metres_per_pixel)
        for level, p in zip(levels, paths, strict=True)
    }
    shifts = _line_up(plans)

    building_id = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-") or "drafted"
    raw: dict[str, Any] = {
        "building": {"id": building_id, "name": name, "source": "floor_plans", "units": "m"},
        "floors": [], "spaces": [], "openings": [], "vertical_connectors": [], "assets": [], "guesses": [],
    }  # fmt: skip
    guesses = raw["guesses"]
    guesses.append(_guess(None, "openings.width_m", DEFAULT_DOOR_WIDTH_M, "default", None))
    guesses.append(_guess(None, "openings.step_free", True, "default", None))
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
        guesses.append(_guess(floor_id, "elevation_m", DEFAULT_STOREY_M * level, "default", None))
        guesses.append(_guess(floor_id, "height_m", DEFAULT_STOREY_M, "default", None))
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
        furniture = _furniture(plan, spaces, (dx, dy))
        raw["assets"] += _type_and_name(level, floor_id, spaces, furniture, guesses)
        raw["spaces"] += spaces
        raw["openings"] += _openings(plan, floor_id, spaces, (dx, dy))
        spaces_by_level[level] = spaces
    raw["vertical_connectors"] = _connectors(building_id, spaces_by_level, raw["openings"])
    for c in raw["vertical_connectors"]:
        if c["synthetic"]:
            guesses.append(_guess(c["id"], "vertical_connector", c["space_ids"], "reachability", None))
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


ASSET_MATCH_M = 0.3
"""A drafted asset counts as a true one when it stands within this distance of it, on the same level."""


def _asset_scores(predicted: Building, truth: Building) -> tuple[float, float]:
    """(share of true assets with a drafted one within ASSET_MATCH_M, share of those found with the right type)."""
    by_floor: dict[str, list[Any]] = defaultdict(list)
    for a in predicted.assets.values():
        by_floor[a.floor_id].append(a)
    found = right = 0
    for floor in truth.floors.values():
        drafted_floor = predicted.floor_at_level(floor.level)
        drafted = by_floor.get(drafted_floor.id, []) if drafted_floor else []
        if not drafted:
            continue
        tree = STRtree([Point(a.position) for a in drafted])
        for a in (a for a in truth.assets.values() if a.floor_id == floor.id):
            nearest = drafted[int(tree.nearest(Point(a.position)))]
            if math.dist(nearest.position, a.position) <= ASSET_MATCH_M:
                found += 1
                right += nearest.type == a.type
    return _ratio(found, len(truth.assets)), _ratio(right, found)


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
    scores["asset_recall"], scores["asset_type_accuracy"] = _asset_scores(p, t)
    headline = (
        scores["area_iou"],
        _f1(space_precision, space_recall),
        scores["room_type_accuracy"],
        _f1(door_precision, door_recall),
        _f1(connector_precision, connector_recall),
    )
    scores["score"] = sum(headline) / len(headline)
    return scores


# ---- what changed between two versions of a building (Level 5: keep the map current) ---------------------
#
# Yesterday's plan against today's, or the building before and after edits: which spaces were added,
# removed, split, merged, reshaped, retyped or renamed; which doors were added, removed or widened; which
# machines and furniture are new, gone or moved. Spaces match by overlap, doors by position, assets by
# type and position, so ids don't need to be stable (drafts from plans number things afresh). Stairs and
# lifts aren't compared yet.

ChangeKind = Literal[
    "space_added", "space_removed", "space_split", "spaces_merged", "space_reshaped", "space_retyped",
    "space_renamed", "door_added", "door_removed", "door_resized", "asset_added", "asset_removed", "asset_moved",
]  # fmt: skip
RESHAPED_IOU = 0.95  # a matched space whose outline overlaps less than this changed shape
PART_OF = 0.8  # a space at least this much inside another is a part of it (splits, merges)
SAME_DOOR_M = 0.5
SAME_SPOT_M = 0.3  # an asset this close to where it was hasn't moved
DOOR_WIDTH_CHANGE_M = 0.05
ASSET_ORDER = {"machine": 0, "equipment": 1, "fixture": 2, "furniture": 3}


@dataclass(frozen=True)
class Change:
    kind: ChangeKind
    text: str
    """One sentence for people: 'CNC mill H-A1-A moved 14.2 m: from ... to ...'."""
    before_ids: tuple[str, ...] = ()
    after_ids: tuple[str, ...] = ()
    category: str = ""  # asset category for asset changes


def _parts(whole: Polygon, pieces: dict[str, Polygon]) -> list[str]:
    """Ids of the pieces lying mostly inside `whole`."""
    return [i for i, p in pieces.items() if p.area and whole.intersection(p).area >= PART_OF * p.area]


def _space_changes(before: Building, after: Building) -> list[Change]:
    matched = {b: a for b, (a, _) in _match_spaces(after, before).items()}
    matched_new = {a: b for b, a in matched.items()}
    out: list[Change] = []
    explained_before, explained_after = set(), set()
    for floor in before.floors_by_level():
        after_floor = after.floor_at_level(floor.level)
        olds = {s.id: s.shape for s in before.spaces_on(floor.id)}
        news = {s.id: s.shape for s in after.spaces_on(after_floor.id)} if after_floor else {}
        # Parts are new or unchanged pieces of this very space, never spaces matched elsewhere: OSM draws
        # rooms inside corridors and halls, which aren't a split.
        for old_id, shape in olds.items():  # one space became several
            pieces = {i: p for i, p in news.items() if matched_new.get(i, old_id) == old_id}
            if len(parts := _parts(shape, pieces)) >= 2:
                names = " and ".join(after.spaces[p].name for p in parts)
                out.append(
                    Change(
                        "space_split",
                        f"{before.label(old_id)} was split into {names}",
                        (old_id,),
                        tuple(parts),
                    )
                )
                explained_before.add(old_id)
                explained_after.update(parts)
        for new_id, shape in news.items():  # several spaces became one
            pieces = {i: p for i, p in olds.items() if matched.get(i, new_id) == new_id}
            if new_id not in explained_after and len(parts := _parts(shape, pieces)) >= 2:
                names = " and ".join(before.spaces[p].name for p in parts)
                out.append(
                    Change(
                        "spaces_merged",
                        f"{names} were merged into {after.label(new_id)}",
                        tuple(parts),
                        (new_id,),
                    )
                )
                explained_before.update(parts)
                explained_after.add(new_id)
    for old_id, new_id in matched.items():
        if old_id in explained_before or new_id in explained_after:
            continue
        old, new = before.spaces[old_id], after.spaces[new_id]
        overlap = old.shape.intersection(new.shape).area
        if overlap / (old.area + new.area - overlap) < RESHAPED_IOU:
            text = f"{after.label(new_id)} changed shape: {old.area:.1f} → {new.area:.1f} m²"
            out.append(Change("space_reshaped", text, (old_id,), (new_id,)))
        if old.room_type != new.room_type:
            was = old.room_type.replace("_", " ")
            text = (
                f"{before.label(old_id)} is now {new.name}, a {new.room_type.replace('_', ' ')} (was {was})"
            )
            out.append(Change("space_retyped", text, (old_id,), (new_id,)))
        elif (old.name, old.ref) != (new.name, new.ref):
            out.append(
                Change(
                    "space_renamed", f"{before.label(old_id)} is now called {new.name}", (old_id,), (new_id,)
                )
            )
    kept_after = set(matched.values()) | explained_after
    for old_id in before.spaces:
        if old_id not in matched and old_id not in explained_before:
            out.append(Change("space_removed", f"{before.label(old_id)} is gone", (old_id,)))
    for new_id in after.spaces:
        if new_id not in kept_after:
            out.append(Change("space_added", f"New space: {after.label(new_id)}", (), (new_id,)))
    return out


def _door_text(b: Building, door: Any) -> str:
    rooms = [b.label(s) for s in door.space_ids if s != OUTSIDE]
    return f"the entrance to {rooms[0]}" if door.is_entrance else f"the door between {' and '.join(rooms)}"


def _pair_up(
    olds: list[Any], news: list[Any], within: float
) -> tuple[list[tuple[Any, Any]], list[Any], list[Any]]:
    """Closest-first pairs of elements on the same level no further apart than `within` (inf: any),
    plus the unpaired ones on each side. Elements are (level, position, payload)."""
    candidates = sorted(
        (math.dist(o[1], n[1]), i, k)
        for i, o in enumerate(olds)
        for k, n in enumerate(news)
        if o[0] == n[0] or within == math.inf
    )
    pairs, used_old, used_new = [], set(), set()
    for distance, i, k in candidates:
        if distance <= within and i not in used_old and k not in used_new:
            pairs.append((olds[i], news[k]))
            used_old.add(i)
            used_new.add(k)
    return (
        pairs,
        [o for i, o in enumerate(olds) if i not in used_old],
        [n for k, n in enumerate(news) if k not in used_new],
    )


def _door_changes(before: Building, after: Building) -> list[Change]:
    def doors(b: Building) -> list[tuple[int, Any, Any]]:
        return [(b.floors[o.floor_id].level, o.position, o) for o in b.openings.values()]

    pairs, gone, new = _pair_up(doors(before), doors(after), SAME_DOOR_M)
    out = []
    for (_, _, old), (_, _, door) in pairs:
        if abs(old.width_m - door.width_m) >= DOOR_WIDTH_CHANGE_M:
            verb = "widened" if door.width_m > old.width_m else "narrowed"
            text = f"{_door_text(after, door).capitalize()} was {verb} from {old.width_m:g} m to {door.width_m:g} m"
            out.append(Change("door_resized", text, (old.id,), (door.id,)))
    out += [Change("door_removed", f"Removed: {_door_text(before, d)}", (d.id,)) for _, _, d in gone]
    out += [Change("door_added", f"New: {_door_text(after, d)}", (), (d.id,)) for _, _, d in new]
    return out


def _asset_changes(before: Building, after: Building) -> list[Change]:
    by_type: dict[str, tuple[list, list]] = defaultdict(lambda: ([], []))
    for side, b in enumerate((before, after)):
        for a in b.assets.values():
            by_type[a.type][side].append((b.floors[a.floor_id].level, a.position, a))
    out = []
    for olds, news in by_type.values():
        _, olds, news = _pair_up(olds, news, SAME_SPOT_M)  # where they were: unchanged
        moved, gone, new = _pair_up(olds, news, math.inf)  # the rest of the same type moved, nearest first
        for (old_level, _, old), (new_level, _, a) in moved:
            metres = math.dist(old.position, a.position)
            how = f"{metres:.1f} m" if old_level == new_level else f"from level {old_level} to {new_level}"
            text = f"{old.name} moved {how}: from {before.label(old.space_id)} to {after.label(a.space_id)}"
            out.append(Change("asset_moved", text, (old.id,), (a.id,), a.category))
        out += [
            Change(
                "asset_removed", f"Gone: {a.name} from {before.label(a.space_id)}", (a.id,), (), a.category
            )
            for _, _, a in gone
        ]
        out += [
            Change("asset_added", f"New: {a.name} in {after.label(a.space_id)}", (), (a.id,), a.category)
            for _, _, a in new
        ]
    return sorted(out, key=lambda c: ASSET_ORDER.get(c.category, 9))


def diff(before: dict[str, Any], after: dict[str, Any], *, align: bool = True) -> list[Change]:
    """What changed from one version of a building to the next (both kit-format building.json): spaces,
    then doors, then assets (machines and equipment before furniture). With `align` (the default) the
    later version is first moved onto the earlier one, so drafts from plans compare whatever their origin."""
    old, new = parse_building(before), parse_building(after)
    if align and (shift := _best_shift(new, old)) != (0.0, 0.0):
        new = parse_building(_shifted(after, *shift))
    return _space_changes(old, new) + _door_changes(old, new) + _asset_changes(old, new)
