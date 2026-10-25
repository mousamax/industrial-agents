"""queries.plan_visits: the free-order trip (generalised TSP) behind the building_guide plan_visits tool.

Checked against brute force over every order and every choice of room, on the extra tricky case it was
built for (xt-beyond_tools-02: 133.18 m, greedy 193.09 m), and on the time budget of one tool call.
"""

from __future__ import annotations

import itertools
import math
import random
import time

import pytest

from app.building.model import load_building
from app.building.queries import VISIT_EXACT_MAX_GROUPS, plan_visits
from app.building.routing import Constraints, RouteGraph, validate_route
from app.settings import ROOT

DATA = ROOT / "synera-kit" / "data" / "dev"
CAMPUS = "proc-campus-dev-000"
OFFICE_307 = "proc-campus-dev-000-F3-S007"


@pytest.fixture(scope="module")
def campus():
    return load_building(DATA / CAMPUS)


def holders(b, item_type: str) -> list[str]:
    return sorted({a.space_id for a in b.assets_of_type(item_type)})


def brute_force(b, graph, start, groups, end):
    """Every order of every choice of one room per group (duplicates collapse): the true optimum."""
    rows: dict[str, dict[str, float]] = {}

    def d(a, z):
        if a not in rows:
            rows[a] = graph.distances_from([a])
        return rows[a].get(z, math.inf)

    best = math.inf
    for choice in itertools.product(*groups):
        stops = list(dict.fromkeys(s for s in choice if s not in (start, end)))
        for order in itertools.permutations(stops):
            path = [start, *order] + ([end] if end is not None else [])
            best = min(best, sum(d(a, z) for a, z in itertools.pairwise(path)))
    return best


def test_reproduces_free_order_trip_with_a_room_covering_two_groups(campus):
    groups = [holders(campus, t) for t in ("coffee_machine", "3d_printer", "laser_cutter")]
    plan = plan_visits(campus, OFFICE_307, groups, return_to_start=True)
    assert plan.total_m == pytest.approx(133.18, abs=0.01)
    assert plan.exact
    assert sorted(campus.label(s).split(" (")[0] for s in plan.stops) == ["Kitchen 0.03", "Makerspace 0.08"]
    makerspace = plan.stops.index("proc-campus-dev-000-F0-S008")
    assert plan.covers[makerspace] == [1, 2]  # one stop settles the 3D printer and the laser cutter
    assert plan.greedy_total_m == pytest.approx(193.09, abs=0.01)  # the trap: nearest-first
    assert sum(plan.legs_m) == pytest.approx(plan.total_m, abs=0.02)


@pytest.mark.parametrize("seed", range(12))
def test_matches_brute_force_on_random_small_trips(campus, seed):
    rng = random.Random(seed)
    graph = RouteGraph(campus)
    rooms = [s.id for s in campus.spaces.values() if s.type == "room"]
    start = rng.choice(rooms)
    groups = [rng.sample(rooms, rng.randint(1, 3)) for _ in range(rng.randint(2, 4))]
    if seed % 3 == 0:  # one room in two groups
        groups[1].append(groups[0][0])
    end = {0: None, 1: start, 2: rng.choice(rooms)}[seed % 3]
    plan = plan_visits(campus, start, groups, end=end, graph=graph)
    assert plan.exact
    assert plan.total_m == pytest.approx(brute_force(campus, graph, start, groups, end), abs=0.02)
    settled = set(plan.start_covers) | {g for c in plan.covers for g in c}
    assert settled == set(range(len(groups)))


def test_round_trip_comes_back_and_open_trip_is_no_longer(campus):
    groups = [holders(campus, t) for t in ("coffee_machine", "3d_printer")]
    round_trip = plan_visits(campus, OFFICE_307, groups, return_to_start=True)
    one_way = plan_visits(campus, OFFICE_307, groups)
    assert round_trip.end == OFFICE_307 and len(round_trip.legs_m) == len(round_trip.stops) + 1
    assert one_way.end is None and len(one_way.legs_m) == len(one_way.stops)
    assert one_way.total_m <= round_trip.total_m


def test_step_free_trip_uses_only_step_free_legs(campus):
    step_free = Constraints(step_free=True)
    graph = RouteGraph(campus, step_free)
    groups = [holders(campus, t) for t in ("coffee_machine", "3d_printer", "laser_cutter")]
    plan = plan_visits(campus, OFFICE_307, groups, return_to_start=True, graph=graph)
    walking = plan_visits(campus, OFFICE_307, groups, return_to_start=True)
    assert plan.total_m >= walking.total_m
    tour = graph.tour([OFFICE_307, *plan.stops, OFFICE_307])
    assert tour.length_m == pytest.approx(plan.total_m, abs=0.02)
    for leg in tour.legs:
        assert validate_route(campus, leg.space_path, step_free) == []
        assert not any(
            e in campus.connectors and campus.connectors[e].kind != "lift" for e in leg.element_path
        )


def test_local_search_fallback_settles_everything_and_beats_greedy(campus):
    groups = [holders(campus, t) for t in ("coffee_machine", "3d_printer", "laser_cutter", "office_chair")]
    exact = plan_visits(campus, OFFICE_307, groups, return_to_start=True)
    local = plan_visits(campus, OFFICE_307, groups, return_to_start=True, exact_max_groups=0)
    assert not local.exact
    assert exact.total_m <= local.total_m <= local.greedy_total_m
    assert set(local.start_covers) | {g for c in local.covers for g in c} == set(range(len(groups)))


def test_unreachable_group_is_reported(campus):
    plan = plan_visits(campus, OFFICE_307, [holders(campus, "coffee_machine"), []])
    assert plan.unreachable_groups == [1] and plan.stops == []


def test_one_tool_call_fits_the_budget_on_the_largest_dev_building():
    """Matrix + search under 1 s on the 1,623-space OSM building with many holders per group (pruned)."""
    b = load_building(DATA / "osm-way27917482")
    graph = RouteGraph(b)
    by_type: dict[str, set[str]] = {}
    for a in b.assets.values():
        by_type.setdefault(a.type, set()).add(a.space_id)
    groups = [
        sorted(by_type[t]) for t in sorted(by_type, key=lambda t: -len(by_type[t]))[:VISIT_EXACT_MAX_GROUPS]
    ]
    start = next(s.id for s in b.spaces.values() if s.type == "room")
    plan_visits(b, start, groups[:2], graph=graph)  # warm the walk cache
    began = time.perf_counter()
    plan = plan_visits(b, start, groups, return_to_start=True, graph=graph)
    assert time.perf_counter() - began < 1.0
    assert plan.pruned_groups and not plan.exact  # flagged: not every holder was compared
    assert plan.total_m <= plan.greedy_total_m
