"""building_guide behaviour that isn't a golden answer: search, scopes, arithmetic, two-turn edits, and
never crashing on a reply that fails the answer check. No model calls."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest
from pydantic_ai import ModelRetry
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.agents.building_guide.agent import make_agent
from app.agents.building_guide.tools import (
    Deps,
    _spaces_named,
    add_door,
    add_wall,
    building_changes,
    calculate,
    find,
    highlight_rooms,
    list_rooms,
    plan_visits,
    remove_door,
    route,
    undo_last_change,
)
from app.settings import ROOT

FACTORY, CAMPUS = "proc-factory-dev-003", "proc-campus-dev-000"


def ctx(deps: Deps, run_id: str = "turn-1") -> SimpleNamespace:
    return SimpleNamespace(deps=deps, run_id=run_id)


def call(tool, c, *args, **kwargs) -> dict:
    return json.loads(tool(c, *args, **kwargs))


def asking(reference: str, detail="rooms") -> Deps:
    """A conversation whose plan edits wait for a yes (the voice call); everywhere else they apply at once."""
    deps = Deps.load(reference, detail=detail)
    deps.confirm_edits = True
    return deps


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


def test_a_door_is_added_at_once_and_the_next_route_in_the_same_turn_uses_it():
    deps = Deps.load(CAMPUS)
    pair = ("Office 0.02", "Kitchen 0.03")
    assert call(route, ctx(deps), *pair)["total_length_m"] == pytest.approx(10.77)
    done = call(add_door, ctx(deps), *pair, width_m=1.0)
    assert done["status"] == "done" and len(deps.state.edits) == 1 and deps.pending is None
    assert "Added a 1 m door between Office 0.02" in done["change"] and "undo_last_change" in done["undo"]
    assert call(route, ctx(deps), *pair)["total_length_m"] == pytest.approx(4.17)
    again = call(add_door, ctx(deps), *pair, width_m=1.0, confirmed=True)  # an old habit: "confirm" it
    assert again["status"] == "done" and len(deps.state.edits) == 1  # not added twice
    call(undo_last_change, ctx(deps))
    assert call(route, ctx(deps), *pair)["total_length_m"] == pytest.approx(10.77)


def test_asking_for_the_same_door_again_adds_nothing_and_says_it_is_in_the_plan():
    # Live bug: the voice agent still asked "shall I add it?", the "yes" reached the guide as a new request,
    # and the second add_door stacked a duplicate door on the first one.
    deps = Deps.load(CAMPUS)
    pair = ("Office 0.02", "Kitchen 0.03")
    first = call(add_door, ctx(deps, "turn-1"), *pair)
    for c in (ctx(deps, "turn-1"), ctx(deps, "turn-2")):  # same turn, or the "yes" in the next one
        again = call(add_door, c, *reversed(pair))
        assert again["status"] == "done" and len(deps.state.edits) == 1
        assert "already in the plan" in again["change"] and "nothing added" in again["change"]
        assert not re.search(r"mistake|error|sorry", json.dumps(again), flags=re.IGNORECASE)
    door_id = deps.state.edits[0].door_id
    assert door_id in first["change"] and door_id in again["change"]
    assert len([o for o in deps.building.openings.values() if o.id not in deps.state.base.openings]) == 1


def test_a_door_where_the_plan_has_one_adds_nothing_unless_a_second_is_asked_for():
    deps = Deps.load(CAMPUS)
    pair = ("Office 0.02", "Corridor 0.01")
    got = call(add_door, ctx(deps), *pair)
    assert got["status"] == "already there" and not deps.state.edits
    assert "F0-O0001" in got["change"] and "0.9 m" in got["change"]
    assert call(add_door, ctx(deps), *pair, another=True)["status"] == "done" and len(deps.state.edits) == 1


async def test_the_agent_is_not_told_its_own_new_door_was_there_before():
    # Live bug: after add_door, the next model request's instructions said "1 change recorded (latest: Added a
    # door ... just now)"; read next to the tool result, the model said a door "had already been added" and
    # that there were now two. The context describes the building as the turn started, the same all turn.
    from pydantic_ai.messages import ModelRequest, ToolCallPart

    seen: list[str] = []

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(next(m.instructions for m in reversed(messages) if isinstance(m, ModelRequest)) or "")
        if len(seen) == 1:
            args = {"first": "Office 0.02", "second": "Kitchen 0.03"}
            return ModelResponse(parts=[ToolCallPart("add_door", args)])
        return ModelResponse(parts=[TextPart("I've added the door between Office 0.02 and Kitchen 0.03.")])

    deps = Deps.load(CAMPUS, detail="walk")
    agent = make_agent()
    with agent.override(model=FunctionModel(model)):
        await agent.run("Add a door between Office 0.02 and Kitchen 0.03", deps=deps)
        assert len(deps.state.edits) == 1 and len(seen) == 2
        assert seen[0] == seen[1] and "Added a 0.9 m door" not in seen[1]
        seen.clear()
        await agent.run("How far is it now?", deps=deps)  # the next turn knows about it
    assert "Added a 0.9 m door" in seen[0]


async def test_one_request_with_a_repeated_add_door_call_gives_one_door():
    from pydantic_ai.messages import ToolCallPart

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls = sum(isinstance(p, ToolCallPart) for m in messages for p in getattr(m, "parts", []))
        if calls < 2:  # the model calls add_door, then again "to be sure"
            args = {"first": "Office 0.02", "second": "Kitchen 0.03"}
            return ModelResponse(parts=[ToolCallPart("add_door", args)])
        return ModelResponse(parts=[TextPart("I've added the door between Office 0.02 and Kitchen 0.03.")])

    deps = Deps.load(CAMPUS, detail="walk")
    agent = make_agent()
    with agent.override(model=FunctionModel(model)):
        result = await agent.run("Add a door between Office 0.02 and Kitchen 0.03", deps=deps)
    assert len(deps.state.edits) == 1 and result.output.startswith("I've added")


def test_a_door_to_the_street_is_a_new_entrance():
    deps = Deps.load(CAMPUS)
    far = call(route, ctx(deps), "outside", "Seminar room 0.17")["total_length_m"]
    done = call(add_door, ctx(deps), "Seminar room 0.17", "the street", width_m=1.5)
    assert done["status"] == "done" and "entrance from outside into Seminar room 0.17" in done["change"]
    assert done["entrance_into"] == ["Seminar room 0.17 (Ground floor)"]
    assert call(route, ctx(deps), "outside", "Seminar room 0.17")["total_length_m"] < far - 30
    with pytest.raises(ModelRetry, match="no entrance"):
        add_door(ctx(deps), "Office 2.03", "outside")


def test_remove_door_says_what_it_cuts_off_and_undo_puts_it_back():
    deps = Deps.load(CAMPUS)
    got = call(remove_door, ctx(deps), between=["Office 0.02", "Corridor 0.01"])
    assert got["status"] == "done" and got["change"].startswith("Removed the door between Office 0.02")
    assert "undo_last_change" in got["undo"]
    assert "Office 0.02 (Ground floor)" in got["no_way_in_any_more"]
    assert call(route, ctx(deps), "Office 0.02", "Kitchen 0.03")["reachable"] is False
    call(undo_last_change, ctx(deps))
    assert call(route, ctx(deps), "Office 0.02", "Kitchen 0.03")["total_length_m"] == pytest.approx(10.77)
    with pytest.raises(ModelRetry):
        remove_door(ctx(deps), between=["Office 0.02", "Kitchen 0.03"])  # no door there


def test_removing_a_door_already_removed_in_this_session_changes_nothing_and_says_so():
    deps = Deps.load(CAMPUS)
    door = call(remove_door, ctx(deps), between=["Office 0.02", "Corridor 0.01"])["door"]
    again = call(remove_door, ctx(deps), door=door)  # the voice "yes" repeats the request
    assert again["status"] == "done" and "already removed" in again["change"] and len(deps.state.edits) == 1


def test_a_width_change_is_remove_then_add_in_the_same_place():
    deps = Deps.load(CAMPUS)
    removed = call(remove_door, ctx(deps), between=["Office 0.02", "Corridor 0.01"])
    again = removed["re_add_at"]
    done = call(add_door, ctx(deps), again["first"], again["second"], width_m=1.5, position=again["position"])
    assert done["status"] == "done"
    (new,) = [o for o in deps.building.openings.values() if o.id not in deps.state.base.openings]
    assert list(new.position) == again["position"] and new.width_m == 1.5
    assert done["position"] == again["position"]  # the map draws the new door here, in the old door's cutout
    assert removed["door"] not in deps.building.openings  # so the map's scene has no trace of the old one
    assert call(route, ctx(deps), "Office 0.02", "Kitchen 0.03")["total_length_m"] == pytest.approx(10.77)


def test_where_edits_need_a_yes_a_door_is_only_proposed_until_confirmed():
    deps = asking(CAMPUS)
    pair = ("Office 0.02", "Kitchen 0.03")
    assert call(add_door, ctx(deps, "turn-1"), *pair)["status"].startswith("proposed")
    assert not deps.state.edits
    assert call(route, ctx(deps, "turn-1"), *pair)["total_length_m"] == pytest.approx(10.77)
    assert call(add_door, ctx(deps, "turn-2"), *pair, confirmed=True)["status"] == "done"
    assert call(route, ctx(deps, "turn-2"), *pair)["total_length_m"] == pytest.approx(4.17)


def test_edits_need_a_yes_in_a_later_turn():
    deps = asking(CAMPUS)
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


def test_several_edits_proposed_together_need_one_yes():
    deps = asking(CAMPUS)
    pairs = [
        ("Seminar room 2.06", "Kitchen 2.07"),
        ("Office 2.03", "Lab 2.02"),
        ("Office 2.12", "Seminar room 2.13"),
    ]
    for pair in pairs:
        assert call(add_door, ctx(deps, "turn-1"), *pair)["status"].startswith("proposed")
    assert not deps.state.edits
    for pair in pairs:  # one "yes" in the next turn applies every door, in any order
        assert call(add_door, ctx(deps, "turn-2"), *pair, confirmed=True)["status"] == "done"
    assert len(deps.state.edits) == 3 and deps.pending is None
    again = call(add_door, ctx(deps, "turn-3"), *pairs[0], confirmed=True)  # each proposal applies once
    assert "already in the plan" in again["change"] and len(deps.state.edits) == 3


def test_a_new_proposal_in_a_later_turn_replaces_the_old_batch():
    deps = asking(CAMPUS)
    call(add_door, ctx(deps, "turn-1"), "Seminar room 2.06", "Kitchen 2.07")
    call(add_door, ctx(deps, "turn-2"), "Office 2.03", "Lab 2.02")
    with pytest.raises(ModelRetry):  # the person moved on: the first one was never confirmed
        add_door(ctx(deps, "turn-3"), "Seminar room 2.06", "Kitchen 2.07", confirmed=True)


def test_confirming_without_a_proposal_is_refused():
    deps = asking(CAMPUS)
    with pytest.raises(ModelRetry):
        add_door(ctx(deps, "turn-2"), "Seminar room 2.06", "Kitchen 2.07", confirmed=True)


def test_the_yes_applies_the_door_that_was_proposed_even_if_restated_differently():
    deps = asking(CAMPUS)
    call(add_door, ctx(deps, "turn-1"), "Seminar room 2.06", "Kitchen 2.07", width_m=5)
    # The confirming call names the rooms the other way round and forgets the width: still the same door.
    got = call(add_door, ctx(deps, "turn-2"), "Kitchen 2.07", "Seminar room 2.06", confirmed=True)
    assert got["status"] == "done" and got["width_m"] == 5
    assert [e.width_m for e in deps.state.edits] == [5]


def test_a_person_who_says_dont_ask_gets_the_change_at_once():
    deps = asking(CAMPUS)
    got = call(add_door, ctx(deps, "turn-1"), "Seminar room 2.06", "Kitchen 2.07", apply_now=True)
    assert got["status"] == "done" and len(deps.state.edits) == 1 and deps.pending is None


def _tool_call_then(reply: str, **door_args):
    """A model that calls add_door once (with door_args), then answers `reply` until told otherwise."""
    from pydantic_ai.messages import ToolCallPart

    seen: list[list[ModelMessage]] = []

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(messages)
        if len(seen) == 1:
            args = {"first": "Seminar room 2.06", "second": "Kitchen 2.07", **door_args}
            return ModelResponse(parts=[ToolCallPart("add_door", args)])
        if len(seen) == 2:
            return ModelResponse(parts=[TextPart(reply)])
        return ModelResponse(parts=[TextPart("I've proposed the door; shall I add it?")])

    return model, seen


async def test_saying_a_door_was_added_when_it_was_only_proposed_is_sent_back():
    model, seen = _tool_call_then("I've added the door between Seminar room 2.06 and Kitchen 2.07.")
    deps = asking(CAMPUS, detail="walk")
    agent = make_agent()
    with agent.override(model=FunctionModel(model)):
        result = await agent.run("Add a door between Seminar room 2.06 and Kitchen 2.07", deps=deps)
    assert not deps.state.edits
    assert len(seen) == 3 and result.output.startswith("I've proposed")


async def test_saying_a_door_was_added_when_it_was_is_fine():
    model, seen = _tool_call_then(
        "I've added the door between Seminar room 2.06 and Kitchen 2.07.", apply_now=True
    )
    deps = asking(CAMPUS, detail="walk")
    agent = make_agent()
    with agent.override(model=FunctionModel(model)):
        result = await agent.run(
            "Just add a door between Seminar room 2.06 and Kitchen 2.07, don't ask", deps=deps
        )
    assert len(deps.state.edits) == 1
    assert len(seen) == 2 and result.output.startswith("I've added")


async def test_by_default_the_agent_adds_the_door_without_asking_and_may_say_so():
    model, seen = _tool_call_then("I've added the door between Seminar room 2.06 and Kitchen 2.07.")
    deps = Deps.load(CAMPUS, detail="walk")
    agent = make_agent()
    with agent.override(model=FunctionModel(model)):
        result = await agent.run("Add a door between Seminar room 2.06 and Kitchen 2.07", deps=deps)
    assert len(deps.state.edits) == 1
    assert len(seen) == 2 and result.output.startswith("I've added")


def test_only_a_conversation_that_asks_first_is_told_to_propose_edits():
    from app.agents.building_guide.agent import CONFIRM_EDITS, _confirm_context

    assert _confirm_context(SimpleNamespace(deps=Deps.load(CAMPUS))) == ""
    assert _confirm_context(SimpleNamespace(deps=asking(CAMPUS))) == CONFIRM_EDITS
    assert "ask them to confirm" in CONFIRM_EDITS


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


def test_a_machine_moved_behind_a_production_bay_gets_no_route_and_a_next_step() -> None:
    """Scenario live-moved-machine: the drill press moves from Production zone H-A1 into Assembly zone H-A2.
    From the office wing the only way in crosses H-A1, so there is no route by the bay rule (scored: no
    route), but a person being guided hears how far they can go and whom to ask."""
    from app.agents.building_guide.tools import move_item

    deps = Deps.load(FACTORY, detail="walk")
    call(move_item, ctx(deps), "Drill press H-A1", "Assembly zone H-A2")
    r = call(route, ctx(deps), "Kitchen 0.28", "Drill press H-A1")
    assert r["reachable"] is False
    assert r["stops"][-1].startswith("Assembly zone H-A2") and "Production zone H-A1" in r["blocked_by"][0]
    step = r["next_step"]
    assert step["closest_by_the_rules"].startswith("Production zone H-A1")
    assert step["bay_borders"].startswith("Assembly zone H-A2") and step["walk_m"] > 0
    # ending in the bay itself is a route, and so is leaving the bay you stand in
    assert call(route, ctx(deps), "Kitchen 0.28", "Production zone H-A1")["reachable"] is True
    assert call(route, ctx(deps), "Assembly zone H-A2", "Main aisle H-B1")["reachable"] is True
    # scored runs (detail="rooms") keep the plain no-route answer
    scored = Deps.load(FACTORY)
    call(move_item, ctx(scored), "Drill press H-A1", "Assembly zone H-A2")
    assert "next_step" not in call(route, ctx(scored), "Kitchen 0.28", "Drill press H-A1")


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


def test_list_rooms_without_a_floor_ranks_the_whole_building_and_flags_a_tie_across_floors():
    from app.agents.building_guide.tools import list_rooms

    got = call(list_rooms, ctx(Deps.load("proc-factory-dev-011")), room_type="kitchen")
    assert got["floor"] == "whole building" and got["count"] == 14
    assert got["largest_is_a_tie"] == ["Kitchen 1.13 (Floor 1)", "Kitchen 2.13 (Floor 2)"]
    assert got["note"].startswith("Ambiguous:")
    one_floor = call(list_rooms, ctx(Deps.load("proc-factory-dev-011")), "1", room_type="kitchen")
    assert one_floor["floor"] == "Floor 1" and "largest_is_a_tie" not in one_floor


@pytest.mark.parametrize(
    ("building", "floor", "start", "within_m", "expected"),
    [
        ("proc-factory-dev-012", "Floor 2", "Office 2.09", 40, 25),
        ("osm-way27917482", "Floor 3", "03.13.060", 30, 14),
    ],
)
def test_walk_distances_counts_the_other_rooms_within_a_walk(building, floor, start, within_m, expected):
    """ "How many other rooms on that floor within X m": one call, the count computed in code."""
    from app.agents.building_guide.tools import walk_distances

    got = call(walk_distances, ctx(Deps.load(building)), floor, start=start, within_m=within_m)
    assert got["count_within"] == expected
    assert len(got["within"]) == expected
    assert all(r["walk_m"] <= within_m for r in got["within"])


BIG = "osm-way22744230"  # floors with more rooms than walk_distances lists, several entrances, upper floors


def _walk_out(deps: Deps, room: str) -> float:
    return call(route, ctx(deps), room, "outside")["total_length_m"]


def test_one_rooms_walk_out_is_never_lost_to_the_ranking_cutoff():
    """exit_distance ("how far from X to get outside") is one room's shortest walk to its nearest open
    entrance. walk_distances lists 30 rooms per floor: on a big floor the asked room may not be listed, so
    the cut says to route that room, and a room given as start carries its own walk out."""
    from app.agents.building_guide.tools import walk_distances
    from app.building.model import OUTSIDE
    from app.building.routing import RouteGraph

    deps = Deps.load(BIG)
    b = deps.building
    floor = max(b.floors_by_level(), key=lambda f: sum(s.type == "room" for s in b.spaces_on(f.id)))
    got = call(walk_distances, ctx(deps), floor.name)
    listed = {r["id"] for r in got["longest_first"]} | {got["shortest"]["id"]}
    out = RouteGraph(b).distances_from([OUTSIDE])
    hidden = next(s for s in b.spaces_on(floor.id) if s.type == "room" and s.id in out and s.id not in listed)
    assert 'destination=...), "outside"' in got["note"]
    assert _walk_out(deps, hidden.id) == round(out[hidden.id], 2)  # route agrees with the ranking
    from_room = call(walk_distances, ctx(deps), floor.name, start=hidden.id)
    assert from_room["start_walk_out_m"] == round(out[hidden.id], 2)
    assert "start_walk_out_m" not in got  # from outside there is no start room


def test_walk_out_from_an_upper_floor_takes_the_nearest_entrance_and_avoids_a_closed_one():
    """Upper floors walk down to the ground-floor entrances; with several entrances the nearest open one
    counts, and a reported closure moves the walk to the next one (never shorter)."""
    from app.agents.building_guide.tools import set_closed
    from app.building.model import OUTSIDE

    deps = Deps.load(BIG)
    b = deps.building
    assert len(b.openings_of(OUTSIDE)) >= 2
    room = next(
        s
        for f in reversed(b.floors_by_level())
        if f.level > 0
        for s in b.spaces_on(f.id)
        if s.type == "room" and call(route, ctx(deps), s.id, "outside")["reachable"]
    )
    before = call(route, ctx(deps), room.id, "outside")
    used = [
        e
        for leg in before["legs"]
        for e in leg["element_path"]
        if e in {o.id for o in b.openings_of(OUTSIDE)}
    ]
    assert len(used) == 1  # leaves through exactly one entrance, at the end
    set_closed(ctx(deps), used[0])
    after = call(route, ctx(deps), room.id, "outside")
    assert not after["reachable"] or after["total_length_m"] >= before["total_length_m"]
    if after["reachable"]:
        assert used[0] not in [e for leg in after["legs"] for e in leg["element_path"]]


def test_rooms_stacked_on_different_floors_are_not_an_overlap():
    """Kitchens drawn one above another share x/y but no floor area: across the building the total stays
    the plain sum (extra_tricky adversarial_language-11 read a 'covered' 120.06 m² instead of 161.34)."""
    from app.agents.building_guide.tools import list_rooms

    got = call(list_rooms, ctx(Deps.load("proc-campus-dev-000")), room_type="kitchen")
    assert got["total_area_m2"] == 161.34 and "covered_area_m2" not in got


def test_room_details_gives_every_room_below_down_to_the_ground_floor():
    from app.agents.building_guide.tools import room_details

    got = call(room_details, ctx(Deps.load("proc-campus-dev-000")), "Kitchen 3.27 (Floor 3)")
    assert got["all_below_down_to_lowest_floor"] == {
        "count": 3,
        "rooms": ["Office 2.27 (Floor 2)", "Storage 1.27 (Floor 1)", "Lecture hall 0.27 (Ground floor)"],
    }


def test_a_room_drawn_twice_is_one_room():
    """OSM sometimes draws a room twice, outline on outline (osm-way58216509 has 84 such pairs). Both ids
    are the same room, so it resolves to one without an 'Ambiguous' (the kit's questions use either copy at random)."""
    got = call(
        route,
        ctx(Deps.load("osm-way58216509")),
        "Generic room N2146 (Floor 2)",
        "Generic room N2103 (Floor 2)",
    )
    assert got["reachable"] and "note" not in got


def scored(deps: Deps) -> SimpleNamespace:
    """A one-shot benchmark question: the prompt asks for an 'Answer:' line."""
    return SimpleNamespace(
        deps=deps, run_id="turn-1", prompt="How do I get there? Finish with 'Answer: <number>'."
    )


SHARED = "Generic room 03.08.033 (Floor 3)"  # three different rooms, side by side, share this name and number
TO = "Generic room 02.07.044 (Floor 2)"


def test_different_rooms_sharing_a_name_stay_ambiguous_for_a_person():
    with pytest.raises(ModelRetry, match="Ambiguous"):
        route(ctx(Deps.load("osm-way27917482")), SHARED, TO)


def test_a_scored_route_from_a_shared_name_commits_to_the_middle_option():
    """The kit's question generator picks one of the same-named rooms at random (osm-way37850565 Q07 and Q08
    name the same 'Büro (Ground floor)' and use different rooms), so no rule finds it. A one-shot answer
    still needs one number: the route of the room whose length is closest to all the others, the least
    wrong whichever was meant. The others are listed, and nothing says 'Ambiguous' (that invites a hedge)."""
    deps = Deps.load("osm-way27917482")
    rooms = sorted(s.id for s in deps.building.resolve(SHARED))
    each = {r: call(route, ctx(deps), r, TO)["total_length_m"] for r in rooms}
    middle = min(each, key=lambda r: sum(abs(each[r] - v) for v in each.values()))
    got = call(route, scored(deps), SHARED, TO)
    assert got["total_length_m"] == each[middle]
    assert "Ambiguous" not in json.dumps(got)
    assert sorted(o["total_length_m"] for o in got["shared_name"]["options"]) == sorted(each.values())


# ---- add_wall: a new wall that halves a room -----------------------------------------------------------------

SPLIT = ("Office 2.03", "Lab 2.02", "Staircase 1")  # north and south neighbours: a west and an east half


def test_a_wall_is_proposed_then_built_after_a_yes_and_undone():
    deps = asking(CAMPUS)
    room = deps.building.resolve("Office 2.03")[0]
    proposed = call(add_wall, ctx(deps, "turn-1"), *SPLIT)
    assert proposed["status"].startswith("proposed") and not deps.state.edits
    assert [h["area_m2"] for h in proposed["halves"]] == [12.5, 12.5]
    assert any("note" in h for h in proposed["halves"])  # the west half has no door yet
    with pytest.raises(ModelRetry):  # same turn: the person hasn't answered
        add_wall(ctx(deps, "turn-1"), *SPLIT, confirmed=True)
    done = call(add_wall, ctx(deps, "turn-2"), "Office 2.03", "Staircase 1", "Lab 2.02", confirmed=True)
    assert done["status"] == "done" and room.id not in deps.building.spaces
    halves = _spaces_named(deps.building, "Office 2.03", None)  # the old name finds both halves
    assert sorted(h.name for h in halves) == ["Office 2.03-1 (west half)", "Office 2.03-2 (east half)"]
    assert [h.id for h in _spaces_named(deps.building, "2.03-2", None)] == [halves[1].id]
    assert sum(h.area for h in halves) == pytest.approx(room.area, rel=1e-3)
    assert call(building_changes, ctx(deps, "turn-3"))["changes"][0]["kind"] == "wall added"
    call(undo_last_change, ctx(deps, "turn-3"))
    assert room.id in deps.building.spaces and not deps.state.edits


def test_a_wall_asked_for_without_confirmation_is_built_at_once_and_a_door_can_join_the_halves():
    deps = Deps.load(CAMPUS)
    got = call(add_wall, ctx(deps, "turn-1"), *SPLIT, apply_now=True)
    assert got["status"] == "done" and len(deps.state.edits) == 1 and deps.pending is None
    door = call(
        add_door, ctx(deps, "turn-1"), "Office 2.03 (west half)", "Office 2.03 (east half)", apply_now=True
    )
    assert door["status"] == "done"
    walk = call(route, ctx(deps, "turn-2"), "Office 2.03 (west half)", "Corridor 2.01")
    assert len(walk["legs"][0]["space_path"]) == 3  # west half, east half, corridor


@pytest.mark.parametrize(
    ("room", "first", "second", "why"),
    [
        ("Office 2.03", "Lab 2.02", "Kitchen 2.07", "doesn't share a wall"),
        ("Corridor 2.01", "Lab 2.02", "Office 2.03", "same side"),
    ],
)
def test_a_wall_that_cannot_halve_the_room_is_refused_clearly(room, first, second, why):
    deps = Deps.load(CAMPUS)
    with pytest.raises(ModelRetry, match=why):
        add_wall(ctx(deps), room, first, second, apply_now=True)
    assert not deps.state.edits


def test_highlight_rooms_sends_exactly_the_given_rooms_to_the_map():
    c = ctx(Deps.load(FACTORY))
    rooms = call(list_rooms, c, "0")["rooms"][:3]
    names = [r["room"] for r in rooms]
    got = call(highlight_rooms, c, [*names, names[0], "swimming pool"], label="next to the lab")
    (view,) = got["view"]
    assert view["kind"] == "highlight" and view["label"] == "next to the lab"
    assert len(view["ids"]) == 3  # the duplicate counted once
    assert list(got["not_found"]) == ["swimming pool"]
    with pytest.raises(ModelRetry):
        highlight_rooms(c, ["swimming pool"])


def test_highlight_rooms_is_offered_only_to_people_with_a_map():
    import asyncio

    from pydantic_ai.models.test import TestModel

    async def offered(detail: str) -> set[str]:
        m = TestModel(call_tools=[])
        await make_agent().run("hi", deps=Deps.load(FACTORY, detail=detail), model=m)
        return {t.name for t in m.last_model_request_parameters.function_tools}

    assert "highlight_rooms" in asyncio.run(offered("walk"))  # app and voice chats
    assert "highlight_rooms" not in asyncio.run(offered("rooms"))  # benchmark
