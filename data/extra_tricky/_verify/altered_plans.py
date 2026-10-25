"""altered_plans: build the altered buildings and recompute every answer of xt-altered_plans-01..20.

    uv run python data/extra_tricky/_verify/altered_plans.py           # verify (files on disk + answers)
    uv run python data/extra_tricky/_verify/altered_plans.py --build   # (re)write the altered buildings first

Each altered building is a copy of a kit building (dev split) with one documented change (ALTERATIONS).
Verify mode rebuilds every alteration in memory and asserts the files on disk are exactly that, loads each
with app.building, runs build.validate (only the issues listed in EXPECTED_ISSUES may appear), recomputes
each case's answer with RouteGraph / queries / geometry on the altered data, the contrast value on the
original, and asserts both against the values hard-coded here and the answers in
_staging/altered_plans.jsonl.
"""

from __future__ import annotations

import copy
import csv
import dataclasses
import io
import json
import math
import sys
from pathlib import Path

from app.building import geometry, queries
from app.building.build import validate
from app.building.model import OUTSIDE, Building, Opening, load_building, parse_building
from app.building.routing import WALKING, Constraints, validate_route
from app.building.routing import RouteGraph as _RouteGraph


class RouteGraph(_RouteGraph):
    """RouteGraph without its built-in production-bay rule: the model these cases were written against. The
    scripts apply the bay rule themselves (closing every bay but a leg's ends), so expected answers follow
    ROUTE_SCORING.md, and the naive bay-crossing shortcut each trap asserts stays computable."""

    def _dijkstra(self, sources, goals=None, *, cross_no_through=True):
        return super()._dijkstra(sources, goals, cross_no_through=True)


PRODUCTION_BAYS = frozenset({"production_zone", "assembly_zone", "additive_zone"})
"""The room types these cases treat as no-through. Synera (Sun 25 Oct) made it a per-space flag instead
(`"through": false`); the cases that need it run on `-xt-nothrough` copies that set it on these types."""

ROOT = Path(__file__).resolve().parents[3]
KIT = ROOT / "synera-kit" / "data"
OUT = ROOT / "data" / "extra_tricky"
STAGING = OUT / "_staging" / "altered_plans.jsonl"


# ---- alterations ------------------------------------------------------------------------------------------


def _src(source_id: str) -> Path:
    hits = [p for split in ("dev", "train") if (p := KIT / split / source_id).is_dir()]
    assert hits, source_id
    return hits[0]


def _by_id(items: list[dict], element_id: str) -> dict:
    hit = [x for x in items if x["id"] == element_id]
    assert len(hit) == 1, element_id
    return hit[0]


def _drop(items: list[dict], ids: set[str]) -> list[dict]:
    found = {x["id"] for x in items} & ids
    assert found == ids, ids - found
    return [x for x in items if x["id"] not in ids]


def nolift(raw, rows):
    p = "proc-factory-dev-003-"
    raw["vertical_connectors"] = _drop(raw["vertical_connectors"], {p + "V02"})
    raw["spaces"] = _drop(raw["spaces"], {p + "F0-S018", p + "F1-S003"})
    raw["openings"] = _drop(raw["openings"], {p + "F0-O0022", p + "F1-O0056"})
    return rows


def narrowlift(raw, rows):
    door = _by_id(raw["openings"], "proc-factory-dev-003-F1-O0056")
    assert door["width_m"] == 1.1
    door["width_m"] = 0.8
    return rows


def sealed(raw, rows):
    raw["openings"] = _drop(raw["openings"], {"proc-factory-dev-003-F0-O0026"})
    return rows


def dupname(raw, rows):
    sid = "proc-factory-dev-003-F1-S028"
    s = _by_id(raw["spaces"], sid)
    assert (s["name"], s["ref"]) == ("Kitchen 1.28", "1.28")
    s["name"], s["ref"] = "Kitchen 1.14", "1.14"
    for r in rows:
        if r["space_id"] == sid:
            r["space_name"], r["room_ref"] = "Kitchen 1.14", "1.14"
    return rows


