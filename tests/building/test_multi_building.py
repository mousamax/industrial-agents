"""Multi-building routing: yard vs bridge, courtyard vs round the U, tours across a Level 3 site.

Cases live in data/multi_building/<building>/questions.jsonl (hand-designed, see its README): each states
the stops, the rules (walking, step-free, 1.1 m trolley, forklift), closures, the best answer under Synera's
rule (docs/synera/ROUTE_SCORING.md) and what the best route must use or avoid. Here RouteGraph must earn
full marks on each: every leg valid under the rules and within 1 % of the best length.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from app.building.model import load_building
from app.building.routing import FORKLIFT, WALKING, Constraints, RouteGraph, validate_route

DATA = Path(__file__).parents[2] / "data" / "multi_building"
CASES = [json.loads(line) for path in sorted(DATA.glob("*/questions.jsonl")) for line in path.open()]
RULES = {
    "walking": WALKING,
    "step_free": Constraints(step_free=True),
    "wide_1.1": Constraints(min_width_m=1.1),
    "forklift": FORKLIFT,
}
KNOWN_GAPS: dict[str, str] = {}


def _param(case: dict, known_gaps: bool = True):
    gap = known_gaps and case["id"] in KNOWN_GAPS
    mark = [pytest.mark.xfail(reason=KNOWN_GAPS[case["id"]], strict=True)] if gap else []
    return pytest.param(case, id=case["id"], marks=mark)


def _setup(case: dict):
    b = load_building(DATA / Path(case["building_path"]).name)
    inputs = case["inputs"]
    c = RULES[case["extra"]["rules"]]
    stops = case["answer"].get(
        "stops", inputs["stops"]
    )  # tour_best_order: the answer's order, not the question's
    return b, (c.with_closed(*inputs["closed"]) if inputs["closed"] else c), stops


def _expected_legs(case: dict) -> list[dict]:
    answer = case["answer"]
    return answer["legs"] if answer["type"] == "tour" else [answer]


def test_multi_building_cases_exist():
    assert len(CASES) >= 30
    assert {c["building_id"] for c in CASES} == {"xmb-bridge-works", "xmb-u-wing", "xmb-tour-campus"}


@pytest.mark.parametrize("case", [_param(c) for c in CASES if c["answer"]["type"] in ("route", "tour")])
def test_multi_building_route_full_marks(case):
    """Every leg RouteGraph picks is valid under the rules and within 1 % of the best (Synera's full marks)."""
    b, c, stops = _setup(case)
    graph = RouteGraph(b, c)
    for (start, goal), want in zip(itertools.pairwise(stops), _expected_legs(case), strict=True):
        leg = graph.shortest(start, goal)
        assert leg is not None, f"{b.label(start)} -> {b.label(goal)}: no route found"
        assert validate_route(b, leg.space_path, c) == []
        assert leg.length_m <= want["optimal_length_m"] * 1.01


@pytest.mark.parametrize("case", [_param(c) for c in CASES if c["answer"]["type"] == "no_route"])
def test_multi_building_no_route(case):
    b, c, stops = _setup(case)
    graph = RouteGraph(b, c)
    assert any(graph.shortest(start, goal) is None for start, goal in itertools.pairwise(stops))


@pytest.mark.parametrize(
    "case", [_param(c, False) for c in CASES if c["answer"]["type"] in ("route", "tour")]
)
def test_multi_building_answers_are_valid_and_keep_their_intent(case):
    """The frozen answers themselves: valid under the rules, and using / avoiding what the case is about."""
    b, c, _ = _setup(case)
    used: set[str] = set()
    for leg in _expected_legs(case):
        assert validate_route(b, leg["space_path"], c) == []
        used |= {b.spaces[s].name for s in leg["space_path"] if s in b.spaces}
        used |= {(b.connectors.get(e) or b.walkways.get(e)).name for e in leg["element_path"]
                 if e in b.connectors or e in b.walkways}  # fmt: skip
    assert set(case["extra"]["must_use"]) <= used
    assert not set(case["extra"]["must_avoid"]) & used


@pytest.mark.parametrize("case", [_param(c) for c in CASES if c["kind"] == "tour_best_order"])
def test_multi_building_best_order_is_strictly_best(case):
    b, c, stops = _setup(case)
    first, *rest = stops
    assert sorted(stops) == sorted(case["inputs"]["stops"]) and first == case["inputs"]["stops"][0]
    graph = RouteGraph(b, c)
    lengths = {tuple(o): graph.tour([first, *o]).length_m for o in itertools.permutations(rest)}
    best = min(lengths, key=lengths.get)
    assert list(best) == rest and all(v > lengths[best] * 1.01 for k, v in lengths.items() if k != best)


@pytest.mark.parametrize("case", [_param(c) for c in CASES if c["answer"]["type"] == "clarification"])
def test_multi_building_ambiguous_names_resolve_to_several(case):
    b = load_building(DATA / Path(case["building_path"]).name)
    name = case["question"].split(" to ")[-1].rstrip("?")
    assert sorted(s.id for s in b.resolve(name)) == sorted(case["answer"]["options"])
