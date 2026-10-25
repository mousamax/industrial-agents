"""Recompute every answer of data/extra_tricky/_staging/beyond_tools.jsonl from the kit data and assert it.

Direction beyond_tools: questions no single building_guide tool answers, solved by chaining routing,
geometry and asset lookups. Ground truth comes from src/app/building (RouteGraph under Synera's route rule,
geometry for areas and centroids, model for assets), never from the agent's tools.

    uv run python data/extra_tricky/_verify/beyond_tools.py            # assert every case
    uv run python data/extra_tricky/_verify/beyond_tools.py --facts    # also print intermediate values
"""

from __future__ import annotations

import itertools
import json
import math
import sys
from collections import Counter
from pathlib import Path

from app.building import geometry
from app.building.model import OUTSIDE, Building, load_building
from app.building.routing import WALKING, Constraints, RouteGraph, validate_route

ROOT = Path(__file__).resolve().parents[3]
JSONL = ROOT / "data/extra_tricky/_staging/beyond_tools.jsonl"
STEP_FREE = Constraints(step_free=True)


def is_room(s) -> bool:
    """What the reachability counts call a room (same rule as building_guide.tools.is_room)."""
    return s.type == "room" and s.room_type not in geometry.NOT_ROOMS


def sid(b: Building, reference: str) -> str:
    hits = b.resolve(reference)
    assert len(hits) == 1, (reference, [h.id for h in hits])
    return hits[0].id


def r2(x: float) -> float:
    return round(x, 2)


def route_ok(b: Building, route, constraints=WALKING) -> None:
    assert route is not None
    problems = validate_route(b, route.space_path, constraints)
    assert not problems, problems


# ---- the cases ---------------------------------------------------------------------------------------------


def c01():
    """Evacuation worst case: room on Floor 2 with the longest walk to its nearest entrance."""
    b = load_building("proc-campus-dev-000")
    d = RouteGraph(b).distances_from([OUTSIDE])
    rooms = [s for s in b.spaces_on("proc-campus-dev-000-F2") if is_room(s)]
    ranked = sorted(rooms, key=lambda s: d[s.id], reverse=True)
    top, second = ranked[0], ranked[1]
    return {
        "type": "space",
        "space_id": top.id,
        "accept": [top.ref, top.name],
        "reject": [second.ref, second.name],
        "walk_m": {
            "type": "number",
            "value": r2(d[top.id]),
            "unit": "m",
            "relative_tolerance": 0.02,
            "absolute_tolerance": 1.0,
        },
    }, {
        "top4": [(b.label(s.id), r2(d[s.id])) for s in ranked[:4]],
        "rooms": len(rooms),
        "nearest_room": (b.label(ranked[-1].id), r2(d[ranked[-1].id])),
    }


def c02():
    """Small TSP: from Office 3.07 collect a coffee machine, a 3D printer and a laser cutter, back to start."""
    b = load_building("proc-campus-dev-000")
    g = RouteGraph(b)
    start = sid(b, "Office 3.07")
    types = ["coffee_machine", "3d_printer", "laser_cutter"]
    holders = [sorted({a.space_id for a in b.assets_of_type(t)}) for t in types]
    cache: dict[str, dict[str, float]] = {}

    def d(x):
        if x not in cache:
            cache[x] = g.distances_from([x])
        return cache[x]

    best = (math.inf, None)
    for perm in itertools.permutations(range(3)):
        for combo in itertools.product(*[holders[i] for i in perm]):
            seq = [start, *combo, start]
            length = sum(d(x)[y] for x, y in itertools.pairwise(seq))
            if length < best[0] - 1e-9:
                best = (length, seq)
    # greedy nearest-neighbour (the trap)
    cur, todo, greedy, gseq = start, set(range(3)), 0.0, [start]
    while todo:
        metres, i, h = min((d(cur)[h], i, h) for i in todo for h in holders[i])
        greedy += metres
        cur = h
        todo.remove(i)
        gseq.append(h)
    greedy += d(cur)[start]
    gseq.append(start)
    seq = best[1]
    stops = list(dict.fromkeys(seq[1:-1]))
    return {
        "type": "number",
        "value": r2(best[0]),
        "unit": "m",
        "relative_tolerance": 0.02,
        "absolute_tolerance": 1.0,
        "stops": stops,
    }, {
        "best_seq": [b.label(x) for x in seq],
        "legs": [r2(d(x)[y]) for x, y in itertools.pairwise(seq)],
        "greedy": r2(greedy),
        "greedy_seq": [b.label(x) for x in gseq],
        "holders": {t: [b.label(h) for h in hs] for t, hs in zip(types, holders, strict=True)},
    }