def samename(raw, rows):
    sid = "proc-campus-dev-000-F2-S021"
    s = _by_id(raw["spaces"], sid)
    assert (s["name"], s["ref"]) == ("Lab 2.21", "2.21")
    s["name"], s["ref"] = "Lab 3.06", "3.06"
    for r in rows:
        if r["space_id"] == sid:
            r["space_name"], r["room_ref"] = "Lab 3.06", "3.06"
    return rows


def f4cut(raw, rows):
    f4 = "proc-campus-dev-000-F4"
    for c in raw["vertical_connectors"]:
        assert c["floor_ids"][-1] == f4
        c["floor_ids"], c["space_ids"] = c["floor_ids"][:-1], c["space_ids"][:-1]
    return rows


def narrowlab(raw, rows):
    door = _by_id(raw["openings"], "proc-campus-dev-000-F0-O0010")
    assert door["width_m"] == 1.2
    door["width_m"] = 0.8
    return rows


def no_through(raw, rows):
    """Synera's flag (Sun 25 Oct): a route may start or end in the space, never pass through it."""
    for space in raw["spaces"]:
        if space["room_type"] in PRODUCTION_BAYS:
            space["through"] = False
    return rows


NO_THROUGH_TEXT = (
    'Every production, assembly and additive zone marked "through": false (Synera\'s flag: a route may start '
    "or end in one, never pass through it)."
)


def aisle295_no_through(raw, rows):
    return no_through(raw, aisle295(raw, rows))


def aisle295(raw, rows):
    o = _by_id(raw["openings"], "proc-factory-dev-003-F0-O0014")
    assert o["width_m"] == 14.6
    o["width_m"] = 2.95
    return rows


NEW_DOOR = {
    "id": "resplan-15179-F0-O0013",
    "floor_id": "resplan-15179-F0",
    "space_ids": ["resplan-15179-F0-S001", "resplan-15179-F0-S005"],
    "kind": "door",
    "position": [5.152, 3.537],
    "width_m": 0.9,
    "step_free": True,
    "synthetic": True,
}


def newdoor(raw, rows):
    assert all(o["id"] != NEW_DOOR["id"] for o in raw["openings"])
    raw["openings"].append(dict(NEW_DOOR))
    return rows


def stepdoor(raw, rows):
    o = _by_id(raw["openings"], "swiss-1785-F0-O0001")
    assert o["step_free"] is True
    o["step_free"] = False
    return rows


def orphan(raw, rows):
    p = "proc-factory-dev-004-"
    raw["spaces"] = _drop(raw["spaces"], {p + "F0-S026"})
    raw["openings"] = _drop(raw["openings"], {p + "F0-O0033"})
    # assets of the deleted room are deliberately kept, in building.json and equipment.csv
    assert sum(a["space_id"] == p + "F0-S026" for a in raw["assets"]) == 3
    return rows


