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
    doc = ROOT / "SUBMISSION" / "BENCHMARK.md"
    if not doc.exists() or "run_benchmark" not in doc.read_text(encoding="utf-8"):
        message = "not implemented yet: document the Sunday command in SUBMISSION/BENCHMARK.md"
        if STRICT:
            pytest.fail(message)
        pytest.skip(message)


def test_sunday_submission_folder_holds_only_the_two_graded_files():
    """Organizers: a folder named exactly SUBMISSION with exactly BENCHMARK.md and answers.json in it.
    os.listdir gives the real case even on macOS's case-insensitive disk."""
    import os

    assert "SUBMISSION" in os.listdir(ROOT), sorted(n for n in os.listdir(ROOT) if n.lower() == "submission")
    names = {n for n in os.listdir(ROOT / "SUBMISSION") if n != ".DS_Store"}
    assert "BENCHMARK.md" in names and names <= {"BENCHMARK.md", "answers.json"}, names


def test_run_benchmark_entry_point_is_the_sunday_command():
    import tomllib

    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    assert scripts["run_benchmark"] == "app.agents.building_guide.sunday:cli"


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


def test_sunday_route_from_walk_directions_still_fills_the_record():
    """Safety net: a route answered with walk_directions (chats only) still gives route and length."""
    from app.agents.building_guide.sunday import to_record
    from app.agents.building_guide.tools import walk_directions

    q = next(q for q in GOLDEN if q["kind"] == "route" and q["answer"].get("reachable") is not False)
    a = q["answer"]
    deps = Deps.load(building_dir(BUILDING_ID), detail="walk")
    result = walk_directions(SimpleNamespace(deps=deps, run_id="1"), a["from_space_id"], a["to_space_id"])
    r = to_record({"id": q["id"]}, "Answer: 1", [{"tool": "walk_directions", "args": {}, "result": result}])
    assert r["reachable"] and route_length_ok(r["length_m"], a["optimal_length_m"]), r
    assert (r["route"][0], r["route"][-1]) == (a["from_space_id"], a["to_space_id"]), r


def test_sunday_tour_from_plan_visits_fills_the_record():
    """A free-order tour answered with plan_visits (what the agent calls for "in any order") still gives
    the joined route, its stops and length, and the route is valid leg by leg."""
    from app.agents.building_guide.sunday import _split, to_record
    from app.agents.building_guide.tools import plan_visits

    q = next(q for q in GOLDEN if q["kind"] == "tour" and q["answer"].get("reachable") is not False)
    stops = q["answer"]["stops"]
    deps = Deps.load(building_dir(BUILDING_ID))
    result = plan_visits(SimpleNamespace(deps=deps, run_id="1"), stops[0], rooms=stops[1:-1], end=stops[-1])
    r = to_record({"id": q["id"]}, "Answer: 1", [{"tool": "plan_visits", "args": {}, "result": result}])
    assert r["reachable"] is True and r["length_m"] == json.loads(result)["total_length_m"], r
    assert (r["route"][0], r["route"][-1]) == (stops[0], stops[-1]) and r["stops"][0] == stops[0], r
    b = load_building(BUILDING_ID)
    assert all(validate_route(b, leg, Constraints()) == [] for leg in _split(r["route"], r["stops"])), r


def test_sunday_edit_then_route_records_the_edit_and_checks_against_it():
    """ "Add a door, then how far is it?": the record lists the edit (so a checker can replay it), and
    --check validates the route on the edited building, not the original one."""
    from app.agents.building_guide.sunday import check_records, deps_for, edits_of, to_record
    from app.agents.building_guide.tools import add_door

    where, q = building_dir("proc-campus-dev-000"), {"id": "edit-1"}
    deps = deps_for(where, q)
    ctx = SimpleNamespace(deps=deps, run_id="1")
    calls = [{"tool": "add_door", "args": {}, "result": add_door(ctx, "Office 0.02", "Kitchen 0.03")}]
    calls.append({"tool": "route", "args": {}, "result": route(ctx, "Office 0.02", "Kitchen 0.03")})
    r = to_record(q, "Answer: 4.17", calls, edits_of(deps))
    assert r["length_m"] == pytest.approx(4.17, abs=0.01), r
    assert [e["edit"] for e in r["edits"]] == ["AddDoor"], r
    assert check_records(where, [q], [r]) == {}
    assert check_records(where, [q], [r | {"edits": None}]), "without the edit the new door is unknown"


def test_sunday_records_removed_doors_and_new_entrances_and_replays_them_with_the_same_ids():
    """A width change (remove + re-add) and a new entrance survive edits_of -> replay_edits: same door ids,
    so a closure recorded on a new door still closes that door on the replayed building."""
    from app.agents.building_guide.sunday import deps_for, edits_of, replay_edits
    from app.agents.building_guide.tools import add_door, remove_door, set_closed, undo_last_change

    where, q = building_dir("proc-campus-dev-000"), {"id": "edit-2"}
    deps = deps_for(where, q)
    ctx = SimpleNamespace(deps=deps, run_id="1")
    add_door(ctx, "Office 0.02", "Kitchen 0.03")
    add_door(ctx, "Seminar room 0.17", "outside")
    undo_last_change(ctx)
    add_door(ctx, "Seminar room 0.17", "outside")  # gets a new number: the undone one is never reused
    again = json.loads(remove_door(ctx, between=["Office 0.02", "Corridor 0.01"]))["re_add_at"]
    add_door(ctx, again["first"], again["second"], width_m=1.5, position=again["position"])
    entrance = next(o.id for o in deps.building.openings.values() if o.kind == "entrance" and o.synthetic)
    set_closed(ctx, entrance)
    edits = json.loads(json.dumps(edits_of(deps)))
    assert [e["edit"] for e in edits] == ["AddDoor", "AddDoor", "RemoveDoor", "AddDoor", "Close"]

    fresh = deps_for(where, q)
    replay_edits(fresh, edits)
    assert fresh.state.export() == deps.state.export()
    assert fresh.closed == deps.closed == {entrance}


def test_sunday_find_question_drops_the_courtesy_route_from_outside():
    """ "Where is X?" with no start: the agent also routes from the nearest entrance to say how to get
    there. That route was never asked and starts at "outside", so answers.json leaves route and length
    empty (the located ids stay). The same route on a question about getting there is kept."""
    from app.agents.building_guide.sunday import to_record

    q = next(q for q in GOLDEN if q["kind"] == "locate")
    a, deps = q["answer"], Deps.load(building_dir(BUILDING_ID))
    c = SimpleNamespace(deps=deps, run_id="1")
    name = deps.building.assets[a["asset_id"]].name
    calls = [
        {"tool": "find", "args": {"query": name}, "result": find(c, name)},
        {"tool": "route", "args": {}, "result": route(c, "outside", a["space_id"])},
    ]
    found = to_record({"id": q["id"], "text": q["question"]}, "Answer: x", calls)
    assert (found["item_id"], found["space_id"]) == (a["asset_id"], a["space_id"]), found
    assert found["route"] is found["length_m"] is found["reachable"] is found["elements"] is None, found

    asked = to_record({"id": q["id"], "text": f"How do I get to the {name}?"}, "Answer: x", calls)
    assert asked["route"][0] == "outside" and asked["route"][-1] == a["space_id"], asked
    assert asked["length_m"] is not None, asked