def c03():
    """Meeting point: meeting room minimising the longer of two walks."""
    b = load_building("proc-factory-dev-003")
    g = RouteGraph(b)
    a, c = sid(b, "Office 1.18"), sid(b, "Locker room 0.39")
    rows = []
    for m in [s.id for s in b.spaces.values() if s.room_type == "meeting_room"]:
        ra, rc = g.shortest(a, m), g.shortest(c, m)
        route_ok(b, ra)
        route_ok(b, rc)
        rows.append((max(ra.length_m, rc.length_m), ra.length_m + rc.length_m, ra.length_m, rc.length_m, m))
    rows.sort()
    best, second = rows[0], rows[1]
    by_sum = min(rows, key=lambda r: (r[1], r[0]))
    s = b.spaces[best[4]]
    return {
        "type": "space",
        "space_id": s.id,
        "accept": [s.ref, s.name],
        "reject": [b.spaces[second[4]].name, b.spaces[by_sum[4]].name],
        "max_walk_m": {
            "type": "number",
            "value": r2(best[0]),
            "unit": "m",
            "relative_tolerance": 0.02,
            "absolute_tolerance": 1.0,
        },
    }, {
        "ranking": [(b.label(r[4]), r2(r[0]), r2(r[2]), r2(r[3]), r2(r[1])) for r in rows[:4]],
        "min_sum": (b.label(by_sum[4]), r2(by_sum[1]), r2(by_sum[0])),
    }


def c04():
    """Walking time at 1.2 m/s for a step-free tour with two lift rides."""
    b = load_building("proc-campus-dev-006")
    g = RouteGraph(b, STEP_FREE)
    stops = [sid(b, "Office 2.12"), sid(b, "Main lobby"), sid(b, "Lab 3.03")]
    legs = [g.shortest(x, y) for x, y in itertools.pairwise(stops)]
    for leg in legs:
        route_ok(b, leg, STEP_FREE)
    total = sum(leg.length_m for leg in legs)
    lifts = [e for leg in legs for e in leg.element_path if e in b.connectors]
    assert len(lifts) == 2 and all(b.connectors[e].kind == "lift" for e in lifts)
    seconds = total / 1.2
    return {
        "type": "number",
        "value": round(seconds / 60, 2),
        "unit": "min",
        "relative_tolerance": 0.02,
        "absolute_tolerance": 0.1,
        "seconds": round(seconds, 1),
        "length_m": r2(total),
    }, {
        "legs": [(r2(leg.length_m), [b.label(x) for x in leg.space_path], leg.element_path) for leg in legs],
        "lift_edge_m": [15 + 7.6, 15 + 11.4],
    }


def c05():
    """Doors (not open passages, not stairs) on the shortest route."""
    b = load_building("swiss-1785")
    r = RouteGraph(b).shortest(sid(b, "Kitchen 1.02 (apartment 5)"), sid(b, "Bathroom 0.04 (apartment 1)"))
    route_ok(b, r)
    kinds = [b.openings[e].kind if e in b.openings else b.connectors[e].kind for e in r.element_path]
    doors = sum(k == "door" for k in kinds)
    return {
        "type": "number",
        "value": doors,
        "unit": "doors",
        "relative_tolerance": 0.0,
        "absolute_tolerance": 0.0,
    }, {
        "length": r.length_m,
        "path": [b.label(x) for x in r.space_path],
        "elements": list(zip(r.element_path, kinds, strict=True)),
        "openings_total": sum(k in ("door", "opening") for k in kinds),
    }