ALTERATIONS = {
    "proc-factory-dev-003-xt-nolift": (
        "proc-factory-dev-003",
        nolift,
        "Removed the only lift: vertical connector proc-factory-dev-003-V02 'Office lift', its lift spaces "
        "F0-S018 'Lift 0.18' and F1-S003 'Lift 1.03', and their doors F0-O0022 and F1-O0056. Office "
        "staircase V01 is now the only way between the floors.",
    ),
    "proc-factory-dev-003-xt-narrowlift": (
        "proc-factory-dev-003",
        narrowlift,
        "Lift door on Floor 1 proc-factory-dev-003-F1-O0056 (Lift 1.03 <-> Office corridor 1.01) narrowed "
        "from 1.1 m to 0.8 m. Nothing else changed.",
    ),
    "proc-factory-dev-003-xt-sealed": (
        "proc-factory-dev-003",
        sealed,
        "Deleted the only door of Quality lab 0.21 (proc-factory-dev-003-F0-O0026, 1.6 m, to Office "
        "corridor 0.16). The room keeps its outline and its 4 assets but has no opening at all.",
    ),
    "proc-factory-dev-003-xt-dupname": (
        "proc-factory-dev-003",
        dupname,
        "Renamed proc-factory-dev-003-F1-S028 from 'Kitchen 1.28' (ref 1.28) to 'Kitchen 1.14' (ref 1.14), "
        "the same name and ref as F1-S014 on the same floor (equipment.csv space_name/room_ref updated "
        "to match).",
    ),
    "proc-campus-dev-000-xt-samename": (
        "proc-campus-dev-000",
        samename,
        "Renamed proc-campus-dev-000-F2-S021 (Floor 2) from 'Lab 2.21' (ref 2.21) to 'Lab 3.06' (ref 3.06), "
        "the same name and ref as F3-S006 on Floor 3 (equipment.csv updated to match).",
    ),
    "proc-campus-dev-000-xt-f4cut": (
        "proc-campus-dev-000",
        f4cut,
        "Removed the Floor 4 stop (proc-campus-dev-000-F4 / F4-S004, S005, S009, S010, S014, S015) from all "
        "six vertical connectors V01..V06, so every stair and lift ends at Floor 3. Floor 4 spaces and "
        "doors are unchanged.",
    ),
    "proc-campus-dev-000-xt-narrowlab": (
        "proc-campus-dev-000",
        narrowlab,
        "The only door of Lab 0.11, proc-campus-dev-000-F0-O0010 (to Corridor 0.01), narrowed from 1.2 m "
        "to 0.8 m.",
    ),
    "proc-factory-dev-003-xt-aisle295": (
        "proc-factory-dev-003",
        aisle295_no_through,
        "Opening proc-factory-dev-003-F0-O0014 between Main aisle H-B3 (F0-S008) and Main aisle H-B4 "
        "(F0-S009) narrowed from 14.6 m to 2.95 m. " + NO_THROUGH_TEXT,
    ),
    **{
        f"{source}-xt-nothrough": (source, no_through, NO_THROUGH_TEXT + " Nothing else changed.")
        for source in (
            "proc-factory-dev-003",
            "proc-factory-dev-005",
            "proc-factory-dev-008",
            "proc-factory-train-006",
            "proc-factory-train-010",
            "proc-factory-train-015",
        )
    },
    "resplan-15179-xt-newdoor": (
        "resplan-15179",
        newdoor,
        "Added a 0.9 m step-free door resplan-15179-F0-O0013 between Living room 0.01 (F0-S001) and "
        "Bathroom 0.05 (F0-S005) at [5.152, 3.537], the middle of their shared wall. Before, Bathroom 0.05 "
        "was only reachable through Bedroom 0.03.",
    ),
    "swiss-1785-xt-stepdoor": (
        "swiss-1785",
        stepdoor,
        "Apartment 1's front door swiss-1785-F0-O0001 (Corridor 0.03 <-> Stair hall 0.35, 0.938 m) set "
        "to step_free=false (a step at the threshold). It is apartment 1's only way out.",
    ),
    "proc-factory-dev-004-xt-orphan": (
        "proc-factory-dev-004",
        orphan,
        "Deleted space proc-factory-dev-004-F0-S026 'Quality lab 0.26' and its only door F0-O0033. Its 3 "
        "assets (A00145..A00147: coordinate measuring machine and two inspection benches) are kept in "
        "building.json and equipment.csv, still pointing at the deleted space id.",
    ),
}


