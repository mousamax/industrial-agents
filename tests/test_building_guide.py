"""building_guide behaviour that isn't a golden answer: search, scopes, arithmetic, two-turn edits, and
never crashing on a reply that fails the answer check. No model calls."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic_ai import ModelRetry
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.agents.building_guide.agent import make_agent
from app.agents.building_guide.tools import (
    Deps,
    add_door,
    calculate,
    find,
    list_rooms,
    plan_visits,
    route,
    undo_last_change,
)
from app.settings import ROOT

FACTORY, CAMPUS = "proc-factory-dev-003", "proc-campus-dev-000"


def ctx(deps: Deps, run_id: str = "turn-1") -> SimpleNamespace:
    return SimpleNamespace(deps=deps, run_id=run_id)


def call(tool, c, *args, **kwargs) -> dict:
    return json.loads(tool(c, *args, **kwargs))


def test_find_matches_words_not_substrings():
    got = call(find, ctx(Deps.load(FACTORY)), "press")
    assert set(got["items_by_type"]) == {"drill_press"}  # not the air compressor


def test_find_says_plainly_when_nothing_matches():
    got = call(find, ctx(Deps.load(FACTORY)), "swimming pool")
    assert got["found"] == 0


def test_list_rooms_scope_includes_halls_when_asked():
    c = ctx(Deps.load(FACTORY))
    rooms = call(list_rooms, c, "0")["total_area_m2"]
    with_halls = call(list_rooms, c, "0", scope="rooms_and_halls")["total_area_m2"]
    assert with_halls > rooms * 2  # factory halls dominate the ground floor


def test_calculate_is_exact_and_safe():
    assert call(calculate, ctx(Deps.load(FACTORY)), "41.37 - 37.57")["result"] == pytest.approx(3.8)
    with pytest.raises(ModelRetry):
        calculate(ctx(Deps.load(FACTORY)), "__import__('os').system('ls')")


def test_edits_need_a_yes_in_a_later_turn():
    deps = Deps.load(CAMPUS)
    pair = ("Seminar room 2.06", "Kitchen 2.07")
    proposed = call(add_door, ctx(deps, "turn-1"), *pair)
    assert proposed["status"].startswith("proposed") and not deps.state.edits
    with pytest.raises(ModelRetry):  # same turn: the person hasn't answered
        add_door(ctx(deps, "turn-1"), *pair, confirmed=True)
    done = call(add_door, ctx(deps, "turn-2"), *pair, confirmed=True)
    assert done["status"] == "done" and len(deps.state.edits) == 1
    walk = call(route, ctx(deps, "turn-3"), "Seminar room 2.06", "Kitchen 2.07")
    assert len(walk["legs"][0]["space_path"]) == 2  # straight through the new door
    call(undo_last_change, ctx(deps, "turn-4"))
    assert not deps.state.edits


def test_confirming_without_a_proposal_is_refused():
    deps = Deps.load(CAMPUS)
    with pytest.raises(ModelRetry):
        add_door(ctx(deps, "turn-2"), "Seminar room 2.06", "Kitchen 2.07", confirmed=True)


async def test_a_reply_that_keeps_failing_the_answer_check_is_returned_not_crashed():
    calls = []

    def stubborn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append(1)
        return ModelResponse(parts=[TextPart("I think it's about 12 m.\nAnswer: 12")])

    agent = make_agent()
    with agent.override(model=FunctionModel(stubborn)):
        result = await agent.run("How far? Finish with a line 'Answer: <number>'.", deps=Deps.load(FACTORY))
    assert result.output.endswith("Answer: 12")
    assert len(calls) == 5  # the check still asked four times for a grounded number before giving way


def test_location_resolves_and_reaches_the_context():
    from app.agents.building_guide.agent import _location_context

    deps = Deps.load("osm-way37850565", here="Büro 0.10 (Ground floor)")
    assert deps.here == "osm-way37850565-F0-S010"
    text = _location_context(SimpleNamespace(deps=deps))
    assert "Büro 0.10 (Ground floor)" in text and "osm-way37850565-F0-S010" in text
    assert _location_context(SimpleNamespace(deps=Deps.load("osm-way37850565"))) == ""


def test_deps_is_not_mistaken_for_ui_state_handler():
    from pydantic_ai.ui._adapter import StateHandler

    deps = Deps.load(FACTORY)
    assert not isinstance(deps, StateHandler)


# ---- hard cases: ambiguity, data errors, the outside, counts on a walk, the Answer line ----------------------


def test_an_item_whose_room_is_not_on_the_plan_is_reported_not_crashed():
    import dataclasses

    from app.agents.building_guide.tools import never_crash, room_details
    from app.building.state import BuildingState

    b = Deps.load(FACTORY).building
    asset = next(iter(b.assets.values()))
    orphan = dataclasses.replace(asset, space_id=f"{b.id}-F0-GONE")
    deps = Deps(state=BuildingState(dataclasses.replace(b, assets={**b.assets, asset.id: orphan})))
    got = call(find, ctx(deps), asset.name)
    item = next(i for i in got["items"] if i["id"] == asset.id)
    assert item["room_missing"] and "not on the floor plan" in item["in"]
    with pytest.raises(ModelRetry, match="not on the floor plan"):
        room_details(ctx(deps), asset.name)
    last = SimpleNamespace(deps=deps, run_id="t", last_attempt=True)
    assert "error" in call(never_crash(route), last, asset.name, "outside")


def test_a_room_number_said_with_one_decimal_offers_both_written_forms():
    from app.agents.building_guide.tools import room_details

    got = call(find, ctx(Deps.load(CAMPUS)), "1.3")
    assert [r["room"] for r in got["rooms"]] == ["Lab 1.03 (Floor 1)", "Storage 1.30 (Floor 1)"]
    assert any("Ambiguous" in n for n in got["notes"])
    with pytest.raises(ModelRetry, match="Ambiguous"):
        room_details(ctx(Deps.load(CAMPUS)), "room 1.3")


def test_a_tie_for_largest_is_flagged():
    got = call(list_rooms, ctx(Deps.load("proc-campus-dev-013")), "Floor 1", "storage")
    assert len(got["largest_is_a_tie"]) == 2


def test_two_rooms_almost_equally_above_are_both_named():
    from app.agents.building_guide.tools import room_details

    got = call(room_details, ctx(Deps.load("osm-way22744230")), "Generic room N5325 (Floor 5)")
    assert len(got["directly_above_ambiguous"]["rooms"]) == 2
    assert "directly_below_ambiguous" not in got


def test_route_counts_doorways_stairs_and_lifts():
    c = ctx(Deps.load("osm-way22744230"))
    got = call(route, c, "Generic room N3307 (Floor 3)", "Generic room N0317B (Ground floor)")
    assert got["passes_in_total"]["doorways"] == 12 and got["passes_in_total"]["stairs_taken"] == 2


def test_from_the_street_starts_at_any_entrance():
    from app.agents.building_guide.tools import reachable_rooms

    c = ctx(Deps.load("swiss-1785"))
    got = call(reachable_rooms, c, "outside (any step-free entrance)", "Floor 2", step_free=True)
    assert (got["reachable_count"], got["unreachable_count"]) == (8, 12)


@pytest.mark.parametrize(
    ("question", "bad", "good"),
    [
        (
            "From A to B? Finish with a line 'Answer: <the spaces I pass, in order> | <total metres>'.",
            "Answer: No indoor route (only from outside)",
            "Answer: no route",
        ),
        (
            "Items per m² there? Finish with a line 'Answer: <items per m²>'.",
            "Answer: 18 items / 32.34 m²",
            "Answer: 32.34",
        ),
    ],
)
async def test_the_answer_line_holds_only_what_is_asked(question, bad, good):
    replies = [bad, good]

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(replies.pop(0) if len(replies) > 1 else replies[0])])

    agent = make_agent()
    deps = Deps.load(FACTORY)
    with agent.override(model=FunctionModel(model)):
        result = await agent.run(question, deps=deps)
    assert result.output == good


async def test_a_value_the_plan_does_not_hold_is_said_in_words_without_a_retry():
    """'Answer: <dB>' for noise the plan doesn't record: 'not in the plan' is the honest line. A retry asking
    for a number made Haiku rewrite the reply and drop the room details and directions it had found."""
    reply = (
        "Seminar room 2.11 is on Floor 2, 12 chairs. The plan has no noise data.\n\nAnswer: not in the plan"
    )
    calls = []

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append(1)
        return ModelResponse(parts=[TextPart(reply)])

    agent = make_agent()
    question = "How loud is Seminar room 2.11, in decibels? Finish with a line 'Answer: <dB>'."
    with agent.override(model=FunctionModel(model)):
        result = await agent.run(question, deps=Deps.load(FACTORY))
    assert result.output == reply and len(calls) == 1


async def test_no_answer_line_unless_asked():
    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[TextPart("Two rooms are called K15: 5.64 m² or 22.26 m². Which one?\n\nAnswer: 5.64")]
        )

    agent = make_agent()
    with agent.override(model=FunctionModel(model)):
        result = await agent.run("How big is K15?", deps=Deps.load(FACTORY))
    assert result.output.endswith("Which one?")


async def test_a_derived_number_from_calculate_passes_the_answer_check():
    """A number no single tool returns (29 rooms - 17 offices) is fine once `calculate` produced it; the
    retry for an ungrounded number points the model to `calculate`, it doesn't tell it to give up."""
    from pydantic_ai.messages import ToolCallPart

    seen: list[str] = []
    answer = ModelResponse(parts=[TextPart("29 rooms minus 17 offices.\n\nAnswer: 12")])

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        last = str(messages[-1].parts[-1])
        if "retry_feedback" in last or "RetryPromptPart" in last:  # the answer check sent it back
            seen.append(last)
            return ModelResponse(parts=[ToolCallPart("calculate", {"expression": "29 - 17"})])
        return answer

    agent = make_agent()
    with agent.override(model=FunctionModel(model)):
        result = await agent.run(
            "How many rooms on Floor 1 are NOT offices? Finish with a line 'Answer: <number>'.",
            deps=Deps.load(FACTORY),
        )
    assert result.output.endswith("Answer: 12")
    assert len(seen) == 1 and "calculate" in seen[0] and "Don't calculate" not in seen[0]


