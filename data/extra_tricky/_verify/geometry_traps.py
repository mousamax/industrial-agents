"""Recompute every answer in data/extra_tricky/_staging/geometry_traps.jsonl from the kit data.

Run: uv run python data/extra_tricky/_verify/geometry_traps.py
Uses the repo's ground-truth geometry (src/app/building/geometry.py, which reproduces Synera's answers on
every dev+train geometry question) plus shapely for things the kit doesn't define (union footprint, raw
contact length). Each `cNN` returns (answer, facts): `answer` must equal the jsonl's answer, `facts` are the
intermediate values quoted in the case's reasoning.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

from shapely.ops import unary_union

from app.building import geometry as g
from app.building.model import Building, load_building

ROOT = Path(__file__).resolve().parents[3]
STAGING = ROOT / "data/extra_tricky/_staging/geometry_traps.jsonl"
M2_TO_FT2 = 1 / 0.3048**2  # 10.7639...

_cache: dict[str, Building] = {}


def B(bid: str) -> Building:
    if bid not in _cache:
        path = next(
            p
            for p in (ROOT / "synera-kit/data/dev" / bid, ROOT / "synera-kit/data/train" / bid)
            if p.exists()
        )
        _cache[bid] = load_building(path)
    return _cache[bid]


def overlaps(b: Building, sid: str, direction: str) -> list[tuple[float, str]]:
    s = b.spaces[sid]
    fid = g.adjacent_floor(b, s.floor_id, direction)
    if fid is None:
        return []
    out = [
        (s.shape.intersection(o.shape).area, o.id) for o in b.spaces_on(fid) if s.shape.intersects(o.shape)
    ]
    return sorted([o for o in out if o[0] > 0.01], reverse=True)


def raw_contact(b: Building, a: str, c: str) -> tuple[float, float]:
    """(length of a's outline touching c's outline within 1 cm, gap between the polygons)."""
    A, C = b.spaces[a].shape, b.spaces[c].shape
    return A.exterior.intersection(C.exterior.buffer(0.01)).length, A.distance(C)


def num(value: float, unit: str, rel: float = 0.0, abs_: float = 0.0) -> dict:
    return {
        "type": "number",
        "value": value,
        "unit": unit,
        "relative_tolerance": rel,
        "absolute_tolerance": abs_,
    }


def anchor_3d(b: Building, a: str, c: str) -> float:
    """Wrong-on-purpose variant: representative points (always inside) instead of centroids."""
    sa, sc = b.spaces[a], b.spaces[c]
    return math.dist(
        (*sa.anchor, b.floors[sa.floor_id].elevation_m), (*sc.anchor, b.floors[sc.floor_id].elevation_m)
    )


# ---- 01-04 straight_line_3d -------------------------------------------------------------------------------


def _3d(bid: str, a: str, c: str) -> tuple[dict, dict]:
    b = B(bid)
    sa, sc = b.spaces[a], b.spaces[c]
    d = g.straight_line_3d(b, a, c)
    facts = {
        "centroid_a": tuple(round(v, 3) for v in sa.centroid),
        "centroid_b": tuple(round(v, 3) for v in sc.centroid),
        "a_centroid_inside": sa.shape.contains(sa.shape.centroid),
        "b_centroid_inside": sc.shape.contains(sc.shape.centroid),
        "z_a": b.floors[sa.floor_id].elevation_m,
        "z_b": b.floors[sc.floor_id].elevation_m,
        "horizontal": round(math.dist(sa.centroid, sc.centroid), 3),
        "distance": round(d, 2),
        "anchor_distance": round(anchor_3d(b, a, c), 2),
    }
    return num(round(d, 2), "m", rel=0.02), facts


def c01():
    return _3d("swiss-5097", "swiss-5097-F2-S074", "swiss-5097-F0-S032")


def c02():
    return _3d("osm-way476929995", "osm-way476929995-F4-S009", "osm-way476929995-F1-S007")


def c03():
    ans, f = _3d("proc-factory-dev-005", "proc-factory-dev-005-F0-S023", "proc-factory-dev-005-F2-S004")
    b = B("proc-factory-dev-005")
    f["ground_height_m"] = b.floors["proc-factory-dev-005-F0"].height_m
    f["naive_dz_11_5"] = round(math.hypot(f["horizontal"], 11.5), 2)
    return ans, f


def c04():
    ans, f = _3d("osm-way27326387", "osm-way27326387-F-2-S003", "osm-way27326387-F1-S006")
    f["naive_dz_6"] = round(math.hypot(f["horizontal"], 6.0), 2)
    return ans, f


# ---- 05-07 room_stacked ----------------------------------------------------------------------------------


def c05():
    b = B("swiss-460")
    sid = "swiss-460-F0-S010"
    above_floor = g.adjacent_floor(b, b.spaces[sid].floor_id, "above")
    best = g.stacked(b, sid, "above")
    facts = {
        "levels": [f.level for f in b.floors_by_level()],
        "above_floor": above_floor,
        "area": round(b.spaces[sid].area, 2),
        "overlaps": [(round(a, 2), i) for a, i in overlaps(b, sid, "above")],
        "best_type": b.spaces[best].type,
    }
    ans = {
        "type": "space",
        "space_id": best,
        "accept": ["2.18", "Stair hall 2.18"],
        "reject": ["2.17", "Bathroom 2.17", "Bathroom 2.17 (apartment 6)"],
    }
    return ans, facts


def c06():
    b = B("osm-way22744230")
    sid = "osm-way22744230-F5-S008"
    ov = overlaps(b, sid, "above")
    (a1, i1), (a2, i2) = ov[0], ov[1]
    assert a2 / a1 > 0.99, "top two overlaps must be a near 50/50 split"
    facts = {
        "area": round(b.spaces[sid].area, 2),
        "overlaps": [(round(a, 2), i) for a, i in ov[:4]],
        "kit_pick": g.stacked(b, sid, "above"),
    }
    ans = {
        "type": "clarification",
        "options": [i1, i2],
        "reason": "The two rooms above each cover about 39% of the room (30.72 vs 30.60 m2): no single room is directly above.",
    }
    return ans, facts


def c07():
    b = B("resplan-439")
    sid = "resplan-439-F0-S002"
    below = g.adjacent_floor(b, b.spaces[sid].floor_id, "below")
    assert below is None and len(b.floors) == 1
    ans = {
        "type": "false_premise",
        "correction": "The building has a single floor (Ground floor, level 0); there is no floor and no room below Bedroom 0.02.",
    }
    return ans, {"floors": [f.id for f in b.floors_by_level()], "below_floor": below}


# ---- 08-11 shared_walls_count -----------------------------------------------------------------------------


def _walls(bid: str, sid: str, extra_pairs: list[str]) -> tuple[dict, dict]:
    b = B(bid)
    counted = g.shared_wall_rooms(b, sid)
    every = [
        (o, round(L, 3), b.spaces[o].type, b.spaces[o].room_type)
        for o, L in g.shared_walls(b, sid, min_length_m=0.3)
    ]
    raw = {o: tuple(round(v, 3) for v in raw_contact(b, sid, o)) for o in extra_pairs}
    facts = {"counted": counted, "all_within_0_35_ge_0_3m": every, "raw_contact_and_gap": raw}
    return num(len(counted), "rooms"), facts


def c08():
    return _walls("resplan-1465", "resplan-1465-F0-S005", ["resplan-1465-F0-S001"])


def c09():
    return _walls("resplan-16154", "resplan-16154-F0-S005", ["resplan-16154-F0-S001"])


def c10():
    return _walls("proc-factory-dev-005", "proc-factory-dev-005-F0-S038", ["proc-factory-dev-005-F0-S004"])


def c11():
    return _walls(
        "proc-factory-dev-008",
        "proc-factory-dev-008-F0-S008",
        ["proc-factory-dev-008-F0-S001", "proc-factory-dev-008-F0-S003"],
    )


# ---- 12-13 largest_room ties ------------------------------------------------------------------------------


def c12():
    b = B("proc-campus-dev-013")
    ranked = g.rooms_by_area(b, "proc-campus-dev-013-F1", "storage")
    (s1, a1), (s2, a2) = ranked[0], ranked[1]
    assert abs(a1 - a2) < 1e-9
    ans = {
        "type": "clarification",
        "options": sorted([s1.id, s2.id]),
        "reason": "Storage 1.12 and Storage 1.13 are both 26.94 m2 (two 6.00 x 4.49 m rectangles side by side): an exact tie; name both.",
    }
    return ans, {"ranked": [(s.id, s.name, round(a, 6)) for s, a in ranked]}


def c13():
    b = B("osm-way360544293")
    ranked = g.rooms_by_area(b, "osm-way360544293-F0", "lobby")
    (s1, a1), (_s2, a2) = ranked[0], ranked[1]
    ans = {
        "type": "space",
        "space_id": s1.id,
        "accept": ["0.13", "Lobby 0.13"],
        "reject": ["0.14", "Edeka"],
        "area": num(round(a1, 2), "m2", rel=0.05),
    }
    return ans, {
        "ranked": [(s.id, s.name, round(a, 3)) for s, a in ranked],
        "gap_pct": round((a1 - a2) / a1 * 100, 3),
    }


# ---- 14-20 derived areas ----------------------------------------------------------------------------------


def c14():
    b = B("proc-campus-dev-000")
    labs = b.spaces_on("proc-campus-dev-000-F2", room_type="lab")
    m2 = g.total_area(b, "proc-campus-dev-000-F2", "lab")
    return num(round(m2 * M2_TO_FT2, 1), "ft2", rel=0.02), {
        "labs": [(s.name, round(s.area, 2)) for s in labs],
        "m2": round(m2, 2),
        "ft2": round(m2 * M2_TO_FT2, 1),
    }


def c15():
    b = B("osm-way360544293")
    spaces = b.spaces_on("osm-way360544293-F1")
    s = sum(x.area for x in spaces)
    u = unary_union([x.shape for x in spaces]).area
    return num(round(u / 10_000, 3), "ha", rel=0.01), {
        "n_spaces": len(spaces),
        "sum_m2": round(s, 1),
        "union_m2": round(u, 1),
        "sum_ha": round(s / 10_000, 3),
        "corridor_1_01_m2": round(b.spaces["osm-way360544293-F1-S001"].area, 1),
        "inside_corridor": sum(
            b.spaces["osm-way360544293-F1-S001"].shape.buffer(0.01).contains(x.shape) for x in spaces
        )
        - 1,
        "union_ha": round(u / 10_000, 3),
        "double_counted_m2": round(s - u, 1),
    }


def c16():
    b = B("swiss-203")
    a = b.spaces["swiss-203-F0-S023"].area
    c = b.spaces["swiss-203-F0-S018"].area
    pct = (a / c - 1) * 100
    return num(round(pct, 1), "%", abs_=1.0), {
        "living": round(a, 3),
        "bedroom": round(c, 3),
        "pct": round(pct, 2),
        "wrong_base_pct": round((a - c) / a * 100, 2),
        "ratio": round(a / c, 3),
    }


def c17():
    b = B("proc-factory-dev-005")
    fid = "proc-factory-dev-005-F1"
    k, o = g.total_area(b, fid, "kitchen"), g.total_area(b, fid, "office")
    return num(round(k / o, 3), "ratio", rel=0.01), {
        "kitchens": [(s.name, round(s.area, 2)) for s in b.spaces_on(fid, room_type="kitchen")],
        "n_offices": len(b.spaces_on(fid, room_type="office")),
        "kitchen_m2": round(k, 2),
        "office_m2": round(o, 2),
        "inverse": round(o / k, 3),
    }


def c18():
    b = B("proc-factory-dev-004")
    labs = [s for s in b.spaces_on("proc-factory-dev-004-F0") if "lab" in s.room_type]
    assert len(labs) == 1 and labs[0].room_type == "quality_lab"
    assert not b.spaces_on("proc-factory-dev-004-F0", room_type="lab")
    s = labs[0]
    ans = {
        "type": "space",
        "space_id": s.id,
        "accept": ["0.26", "Quality lab 0.26"],
        "reject": [],
        "area": num(round(s.area, 2), "m2", rel=0.05),
    }
    return ans, {
        "name": s.name,
        "room_type": s.room_type,
        "area": round(s.area, 2),
        "all_lab_like_in_building": [(x.id, x.room_type) for x in b.spaces.values() if "lab" in x.room_type],
    }


def c19():
    b = B("proc-factory-dev-011")
    ks = sorted(((s.area, s.id) for s in b.spaces.values() if s.room_type == "kitchen"), reverse=True)
    top = ks[0][0]
    tied = sorted(i for a, i in ks if abs(a - top) < 1e-6)
    per_floor = {
        f.id: round(max((s.area for s in b.spaces_on(f.id, room_type="kitchen")), default=0), 2)
        for f in b.floors_by_level()
    }
    ans = {
        "type": "clarification",
        "options": tied,
        "reason": "Kitchen 1.13 (Floor 1) and Kitchen 2.13 (Floor 2) are both 29.70 m2: the building-wide largest is a tie across floors.",
    }
    return ans, {
        "n_kitchens": len(ks),
        "top3": [(round(a, 2), i) for a, i in ks[:3]],
        "largest_per_floor": per_floor,
    }


def c20():
    b = B("swiss-1689")
    apt = [s for s in b.spaces.values() if s.name.endswith("(apartment 7)")]
    rooms = [s for s in apt if s.type == "room"]
    total = sum(s.area for s in rooms)
    return num(round(total, 2), "m2", rel=0.01), {
        "rooms": [(s.name, round(s.area, 3)) for s in rooms],
        "excluded": [(s.name, s.type, round(s.area, 3)) for s in apt if s.type != "room"],
        "rooms_m2": round(total, 2),
        "with_corridors": round(total + sum(s.area for s in apt if s.type == "corridor"), 2),
        "everything": round(sum(s.area for s in apt), 2),
    }


CASES = {
    f"xt-geometry_traps-{i:02d}": fn
    for i, fn in enumerate(
        [c01, c02, c03, c04, c05, c06, c07, c08, c09, c10, c11, c12, c13, c14, c15, c16, c17, c18, c19, c20],
        start=1,
    )
}


def main() -> int:
    rows = [json.loads(line) for line in STAGING.read_text().splitlines() if line.strip()]
    assert [r["id"] for r in rows] == list(CASES), "ids must be 01..20 in order"
    bad = 0
    for r in rows:
        expected, facts = CASES[r["id"]]()
        ok = r["answer"] == expected
        bad += not ok
        print(("OK  " if ok else "FAIL"), r["id"], json.dumps(expected)[:110])
        if not ok:
            print("   jsonl:", json.dumps(r["answer"]))
            print("   facts:", facts)
    print(f"{len(rows) - bad}/{len(rows)} match")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