def make(new_id: str) -> tuple[dict, str]:
    source_id, change, text = ALTERATIONS[new_id]
    folder = _src(source_id)
    raw = json.loads((folder / "building.json").read_text(encoding="utf-8"))
    text_csv = (folder / "equipment.csv").read_text(encoding="utf-8")
    reader = csv.DictReader(io.StringIO(text_csv))
    fields, rows = reader.fieldnames, list(reader)
    raw = copy.deepcopy(raw)
    rows = change(raw, rows)
    raw["building"]["id"] = new_id
    raw["extra_tricky_modification"] = text
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return raw, out.getvalue()


def write_all() -> None:
    for new_id in ALTERATIONS:
        raw, csv_text = make(new_id)
        folder = OUT / new_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "building.json").write_text(json.dumps(raw, ensure_ascii=False, indent=1) + "\n")
        (folder / "equipment.csv").write_text(csv_text)


# ---- validation issues we expect (the alteration is the point) --------------------------------------------

EXPECTED_ISSUES = {
    "proc-factory-dev-003-xt-sealed": {("error", "proc-factory-dev-003-F0-S021", "not reachable from outside")},
    "proc-campus-dev-000-xt-f4cut": {
        ("error", f"proc-campus-dev-000-F4-S{n:03d}", "not reachable from outside") for n in range(1, 32)
    },
    "proc-factory-dev-004-xt-orphan": {
        ("error", f"proc-factory-dev-004-A00{n}", "in unknown space 'proc-factory-dev-004-F0-S026'")
        for n in (145, 146, 147)
    },
}


# ---- helpers ----------------------------------------------------------------------------------------------

SF = Constraints(step_free=True)


def alt(new_id: str) -> Building:
    return load_building(OUT / new_id)


def orig(source_id: str) -> Building:
    return load_building(_src(source_id))


def route(b: Building, a: str, z: str, c: Constraints = WALKING):
    r = RouteGraph(b, c).shortest(a, z)
    if r is not None:
        assert validate_route(b, r.space_path, c) == [], validate_route(b, r.space_path, c)
    return r


def forklift(b: Building, a: str, z: str):
    """Synera's forklift rule: no steps, openings >= 3 m, production bays only as start or end."""
    bays = frozenset(s.id for s in b.spaces.values() if s.room_type in PRODUCTION_BAYS) - {a, z}
    c = Constraints(step_free=True, min_width_m=3.0, closed=bays)
    r = RouteGraph(b, c).shortest(a, z)
    if r is not None:
        assert validate_route(b, r.space_path, Constraints(step_free=True, min_width_m=3.0)) == []
    return r, c


def close(x: float, y: float, tol: float = 0.01) -> bool:
    return math.isclose(x, y, abs_tol=tol)


# ---- the cases --------------------------------------------------------------------------------------------

F3 = "proc-factory-dev-003-"
C0 = "proc-campus-dev-000-"
R = "resplan-15179-"
S = "swiss-1785-"
F4_ = "proc-factory-dev-004-"

computed: dict[str, dict] = {}


def check(case: str, **values) -> None:
    computed[case] = values