def test_what_if_counts_a_room_that_gets_its_first_way_out():
    import dataclasses

    from app.agents.building_guide.tools import what_if
    from app.building.state import BuildingState

    b = Deps.load(FACTORY).building
    lab = b.resolve("Quality lab 0.21")[0].id
    sealed = {k: o for k, o in b.openings.items() if lab not in o.space_ids}
    deps = Deps(state=BuildingState(dataclasses.replace(b, openings=sealed)))
    got = call(what_if, ctx(deps), "0", add_door_between=["Quality lab 0.21", "Meeting room 0.20"])
    assert "Quality lab 0.21 (Ground floor)" in [r["room"] for r in got["shorter"]]


def test_no_route_names_every_restriction_that_blocks_it() -> None:
    """A forklift blocked by a narrow opening AND by production bays must hear both, not just the opening."""
    altered = ROOT / "data" / "extra_tricky" / "proc-factory-dev-003-xt-aisle295"
    if not altered.exists():
        pytest.skip("data/extra_tricky not present")
    r = call(
        route,
        ctx(Deps.load(altered)),
        "Logistics zone H-C5",
        "Warehouse zone H-C2",
        step_free=True,
        min_width_m=3,
    )
    assert r["reachable"] is False
    causes = " | ".join(r["blocked_by"])
    assert "production bays" in causes and "2.95 m opening" in causes