def c06():
    """Percentage of rooms on Floor 2 reachable step-free from the street."""
    b = load_building("swiss-1785")
    reach = set(RouteGraph(b, STEP_FREE).distances_from([OUTSIDE]))
    walk = set(RouteGraph(b).distances_from([OUTSIDE]))
    rooms = [s for s in b.spaces_on("swiss-1785-F2") if is_room(s)]
    n = sum(s.id in reach for s in rooms)
    assert all(s.id in walk for s in rooms)
    return {
        "type": "number",
        "value": round(100 * n / len(rooms), 2),
        "unit": "%",
        "relative_tolerance": 0.0,
        "absolute_tolerance": 0.5,
        "reachable": n,
        "rooms": len(rooms),
    }, {
        "lifts": [k.name for k in b.connectors.values() if k.kind == "lift"],
        "connectors": [
            (k.name, k.kind, [b.floors[f].level for f, _ in k.stops]) for k in b.connectors.values()
        ],
        "reachable_names": sorted(b.spaces[s.id].name for s in rooms if s.id in reach),
        "entrances": [(o.id, b.label(o.other_side(OUTSIDE)), o.step_free) for o in b.openings_of(OUTSIDE)],
    }


def c07():
    """Most numerous item type on Floor 2 (differs from the building-wide winner)."""
    b = load_building("proc-campus-dev-001")
    floor = Counter(a.type for a in b.assets.values() if a.floor_id == "proc-campus-dev-001-F2").most_common()
    whole = Counter(a.type for a in b.assets.values()).most_common(3)
    assert floor[0][1] > floor[1][1]
    return {"type": "set", "values": [floor[0][0]], "count": floor[0][1]}, {
        "floor_top5": floor[:5],
        "building_top3": whole,
        "shelf_rooms": Counter(
            b.label(a.space_id) for a in b.assets_of_type("shelf", "proc-campus-dev-001-F2")
        ),
    }


def c08():
    """Nearest lab that has both a fume hood and an eyewash station."""
    b = load_building("proc-campus-dev-000")
    start = sid(b, "Office 2.16")
    d = RouteGraph(b).distances_from([start])
    labs = [s.id for s in b.spaces.values() if s.room_type == "lab"]
    types = lambda x: {a.type for a in b.assets_in(x)}  # noqa: E731
    both = sorted((x for x in labs if {"fume_hood", "eyewash_station"} <= types(x)), key=lambda x: d[x])
    fh = min({a.space_id for a in b.assets_of_type("fume_hood")}, key=lambda x: d[x])
    ew = min({a.space_id for a in b.assets_of_type("eyewash_station")}, key=lambda x: d[x])
    s = b.spaces[both[0]]
    return {
        "type": "space",
        "space_id": s.id,
        "accept": [s.ref, s.name],
        "reject": [b.spaces[fh].name, b.spaces[ew].name, b.spaces[both[1]].name],
        "walk_m": {
            "type": "number",
            "value": r2(d[s.id]),
            "unit": "m",
            "relative_tolerance": 0.02,
            "absolute_tolerance": 1.0,
        },
    }, {
        "both_ranked": [(b.label(x), r2(d[x])) for x in both[:4]],
        "nearest_fume_hood": (b.label(fh), r2(d[fh]), sorted(types(fh))),
        "nearest_eyewash": (b.label(ew), r2(d[ew]), sorted(types(ew))),
    }