def cases() -> None:
    # 01-03 nolift --------------------------------------------------------------------------------------
    b, o = alt("proc-factory-dev-003-xt-nolift"), orig("proc-factory-dev-003")
    a, z = F3 + "F0-S030", F3 + "F1-S014"
    assert route(b, a, z, SF) is None
    r0 = route(o, a, z, SF)
    assert close(r0.length_m, 123.7) and F3 + "V02" in r0.element_path
    check("01", answer=None, original=r0.length_m)

    reach = queries.step_free_reachable_rooms(b, F3 + "F0-S016", F3 + "F1", [])
    reach0 = queries.step_free_reachable_rooms(o, F3 + "F0-S016", F3 + "F1", [])
    assert len(reach) == 0 and len(reach0) == 29
    check("02", answer=0, original=29)

    w = route(b, a, z)
    assert not [k for k in b.connectors.values() if k.kind == "lift"]
    assert close(w.length_m, 118.69) and w.element_path[2] == F3 + "V01"
    check("03", answer=w.length_m, spaces=w.space_path)

    # 04-05 narrowlift ----------------------------------------------------------------------------------
    b = alt("proc-factory-dev-003-xt-narrowlift")
    stairs_closed = Constraints(step_free=True, min_width_m=0.9, closed=frozenset({F3 + "V01"}))
    assert route(b, a, z, stairs_closed) is None
    r0 = route(o, a, z, stairs_closed)
    assert close(r0.length_m, 123.7)
    # walking (no width) with the stairs closed still works, through the narrow lift door
    rw = route(b, a, z, Constraints(closed=frozenset({F3 + "V01"})))
    assert close(rw.length_m, 123.7) and F3 + "F1-O0056" in rw.element_path
    check("04", answer=None, original=r0.length_m, walking=rw.length_m)

    d08 = queries.deliverable_rooms(b, F3 + "F0-S016", F3 + "F1", 0.8)
    d081 = queries.deliverable_rooms(b, F3 + "F0-S016", F3 + "F1", 0.81)
    d08o = queries.deliverable_rooms(o, F3 + "F0-S016", F3 + "F1", 0.8)
    assert (len(d08), len(d081), len(d08o)) == (29, 0, 29)
    check("05", answer=29, at_0_81=0)

    # 06-07 sealed --------------------------------------------------------------------------------------
    b = alt("proc-factory-dev-003-xt-sealed")
    lab = F3 + "F0-S021"
    cmm = b.assets_of_type("coordinate_measuring_machine")
    assert [x.space_id for x in cmm] == [lab] and b.openings_of(lab) == []
    assert route(b, F3 + "F0-S023", lab) is None
    r0 = route(o, F3 + "F0-S023", lab)
    check("06", answer=None, located=lab, original=r0.length_m)

    walls = geometry.shared_wall_rooms(b, lab)
    assert set(walls) == {F3 + "F0-S001", F3 + "F0-S020", F3 + "F0-S022"}
    gained = queries.what_if_door(b, lab, F3 + "F0-S020", F3 + "F0")
    gained0 = queries.what_if_door(o, lab, F3 + "F0-S020", F3 + "F0")
    assert gained == [lab] and gained0 == [F3 + "F0-S020"]
    after = RouteGraph(
        dataclasses.replace(
            b,
            openings={
                **b.openings,
                "x": Opening("x", F3 + "F0", (lab, F3 + "F0-S020"), "door",
                             geometry.shared_wall_midpoint(b, lab, F3 + "F0-S020"), 0.9, True, True),
            },
        )
    ).distances_from([OUTSIDE])
    check("07", answer=gained, original=gained0, lab_walk_out_after=round(after[lab], 2))

    # 08-09 dupname -------------------------------------------------------------------------------------
    b = alt("proc-factory-dev-003-xt-dupname")
    hits = sorted(s.id for s in b.resolve("Kitchen 1.14", 1))
    assert hits == [F3 + "F1-S014", F3 + "F1-S028"]
    r14, r28 = (route(b, F3 + "F1-S004", h) for h in hits)
    check("08", options=hits, lengths=[r14.length_m, r28.length_m])
    areas = [round(b.spaces[h].area, 1) for h in hits]
    assert areas == [28.0, 23.9]
    check("09", options=hits, areas=areas)

    # 10-11 samename ------------------------------------------------------------------------------------
    b = alt("proc-campus-dev-000-xt-samename")
    hits = sorted(s.id for s in b.resolve("Lab 3.06"))
    assert hits == [C0 + "F2-S021", C0 + "F3-S006"]
    lobby = C0 + "F0-S018"
    d = [round(geometry.straight_line_3d(b, lobby, h), 2) for h in hits]
    check("10", options=hits, distances=d)
    one = b.resolve("Lab 3.06 (Floor 3)")
    assert [s.id for s in one] == [C0 + "F3-S006"]
    o0 = orig("proc-campus-dev-000")
    assert close(geometry.straight_line_3d(o0, lobby, C0 + "F3-S006"), d[1], 0.006)
    check("11", answer=d[1])

    # 12 f4cut ------------------------------------------------------------------------------------------
    b = alt("proc-campus-dev-000-xt-f4cut")
    tgt = C0 + "F4-S011"
    assert route(b, lobby, tgt) is None
    r0 = route(o0, lobby, tgt)
    assert all(fid != C0 + "F4" for k in b.connectors.values() for fid, _ in k.stops)
    check("12", answer=None, original=r0.length_m)

    # 13-14 narrowlab -----------------------------------------------------------------------------------
    b = alt("proc-campus-dev-000-xt-narrowlab")
    n = queries.deliverable_rooms(b, lobby, C0 + "F0", 1.1)
    n0 = queries.deliverable_rooms(o0, lobby, C0 + "F0", 1.1)
    assert len(n0) == 11 and set(n0) - set(n) == {C0 + "F0-S011"}
    check("13", answer=len(n), original=len(n0), rooms=sorted(n))
    assert route(b, lobby, C0 + "F0-S011", Constraints(step_free=True, min_width_m=1.0)) is None
    r0 = route(o0, lobby, C0 + "F0-S011", Constraints(step_free=True, min_width_m=1.0))
    check("14", answer=None, original=r0.length_m)

    # 15-16 aisle295 ------------------------------------------------------------------------------------
    b = alt("proc-factory-dev-003-xt-aisle295")
    a, z = F3 + "F0-S015", F3 + "F0-S012"
    rf, _ = forklift(b, a, z)
    rf0, _ = forklift(o, a, z)
    assert rf is None and close(rf0.length_m, 71.47)
    naive = RouteGraph(b, Constraints(step_free=True, min_width_m=3.0)).shortest(a, z)
    bad = validate_route(b, naive.space_path, Constraints(step_free=True, min_width_m=3.0))
    assert bad and all("no-through" in p for p in bad)
    check("15", answer=None, original=rf0.length_m, naive=naive.length_m,
          naive_spaces=naive.space_path, naive_problems=bad)
    zones = sorted(s.id for s in b.spaces.values() if s.room_type == "warehouse_zone")
    reach = sorted(zid for zid in zones if forklift(b, a, zid)[0] is not None)
    reach0 = sorted(zid for zid in zones if forklift(o, a, zid)[0] is not None)
    assert len(reach0) == 5
    check("16", answer=reach, original=reach0)

    # 17-18 newdoor -------------------------------------------------------------------------------------
    b, ob = alt("resplan-15179-xt-newdoor"), orig("resplan-15179")
    r1, r10 = route(b, R + "F0-S009", R + "F0-S005"), route(ob, R + "F0-S009", R + "F0-S005")
    assert r1.length_m < r10.length_m
    check("17", spaces=r1.space_path, length=r1.length_m, original=r10.length_m,
          original_spaces=r10.space_path)
    near = queries.nearest_with_asset(b, R + "F0-S001", "washing_machine")
    near0 = queries.nearest_with_asset(ob, R + "F0-S001", "washing_machine")
    assert near == (R + "F0-S005", 5.55) and near0 == (R + "F0-S008", 7.32)
    check("18", answer=near, original=near0)

    # 19 stepdoor ---------------------------------------------------------------------------------------
    b, osw = alt("swiss-1785-xt-stepdoor"), orig("swiss-1785")
    a, z = S + "F0-S006", S + "F2-S009"
    assert route(b, a, z, SF) is None
    r0 = route(osw, a, z, SF)
    rw = route(b, a, z)
    check("19", answer=None, original=r0.length_m, original_spaces=r0.space_path, walking=rw.length_m)

    # 20 orphan -----------------------------------------------------------------------------------------
    b = alt("proc-factory-dev-004-xt-orphan")
    cmm = b.assets_of_type("coordinate_measuring_machine")
    assert len(cmm) == 1 and cmm[0].space_id == F4_ + "F0-S026" and cmm[0].space_id not in b.spaces
    assert b.resolve("Quality lab 0.26") == [] and b.resolve("0.26") == []
    rows = list(csv.DictReader((OUT / "proc-factory-dev-004-xt-orphan" / "equipment.csv").open()))
    row = [r for r in rows if r["type"] == "coordinate_measuring_machine"]
    assert len(row) == 1 and row[0]["space_name"] == "Quality lab 0.26"
    assert b.space_at(F4_ + "F0", cmm[0].position) is None  # a hole in the plan now
    check("20", answer=None, listed_in="Quality lab 0.26", position=list(cmm[0].position))


