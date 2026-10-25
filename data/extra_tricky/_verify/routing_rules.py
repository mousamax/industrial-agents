"""Recompute every routing_rules case (data/extra_tricky/_staging/routing_rules.jsonl) from the building data.

    uv run python data/extra_tricky/_verify/routing_rules.py           # verify: recompute and compare with the jsonl
    uv run python data/extra_tricky/_verify/routing_rules.py --write   # regenerate the jsonl from this file

Route rule: docs/synera/ROUTE_SCORING.md, as implemented by app.building.routing.RouteGraph (door-to-door
walking 15 cm off the walls, stairs 2 m per metre climbed, lift 15 m + 1 m per metre, no way round the outside).
RouteGraph does not rank by the production-bay rule, so `best()` closes every production bay except the start
and the end of a leg before searching ("may end in one, never pass through one"). Production bays are
PRODUCTION_BAYS = production_zone, assembly_zone, additive_zone (our reading; warehouse and logistics zones
are not bays). Forklift = FORKLIFT (step-free, every opening >= 3.0 m). Every route answer is also checked
with validate_route (closures, step-free, width, no bay passed through).
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

from app.building.model import Building, load_building
from app.building.routing import (
    FORKLIFT,
    PRODUCTION_BAYS,
    WALKING,
    Constraints,
    Route,
    RouteGraph as _RouteGraph,
    validate_route,
)


class RouteGraph(_RouteGraph):
    """RouteGraph without its built-in production-bay rule: the model these cases were written against. The
    scripts apply the bay rule themselves (closing every bay but a leg's ends), so expected answers follow
    ROUTE_SCORING.md, and the naive bay-crossing shortcut each trap asserts stays computable."""

    def _dijkstra(self, sources, goals=None, *, bays_are_ends=False):
        return super()._dijkstra(sources, goals, bays_are_ends=False)

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "data/extra_tricky/_staging/routing_rules.jsonl"
DIRECTION = "routing_rules"
FINISH_ROUTE = "Finish with a line 'Answer: <the spaces I pass, in order> | <total metres>'."


# ---- helpers -------------------------------------------------------------------------------------------------


def kit_path(bid: str) -> str:
    for split in ("dev", "train"):
        if (ROOT / f"synera-kit/data/{split}/{bid}/building.json").exists():
            return f"synera-kit/data/{split}/{bid}"
    altered = ROOT / f"data/extra_tricky/{bid}"
    if (altered / "building.json").exists():
        return f"data/extra_tricky/{bid}"
    raise FileNotFoundError(bid)


def B(bid: str) -> Building:
    return load_building(ROOT / kit_path(bid))


def bays(b: Building) -> set[str]:
    return {s.id for s in b.spaces.values() if s.room_type in PRODUCTION_BAYS}


def best(b: Building, s: str, t: str, c: Constraints = WALKING, avoid_bays: bool = True) -> Route | None:
    """Shortest route under the constraints; with avoid_bays, no production bay is passed through."""
    if avoid_bays:
        c = Constraints(c.step_free, c.min_width_m, c.closed | (bays(b) - {s, t}))
    return RouteGraph(b, c).shortest(s, t)


def name(b: Building, sid: str) -> str:
    """Space name; names repeated in the building (Staircase 1 on every floor) get their floor level."""
    if sid == "outside":
        return sid
    space = b.spaces[sid]
    if sum(1 for x in b.spaces.values() if x.name == space.name) > 1:
        return f"{space.name} [level {b.floors[space.floor_id].level}]"
    return space.name


def names(b: Building, path: list[str]) -> str:
    return " > ".join(name(b, x) for x in path)


def resolve1(b: Building, ref: str) -> str:
    hits = b.resolve(ref)
    assert len(hits) == 1, (ref, [h.id for h in hits])
    return hits[0].id


def constraints_json(c: Constraints, forklift: bool = False) -> dict:
    return {
        "step_free": c.step_free,
        "min_width_m": c.min_width_m,
        "closed": sorted(c.closed),
        "forklift": forklift,
        "production_bays_pass_through": False,
    }


def route_answer(b: Building, r: Route, c: Constraints = WALKING, forklift: bool = False) -> dict:
    problems = validate_route(b, r.space_path, c)
    assert not problems, problems
    return {
        "type": "route",
        "from_space_id": r.from_space_id,
        "to_space_id": r.to_space_id,
        "spaces": r.space_path,
        "length_m": r.length_m,
        "constraints": constraints_json(c, forklift),
    }


def tour_answer(b: Building, legs: list[Route], c: Constraints = WALKING) -> dict:
    for leg in legs:
        assert not validate_route(b, leg.space_path, c)
    stops = [legs[0].from_space_id] + [leg.to_space_id for leg in legs]
    return {
        "type": "tour",
        "stops": stops,
        "length_m": round(sum(leg.length_m for leg in legs), 2),
        "legs": [
            {"from_space_id": x.from_space_id, "to_space_id": x.to_space_id, "spaces": x.space_path, "length_m": x.length_m}
            for x in legs
        ],
        "constraints": constraints_json(c),
    }


def no_route(reason: str) -> dict:
    return {"type": "no_route", "reason": reason}


def connectors_used(b: Building, r: Route) -> list[str]:
    return [b.connectors[e].name for e in r.element_path if e in b.connectors]


def bays_passed(b: Building, r: Route) -> list[str]:
    return [name(b, x) for x in r.space_path[1:-1] if x in bays(b)]


CASES: dict[int, callable] = {}


def case(n: int):
    def deco(fn):
        CASES[n] = fn
        return fn

    return deco


# ---- cases ---------------------------------------------------------------------------------------------------
# Each returns (record without id/extra.verification, facts used in the text). Text is in the record itself.


@case(1)
def c01():
    bid = "proc-factory-dev-005"
    b = B(bid)
    s, t = resolve1(b, "Meeting room 0.56 (Ground floor)"), resolve1(b, "Warehouse zone H-C5 (Ground floor)")
    naive, r = best(b, s, t, avoid_bays=False), best(b, s, t)
    assert bays_passed(b, naive) and not bays_passed(b, r)
    return dict(
        building_id=bid,
        kind="route",
        skill="route avoiding production bays",
        question=f"How do I walk from Meeting room 0.56 (Ground floor) to Warehouse zone H-C5 (Ground floor)? {FINISH_ROUTE}",
        inputs={"from_space_id": s, "to_space_id": t},
        answer=route_answer(b, r),
        extra=dict(
            difficulty="very_hard",
            solvable="yes",
            expected_behavior="answer",
            tool_coverage="partial",
            tools_needed=["route", "room_details"],
            based_on=None,
            modification=None,
            trap=f"The obvious route goes through Hall access 0.40 straight into Production zone H-A5 ({naive.length_m} m), but a production bay may not be passed through; the valid route goes all the way round via Hall access 0.22 and the main aisle ({r.length_m} m, 4x longer). Our route tool does not apply the bay rule.",
            reasoning=(
                f"1. Office wing to hall: only two doors, Hall access 0.22 -> Warehouse zone H-A1 and Hall access 0.40 -> Production zone H-A5. "
                f"2. Unconstrained shortest: {names(b, naive.space_path)} = {naive.length_m} m; it passes {bays_passed(b, naive)} (production_zone), which ROUTE_SCORING forbids, so it scores 0. "
                f"3. Close every production/assembly/additive zone except start and end and search again: {names(b, r.space_path)} = {r.length_m} m. "
                f"4. Warehouse zone H-A1 and the main aisle tiles are warehouse_zone/corridor, not bays, so passing them is allowed."
            ),
        ),
    )


@case(2)
def c02():
    bid = "proc-factory-dev-005"
    b = B(bid)
    s = resolve1(b, "Kitchen 2.36 (Floor 2)")
    holders = sorted({a.space_id for a in b.assets_of_type("cnc_mill")})
    assert len(holders) == 1
    t = holders[0]
    naive, r = best(b, s, t, avoid_bays=False), best(b, s, t)
    assert bays_passed(b, naive) and not bays_passed(b, r)
    lift = best(b, s, t, Constraints(closed=frozenset({f"{bid}-V01"})))
    return dict(
        building_id=bid,
        kind="route",
        skill="route to an item, multi-floor, production-bay rule",
        question=f"I'm in Kitchen 2.36 (Floor 2). Walk me to the CNC mill. {FINISH_ROUTE}",
        inputs={"from_space_id": s, "asset_type": "cnc_mill", "to_space_id": t},
        answer=route_answer(b, r),
        extra=dict(
            difficulty="hard",
            solvable="yes",
            expected_behavior="answer",
            tool_coverage="partial",
            tools_needed=["find", "route"],
            based_on=None,
            modification=None,
            trap=f"The single CNC mill is in {name(b, t)}; the shortest walk ({naive.length_m} m) cuts through {bays_passed(b, naive)}, which is invalid. The valid route stays in the main aisle until H-B4 ({r.length_m} m, only {round(r.length_m - naive.length_m, 2)} m longer, so a 1% check can't hide the invalid one).",
            reasoning=(
                f"1. find cnc_mill -> one asset, in {name(b, t)} (production_zone, a valid destination). "
                f"2. Floor 2 -> ground floor: Office staircase costs 2 x 7.0 m = 14 m, Office lift 15 + 7 = 22 m; the stairs win (best with stairs closed = {lift.length_m} m). "
                f"3. Unconstrained shortest: {names(b, naive.space_path)} = {naive.length_m} m, passes {bays_passed(b, naive)}: invalid. "
                f"4. With bays closed except the destination: {names(b, r.space_path)} = {r.length_m} m."
            ),
        ),
    )


@case(3)
def c03():
    bid = "proc-factory-dev-005"
    b = B(bid)
    s, t = resolve1(b, "Meeting room 0.26 (Ground floor)"), resolve1(b, "Warehouse zone H-C3 (Ground floor)")
    closed = resolve1(b, "Hall access 0.22 (Ground floor)")
    c = Constraints(closed=frozenset({closed}))
    naive, r, open_ = best(b, s, t, c, avoid_bays=False), best(b, s, t, c), best(b, s, t)
    assert naive is not None and r is None and open_ is not None
    return dict(
        building_id=bid,
        kind="route_with_closure",
        skill="closure + production-bay rule -> no route",
        question=f"Hall access 0.22 is closed today, its floor is being resealed. How do I walk from Meeting room 0.26 (Ground floor) to Warehouse zone H-C3 (Ground floor)? {FINISH_ROUTE}",
        inputs={"from_space_id": s, "to_space_id": t, "closed_element_id": closed},
        answer=no_route(
            "With Hall access 0.22 closed, the only way from the office wing into the hall is Hall access 0.40 -> Production zone H-A5, "
            "and every way on from H-A5 to the main aisle passes production bays (H-A5, H-A4, ...). Bays may be destinations but never passed through, "
            "and walking round the outside is not a route."
        ),
        extra=dict(
            difficulty="very_hard",
            solvable="no",
            expected_behavior="say_no_route",
            tool_coverage="not_covered",
            tools_needed=["set_closed", "route", "room_details"],
            based_on=None,
            modification=None,
            trap=f"set_closed + route returns a route ({naive.length_m} m) through Production zones H-A5 and H-A4; it ignores the bay rule. The honest answer is 'no valid route while 0.22 is closed'.",
            reasoning=(
                f"1. Hall access 0.22 joins the office corridor to Warehouse zone H-A1; Hall access 0.40 joins it to Production zone H-A5. No other door leads from the office wing into the hall (entrances lead outside, and there is no route round the outside). "
                f"2. Without the closure the valid route is {names(b, open_.space_path)} = {open_.length_m} m. "
                f"3. With 0.22 closed, the unconstrained shortest is {names(b, naive.space_path)} = {naive.length_m} m; it passes {bays_passed(b, naive)}: invalid. "
                f"4. Closing 0.22 and every bay except the end leaves Warehouse zone H-C3 unreachable from Meeting room 0.26: RouteGraph returns None. So: no route."
            ),
        ),
    )


@case(4)
def c04():
    bid = "proc-factory-train-015"
    b = B(bid)
    s, t = resolve1(b, "Office 0.29 (Ground floor)"), resolve1(b, "Production zone H-A2 (Ground floor)")
    naive, r = best(b, s, t, avoid_bays=False), best(b, s, t)
    assert bays_passed(b, naive) and not bays_passed(b, r)
    return dict(
        building_id=bid,
        kind="route",
        skill="destination is a bay, but its neighbour bay can't be crossed",
        question=f"How do I get from Office 0.29 (Ground floor) to Production zone H-A2 (Ground floor)? {FINISH_ROUTE}",
        inputs={"from_space_id": s, "to_space_id": t},
        answer=route_answer(b, r),
        extra=dict(
            difficulty="hard",
            solvable="yes",
            expected_behavior="answer",
            tool_coverage="partial",
            tools_needed=["route", "room_details"],
            based_on=None,
            modification=None,
            trap=f"Ending in a production bay is allowed, so an agent may think the bay rule doesn't apply; but the short way ({naive.length_m} m) enters through the neighbouring Production zone H-A3. The valid route enters H-A2 from the main aisle ({r.length_m} m).",
            reasoning=(
                f"1. Hall access 0.21 opens into Production zone H-A3 (bay); Hall access 0.13 opens into Warehouse zone H-A1 (not a bay). "
                f"2. Unconstrained shortest: {names(b, naive.space_path)} = {naive.length_m} m, passes {bays_passed(b, naive)}: invalid (only the end may be a bay). "
                f"3. Bays closed except the destination: {names(b, r.space_path)} = {r.length_m} m."
            ),
        ),
    )


@case(5)
def c05():
    bid = "proc-factory-train-010"
    b = B(bid)
    s, t = resolve1(b, "Office 1.14 (Floor 1)"), resolve1(b, "Logistics zone H-C1 (Ground floor)")
    naive, r = best(b, s, t, avoid_bays=False), best(b, s, t)
    assert bays_passed(b, naive) and not bays_passed(b, r)
    return dict(
        building_id=bid,
        kind="route",
        skill="multi-floor route, production-bay rule",
        question=f"I need to get from Office 1.14 (Floor 1) down to Logistics zone H-C1 (Ground floor). Which way? {FINISH_ROUTE}",
        inputs={"from_space_id": s, "to_space_id": t},
        answer=route_answer(b, r),
        extra=dict(
            difficulty="hard",
            solvable="yes",
            expected_behavior="answer",
            tool_coverage="partial",
            tools_needed=["route"],
            based_on=None,
            modification=None,
            trap=f"Downstairs, the nearer hall door (Hall access 0.13) opens into Production zone H-A1; the shortest walk ({naive.length_m} m) crosses it. The valid route uses Hall access 0.21 and Warehouse zone H-A3, then walks the aisle back ({r.length_m} m).",
            reasoning=(
                f"1. Floor 1 -> ground: Office staircase 2 x 3.5 = 7 m vs Office lift 15 + 3.5 = 18.5 m; stairs used ({connectors_used(b, r)}). "
                f"2. Unconstrained shortest: {names(b, naive.space_path)} = {naive.length_m} m, passes {bays_passed(b, naive)}: invalid. "
                f"3. Bays closed: {names(b, r.space_path)} = {r.length_m} m."
            ),
        ),
    )


@case(6)
def c06():
    bid = "osm-way22744230"
    b = B(bid)
    s, t = resolve1(b, "Generic room N3307 (Floor 3)"), resolve1(b, "Generic room N0317B (Ground floor)")
    g = RouteGraph(b)
    r = g.shortest(s, t)
    def count(route: Route) -> int:
        return sum(1 for e in route.element_path if e in b.openings)
    n = count(r)
    # robustness: no alternative route within 1% has a different count (close each passed element in turn)
    alts, nearest_alt = [], math.inf
    for e in r.element_path:
        alt = RouteGraph(b, Constraints(closed=frozenset({e}))).shortest(s, t)
        if alt:
            nearest_alt = min(nearest_alt, alt.length_m)
        if alt and alt.length_m <= r.length_m * 1.01:
            alts.append((alt.length_m, count(alt)))
    assert all(k == n for _, k in alts), alts
    kinds = [b.openings[e].kind for e in r.element_path if e in b.openings]
    stairs = connectors_used(b, r)
    return dict(
        building_id=bid,
        kind="route_door_count",
        skill="count doorways on the best route",
        question="On the shortest walk from Generic room N3307 (Floor 3) to Generic room N0317B (Ground floor), how many doorways do I go through? Count every door or open archway between two spaces; stairs and lifts don't count. Finish with a line 'Answer: <number>'",
        inputs={"from_space_id": s, "to_space_id": t},
        answer={"type": "number", "value": n, "unit": "doorways", "relative_tolerance": 0, "absolute_tolerance": 0},
        extra=dict(
            difficulty="hard",
            solvable="yes",
            expected_behavior="answer",
            tool_coverage="covered",
            tools_needed=["route", "calculate"],
            based_on=None,
            modification=None,
            trap=f"The route crosses {len(r.space_path)} spaces in an irregular OSM building, mixing {kinds.count('door')} doors and {kinds.count('opening')} open archways between corridor segments; counting rooms, counting only 'door' kinds, or counting the stairwell landings gives a wrong number.",
            reasoning=(
                f"1. Best route: {names(b, r.space_path)} = {r.length_m} m, via {stairs}. "
                f"2. Openings crossed (element_path minus stairs/lifts): {n} = {kinds.count('door')} kind 'door' + {kinds.count('opening')} kind 'opening'. "
                f"3. Robustness: closing each element of the route in turn, the best alternative is {nearest_alt} m; alternatives within 1% of {r.length_m} m: {alts or 'none'}, so the count is not a near-tie artefact."
            ),
        ),
    )


@case(7)
def c07():
    bid = "proc-factory-dev-008"
    b = B(bid)
    s = resolve1(b, "Office 0.29 (Ground floor)")
    holders = sorted({a.space_id for a in b.assets_of_type("assembly_station")})
    naive = {h: RouteGraph(b).shortest(s, h) for h in holders}
    aware = {h: best(b, s, h) for h in holders}
    n0 = min((r.length_m, h) for h, r in naive.items())[1]
    n1 = min((r.length_m, h) for h, r in aware.items() if r)[1]
    assert n0 != n1 and aware[n0] is None
    r = aware[n1]
    labels = lambda sid: [b.spaces[sid].name, *([b.spaces[sid].ref] if b.spaces[sid].ref else [])]  # noqa: E731
    ans = {
        "type": "space",
        "space_id": n1,
        "accept": labels(n1),
        "reject": labels(n0),
        "length_m": r.length_m,
        "route": route_answer(b, r),
    }
    return dict(
        building_id=bid,
        kind="nearest_item_route",
        skill="nearest item holder under the production-bay rule",
        question="I'm in Office 0.29 (Ground floor). Which assembly station can I walk to soonest, and how far is the walk? Finish with a line 'Answer: <room>, <metres> m'",
        inputs={"from_space_id": s, "asset_type": "assembly_station"},
        answer=ans,
        extra=dict(
            difficulty="very_hard",
            solvable="yes",
            expected_behavior="answer",
            tool_coverage="partial",
            tools_needed=["nearest", "route"],
            based_on=None,
            modification=None,
            trap=f"`nearest` says {name(b, n0)} ({naive[n0].length_m} m), but the only ways there cross Production zone H-A1 or other bays, so it is not reachable under the rules; the answer is {name(b, n1)} ({r.length_m} m), entered directly from Hall access 0.42.",
            reasoning=(
                f"1. assembly_station holders: {[name(b, h) for h in holders]}. "
                f"2. Unconstrained walks: " + ", ".join(f"{name(b, h)} {naive[h].length_m} m" for h in holders) + ". "
                f"3. The office wing reaches the hall only through Hall access 0.28 -> Production zone H-A1 and Hall access 0.42 -> Assembly zone H-A4; both are bays, so they can be destinations but nothing behind them can be reached (no route round the outside). "
                f"4. With bays closed except the target: " + ", ".join(f"{name(b, h)} {aware[h].length_m if aware[h] else 'unreachable'}" for h in holders) + ". "
                f"5. Nearest valid: {name(b, n1)}, route {names(b, r.space_path)} = {r.length_m} m."
            ),
        ),
    )


@case(8)
def c08():
    bid = "proc-factory-dev-005"
    b = B(bid)
    s = resolve1(b, "Server room 1.21 (Floor 1)")
    holders = sorted({a.space_id for a in b.assets_of_type("cnc_lathe")})
    assert len(holders) == 2
    rs = {h: best(b, s, h) for h in holders}
    naive = {h: best(b, s, h, avoid_bays=False) for h in holders}
    return dict(
        building_id=bid,
        kind="route_to_item",
        skill="item in several rooms -> ask",
        question="Walk me from Server room 1.21 (Floor 1) to the CNC lathe.",
        inputs={"from_space_id": s, "asset_type": "cnc_lathe"},
        answer={
            "type": "clarification",
            "options": holders,
            "reason": "There are two CNC lathes, one in Production zone H-A3 and one in Production zone H-A5. Ask which one (or give both routes); if the user wants the nearest, it is Production zone H-A3.",
        },
        extra=dict(
            difficulty="hard",
            solvable="partially",
            expected_behavior="ask_clarification",
            tool_coverage="covered",
            tools_needed=["find", "route", "nearest"],
            based_on=None,
            modification=None,
            trap="'The CNC lathe' sounds unique but there are two; an agent that silently picks one may pick the wrong machine, and the nearest one is only reachable validly through the main aisle (the short cut through Assembly zone H-A2 is invalid).",
            reasoning=(
                f"1. find cnc_lathe -> {[name(b, h) for h in holders]} (two rooms). "
                f"2. Valid routes (bays closed except the end): " + "; ".join(f"{name(b, h)} {rs[h].length_m} m via {names(b, rs[h].space_path)}" for h in holders) + ". "
                f"3. Unconstrained: " + "; ".join(f"{name(b, h)} {naive[h].length_m} m (bays passed {bays_passed(b, naive[h])})" for h in holders) + ". "
                f"4. The question doesn't say which lathe, so the agent should ask, or list both with the valid routes and say which is nearest."
            ),
        ),
    )


@case(9)
def c09():
    bid = "proc-factory-dev-008-xt-narrow"
    b = B(bid)
    src = B("proc-factory-dev-008")
    s, t = resolve1(b, "Main aisle H-C1 (Ground floor)"), resolve1(b, "Production zone H-B6 (Ground floor)")
    r = best(b, s, t, FORKLIFT)
    walk = best(b, s, t)
    before = best(src, s, t, FORKLIFT)
    narrowed = "proc-factory-dev-008-F0-O0027"
    boundary = "proc-factory-dev-008-F0-O0035"
    assert narrowed in walk.element_path and narrowed not in r.element_path and boundary in r.element_path
    strict = best(b, s, t, Constraints(step_free=True, min_width_m=3.0001))
    return dict(
        building_id=bid,
        kind="route_forklift",
        skill="forklift route: 3 m width rule at the boundary + bay rule",
        question=f"A forklift has to drive from Main aisle H-C1 to Production zone H-B6 (both ground floor). Which way does it go? {FINISH_ROUTE}",
        inputs={"from_space_id": s, "to_space_id": t, "constraints": "forklift"},
        answer=route_answer(b, r, FORKLIFT, forklift=True),
        extra=dict(
            difficulty="very_hard",
            solvable="yes",
            expected_behavior="answer",
            tool_coverage="partial",
            tools_needed=["route", "room_details"],
            based_on=None,
            modification="Opening Main aisle H-C3 <-> H-C4 narrowed to 2.9 m; opening Logistics zone H-D3 <-> H-D4 set to exactly 3.0 m.",
            trap=f"Straight down the main aisle ({walk.length_m} m on foot) is blocked for a forklift by the 2.9 m gap at H-C3/H-C4; the detour through the logistics zones uses the H-D3/H-D4 opening of exactly 3.0 m, which counts ('3 m or wider'). The production zones next to the aisle can't be used as a bypass.",
            reasoning=(
                f"1. Forklift = step-free and every opening >= 3.0 m (ROUTE_SCORING: 'forklift aisles and openings only (3 m or wider)'). "
                f"2. In the source building the forklift route is {names(src, before.space_path)} = {before.length_m} m. "
                f"3. Here H-C3 -> H-C4 is 2.9 m: excluded. Walking still uses it: {names(b, walk.space_path)} = {walk.length_m} m. "
                f"4. Bypasses through Production zones H-B3/H-B4 are forbidden (bays may not be passed through). H-D3 -> H-D4 is exactly 3.0 m: allowed. "
                f"5. Forklift route: {names(b, r.space_path)} = {r.length_m} m. With a strict '> 3 m' reading there would be {'no route' if strict is None else f'a {strict.length_m} m route'}. "
                f"6. Caveat: we read 'forklift aisles' as hall floor with >= 3 m openings (the data has no aisle type); the logistics zones are forklift areas (docks, charging stations), not pedestrian walkways."
            ),
        ),
    )


@case(10)
def c10():
    bid = "proc-factory-dev-005"
    b = B(bid)
    s, t = resolve1(b, "Logistics zone H-C2 (Ground floor)"), resolve1(b, "Server room 1.21 (Floor 1)")
    walk = best(b, s, t)
    sf = best(b, s, t, Constraints(step_free=True))
    assert best(b, s, t, FORKLIFT) is None and walk and sf
    reach = RouteGraph(b, Constraints(step_free=True, min_width_m=3.0, closed=frozenset(bays(b)))).reachable(s)
    floors = sorted({b.floors[b.spaces[x].floor_id].level for x in reach if x in b.spaces})
    lift_doors = sorted({o.width_m for x in b.spaces.values() if x.type == "lift" for o in b.openings_of(x.id)})
    return dict(
        building_id=bid,
        kind="route_forklift",
        skill="forklift can't leave the hall floor",
        question="Can the forklift take a pallet of server racks from Logistics zone H-C2 (Ground floor) all the way into Server room 1.21 (Floor 1)? If yes, which way?",
        inputs={"from_space_id": s, "to_space_id": t, "constraints": "forklift"},
        answer=no_route(
            f"A forklift needs openings of 3 m or more and no stairs. Every door of the office wing is 0.9-1.6 m, the Office lift doors are {lift_doors} m, and Server room 1.21's door is 1.6 m, so the forklift can't leave the hall."
        ),
        extra=dict(
            difficulty="hard",
            solvable="no",
            expected_behavior="say_no_route",
            tool_coverage="covered",
            tools_needed=["route"],
            based_on=None,
            modification=None,
            trap=f"A person walks it ({walk.length_m} m) and even a wheelchair can ({sf.length_m} m, via the lift), so an agent that forgets the 3 m width or treats the lift as forklift-capable gives a route.",
            reasoning=(
                f"1. Walking route exists: {names(b, walk.space_path)} = {walk.length_m} m. "
                f"2. Step-free route exists via the Office lift ({sf.length_m} m), but its doors are {lift_doors} m. "
                f"3. Forklift constraints (step-free, openings >= 3 m, no bay passed): reachable floors from H-C2 = {floors}; Floor 1 is not reachable. So no route."
            ),
        ),
    )


@case(11)
def c11():
    bid = "proc-campus-dev-009"
    b = B(bid)
    s, t = resolve1(b, "Meeting room 0.06 (Ground floor)"), resolve1(b, "Print room 5.07 (Floor 5)")
    lifts = frozenset(k.id for k in b.connectors.values() if k.kind == "lift")
    stairs = frozenset(k.id for k in b.connectors.values() if k.kind == "stairs")
    r = best(b, s, t)
    by_stairs = best(b, s, t, Constraints(closed=lifts))
    by_lift = best(b, s, t, Constraints(closed=stairs))
    assert r.length_m == by_lift.length_m < by_stairs.length_m
    rise = 19.0
    return dict(
        building_id=bid,
        kind="route",
        skill="lift vs stairs: the lift wins on a long climb",
        question=f"What's the shortest way from Meeting room 0.06 (Ground floor) to Print room 5.07 (Floor 5)? {FINISH_ROUTE}",
        inputs={"from_space_id": s, "to_space_id": t},
        answer=route_answer(b, r),
        extra=dict(
            difficulty="hard",
            solvable="yes",
            expected_behavior="answer",
            tool_coverage="covered",
            tools_needed=["route"],
            based_on=None,
            modification=None,
            trap=f"'Stairs are shorter than the lift' holds for one or two floors, but over {rise} m the stairs cost 2 x {rise} = {2 * rise} m and the lift 15 + {rise} = {15 + rise} m; the best stairs route is {by_stairs.length_m} m vs {by_lift.length_m} m by lift ({round(100 * (1 - by_lift.length_m / by_stairs.length_m), 1)}% worse, outside the 1% band).",
            reasoning=(
                f"1. Ground floor elevation 0.0 m, Floor 5 elevation 19.0 m: rise {rise} m. "
                f"2. Connector cost: stairs 2 m per metre = {2 * rise} m; lift 15 m + 1 m per metre = {15 + rise} m. Break-even is a 15 m rise. "
                f"3. Best with lifts closed: {names(b, by_stairs.space_path)} = {by_stairs.length_m} m. "
                f"4. Best with stairs closed: {names(b, by_lift.space_path)} = {by_lift.length_m} m via {connectors_used(b, by_lift)}. "
                f"5. Overall best = the lift route, {r.length_m} m."
            ),
        ),
    )


@case(12)
def c12():
    bid = "proc-campus-dev-009"
    b = B(bid)
    stops = [resolve1(b, x) for x in ("Meeting room 0.06 (Ground floor)", "Print room 5.07 (Floor 5)", "Office 4.03 (Floor 4)", "Lab 0.02 (Ground floor)")]
    legs = [best(b, a, z) for a, z in zip(stops, stops[1:])]
    lifts = frozenset(k.id for k in b.connectors.values() if k.kind == "lift")
    stairs = frozenset(k.id for k in b.connectors.values() if k.kind == "stairs")
    alt = {i: (best(b, a, z, Constraints(closed=lifts)).length_m, best(b, a, z, Constraints(closed=stairs)).length_m) for i, (a, z) in enumerate(zip(stops, stops[1:]))}
    reordered = best(b, stops[0], stops[3]).length_m + best(b, stops[3], stops[2]).length_m + best(b, stops[2], stops[1]).length_m
    ans = tour_answer(b, legs)
    return dict(
        building_id=bid,
        kind="tour",
        skill="fixed-order tour, the best connector flips per leg",
        question="Starting at Meeting room 0.06 (Ground floor), visit Print room 5.07 (Floor 5), then Office 4.03 (Floor 4), then Lab 0.02 (Ground floor), in this order. What is the route and the total walking distance? Finish with a line 'Answer: <total metres> m'",
        inputs={"stops": stops},
        answer=ans,
        extra=dict(
            difficulty="hard",
            solvable="yes",
            expected_behavior="answer",
            tool_coverage="covered",
            tools_needed=["route"],
            based_on=None,
            modification=None,
            trap="The cheapest way between floors changes per leg: lift for 0->5 (19 m rise), stairs for 5->4 (3.8 m), and 4->0 (15.2 m) is almost a tie (stairs 30.4 m vs lift 30.2 m) decided by the walk to the shaft. Using one connector type for the whole tour, or reordering the stops, gives the wrong total.",
            reasoning=(
                f"1. Leg 1 0->5: {names(b, legs[0].space_path)} = {legs[0].length_m} m (stairs-only {alt[0][0]}, lift-only {alt[0][1]}). "
                f"2. Leg 2 5->4: {names(b, legs[1].space_path)} = {legs[1].length_m} m (stairs-only {alt[1][0]}, lift-only {alt[1][1]}). "
                f"3. Leg 3 4->0: {names(b, legs[2].space_path)} = {legs[2].length_m} m (stairs-only {alt[2][0]}, lift-only {alt[2][1]}). "
                f"4. Total {legs[0].length_m} + {legs[1].length_m} + {legs[2].length_m} = {ans['length_m']} m. (Visiting in the reverse order would be {round(reordered, 2)} m; the order is fixed.)"
            ),
        ),
    )


@case(13)
def c13():
    bid = "proc-campus-dev-001"
    b = B(bid)
    s, t = resolve1(b, "Office 3.03 (Floor 3)"), resolve1(b, "Seminar room 2.02 (Floor 2)")
    lift1 = f"{bid}-V02"
    assert b.connectors[lift1].name == "Lift 1"
    c = Constraints(step_free=True, closed=frozenset({lift1}))
    r = best(b, s, t, c)
    sf = best(b, s, t, Constraints(step_free=True))
    walk = best(b, s, t)
    assert connectors_used(b, r) == ["Lift 2"]
    return dict(
        building_id=bid,
        kind="route_with_closure",
        skill="step-free + lift closure -> long detour",
        question=f"Lift 1 is out of order. I use a wheelchair and need to get from Office 3.03 (Floor 3) to Seminar room 2.02 (Floor 2). How? {FINISH_ROUTE}",
        inputs={"from_space_id": s, "to_space_id": t, "closed_element_id": lift1, "step_free": True},
        answer=route_answer(b, r, c),
        extra=dict(
            difficulty="hard",
            solvable="yes",
            expected_behavior="answer",
            tool_coverage="covered",
            tools_needed=["set_closed", "route"],
            based_on="proc-campus-dev-001 route_with_closure / route_step_free",
            modification=None,
            trap=f"Both rooms sit next to Staircase 1 / Lift 1; walking is {walk.length_m} m by the stairs. Step-free with Lift 1 closed means going to Lift 2 at the far end of both corridors ({r.length_m} m, double the {sf.length_m} m step-free route with Lift 1). Answering with the stairs, or with Lift 1, scores 0.",
            reasoning=(
                f"1. Walking: {names(b, walk.space_path)} = {walk.length_m} m (stairs: not allowed for a wheelchair). "
                f"2. Step-free with all lifts: {names(b, sf.space_path)} = {sf.length_m} m via {connectors_used(b, sf)}. "
                f"3. Step-free with Lift 1 closed: {names(b, r.space_path)} = {r.length_m} m via {connectors_used(b, r)}."
            ),
        ),
    )


@case(14)
def c14():
    bid = "proc-campus-dev-001"
    b = B(bid)
    s, t = resolve1(b, "Storage 3.08 (Floor 3)"), resolve1(b, "Office 2.08 (Floor 2)")
    st = sorted(k.id for k in b.connectors.values() if k.kind == "stairs")
    assert len(st) == 2
    res = {k: best(b, s, t, Constraints(closed=frozenset({k}))) for k in st}
    assert len({r.length_m for r in res.values()}) == 2
    open_ = best(b, s, t)
    return dict(
        building_id=bid,
        kind="route_with_closure",
        skill="ambiguous closure: which staircase?",
        question=f"The staircase is closed for cleaning. How do I get from Storage 3.08 (Floor 3) to Office 2.08 (Floor 2)?",
        inputs={"from_space_id": s, "to_space_id": t, "closed_element_id": None},
        answer={
            "type": "clarification",
            "options": st,
            "reason": "The building has two staircases (Staircase 1 and Staircase 2) and the best route depends on which one is closed. Ask, or give both conditional answers.",
        },
        extra=dict(
            difficulty="hard",
            solvable="partially",
            expected_behavior="ask_clarification",
            tool_coverage="covered",
            tools_needed=["set_closed", "route", "find"],
            based_on="proc-campus-dev-001 route_with_closure",
            modification=None,
            trap="'The staircase' sounds unique; an agent that closes the first match (Staircase 1) answers as if nothing changed, while if Staircase 2 (next to both rooms) is the closed one the route moves to Lift 2 and gets much longer.",
            reasoning=(
                f"1. Stairs connectors: " + ", ".join(f"{k} = {b.connectors[k].name}" for k in st) + ". "
                f"2. Nothing closed: {names(b, open_.space_path)} = {open_.length_m} m via {connectors_used(b, open_)}. "
                + " ".join(f"{i + 3}. {b.connectors[k].name} closed: {names(b, r.space_path)} = {r.length_m} m via {connectors_used(b, r)}." for i, (k, r) in enumerate(res.items()))
                + f" {len(res) + 3}. The answers differ, so the closure must be identified first."
            ),
        ),
    )


@case(15)
def c15():
    bid = "proc-campus-dev-006"
    b = B(bid)
    s, t = resolve1(b, "Office 0.03 (Ground floor)"), resolve1(b, "Office 2.04 (Floor 2)")
    lift = f"{bid}-V02"
    assert b.connectors[lift].kind == "lift" and sum(k.kind == "lift" for k in b.connectors.values()) == 1
    c = Constraints(step_free=True, closed=frozenset({lift}))
    assert best(b, s, t, c) is None
    walk, sf = best(b, s, t), best(b, s, t, Constraints(step_free=True))
    return dict(
        building_id=bid,
        kind="route_with_closure",
        skill="step-free + the only lift closed -> no route",
        question="Lift 1 is being serviced this morning. Is there a step-free way for a wheelchair user from Office 0.03 (Ground floor) to Office 2.04 (Floor 2)? If so, which?",
        inputs={"from_space_id": s, "to_space_id": t, "closed_element_id": lift, "step_free": True},
        answer=no_route("Lift 1 is the building's only lift; the only other link between floors is Staircase 1, which is not step-free."),
        extra=dict(
            difficulty="hard",
            solvable="no",
            expected_behavior="say_no_route",
            tool_coverage="covered",
            tools_needed=["set_closed", "route"],
            based_on="proc-campus-dev-001 step_free_reachable_count",
            modification=None,
            trap=f"A walking route exists ({walk.length_m} m via Staircase 1) and normally a step-free one via Lift 1 ({sf.length_m} m); an agent that forgets either the closure or the step-free rule gives one of those.",
            reasoning=(
                f"1. Connectors: {[f'{k.name} ({k.kind})' for k in b.connectors.values()]}: one lift only. "
                f"2. Walking: {walk.length_m} m via {connectors_used(b, walk)} (not step-free). Step-free with the lift: {sf.length_m} m via {connectors_used(b, sf)}. "
                f"3. Step-free with Lift 1 closed: RouteGraph finds no route. So: no step-free route until the lift is back."
            ),
        ),
    )


@case(16)
def c16():
    bid = "osm-way22744230"
    b = B(bid)
    s, t = resolve1(b, "Lecture hall N0314 (Ground floor)"), resolve1(b, "Generic room N6310 (Floor 6)")
    c = Constraints(step_free=True)
    assert best(b, s, t, c) is None
    walk = best(b, s, t)
    f6 = b.floor_at_level(6).id
    to6 = [k.name for k in b.connectors.values() if any(f == f6 for f, _ in k.stops)]
    lifts = {k.name: sorted(b.floors[f].level for f, _ in k.stops) for k in b.connectors.values() if k.kind == "lift"}
    reach6 = [x for x in RouteGraph(b, c).reachable(s) if x in b.spaces and b.spaces[x].floor_id == f6]
    assert not reach6
    return dict(
        building_id=bid,
        kind="route_step_free",
        skill="step-free to a floor the lift doesn't serve",
        question="Is there a step-free route from Lecture hall N0314 (Ground floor) to Generic room N6310 (Floor 6)? If yes, give it.",
        inputs={"from_space_id": s, "to_space_id": t, "step_free": True},
        answer=no_route(f"Lift 2 serves levels {lifts['Lift 2']} and Lift 1 only {lifts['Lift 1']}; Floor 6 is reached only by {to6}. No step-free way up."),
        extra=dict(
            difficulty="hard",
            solvable="no",
            expected_behavior="say_no_route",
            tool_coverage="covered",
            tools_needed=["route", "room_details"],
            based_on="osm-way22744230 route_step_free",
            modification=None,
            trap=f"The building has lifts and a {walk.length_m} m walking route exists, but the top floor isn't served by any lift; an agent that assumes 'there is a lift' or answers with the stairs route is wrong.",
            reasoning=(
                f"1. Lifts: {lifts}. "
                f"2. Connectors stopping on Floor 6: {to6} (all stairs). "
                f"3. Step-free reachable spaces on Floor 6 from the start: {len(reach6)}. "
                f"4. Walking route (not step-free): {names(b, walk.space_path)} = {walk.length_m} m."
            ),
        ),
    )


@case(17)
def c17():
    bid = "osm-way22744230"
    b = B(bid)
    s, t = resolve1(b, "Lecture hall N0314 (Ground floor)"), resolve1(b, "Generic room N0315C (Ground floor)")
    assert RouteGraph(b).shortest(s, t) is None
    ops = b.openings_of(t)
    assert len(ops) == 1 and ops[0].is_entrance
    return dict(
        building_id=bid,
        kind="route",
        skill="room only reachable from outside",
        question=f"How do I get from Lecture hall N0314 (Ground floor) to Generic room N0315C (Ground floor)? {FINISH_ROUTE}",
        inputs={"from_space_id": s, "to_space_id": t},
        answer=no_route(f"Generic room N0315C has a single opening ({ops[0].id}), an entrance from outside; no door joins it to the rest of the building, and a route may not go round the outside."),
        extra=dict(
            difficulty="very_hard",
            solvable="no",
            expected_behavior="say_no_route",
            tool_coverage="covered",
            tools_needed=["route", "room_details"],
            based_on=None,
            modification=None,
            trap="Both rooms are on the ground floor a few metres apart; an agent may invent a door or route out of one entrance and in through another (the kit 1.0 model allowed that at 0 m). Under kit 1.1 there is no route round the outside, so the answer is no route (mentioning it is only reachable from the street is a good caveat).",
            reasoning=(
                f"1. room_details Generic room N0315C: openings = [{ops[0].id} kind={ops[0].kind} to outside]. "
                f"2. No door, opening, stair or lift joins it to another space. "
                f"3. RouteGraph (no outdoor shortcut) returns None from Lecture hall N0314. So: no route inside the building."
            ),
        ),
    )


@case(18)
def c18():
    bid = "swiss-2325"
    b = B(bid)
    s, t = resolve1(b, "Kitchen 0.07 (apartment 1)"), resolve1(b, "Living room 5.08 (apartment 11)")
    main = f"{bid}-V01"
    assert b.connectors[main].name == "Main staircase"
    c = Constraints(closed=frozenset({main}))
    r = best(b, s, t, c)
    landing = resolve1(b, "Landing 0.18")
    wrong = best(b, s, t, Constraints(closed=frozenset({landing})))
    assert wrong is None and connectors_used(b, r) == ["Main lift"]
    stairs_only = best(b, s, t, Constraints(closed=frozenset({f"{bid}-V02"})))
    return dict(
        building_id=bid,
        kind="route_with_closure",
        skill="closed staircase vs the landing it shares with the lift",
        question=f"The main staircase is closed for renovation. How do I get from Kitchen 0.07 (apartment 1) on the ground floor to Living room 5.08 (apartment 11) on Floor 5? {FINISH_ROUTE}",
        inputs={"from_space_id": s, "to_space_id": t, "closed_element_id": main},
        answer=route_answer(b, r, c),
        extra=dict(
            difficulty="hard",
            solvable="yes",
            expected_behavior="answer",
            tool_coverage="covered",
            tools_needed=["set_closed", "route"],
            based_on="swiss route_with_closure",
            modification=None,
            trap="The Main staircase and the Main lift stop in the same landing spaces (Landing 0.18 ... Landing 5.11). Closing the staircase must not close the landing: an agent that closes 'Landing 0.18' finds no route at all, because apartment 1 only opens onto that landing.",
            reasoning=(
                f"1. Main staircase {main} and Main lift {bid}-V02 share stops: {[name(b, x) for _, x in b.connectors[main].stops]}. "
                f"2. Rise 0 -> 15 m: stairs 2 x 15 = 30 m, lift 15 + 15 = 30 m, a tie; with the stairs open the stairs route is {stairs_only.length_m} m. "
                f"3. Main staircase closed: {names(b, r.space_path)} = {r.length_m} m via {connectors_used(b, r)}. "
                f"4. If the landing space were closed instead: no route (apartment 1's only exit is Landing 0.18)."
            ),
        ),
    )


@case(19)
def c19():
    bid = "proc-campus-dev-013"
    b = B(bid)
    stops = [resolve1(b, x) for x in ("Office 0.02 (Ground floor)", "Print room 2.09 (Floor 2)", "Canteen 0.19 (Ground floor)")]
    legs = [best(b, a, z) for a, z in zip(stops, stops[1:])]
    direct = best(b, stops[0], stops[2])
    wrong_print = resolve1(b, "Print room 0.08 (Ground floor)")
    wrong = best(b, stops[0], wrong_print).length_m + best(b, wrong_print, stops[2]).length_m
    ans = tour_answer(b, legs)
    return dict(
        building_id=bid,
        kind="tour",
        skill="via-point on another floor",
        question="I'm going from Office 0.02 (Ground floor) to Canteen 0.19 (Ground floor), but on the way I have to pick up a print job in the print room on Floor 2. What's my route and how far do I walk in total? Finish with a line 'Answer: <total metres> m'",
        inputs={"stops": stops},
        answer=ans,
        extra=dict(
            difficulty="hard",
            solvable="yes",
            expected_behavior="answer",
            tool_coverage="covered",
            tools_needed=["find", "route"],
            based_on="proc-campus-dev-013 tour",
            modification=None,
            trap=f"Start and end are on the ground floor and there is a Print room 0.08 on the ground floor too; the via-point is Print room 2.09 two floors up. Direct route = {direct.length_m} m, via the wrong print room = {round(wrong, 2)} m; the different staircases used up and down change the total.",
            reasoning=(
                f"1. Print room on Floor 2: Print room 2.09 (find print_room on Floor 2). "
                f"2. Leg 1: {names(b, legs[0].space_path)} = {legs[0].length_m} m via {connectors_used(b, legs[0])}. "
                f"3. Leg 2: {names(b, legs[1].space_path)} = {legs[1].length_m} m via {connectors_used(b, legs[1])}. "
                f"4. Total = {ans['length_m']} m (direct without the stop {direct.length_m} m)."
            ),
        ),
    )


@case(20)
def c20():
    bid = "proc-factory-train-006"
    b = B(bid)
    stops = [resolve1(b, x) for x in ("Quality lab 0.24 (Ground floor)", "Assembly zone H-A2 (Ground floor)", "Logistics zone H-C2 (Ground floor)", "Quality lab 0.24 (Ground floor)")]
    legs = [best(b, a, z) for a, z in zip(stops, stops[1:])]
    naive = [best(b, a, z, avoid_bays=False) for a, z in zip(stops, stops[1:])]
    assert bays_passed(b, naive[2]) and not any(bays_passed(b, x) for x in legs)
    ans = tour_answer(b, legs)
    naive_total = round(sum(x.length_m for x in naive), 2)
    return dict(
        building_id=bid,
        kind="tour",
        skill="tour whose stop is a production bay; later legs may not cross it",
        question="Starting at Quality lab 0.24, go to Assembly zone H-A2, then to Logistics zone H-C2, then back to Quality lab 0.24 (all ground floor), in this order. What is the route and the total distance? Finish with a line 'Answer: <total metres> m'",
        inputs={"stops": stops},
        answer=ans,
        extra=dict(
            difficulty="very_hard",
            solvable="yes",
            expected_behavior="answer",
            tool_coverage="partial",
            tools_needed=["route"],
            based_on=None,
            modification=None,
            trap=f"Assembly zone H-A2 is a stop, so ending and starting a leg there is fine, but the last leg (Logistics zone H-C2 -> Quality lab 0.24) may not cut back through it to Hall access 0.19; it must go via Warehouse zone H-A1 and Hall access 0.13. Unconstrained total {naive_total} m vs valid {ans['length_m']} m.",
            reasoning=(
                f"1. Leg 1 (ends in a bay: allowed): {names(b, legs[0].space_path)} = {legs[0].length_m} m. "
                f"2. Leg 2 (starts in a bay: allowed): {names(b, legs[1].space_path)} = {legs[1].length_m} m. "
                f"3. Leg 3 unconstrained: {names(b, naive[2].space_path)} = {naive[2].length_m} m, passes {bays_passed(b, naive[2])}: invalid. "
                f"4. Leg 3 valid: {names(b, legs[2].space_path)} = {legs[2].length_m} m. "
                f"5. Total {legs[0].length_m} + {legs[1].length_m} + {legs[2].length_m} = {ans['length_m']} m."
            ),
        ),
    )


# ---- write / verify ------------------------------------------------------------------------------------------

VERIFICATION = (
    "data/extra_tricky/_verify/routing_rules.py case {n}: RouteGraph (kit 1.1 distance rule) with every production bay "
    "except the leg's start/end closed; answer route checked with validate_route; asserts in the script check the trap "
    "(naive route invalid / alternative differs / no route) as stated."
)


def summary(a: dict) -> str:
    if a["type"] == "route":
        return f"route of {len(a['spaces'])} spaces, {a['length_m']} m"
    if a["type"] == "tour":
        return f"tour of {len(a['legs'])} legs, {a['length_m']} m"
    if a["type"] == "space":
        return f"space {a['space_id']}, {a['length_m']} m"
    if a["type"] == "number":
        return f"number {a['value']}"
    if a["type"] == "clarification":
        return f"clarification between {a['options']}"
    return "no route (RouteGraph returns None under the rules)"


def build(n: int) -> dict:
    rec = CASES[n]()
    extra = rec.pop("extra")
    out = {"id": f"xt-{DIRECTION}-{n:02d}", "building_id": rec["building_id"], "building_path": kit_path(rec["building_id"])}
    out.update({k: rec[k] for k in ("kind", "skill", "question", "inputs", "answer")})
    out["extra"] = {"direction": DIRECTION, **extra, "verification": VERIFICATION.format(n=n) + f" Recomputed: {summary(out['answer'])}."}
    return out


def same(a, b) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        return isinstance(a, (int, float)) and isinstance(b, (int, float)) and math.isclose(a, b, abs_tol=0.011)
    if isinstance(a, dict):
        return isinstance(b, dict) and a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return isinstance(b, list) and len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return a == b


def main() -> None:
    records = [build(n) for n in sorted(CASES)]
    if "--write" in sys.argv:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
        print(f"wrote {len(records)} cases to {OUT}")
        return
    stored = {r["id"]: r for r in map(json.loads, OUT.read_text(encoding="utf-8").splitlines())}
    assert len(stored) == len(records) == 20, (len(stored), len(records))
    for r in records:
        s = stored[r["id"]]
        assert same(r["answer"], s["answer"]), f"{r['id']}: answer differs\n{r['answer']}\n{s['answer']}"
        assert r["inputs"] == s["inputs"] and r["building_id"] == s["building_id"], r["id"]
        print(f"ok {r['id']} {r['building_id']} {r['answer']['type']}")
    print("all 20 routing_rules cases verified")


if __name__ == "__main__":
    main()