def c09():
    """Articulation door: which single door closure cuts the most rooms off from every entrance."""
    b = load_building("osm-way27326387")
    base = set(RouteGraph(b).distances_from([OUTSIDE]))
    rooms = {s.id for s in b.spaces.values() if is_room(s)} & base
    res = []
    for o in b.openings.values():
        if o.is_entrance:
            continue
        after = set(RouteGraph(b, Constraints(closed=frozenset([o.id]))).distances_from([OUTSIDE]))
        res.append((len(rooms - after), o.id, sorted(rooms - after)))
    res.sort(key=lambda r: (-r[0], r[1]))
    best, second = res[0], res[1]
    assert best[0] > second[0]
    o = b.openings[best[1]]
    return {
        "type": "number",
        "value": best[0],
        "unit": "rooms",
        "relative_tolerance": 0.0,
        "absolute_tolerance": 0.0,
        "opening_id": o.id,
        "between": list(o.space_ids),
    }, {
        "door": [b.label(x) for x in o.space_ids],
        "second": (second[0], [b.label(x) for x in b.openings[second[1]].space_ids]),
        "cut_floors": Counter(b.floors[b.spaces[x].floor_id].name for x in best[2]),
        "rooms_total": len(rooms),
        "basement_rooms": sum(1 for x in rooms if b.spaces[x].floor_id.endswith("F-2")),
    }


def c10():
    """Floor with the highest average office area (not the highest total)."""
    b = load_building("proc-campus-dev-002")
    rows = []
    for f in b.floors_by_level():
        offices = b.spaces_on(f.id, room_type="office")
        if offices:
            total = sum(s.area for s in offices)
            rows.append((f.id, f.name, len(offices), total, total / len(offices)))
    best = max(rows, key=lambda r: r[4])
    biggest_total = max(rows, key=lambda r: r[3])
    assert best[0] != biggest_total[0]
    return {
        "type": "set",
        "values": [best[1]],
        "floor_id": best[0],
        "average_m2": {
            "type": "number",
            "value": r2(best[4]),
            "unit": "m2",
            "relative_tolerance": 0.02,
            "absolute_tolerance": 0.0,
        },
    }, {"rows": [(r[1], r[2], r2(r[3]), r2(r[4])) for r in rows]}


def c11():
    """Is there a room on Floor 1 with both a sink and a stove? (No.)"""
    b = load_building("proc-factory-dev-003")
    f = "proc-factory-dev-003-F1"
    sinks = {a.space_id for a in b.assets_of_type("sink", f)}
    stoves = {a.space_id for a in b.assets_of_type("stove", f)}
    assert sinks and stoves
    return {"type": "set", "values": sorted(sinks & stoves)}, {
        "sink_rooms": [b.label(x) for x in sinks],
        "stove_rooms": sorted(b.label(x) for x in stoves),
        "building_both": [
            b.label(x)
            for x in {a.space_id for a in b.assets_of_type("sink")}
            & {a.space_id for a in b.assets_of_type("stove")}
        ],
    }


def c12():
    """Item density of the room on Floor 1 holding the most items."""
    b = load_building("proc-campus-dev-001")
    spaces = b.spaces_on("proc-campus-dev-001-F1")
    ranked = sorted(spaces, key=lambda s: len(b.assets_in(s.id)), reverse=True)
    top = ranked[0]
    assert len(b.assets_in(top.id)) > len(b.assets_in(ranked[1].id))
    densest = max(spaces, key=lambda s: len(b.assets_in(s.id)) / s.area)
    n = len(b.assets_in(top.id))
    return {
        "type": "number",
        "value": round(n / top.area, 3),
        "unit": "items/m2",
        "relative_tolerance": 0.03,
        "absolute_tolerance": 0.0,
        "space_id": top.id,
        "items": n,
        "area_m2": r2(top.area),
    }, {
        "top3": [(b.label(s.id), len(b.assets_in(s.id)), r2(s.area)) for s in ranked[:3]],
        "densest": (
            b.label(densest.id),
            len(b.assets_in(densest.id)),
            r2(densest.area),
            round(len(b.assets_in(densest.id)) / densest.area, 3),
        ),
        "items": Counter(a.type for a in b.assets_in(top.id)),
    }


