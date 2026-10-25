"""Sunday benchmark plumbing (app.agents.building_guide.sunday), no model.

    uv run pytest tests/building/test_sunday.py -q

The challenge PDF's Sunday run: a new building folder plus questions.json in, answers.json out, one
command documented in BENCHMARK.md, every record holding the located item or room id, the route as
ordered node ids and its length. Whether the agent picks the right tools is the warm-up suite's job
(scoreboard row sunday-find-and-route); this checks that what the tools computed reaches answers.json
intact and scores: valid routes, best length within 1%, right ids and counts.

The answerer here is deterministic: it calls the agent's real tools with the golden question's ids, the
way the agent would, so every miss is in the runner, not in a model.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.agents.building_guide.sunday import run_benchmark
from app.agents.building_guide.tools import Deps, count_items, find, route, set_closed
from app.building.model import building_dir, load_building
from app.building.routing import Constraints, validate_route
from app.settings import ROOT

from .test_golden import DATA, SPLIT, STRICT, call, route_length_ok

KINDS = {"locate", "count", "route", "route_step_free", "route_with_closure", "tour"}


def _sunday_building() -> tuple[str, list[dict[str, Any]]]:
    """The first dev building whose warm-up set has every Sunday-style kind (chosen by data, not by name)."""
    for path in sorted((DATA / SPLIT).glob("*/questions_basic.jsonl")):
        qs = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if {q["kind"] for q in qs} >= KINDS:
            return path.parent.name, qs
    pytest.skip("not applicable: no building with every Sunday-style kind", allow_module_level=True)


BUILDING_ID, GOLDEN = _sunday_building()
BY_ID = {q["id"]: q for q in GOLDEN}


def oracle(building: Path, question: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """What a perfect agent would call for this question, using the real tools."""
    q, deps = BY_ID[question["id"]], Deps.load(building)
    a, b, calls = q["answer"], deps.building, []

    def use(tool, *args, **kwargs) -> dict:
        result = tool(SimpleNamespace(deps=deps, run_id="1"), *args, **kwargs)
        calls.append({"tool": tool.__name__, "args": {"args": list(args)} | kwargs, "result": result})
        return json.loads(result)

    kind = q["kind"]
    if kind == "locate":
        use(find, b.assets[a["asset_id"]].name)
        return f"It's in {a['space_name']}.\nAnswer: {a['space_name']}", calls
    if kind == "count":
        got = use(count_items, a["asset_type"], str(a["floor_level"]))
        return f"Answer: {got.get('count')}", calls
    if kind == "route_with_closure":
        use(set_closed, a["closed_element_id"])
    if kind == "tour":
        stops = a["stops"]
        got = use(route, stops[0], stops[-1], via=stops[1:-1])
    else:
        got = use(route, a["from_space_id"], a["to_space_id"], step_free=kind == "route_step_free")
    return f"Answer: {got.get('total_length_m', 'no route')}", calls


@pytest.fixture(scope="module")
def answers(tmp_path_factory) -> dict[str, dict[str, Any]]:
    tmp = tmp_path_factory.mktemp("sunday")
    building = tmp / "unseen-building"  # outside the kit: the Sunday building arrives in its own folder
    shutil.copytree(building_dir(BUILDING_ID), building)
    questions = [
        {"id": q["id"], "text": q["question"]}
        | ({"constraints": {"step_free": True}} if "step_free" in q["kind"] else {})
        for q in GOLDEN
    ]
    (tmp / "questions.json").write_text(json.dumps(questions), encoding="utf-8")
    call(run_benchmark, building, tmp / "questions.json", tmp / "answers.json", oracle)
    records = json.loads((tmp / "answers.json").read_text(encoding="utf-8"))
    records = records["answers"] if isinstance(records, dict) else records
    assert [r["id"] for r in records] == [q["id"] for q in GOLDEN]  # one per question, in order
    return {r["id"]: r for r in records}


def test_sunday_command_is_documented():
    if "building_guide.sunday" not in (ROOT / "BENCHMARK.md").read_text(encoding="utf-8"):
        message = "not implemented yet: document the Sunday command in BENCHMARK.md"
        if STRICT:
            pytest.fail(message)
        pytest.skip(message)


@pytest.mark.parametrize("q", [pytest.param(q, id=q["id"]) for q in GOLDEN if q["kind"] == "locate"])
def test_sunday_locate_gives_item_and_room_ids(q, answers):
    r = answers[q["id"]]
    assert (r["item_id"], r["space_id"]) == (q["answer"]["asset_id"], q["answer"]["space_id"]), r


@pytest.mark.parametrize("q", [pytest.param(q, id=q["id"]) for q in GOLDEN if q["kind"] == "count"])
def test_sunday_count(q, answers):
    assert answers[q["id"]]["count"] == q["answer"]["count"], answers[q["id"]]


@pytest.mark.parametrize(
    "q", [pytest.param(q, id=q["id"]) for q in GOLDEN if q["kind"] in KINDS - {"locate", "count"}]
)
def test_sunday_route_is_valid_and_best(q, answers):
    r, a = answers[q["id"]], q["answer"]
    if a.get("reachable") is False:
        assert r["reachable"] is False, r
        return
    assert r["reachable"] is True and route_length_ok(r["length_m"], a["optimal_length_m"]), r
    closed = frozenset({a["closed_element_id"]}) if "closed_element_id" in a else frozenset()
    rules = Constraints(step_free=q["kind"] == "route_step_free", closed=closed)
    assert validate_route(load_building(BUILDING_ID), r["route"], rules) == [], r
    start, end = (
        (a["stops"][0], a["stops"][-1]) if q["kind"] == "tour" else (a["from_space_id"], a["to_space_id"])
    )
    assert (r["route"][0], r["route"][-1]) == (start, end), r


@pytest.mark.parametrize(
    "q", [pytest.param(q, id=q["id"]) for q in GOLDEN if q["kind"] == "route_with_closure"]
)
@pytest.mark.parametrize("key", ["closed", "closed_element_id"])
def test_sunday_closure_constraint_is_applied(q, key, tmp_path):
    """A closure in questions.json constraints is recorded before the agent starts, so the route tool
    avoids it even if the agent never calls set_closed."""
    from app.agents.building_guide.sunday import deps_for

    a = q["answer"]
    name = load_building(BUILDING_ID).connectors.get(a["closed_element_id"])
    value = name.name if name and key == "closed" else a["closed_element_id"]
    deps = deps_for(building_dir(BUILDING_ID), {"id": q["id"], "constraints": {key: value}})
    assert a["closed_element_id"] in deps.closed
    got = json.loads(route(SimpleNamespace(deps=deps, run_id="1"), a["from_space_id"], a["to_space_id"]))
    assert route_length_ok(got["total_length_m"], a["optimal_length_m"]), got


def _questions() -> list[dict[str, Any]]:
    return [
        {"id": q["id"], "text": q["question"]}
        | ({"constraints": {"step_free": True}} if "step_free" in q["kind"] else {})
        for q in GOLDEN
    ]


def test_sunday_check_passes_good_answers_and_flags_bad_ones(answers):
    from app.agents.building_guide.sunday import check_records

    records = list(answers.values())
    building = building_dir(BUILDING_ID)
    assert call(check_records, building, _questions(), records) == {}
    route = next(r for r in records if r.get("route") and not r.get("stops"))
    broken = route | {"route": [route["route"][0], route["route"][-1]]}  # skips every door in between
    silent = records[0] | {"answer": None}
    found = check_records(building, _questions(), [broken, silent])
    assert set(found) == {broken["id"], silent["id"]}, found


def test_sunday_csv_has_every_full_reply(answers, tmp_path):
    import csv

    from app.agents.building_guide.sunday import write_csv

    records = list(answers.values())
    call(write_csv, records, tmp_path / "answers.csv")
    with (tmp_path / "answers.csv").open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert [(r["question_id"], r["answer"]) for r in rows] == [(r["id"], r["reply"]) for r in records]