def test_no_route_says_when_places_are_not_connected() -> None:
    sealed = ROOT / "data" / "extra_tricky" / "proc-factory-dev-003-xt-sealed"
    if not sealed.exists():
        pytest.skip("data/extra_tricky not present")
    r = call(route, ctx(Deps.load(sealed)), "Office 0.23", "Quality lab 0.21")
    assert r["reachable"] is False and r["blocked_by"][0].startswith("not connected")


def test_plan_visits_picks_rooms_and_order_for_a_free_order_round_trip():
    c = ctx(Deps.load(CAMPUS))
    got = call(
        plan_visits, c, "Office 3.07", item_types=["coffee machine", "3d_printer", "laser_cutter"],
        return_to_start=True,
    )  # fmt: skip
    assert got["reachable"] and got["exact"]
    assert got["total_length_m"] == pytest.approx(133.18, abs=0.01)
    assert got["greedy_total_m"] == pytest.approx(193.09, abs=0.01)
    assert [s["room"].split(" (")[0] for s in got["stops"]] in (
        ["Kitchen 0.03", "Makerspace 0.08"],
        ["Makerspace 0.08", "Kitchen 0.03"],
    )
    assert len(got["legs"]) == 3 and all(leg["steps"] for leg in got["legs"])
    fixed = call(
        plan_visits, c, "Office 3.07", rooms=["Kitchen 0.03", "Makerspace 0.08"], return_to_start=True
    )
    assert fixed["total_length_m"] == pytest.approx(133.18, abs=0.01)