def c13():
    """Walking distance minus straight-line distance between two rooms on different floors."""
    b = load_building("proc-campus-dev-001")
    a, c = sid(b, "Toilet 0.16"), sid(b, "Kitchen 1.03")
    r = RouteGraph(b).shortest(a, c)
    route_ok(b, r)
    straight = geometry.straight_line_3d(b, a, c)
    return {
        "type": "number",
        "value": r2(r.length_m - straight),
        "unit": "m",
        "relative_tolerance": 0.03,
        "absolute_tolerance": 1.0,
        "walk_m": r.length_m,
        "straight_m": r2(straight),
    }, {"path": [b.label(x) for x in r.space_path], "elements": r.element_path}


def c14():
    """Two-person errand to one meeting room; who arrives last and after how many metres."""
    b = load_building("proc-campus-dev-000")
    g = RouteGraph(b)
    z = sid(b, "Meeting room 1.12")
    dz = g.distances_from([z])
    out = {}
    for who, start, item in [("Anna", "Storage 4.08", "plant"), ("Ben", "Canteen 0.28", "printer")]:
        s = sid(b, start)
        ds = g.distances_from([s])
        holders = {a.space_id for a in b.assets_of_type(item)}
        best = min(holders, key=lambda h: ds[h] + dz[h])
        near = min(holders, key=lambda h: ds[h])
        out[who] = {
            "best": (b.label(best), r2(ds[best]), r2(dz[best]), r2(ds[best] + dz[best])),
            "nearest": (b.label(near), r2(ds[near]), r2(dz[near]), r2(ds[near] + dz[near])),
            "length": ds[best] + dz[best],
            "naive": ds[near] + dz[near],
        }
    last = max(out, key=lambda w: out[w]["length"])
    return {
        "type": "number",
        "value": r2(out[last]["length"]),
        "unit": "m",
        "relative_tolerance": 0.02,
        "absolute_tolerance": 1.0,
        "last_to_arrive": last,
    }, {
        **{w: {k: v for k, v in o.items()} for w, o in out.items()},
        "naive_max": r2(max(o["naive"] for o in out.values())),
    }


def c15():
    """Step-free detour: extra metres of the wheelchair route over the walking route."""
    b = load_building("osm-way22744230")
    a, c = "osm-way22744230-F0-S029", sid(b, "N1311")
    assert b.spaces[a].name == "Stairwell N0320A"
    w = RouteGraph(b).shortest(a, c)
    s = RouteGraph(b, STEP_FREE).shortest(a, c)
    route_ok(b, w)
    route_ok(b, s, STEP_FREE)
    return {
        "type": "number",
        "value": r2(s.length_m - w.length_m),
        "unit": "m",
        "relative_tolerance": 0.03,
        "absolute_tolerance": 1.0,
        "walk_m": w.length_m,
        "step_free_m": s.length_m,
    }, {
        "walk_path": [b.label(x) for x in w.space_path],
        "walk_el": w.element_path,
        "sf_path": [b.label(x) for x in s.space_path],
        "sf_el": s.element_path,
    }


def c16():
    """Rooms on Floor 1 you can only enter through another room."""
    b = load_building("swiss-1785")
    out = []
    for s in b.spaces_on("swiss-1785-F1"):
        if not is_room(s):
            continue
        ops = b.openings_of(s.id)
        if ops and all(o.other_side(s.id) != OUTSIDE and is_room(b.spaces[o.other_side(s.id)]) for o in ops):
            out.append(s.id)
    return {"type": "set", "values": sorted(out)}, {
        "names": [b.label(x) for x in sorted(out)],
        "via": {
            b.spaces[x].name: [(o.kind, o.width_m, b.spaces[o.other_side(x)].name) for o in b.openings_of(x)]
            for x in out
        },
    }


def _facts_only(building_id: str, checks):
    b = load_building(building_id)
    return b, {name: fn(b) for name, fn in checks.items()}


