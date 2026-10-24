"""Level 5 add-in "live building": things move ("this is now here"), deliveries arrive or are expected,
and every colleague using the building sees each change, with when it happened and who reported it.

    uv run pytest tests/building/test_live.py -q

Scored on the team board as level5-addin (with the live-* scenarios in scenarios.yaml).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic_ai import ModelRetry

from app.agents.building_guide.tools import (
    Deps,
    building_changes,
    find,
    item_history,
    move_item,
    set_closed,
    undo_last_change,
)
from app.api import main
from app.building.model import load_building
from app.building.state import BuildingState

FACTORY = "proc-factory-dev-003"
NOW = datetime(2026, 10, 24, 14, 0).astimezone()
DAY = timedelta(days=1)


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def state(clock: Clock) -> BuildingState:
    return BuildingState(load_building(FACTORY), clock=clock)


def call(tool, deps: Deps, *args, **kwargs) -> dict:
    return json.loads(tool(SimpleNamespace(deps=deps, run_id="turn"), *args, **kwargs))


def found_in(deps: Deps, query: str) -> list[str]:
    return [i["in"] for i in call(find, deps, query).get("items", [])]


def test_live_a_reported_move_shows_at_once_for_a_colleague(state):
    maintenance, apprentice = Deps(state, who="maintenance"), Deps(state, who="apprentice")
    moved = call(move_item, maintenance, "Drill press H-A1", "Assembly zone H-A2")
    assert moved["status"] == "done"  # a report of where something is now needs no yes
    assert "Production zone H-A1" in moved["from"] and "Assembly zone H-A2" in moved["to"]
    item = call(find, apprentice, "drill press H-A1")["items"][0]
    assert "Assembly zone H-A2" in item["in"]
    assert "maintenance" in item["reported"]


def test_live_a_delivery_is_a_new_item_people_can_find(state):
    goods_in = Deps(state, who="goods-in")
    arrived = call(
        move_item, goods_in, "Pallet of aluminium sheets", "Logistics zone H-C5", new_item_type="pallet"
    )
    assert arrived["status"] == "done" and arrived["from"] is None
    assert found_in(Deps(state, who="machinist"), "aluminium") == ["Logistics zone H-C5 (Ground floor)"]
    assert any(a["name"] == "Pallet of aluminium sheets" for a in state.export()["assets"])


def test_live_an_unknown_item_without_a_type_is_a_question_back(state):
    with pytest.raises(ModelRetry, match="new_item_type"):
        move_item(
            SimpleNamespace(deps=Deps(state), run_id="t"), "Pallet of aluminium sheets", "Logistics zone H-C5"
        )


def test_live_an_item_that_fits_several_is_a_question_back(state):
    with pytest.raises(ModelRetry, match="Drill press H-A4-A"):
        move_item(SimpleNamespace(deps=Deps(state), run_id="t"), "drill press", "Assembly zone H-A2")


def test_live_an_expected_delivery_shows_up_when_it_is_due(state, clock):
    planner = Deps(state, who="planner")
    due = (NOW + 2 * DAY).isoformat()
    expected = call(
        move_item, planner, "Crate of motor parts", "Warehouse zone H-C2", when=due, new_item_type="crate"
    )
    assert expected["status"] == "expected"
    assert found_in(planner, "motor parts") == []  # not in the building yet
    coming = call(building_changes, planner, since=NOW.isoformat())["changes"]
    assert [(c["item"], c["status"]) for c in coming] == [("Crate of motor parts", "expected")]
    clock.now = NOW + 3 * DAY
    assert found_in(planner, "motor parts") == ["Warehouse zone H-C2 (Ground floor)"]


def test_live_history_says_where_an_item_was_and_who_said_so(state):
    maintenance, apprentice = Deps(state, who="maintenance"), Deps(state, who="apprentice")
    call(move_item, maintenance, "Drill press H-A1", "Assembly zone H-A2", when=(NOW - DAY).isoformat())
    call(move_item, apprentice, "Drill press H-A1", "Production zone H-A4")
    history = call(item_history, apprentice, "Drill press H-A1")
    assert "Production zone H-A4" in history["now_in"]
    assert [("Assembly zone H-A2" in h["in"], h["by"]) for h in history["history"][1:]] == [
        (True, "maintenance"),
        (False, "apprentice"),
    ]
    assert "Production zone H-A1" in history["history"][0]["in"]  # where the plan had it
    drill = next(a for a in state.base.assets.values() if a.name == "Drill press H-A1")
    assert state.view(NOW - DAY / 2).assets[drill.id].space_id.endswith("F0-S002")  # Assembly zone H-A2


def test_live_changes_in_a_time_window_say_where_things_are_now(state):
    goods_in = Deps(state, who="goods-in")
    yesterday = NOW.replace(hour=9, minute=30) - DAY
    call(
        move_item,
        goods_in,
        "Pallet of steel tubes",
        "Logistics zone H-C5",
        when=yesterday.isoformat(),
        new_item_type="pallet",
    )
    call(
        move_item,
        goods_in,
        "Box of fasteners",
        "Warehouse zone H-C1",
        when=NOW.replace(hour=10).isoformat(),
        new_item_type="box",
    )
    call(move_item, Deps(state, who="forklift"), "Pallet of steel tubes", "Warehouse zone H-C3")
    window = call(
        building_changes,
        goods_in,
        since=(NOW.replace(hour=0) - DAY).isoformat(),
        until=NOW.replace(hour=0).isoformat(),
    )["changes"]
    assert [(c["item"], c["kind"], c["by"]) for c in window] == [
        ("Pallet of steel tubes", "arrived", "goods-in")
    ]
    assert "Warehouse zone H-C3" in window[0]["now_in"]


def test_live_undo_takes_back_only_your_own_change(state):
    maintenance, apprentice = Deps(state, who="maintenance"), Deps(state, who="apprentice")
    call(move_item, maintenance, "Drill press H-A1", "Assembly zone H-A2")
    call(set_closed, apprentice, "Lift 0.18")
    call(undo_last_change, maintenance)
    assert "Production zone H-A1" in found_in(apprentice, "drill press H-A1")[0]
    assert apprentice.closed  # the apprentice's closure is still there


def test_live_benchmark_questions_never_share_state():
    first, second = Deps.load(FACTORY), Deps.load(FACTORY)
    call(set_closed, first, "Lift 0.18")
    assert not second.closed


def test_live_a_scanner_updates_every_chat_and_the_map():
    client = TestClient(main.app)
    try:
        scan = {
            "item": "Pallet of copper wire",
            "place": "Logistics zone H-C5",
            "type": "pallet",
            "by": "dock 2",
        }
        response = client.post(f"/buildings/{FACTORY}/scan", json=scan | {"source": "barcode"})
        assert response.status_code == 200, response.text
        changes = client.get(f"/buildings/{FACTORY}/changes").json()["changes"]
        assert [(c["item"], c["by"], c["source"]) for c in changes] == [
            ("Pallet of copper wire", "dock 2", "barcode")
        ]
        names = {a["name"] for a in client.get(f"/buildings/{FACTORY}/scene").json()["assets"]}
        assert "Pallet of copper wire" in names
        assert (
            client.post(
                f"/buildings/{FACTORY}/scan", json={"item": "nothing like it", "place": "Logistics zone H-C5"}
            ).status_code
            == 400
        )
    finally:
        main._live.clear()


def test_live_a_time_keeps_its_wall_clock_across_a_clock_change(state):
    planner = Deps(state, who="planner")
    due = call(
        move_item,
        planner,
        "Crate of motor parts",
        "Warehouse zone H-C2",
        when="2026-10-26T08:00",
        new_item_type="crate",
    )
    assert due["when"].endswith("08:00")  # Sunday 25 Oct ends summer time in Europe; still 08:00 on Monday