def test_reachable_rooms_can_apply_the_production_bay_rule() -> None:
    """Default reach may cross production bays (Synera's delivery counts do); opted out, a forklift that may
    not drive through bays reaches only what a route can, and the tool names what the rule dropped."""
    from app.agents.building_guide.tools import reachable_rooms

    altered = ROOT / "data" / "extra_tricky" / "proc-factory-dev-003-xt-aisle295"
    if not altered.exists():
        pytest.skip("data/extra_tricky not present")
    c = ctx(Deps.load(altered))
    args = ("Logistics zone H-C5", "0")
    kw = {"step_free": True, "min_width_m": 3, "room_type": "warehouse_zone"}
    default = call(reachable_rooms, c, *args, **kw)
    assert default["reachable_count"] == 5 and "dropped_by_production_bay_rule" not in default
    ruled = call(reachable_rooms, c, *args, **kw, through_production_bays=False)
    names = {r.split(" (")[0] for r in ruled["reachable"]}
    assert names == {"Warehouse zone H-A5", "Warehouse zone H-C4"}
    assert len(ruled["dropped_by_production_bay_rule"]) == 3


def test_walk_distances_ranks_every_room_by_its_walk_out() -> None:
    """The worst-case walk to the nearest entrance on a floor, from one call (the fire-drill question)."""
    from app.agents.building_guide.tools import walk_distances
    from app.building.model import OUTSIDE
    from app.building.routing import RouteGraph

    deps = Deps.load(CAMPUS)
    b = deps.building
    got = call(walk_distances, ctx(deps), "Floor 2")
    rows = got["longest_first"]
    assert [r["walk_m"] for r in rows] == sorted((r["walk_m"] for r in rows), reverse=True)
    out = RouteGraph(b).distances_from([OUTSIDE])  # the walk to whichever entrance is nearer
    farthest = max((s for s in b.spaces_on(f"{CAMPUS}-F2") if s.type == "room"), key=lambda s: out[s.id])
    assert rows[0]["id"] == farthest.id and rows[0]["walk_m"] == round(out[farthest.id], 2)
    assert got["rooms_compared"] == len(rows) and got["shortest"] == rows[-1]