def c17():
    """Out of scope: noise level of a seminar room."""
    b, facts = _facts_only(
        "proc-campus-dev-000",
        {
            "room": lambda b: b.label(sid(b, "Seminar room 2.11")),
            "items": lambda b: dict(Counter(a.type for a in b.assets_in(sid(b, "Seminar room 2.11")))),
            "raw_keys": lambda b: sorted(b.raw["spaces"][0].keys()),
        },
    )
    _assert_no_sensor_fields(b)
    return None, facts


def c18():
    """Out of scope: temperature in the server rooms."""
    b, facts = _facts_only(
        "proc-factory-dev-003",
        {
            "server_rooms": lambda b: [
                b.label(s.id) for s in b.spaces.values() if s.room_type == "server_room"
            ],
            "server_racks": lambda b: len(b.assets_of_type("server_rack")),
            "ups": lambda b: len(b.assets_of_type("ups_unit")),
        },
    )
    _assert_no_sensor_fields(b)
    return None, facts


def c19():
    """Out of scope (with a caveat): who works in Office 2.12."""
    b, facts = _facts_only(
        "proc-campus-dev-000",
        {
            "items": lambda b: dict(Counter(a.type for a in b.assets_in(sid(b, "Office 2.12")))),
            "asset_names": lambda b: sorted({a.name for a in b.assets_in(sid(b, "Office 2.12"))}),
        },
    )
    _assert_no_sensor_fields(b)
    return None, facts


def c20():
    """Out of scope: opening hours of the canteen."""
    b, facts = _facts_only(
        "proc-campus-dev-000",
        {
            "canteen": lambda b: [b.label(s.id) for s in b.spaces.values() if s.room_type == "canteen"],
            "info": lambda b: b.raw.get("info") or b.info,
        },
    )
    _assert_no_sensor_fields(b)
    return None, facts


FORBIDDEN = (
    "noise",
    "decibel",
    "temperature",
    "occupant",
    "employee",
    "person",
    "opening_hours",
    "hours",
    "schedule",
    "tenant",
    "owner",
    "sensor",
)


def _assert_no_sensor_fields(b: Building) -> None:
    """The plan has no field naming noise, temperature, people or opening hours (keys anywhere in the JSON)."""
    keys: set[str] = set()

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                keys.add(str(k).casefold())
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(b.raw)
    hits = [k for k in keys if any(f in k for f in FORBIDDEN)]
    assert not hits, hits


CASES = {
    f"xt-beyond_tools-{i:02d}": fn
    for i, fn in enumerate(
        [c01, c02, c03, c04, c05, c06, c07, c08, c09, c10, c11, c12, c13, c14, c15, c16, c17, c18, c19, c20],
        start=1,
    )
}


def same(expected, got) -> bool:
    if isinstance(expected, float) or isinstance(got, float):
        return isinstance(got, int | float) and abs(float(expected) - float(got)) <= 1e-6
    if isinstance(expected, dict):
        return (
            isinstance(got, dict)
            and expected.keys() == got.keys()
            and all(same(expected[k], got[k]) for k in expected)
        )
    if isinstance(expected, list):
        return (
            isinstance(got, list)
            and len(expected) == len(got)
            and all(same(x, y) for x, y in zip(expected, got, strict=True))
        )
    return expected == got


def main() -> None:
    show = "--facts" in sys.argv
    lines = {
        json.loads(line)["id"]: json.loads(line) for line in JSONL.read_text().splitlines() if line.strip()
    }
    assert set(lines) == set(CASES), set(lines) ^ set(CASES)
    for case_id, fn in CASES.items():
        answer, facts = fn()
        stored = lines[case_id]["answer"]
        if answer is not None:  # out-of-scope cases: facts asserted inside the function
            assert same(answer, stored), (case_id, answer, stored)
        else:
            assert stored["type"] == "text", case_id
        if show:
            print(case_id, json.dumps(answer, default=str))
            print("   ", json.dumps(facts, default=str, ensure_ascii=False))
    print(f"OK: {len(CASES)} beyond_tools cases recomputed and match {JSONL.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