# ---- main -------------------------------------------------------------------------------------------------


def verify_files() -> None:
    for new_id in ALTERATIONS:
        raw, csv_text = make(new_id)
        folder = OUT / new_id
        assert json.loads((folder / "building.json").read_text()) == raw, f"{new_id}: building.json differs"
        assert (folder / "equipment.csv").read_text() == csv_text, f"{new_id}: equipment.csv differs"
        assert not list(folder.glob("*.png")) and not list(folder.glob("*.svg"))
        parse_building(raw)
        issues = {(i.severity, i.element_id, i.message) for i in validate(raw)}
        assert issues == EXPECTED_ISSUES.get(new_id, set()), (new_id, issues)


def verify_staging() -> None:
    lines = [json.loads(x) for x in STAGING.read_text().splitlines() if x.strip()]
    assert [c["id"] for c in lines] == [f"xt-altered_plans-{i:02d}" for i in range(1, 21)]
    by = {c["id"][-2:]: c for c in lines}
    for key, c in by.items():
        assert c["building_path"] == f"data/extra_tricky/{c['building_id']}", key
        assert c["building_id"] in ALTERATIONS, key
        assert (ROOT / c["building_path"] / "building.json").exists()
    v, ans = computed, {k: c["answer"] for k, c in by.items()}
    for k in ("01", "04", "06", "12", "14", "15", "19", "20"):
        assert ans[k]["type"] in ("no_route", "false_premise"), k
    assert ans["02"]["value"] == v["02"]["answer"]
    assert ans["03"]["type"] == "false_premise" and str(v["03"]["answer"]) in ans["03"]["correction"]
    assert ans["05"]["value"] == v["05"]["answer"]
    assert ans["07"]["value"] == len(v["07"]["answer"]) and ans["07"]["rooms"] == v["07"]["answer"]
    for k in ("08", "09", "10"):
        assert ans[k]["type"] == "clarification" and ans[k]["options"] == v[k]["options"], k
    assert ans["11"]["value"] == v["11"]["answer"]
    assert ans["13"]["value"] == v["13"]["answer"]
    assert sorted(ans["16"]["values"]) == v["16"]["answer"]
    assert ans["17"]["spaces"] == v["17"]["spaces"] and ans["17"]["length_m"] == v["17"]["length"]
    assert ans["18"]["space_id"] == v["18"]["answer"][0]
    assert ans["18"]["distance"]["value"] == v["18"]["answer"][1]
    assert ans["11"]["value"] == v["11"]["answer"] and ans["13"]["value"] == v["13"]["answer"]
    assert [o["area_m2"] for o in ans["09"]["option_details"]] == v["09"]["areas"]
    assert [o["distance_m"] for o in ans["10"]["option_details"]] == v["10"]["distances"]


if __name__ == "__main__":
    if "--build" in sys.argv:
        write_all()
    verify_files()
    cases()
    for k, val in computed.items():
        print(k, json.dumps(val, default=str))
    if STAGING.exists() and "--no-staging" not in sys.argv:
        verify_staging()
        print("staging answers match")
    print("OK: altered_plans verified")
