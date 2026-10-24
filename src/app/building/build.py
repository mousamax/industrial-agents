"""Level 4: create or update a building from floor plans, by conversation. "Minutes, not days."

OWNER: Owner B, geometry & build. Scored automatically ("correct buildings built from plans"), but the
input and the comparison aren't published yet: ask Synera (docs/synera/README.md, question 2) before
going deep. Until then, target: plan images (floor_<level>_plain.png) -> building.json in the kit format
that loads with app.building.model.parse_building and passes `validate` with no errors.

Train has 200 buildings with both the image and the true building.json: that's the test set for this.

Measured: `validate` finds 0 errors in all 280 kit buildings (266 warnings, all OSM/Swiss doors drawn
0.5-53 m off their wall). `compare` scores every train building 1.0 against itself; it assumes the draft
uses the truth's metre frame, so a draft shifted by 0.5 m already loses ~14% of its space matches.
"""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from numbers import Real
from pathlib import Path
from typing import Any, Literal, get_args

from shapely import STRtree
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
    seen, queue = {OUTSIDE}, deque([OUTSIDE])
    while queue:
        for nxt in neighbours[queue.popleft()] - seen:
            seen.add(nxt)
            queue.append(nxt)
    if neighbours[OUTSIDE]:
        for s in spaces:
            if s["id"] not in seen:
                error(s["id"], "not reachable from outside")
    return issues


def draft_from_plans(
    images: list[Path], *, name: str, levels: list[int] | None = None, metres_per_pixel: float | None = None
) -> dict[str, Any]:
    """Floor-plan images -> kit-format building.json (Claude vision + geometry clean-up). One image per
    floor, `levels` in the same order (default 0, 1, 2, ...)."""
    raise NotImplementedError("Owner B")


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


def compare(predicted: dict[str, Any], truth: dict[str, Any]) -> dict[str, float]:
    """How close a drafted building is to the true one (room count, area IoU, door/adjacency recall):
    our own score for Level 4 until Synera tells us theirs."""
    p, t = parse_building(predicted), parse_building(truth)
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
