"""Golden tests: every domain interface against Synera's own answers. No LLM, no API calls, seconds.

This is each owner's scoreboard. An interface that still raises NotImplementedError is skipped, not failed,
so the suite is green from the start and turns into real checks as each part lands. Set GOLDEN_STRICT=1 to
fail on unimplemented interfaces instead, so the scoreboard shows everything still missing.

    uv run pytest tests/building -q -rs                       # dev split, 6 questions per kind
    GOLDEN_SAMPLE=all uv run pytest tests/building -q         # every dev question
    GOLDEN_SPLIT=train GOLDEN_SAMPLE=all uv run pytest tests/building -q -x
    uv run pytest tests/building -k "errand or wide_delivery"  # just your kinds
    GOLDEN_STRICT=1 uv run pytest tests/building -k "route or graph"  # stubs fail, not skip

Inputs come from the questions' `inputs` (benchmark) or answer ids (warm-up), so these tests check the
computation only. Whether the agent turns the question text into those inputs is checked separately
(`ia eval building_guide`).
"""

from __future__ import annotations

import json
import os
import random
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.building import geometry, queries
from app.building.model import load_building
from app.building.routing import WALKING, Constraints, RouteGraph
from app.settings import get_settings

DATA = get_settings().synera_kit_dir / "data"
SPLIT = os.environ.get("GOLDEN_SPLIT", "dev")
SAMPLE = os.environ.get("GOLDEN_SAMPLE", "6")
STRICT = os.environ.get("GOLDEN_STRICT", "") not in ("", "0")

if not (DATA / SPLIT).exists():
    pytest.skip("synera-kit data missing: git submodule update --init", allow_module_level=True)


