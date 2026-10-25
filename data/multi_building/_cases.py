"""Hand-written multi-building cases → data/multi_building/<building>/questions.jsonl, with computed answers.

Run: uv run python data/multi_building/_cases.py   (after _build.py)

Each case states its intent by hand: the spaces, stairs, lifts or walkways the best route must use and
must avoid, and the naive alternative (`alt`): either the same question with something closed, or the
route a walker would take (`"walking"`), checked against the case's rules. Answers are computed with
RouteGraph (Synera's distance rule) with production bays allowed only as a leg's start or end (Synera's
rule, which RouteGraph alone doesn't apply: `extra.routegraph_agrees` is false where it matters); the script refuses to write a case whose intent doesn't hold, whose
alternative isn't worse by more than the 1 % full-marks band, or whose stated alternative is still valid.
tests/building/test_multi_building.py re-checks the frozen files.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

from app.building.model import Building, load_building
from app.building.routing import (
    FORKLIFT,
    PRODUCTION_BAYS,
    WALKING,
    Constraints,
    RouteGraph,
    Tour,
    validate_route,
)

HERE = Path(__file__).parent
SF = "step_free"
WIDE = "wide_1.1"
FORK = "forklift"
RULES = {
    "walking": WALKING,
    SF: Constraints(step_free=True),
    WIDE: Constraints(min_width_m=1.1),
    FORK: FORKLIFT,
}
RULE_TEXT = {
    "walking": "",
    SF: " I use a wheelchair, so no stairs or steps.",
    WIDE: " I'm pushing a trolley that needs doors at least 1.1 m wide; I can carry it up stairs.",
    FORK: " I'm driving a forklift: only openings 3 m or wider, no stairs.",
}

# fmt: off
CASES: list[dict] = [
    # ---- xmb-bridge-works: yard on the ground floor, bridge on the first floor --------------------------
    dict(id="xmb-bridge-01", building="xmb-bridge-works", kind="route", stops=["Office A1.1", "Office B1.1"],
         use=["Bridge A-B"], avoid=["Yard"], alt={"closed": ["Bridge A-B"]},
         trap="Both offices are on the first floor: going down and across the yard is a long detour."),
    dict(id="xmb-bridge-02", building="xmb-bridge-works", kind="route", stops=["Office A0.1", "Office B0.1"],
         use=["Yard"], avoid=["Bridge A-B"], alt={"closed": ["Yard"]},
         trap="The bridge looks like 'the' link between the halls, but on the ground floor the yard is direct."),
    dict(id="xmb-bridge-03", building="xmb-bridge-works", kind="route", stops=["Meeting room A1.4", "Lab B0.4"],
         use=["Bridge A-B", "Staircase B"], avoid=["Yard"], alt={"closed": ["Bridge A-B"]},
         trap="Changing floor matters: Hall A's staircase is at its far end, Hall B's next to the yard, so "
              "cross on the first floor and take the stairs down in Hall B."),
    dict(id="xmb-bridge-04", building="xmb-bridge-works", kind="route", stops=["Lab A0.4", "Meeting room B1.4"],
         use=["Yard", "Staircase B"], avoid=["Bridge A-B"], alt={"closed": ["Yard"]},
         trap="The mirror of xmb-bridge-03: now going outside first and up Hall B's stairs is shorter."),
    dict(id="xmb-bridge-05", building="xmb-bridge-works", kind="route_step_free", rules=SF,
         stops=["Lab A0.4", "Meeting room B1.4"],
         use=["Bridge A-B", "Freight lift A"], avoid=["Yard"], alt={"closed": ["Bridge A-B"]}, alt_is="walking",
         trap="The walking answer (xmb-bridge-04) goes outside; in a wheelchair the yard door of Hall A has "
              "steps and Hall B's lift is at the far end, so the bridge wins."),
    dict(id="xmb-bridge-06", building="xmb-bridge-works", kind="route_step_free", rules=SF,
         stops=["Office A1.1", "Lab B0.4"],
         use=["Freight lift A", "Goods hall A", "Yard"], avoid=["Bridge A-B"], alt={"closed": ["Yard"]},
         trap="Close call (about 5 %): even from the first floor, a wheelchair is better off taking lift A down "
              "and crossing the yard through the goods hall than taking the bridge and lift B."),
    dict(id="xmb-bridge-07", building="xmb-bridge-works", kind="route_step_free", rules=SF,
         stops=["Office A0.1", "Office B0.1"],
         use=["Goods hall A", "Yard"], avoid=["Bridge A-B"], alt="walking",
         trap="The pedestrian door from Corridor A0 to the yard has two steps: go round through the goods hall "
              "and its level truck gate."),
    dict(id="xmb-bridge-08", building="xmb-bridge-works", kind="route_forklift", rules=FORK,
         stops=["Mezzanine store A1", "Mezzanine store B1"],
         use=["Freight lift A", "Yard", "Freight lift B"], avoid=["Bridge A-B"], alt="walking",
         trap="Both stores are on the first floor and the bridge is right there, but its doors are 1.4 m: the "
              "forklift must ride lift A down, cross the yard by the truck gates and ride lift B up."),
    dict(id="xmb-bridge-09", building="xmb-bridge-works", kind="route_forklift", rules=FORK,
         stops=["Mezzanine store A1", "Mezzanine store B1"], closed=[("Goods hall B", "Yard")],
         expect="no_route",
         trap="With Hall B's truck gate shut there is no 3 m way into Hall B: the bridge and the pedestrian yard "
              "door are too narrow. The right answer is that there's no forklift route."),
    dict(id="xmb-bridge-10", building="xmb-bridge-works", kind="route_with_closure",
         stops=["Office A0.1", "Office B0.1"], closed=["Yard"],
         use=["Staircase A", "Bridge A-B", "Staircase B"], avoid=["Yard"],
         trap="The yard is closed for unloading, so the only way is up, over the bridge and down again."),
    dict(id="xmb-bridge-11", building="xmb-bridge-works", kind="tour",
         stops=["Office A0.1", "Meeting room A1.4", "Lab B0.4"], use=["Bridge A-B"], leg_use={1: ["Bridge A-B"]},
         trap="After fetching the projector upstairs, don't go back down to the yard: take the bridge and Hall "
              "B's stairs."),
    dict(id="xmb-bridge-12", building="xmb-bridge-works", kind="tour", rules=FORK,
         stops=["Mezzanine store A1", "Goods hall B", "Mezzanine store A1"], use=["Yard"], avoid=["Bridge A-B"],
         trap="A forklift pallet run between floors and halls: every leg must use lifts and truck gates."),
    dict(id="xmb-bridge-13", building="xmb-bridge-works", kind="tour", rules=SF,
         stops=["Lab A0.4", "Meeting room B1.4", "Office A0.1"],
         leg_use={0: ["Bridge A-B"], 1: ["Bridge A-B"]}, avoid=["Yard"],
         trap="Out over the bridge and back over the bridge: the stepped yard door rules out the short way "
              "back on the ground floor."),
    # ---- xmb-u-wing: courtyard across the U on the ground floor ---------------------------------------
    dict(id="xmb-uwing-01", building="xmb-u-wing", kind="route", stops=["Seminar room W0.1", "Seminar room E0.1"],
         use=["Courtyard"], alt={"closed": ["Courtyard"]},
         trap="The two seminar rooms face each other across the courtyard; inside, the way goes all round the U."),
    dict(id="xmb-uwing-02", building="xmb-u-wing", kind="route_step_free", rules=SF,
         stops=["Seminar room W0.1", "Seminar room E0.1"], use=["Courtyard"], alt={"closed": ["Courtyard"]},
         trap="The courtyard door next to Seminar room W0.1 has a step; the ramp door further down the west "
              "corridor is still far shorter than going round the inside. Same spaces as walking "
              "(xmb-uwing-01), different door: only element_path and length show the step was avoided."),
    dict(id="xmb-uwing-03", building="xmb-u-wing", kind="route", stops=["Office W0.3", "Office E0.3"],
         avoid=["Courtyard"], alt={"closed": [("South corridor 0 west", "South corridor 0 east")]},
         trap="From the inner ends of the wings, going round the base is shorter than the courtyard."),
    dict(id="xmb-uwing-04", building="xmb-u-wing", kind="route_wide", rules=WIDE, stops=["Office W0.3", "Lab S0.4"],
         use=["Courtyard", "East corridor 0"], alt="walking",
         trap="The lab is just down the corridor, but the fire door in the south corridor is 0.9 m: a 1.1 m "
              "trolley goes out through the courtyard and comes back along the east wing."),
    dict(id="xmb-uwing-05", building="xmb-u-wing", kind="route_wide", rules=WIDE,
         stops=["Seminar room W0.1", "Office E0.3"], use=["Courtyard"], alt={"closed": ["Courtyard"]},
         trap="The nearest courtyard door is 1.0 m (and stepped), the fire door 0.9 m: use the 1.2 m ramp door. "
              "Without the courtyard the trolley would have to go over the first floor."),
    dict(id="xmb-uwing-06", building="xmb-u-wing", kind="route", stops=["Seminar room W1.1", "Seminar room E1.1"],
         use=["Staircase W", "Courtyard", "Staircase E"], alt={"closed": ["Courtyard"]},
         trap="Both rooms are on the first floor, which has no courtyard; still, down, across and up beats "
              "walking round the first floor by about 7 %."),
    dict(id="xmb-uwing-07", building="xmb-u-wing", kind="route_step_free", rules=SF,
         stops=["Seminar room W1.1", "Seminar room E1.1"], avoid=["Courtyard", "Lift"], alt="walking",
         trap="In a wheelchair the only lift is in the base of the U, on the way round anyway: stay upstairs."),
    dict(id="xmb-uwing-08", building="xmb-u-wing", kind="route", stops=["Kitchen S0.2", "Seminar room E0.1"],
         use=["Courtyard"], alt={"closed": ["Courtyard"]},
         trap="Starting in the base, the inside way looks natural, but the courtyard is about 3 % shorter: "
              "outside the 1 % band for full marks."),
    dict(id="xmb-uwing-09", building="xmb-u-wing", kind="route_with_closure", rules=WIDE,
         stops=["Kitchen S0.2", "Seminar room E0.1"], closed=["Courtyard"],
         use=["Staircase W", "Staircase E"], avoid=["Courtyard"],
         trap="Courtyard closed and the fire door too narrow for the trolley: the only way is over the first "
              "floor, carrying it up one staircase and down the other."),
    dict(id="xmb-uwing-10", building="xmb-u-wing", kind="tour",
         stops=["Seminar room W0.1", "Kitchen S0.2", "Seminar room E0.1"], leg_use={1: ["Courtyard"]},
         leg_avoid={0: ["Courtyard"]},
         trap="Coffee in the base first, then on to the east wing: from the kitchen the courtyard is still "
              "shorter than carrying on along the base (see xmb-uwing-08)."),
    dict(id="xmb-uwing-11", building="xmb-u-wing", kind="tour", rules=SF,
         stops=["Seminar room W1.1", "Office E0.3", "Seminar room W0.1"], use=["Lift"],
         trap="Upstairs to downstairs and back across the U in a wheelchair: one lift, a stepped courtyard "
              "door and a ramp door decide each leg."),
    # ---- xmb-tour-campus: three kit buildings, three walkways (Level 3 site) ---------------------------
    dict(id="xmb-campus-01", building="xmb-tour-campus", kind="route",
         stops=["proc-campus-dev-000:Lecture hall 0.27", "proc-campus-dev-001:Lecture hall 0.21"],
         use=["Garden path"], avoid=["Covered walkway", "Factory road"], alt={"closed": ["Garden path"]},
         trap="The garden path joins the two campus buildings directly; no need to go through the factory."),
    dict(id="xmb-campus-02", building="xmb-tour-campus", kind="route_step_free", rules=SF,
         stops=["proc-campus-dev-000:Lecture hall 0.27", "proc-campus-dev-001:Lecture hall 0.21"],
         use=["Covered walkway", "Factory road"], avoid=["Garden path"], alt="walking",
         trap="The garden path ends at a stepped side entrance on both sides: step-free means through the "
              "factory."),
    dict(id="xmb-campus-03", building="xmb-tour-campus", kind="tour",
         stops=["proc-campus-dev-000:Lecture hall 0.27", "proc-factory-dev-005:Kitchen 0.25",
                "proc-campus-dev-001:Lecture hall 0.21"],
         leg_use={0: ["Covered walkway"], 1: ["Factory road"]}, avoid=["Garden path"],
         trap="The stop in the factory puts it on the way: the direct garden path (xmb-campus-01) is no use."),
    dict(id="xmb-campus-04", building="xmb-tour-campus", kind="tour", rules=SF,
         stops=["proc-campus-dev-001:Lab 0.06", "proc-campus-dev-000:Canteen 0.28", "proc-campus-dev-001:Lab 0.06"],
         use=["Covered walkway", "Factory road"], avoid=["Garden path"],
         trap="Lunch in the next building and back, in a wheelchair: both ways through the factory, almost twice "
              "the walking distance."),
    dict(id="xmb-campus-05", building="xmb-tour-campus", kind="tour_best_order",
         stops=["proc-campus-dev-001:Lab 0.06", "proc-factory-dev-005:Kitchen 0.25", "proc-campus-dev-000:Library 1.28"],
         trap="Two errands in two other buildings, any order: the kitchen first is about 6 % shorter, because the "
              "factory lies between the labs and the library."),
    dict(id="xmb-campus-06", building="xmb-tour-campus", kind="tour_best_order",
         stops=["proc-factory-dev-005:Kitchen 0.25", "proc-campus-dev-000:Canteen 0.28", "proc-campus-dev-001:Lab 0.06"],
         leg_use={1: ["Garden path"]},
         trap="Starting in the factory in the middle, either order crosses the garden path for the second leg; "
              "which campus building to visit first is decided by the first leg alone (about 6 %)."),
    dict(id="xmb-campus-07", building="xmb-tour-campus", kind="tour", rules=SF, closed=["Covered walkway"],
         stops=["proc-campus-dev-000:Lecture hall 0.27", "proc-factory-dev-005:Kitchen 0.25",
                "proc-campus-dev-001:Lecture hall 0.21"],
         expect="no_route",
         trap="With the covered walkway closed, Hörsaalzentrum 024's only step-free entrance leads nowhere; the "
              "garden path is stepped. No step-free tour exists."),
    dict(id="xmb-campus-08", building="xmb-tour-campus", kind="tour", closed=["Garden path"],
         stops=["proc-campus-dev-001:Lab 0.06", "proc-campus-dev-000:Canteen 0.28", "proc-campus-dev-001:Lab 0.06"],
         use=["Covered walkway", "Factory road"], avoid=["Garden path"],
         trap="The garden path is closed: walking now takes the same long way through the factory as a "
              "wheelchair (xmb-campus-04)."),
    dict(id="xmb-campus-09", building="xmb-tour-campus", kind="route", expect="clarification",
         stops=["proc-campus-dev-000:Lecture hall 0.27", "Meeting room 1.22"],
         trap="There is a Meeting room 1.22 in Hörsaalzentrum 024 and another in Produktion West 29: ask which "
              "one instead of picking the nearer."),
]
# fmt: on


def space_id(b: Building, ref: str) -> str:
    """'Name' or 'building-id:Name' → the one space of that name (in that building)."""
    prefix, _, name = ref.rpartition(":")
    hits = [s.id for s in b.spaces.values() if s.name == name and s.id.startswith(prefix)]
    if len(hits) != 1:
        raise ValueError(f"{ref!r}: {len(hits)} spaces")
    return hits[0]


def element_id(b: Building, ref: str | tuple[str, str]) -> str:
    """A space or walkway by name, or the one opening between two named spaces."""
    if isinstance(ref, tuple):
        pair = {space_id(b, r) for r in ref}
        (hit,) = [o.id for o in b.openings.values() if set(o.space_ids) == pair]
        return hit
    walkways = [w.id for w in b.walkways.values() if w.name == ref]
    return walkways[0] if walkways else space_id(b, ref)


def constraints(b: Building, case: dict, extra_closed: tuple = ()) -> Constraints:
    c = RULES[case.get("rules", "walking")]
    closed = [element_id(b, r) for r in [*case.get("closed", []), *extra_closed]]
    return c.with_closed(*closed) if closed else c


def names_used(b: Building, route) -> set[str]:
    """Spaces, stairs, lifts and walkways a route uses, by name."""
    out = {b.spaces[s].name for s in route.space_path if s in b.spaces}
    for e in route.element_path:
        if e in b.connectors:
            out.add(b.connectors[e].name)
        elif e in b.walkways:
            out.add(b.walkways[e].name)
    return out


def leg_json(leg) -> dict:
    return {
        "from_space_id": leg.from_space_id,
        "to_space_id": leg.to_space_id,
        "optimal_length_m": leg.length_m,
        "space_path": leg.space_path,
        "element_path": leg.element_path,
    }


def shortest_valid(b: Building, start: str, goal: str, c: Constraints):
    """Shortest leg under Synera's rule: production bays only as start or end (RouteGraph alone doesn't
    enforce this, see ROUTE_SCORING.md), so every other bay is closed for the leg."""
    bays = {s.id for s in b.spaces.values() if s.room_type in PRODUCTION_BAYS} - {start, goal}
    return RouteGraph(b, c.with_closed(*bays) if bays else c).shortest(start, goal)


def solve(b: Building, case: dict, stops: list[str], c: Constraints):
    legs = [shortest_valid(b, x, y, c) for x, y in itertools.pairwise(stops)]
    if any(leg is None for leg in legs):
        return None
    return legs[0] if len(legs) == 1 else Tour(stops, round(sum(leg.length_m for leg in legs), 2), legs)


def routegraph_agrees(b: Building, stops: list[str], c: Constraints, result) -> bool:
    """Does plain RouteGraph (no bay rule) give the same space sequences? False marks a known gap."""
    graph = RouteGraph(b, c)
    plain = [graph.shortest(x, y) for x, y in itertools.pairwise(stops)]
    if result is None:
        return any(leg is None for leg in plain)
    return [leg and leg.space_path for leg in plain] == [leg.space_path for leg in legs_of(result)]


def legs_of(result) -> list:
    return result.legs if hasattr(result, "legs") else [result]


def question_text(b: Building, case: dict) -> str:
    def label(ref: str) -> str:
        """'Office A1.1 (First floor)'; an ambiguous name stays bare (the clarification cases)."""
        name = ref.rpartition(":")[2]
        try:
            sid = space_id(b, ref)
        except ValueError:
            return name
        return f"{name} ({b.floors[b.spaces[sid].floor_id].name})"

    names = [label(r) for r in case["stops"]]
    rules = RULE_TEXT[case.get("rules", "walking")]
    closed = [
        r if isinstance(r, str) else f"the door between {r[0]} and {r[1]}" for r in case.get("closed", [])
    ]
    closure = f" {', '.join(closed)} {'is' if len(closed) == 1 else 'are'} closed today." if closed else ""
    if case["kind"] == "tour_best_order":
        first, *rest = names
        return (f"I'm in {first} and need to stop at {' and '.join(rest)}, in whichever order is shorter. "
                f"Which order, and what is the route?{rules}{closure}")  # fmt: skip
    if len(names) > 2:
        return (f"Starting at {names[0]}, visit {', then '.join(names[1:-1])}, then {names[-1]} in this order. "
                f"What is the route?{rules}{closure}")  # fmt: skip
    return f"How do I get from {names[0]} to {names[1]}?{rules}{closure}"


def build_case(case: dict) -> dict:
    b = load_building(HERE / case["building"])
    expect = case.get("expect", "answer")
    line: dict = {
        "id": case["id"],
        "building_id": b.id,
        "building_path": f"data/multi_building/{case['building']}",
        "kind": case["kind"],
        "question": question_text(b, case),
    }
    extra = {"trap": case["trap"], "rules": case.get("rules", "walking"), "closed": case.get("closed", [])}
    if expect == "clarification":
        name = case["stops"][-1]
        options = [s.id for s in b.spaces.values() if s.name == name]
        assert len(options) > 1, case["id"]
        line["answer"] = {
            "type": "clarification",
            "options": options,
            "reason": f"{len(options)} spaces named {name}",
        }
        line["extra"] = extra
        return line

    stops = [space_id(b, r) for r in case["stops"]]
    c = constraints(b, case)
    line["inputs"] = {"stops": stops, "step_free": c.step_free, "min_width_m": c.min_width_m,
                      "closed": sorted(c.closed)}  # fmt: skip
    if case["kind"] == "tour_best_order":
        first, *rest = stops
        tours = {tuple(order): solve(b, case, [first, *order], c) for order in itertools.permutations(rest)}
        ranked = sorted(tours.items(), key=lambda kv: kv[1].length_m)
        (best_order, best), (_, runner_up) = ranked[0], ranked[1]
        assert runner_up.length_m > best.length_m * 1.01, (case["id"], best.length_m, runner_up.length_m)
        result = best
        stops = [first, *best_order]
        extra["alternative"] = {"order": [first, *ranked[1][0]], "length_m": runner_up.length_m}
    else:
        result = solve(b, case, stops, c)

    extra["routegraph_agrees"] = routegraph_agrees(b, stops, c, result)
    if expect == "no_route":
        assert result is None, f"{case['id']}: expected no route, got {result.length_m}"
        line["answer"] = {"type": "no_route", "reason": case["trap"]}
        line["extra"] = extra
        return line
    assert result is not None, f"{case['id']}: no route"

    legs = legs_of(result)
    used = set().union(*(names_used(b, leg) for leg in legs))
    for name in case.get("use", []):
        assert name in used, f"{case['id']}: doesn't use {name}: {sorted(used)}"
    for name in case.get("avoid", []):
        assert name not in used, f"{case['id']}: uses {name}"
    for i, names in case.get("leg_use", {}).items():
        for name in names:
            assert name in names_used(b, legs[i]), f"{case['id']} leg {i}: doesn't use {name}"
    for i, names in case.get("leg_avoid", {}).items():
        for name in names:
            assert name not in names_used(b, legs[i]), f"{case['id']} leg {i}: uses {name}"
    for leg in legs:
        assert not validate_route(b, leg.space_path, c), (case["id"], validate_route(b, leg.space_path, c))

    extra["must_use"] = case.get("use", [])
    extra["must_avoid"] = case.get("avoid", [])
    alt = case.get("alt")
    if isinstance(alt, dict):
        alt_result = solve(b, case, stops, constraints(b, case, tuple(alt["closed"])))
        assert alt_result is not None and alt_result.length_m > result.length_m * 1.01, (
            case["id"],
            alt_result,
        )
        extra["alternative"] = {"closed": alt["closed"], "length_m": alt_result.length_m,
                                "space_path": [b.label(s) for leg in legs_of(alt_result) for s in leg.space_path]}  # fmt: skip
    if alt == "walking" or case.get("alt_is") == "walking":
        walk = solve(b, case, stops, constraints(b, {**case, "rules": "walking"}))
        problems = sorted({p for leg in legs_of(walk) for p in validate_route(b, leg.space_path, c)})
        assert problems, f"{case['id']}: the walking route is valid under the rules too"
        extra["walking_route"] = {"length_m": walk.length_m, "invalid_because": problems}

    if len(stops) == 2:
        line["answer"] = {"type": "route", **leg_json(result)}
    else:
        line["answer"] = {"type": "tour", "stops": stops, "optimal_length_m": result.length_m,
                          "legs": [leg_json(leg) for leg in legs]}  # fmt: skip
    line["extra"] = extra
    return line


if __name__ == "__main__":
    by_building: dict[str, list[dict]] = {}
    for case in CASES:
        by_building.setdefault(case["building"], []).append(build_case(case))
    for building, lines in by_building.items():
        path = HERE / building / "questions.jsonl"
        path.write_text("".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines))
        print(f"{path.relative_to(HERE)}: {len(lines)} cases")
