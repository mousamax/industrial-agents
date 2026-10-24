"""Creativity add-in "room calendar": rooms are calendar resources, so "where is the hackathon?" finds the room
it's booked in today. Demo bookings live in data/calendars/<building id>.json.

    uv run pytest tests/building/test_room_calendar.py -q
"""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

from app.agents.building_guide.room_calendar import calendar, calendar_context
from app.agents.building_guide.tools import Deps
from app.building.model import load_building
from app.building.state import BuildingState

MUCL = "data/MUCL"


def _ctx(reference: str, hour: int = 14) -> SimpleNamespace:
    at = datetime(2026, 10, 25, hour, 0).astimezone()
    return SimpleNamespace(deps=Deps(BuildingState(load_building(reference), clock=lambda: at)))


def test_calendar_finds_the_hackathon_in_the_arena_on_now():
    out = json.loads(calendar(_ctx(MUCL), "hackathon"))
    assert [e["room_id"] for e in out["events"]] == ["F0-ARENA"]
    assert out["events"][0]["status"] == "on now" and out["highlight"] == ["F0-ARENA"]


def test_calendar_is_on_now_at_any_hour_of_the_demo():
    for hour in (0, 9, 23):
        assert json.loads(calendar(_ctx(MUCL, hour), "hackathon"))["events"][0]["status"] == "on now"


def test_calendar_lists_today_and_says_what_is_on_now_first():
    events = json.loads(calendar(_ctx(MUCL, 14)))["events"]
    assert len(events) == 5 and all(e["room_id"] for e in events)
    on_now = [e["status"] == "on now" for e in events]
    assert on_now == sorted(on_now, reverse=True)


def test_calendar_unknown_event_and_building_without_calendar():
    assert json.loads(calendar(_ctx(MUCL), "cooking class"))["events"] == []
    assert json.loads(calendar(_ctx("proc-campus-dev-000"), "hackathon"))["calendar"] is None


def test_calendar_context_only_where_there_is_a_calendar():
    assert "F0-EING" in calendar_context(_ctx(MUCL).deps)
    assert calendar_context(_ctx("proc-campus-dev-000").deps) == ""