def _questions(filename: str) -> dict[str, list[dict[str, Any]]]:
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for path in sorted((DATA / SPLIT).glob(f"*/{filename}")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                q = json.loads(line)
                by_kind.setdefault(q["kind"], []).append(q)
    if SAMPLE != "all":
        rng = random.Random(7)
        by_kind = {k: rng.sample(v, min(int(SAMPLE), len(v))) for k, v in by_kind.items()}
    return by_kind


BENCHMARK = _questions("questions.jsonl")
WARMUP = _questions("questions_basic.jsonl")


def cases(source: dict[str, list[dict[str, Any]]], kind: str) -> list[Any]:
    return [pytest.param(q, id=q["id"]) for q in source.get(kind, [])]


def call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except NotImplementedError as e:
        message = f"not implemented yet: {e or fn.__qualname__}"
        if STRICT:
            pytest.fail(message)
        pytest.skip(message)


def close_to(value: float | None, answer: dict[str, Any]) -> bool:
    if value is None:
        return False
    expected = float(answer["value"])
    tolerance = max(abs(expected) * float(answer["relative_tolerance"]), float(answer["absolute_tolerance"]))
    return abs(value - expected) <= tolerance


def route_length_ok(length: float, expected: float) -> bool:
    """Warm-up routes must match within 1% (the kit's own check), at least 0.05 m."""
    return abs(length - expected) <= max(0.01 * expected, 0.05)


# ---- Owner B: geometry ---------------------------------------------------------------------------------


@pytest.mark.parametrize("q", cases(BENCHMARK, "largest_room"))
def test_geometry_largest_room(q):
    b, i, a = load_building(q["building_id"]), q["inputs"], q["answer"]
    ranked = call(geometry.rooms_by_area, b, i["floor_id"], i["room_type"])
    assert ranked, "no rooms of that type"
    space, area = ranked[0]
    assert space.id == a["space_id"]
    assert close_to(area, a["area"]), f"area {area:.2f} vs {a['area']['value']}"


@pytest.mark.parametrize("q", cases(BENCHMARK, "floor_area_total"))
def test_geometry_floor_area_total(q):
    b, i = load_building(q["building_id"]), q["inputs"]
    total = call(geometry.total_area, b, i["floor_id"], i["room_type"])
    assert close_to(total, q["answer"]), f"{total} vs {q['answer']['value']}"


@pytest.mark.parametrize("q", cases(BENCHMARK, "shared_walls_count"))
def test_geometry_shared_walls_count(q):
    b = load_building(q["building_id"])
    rooms = call(geometry.shared_wall_rooms, b, q["inputs"]["start"])
    assert len(rooms) == q["answer"]["value"], [b.label(r) for r in rooms]


@pytest.mark.parametrize("q", cases(BENCHMARK, "room_stacked"))
def test_geometry_room_stacked(q):
    b, i = load_building(q["building_id"]), q["inputs"]
    got = call(geometry.stacked, b, i["start"], i["direction"])
    assert got == q["answer"]["space_id"], f"{got and b.label(got)} vs {b.label(q['answer']['space_id'])}"


@pytest.mark.parametrize("q", cases(BENCHMARK, "straight_line_3d"))
def test_geometry_straight_line_3d(q):
    b, i = load_building(q["building_id"]), q["inputs"]
    d = call(geometry.straight_line_3d, b, i["first"], i["second"])
    assert close_to(d, q["answer"]), f"{d} vs {q['answer']['value']}"


# ---- Owner A: routing on warm-up questions --------------------------------------------------------------


def _assert_route(b, route, a):
    if a.get("reachable") is False:
        assert route is None, f"expected unreachable, got {route.length_m} m"
        return
    assert route is not None, "expected a route"
    assert route_length_ok(route.length_m, a["optimal_length_m"]), (
        f"{route.length_m} vs {a['optimal_length_m']}"
    )
    assert route.space_path[0] == a["from_space_id"] and route.space_path[-1] == a["to_space_id"]


@pytest.mark.parametrize("q", cases(WARMUP, "route"))
def test_route(q):
    b, a = load_building(q["building_id"]), q["answer"]
    graph = call(RouteGraph, b, WALKING)
    _assert_route(b, call(graph.shortest, a["from_space_id"], a["to_space_id"]), a)


@pytest.mark.parametrize("q", cases(WARMUP, "route_step_free"))
def test_route_step_free(q):
    b, a = load_building(q["building_id"]), q["answer"]
    graph = call(RouteGraph, b, Constraints(step_free=True))
    _assert_route(b, call(graph.shortest, a["from_space_id"], a["to_space_id"]), a)


@pytest.mark.parametrize("q", cases(WARMUP, "route_with_closure"))
def test_route_with_closure(q):
    b, a = load_building(q["building_id"]), q["answer"]
    graph = call(RouteGraph, b, WALKING.with_closed(a["closed_element_id"]))
    _assert_route(b, call(graph.shortest, a["from_space_id"], a["to_space_id"]), a)


@pytest.mark.parametrize("q", cases(WARMUP, "tour"))
def test_route_tour(q):
    b, a = load_building(q["building_id"]), q["answer"]
    tour = call(call(RouteGraph, b, WALKING).tour, a["stops"])
    assert tour is not None
    assert route_length_ok(tour.length_m, a["optimal_length_m"]), (
        f"{tour.length_m} vs {a['optimal_length_m']}"
    )


# ---- Owner A: graph questions ---------------------------------------------------------------------------


@pytest.mark.parametrize("q", cases(BENCHMARK, "errand"))
def test_graph_errand(q):
    b, i, a = load_building(q["building_id"]), q["inputs"], q["answer"]
    result = call(queries.errand, b, i["start"], i["asset_type"], i["floor_id"], i["room_type"])
    assert result is not None
    assert result.pickup_space_id == a["via"], (
        f"pickup {b.label(result.pickup_space_id)} vs {b.label(a['via'])}"
    )
    assert result.target_space_id == a["target"]
    assert close_to(result.length_m, a), f"{result.length_m} vs {a['value']}"


@pytest.mark.parametrize("q", cases(BENCHMARK, "wide_delivery_count"))
def test_graph_wide_delivery_count(q):
    b, i = load_building(q["building_id"]), q["inputs"]
    rooms = call(queries.deliverable_rooms, b, i["start"], i["floor_id"], i["min_width"])
    assert len(rooms) == q["answer"]["value"]


@pytest.mark.parametrize("q", cases(BENCHMARK, "step_free_reachable_count"))
def test_graph_step_free_reachable_count(q):
    b, i = load_building(q["building_id"]), q["inputs"]
    rooms = call(queries.step_free_reachable_rooms, b, i["start"], i["floor_id"], [i["closed"]])
    assert len(rooms) == q["answer"]["value"]


@pytest.mark.parametrize("q", cases(BENCHMARK, "what_if_door"))
def test_graph_what_if_door(q):
    b, i = load_building(q["building_id"]), q["inputs"]
    rooms = call(queries.what_if_door, b, i["first"], i["second"], i["floor_id"])
    assert len(rooms) == q["answer"]["value"]


# ---- shared model (implemented): sanity on every sampled building ---------------------------------------


@pytest.mark.parametrize("building_id", sorted({q["building_id"] for qs in BENCHMARK.values() for q in qs}))
def test_model_loads_and_resolves(building_id):
    b = load_building(building_id)
    assert b.spaces and b.floors
    for s in list(b.spaces.values())[:25]:
        assert s in b.resolve(b.label(s.id)), f"label {b.label(s.id)!r} doesn't resolve back"


def test_kit_data_is_present():
    assert any(Path(DATA, SPLIT).iterdir())
