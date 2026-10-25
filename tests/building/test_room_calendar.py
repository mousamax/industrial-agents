"""Creativity add-in "room calendar": rooms are calendar resources, so "where is the hackathon?" finds the room
it's booked in today. Demo bookings live in data/calendars/<building id>.json.

    uv run pytest tests/building/test_room_calendar.py -q
"""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

from app.agents.building_guide import room_calendar
from app.agents.building_guide.room_calendar import calendar, calendar_context, plan_event
from app.agents.building_guide.tools import Deps
from app.building.model import load_building
from app.building.state import BuildingState

MUCL = "data/MUCL"


def _ctx(reference: str, hour: int = 14) -> SimpleNamespace:
    at = datetime(2026, 10, 25, hour, 0).astimezone()
    return SimpleNamespace(deps=Deps(BuildingState(load_building(reference), clock=lambda: at)))


def test_calendar_finds_the_hackathon_in_the_open_office_on_now():
    out = json.loads(calendar(_ctx(MUCL), "hackathon"))
    assert [e["room_id"] for e in out["events"]] == ["F3-OPEN-S"]
    assert out["events"][0]["status"] == "on now" and out["highlight"] == ["F3-OPEN-S"]


def test_calendar_is_on_now_at_any_hour_of_the_demo():
    for hour in (0, 9, 23):
        assert json.loads(calendar(_ctx(MUCL, hour), "hackathon"))["events"][0]["status"] == "on now"


def test_calendar_lists_today_and_says_what_is_on_now_first():
    events = json.loads(calendar(_ctx(MUCL, 14)))["events"]
    assert len(events) == 8 and all(e["room_id"] for e in events)
    on_now = [e["status"] == "on now" for e in events]
    assert on_now == sorted(on_now, reverse=True)


def test_calendar_unknown_event_and_building_without_calendar():
    assert json.loads(calendar(_ctx(MUCL), "cooking class"))["events"] == []
    assert json.loads(calendar(_ctx("proc-campus-dev-000"), "hackathon"))["calendar"] is None


def test_calendar_context_only_where_there_is_a_calendar():
    assert "F0-EING" in calendar_context(_ctx(MUCL).deps)
    assert calendar_context(_ctx("proc-campus-dev-000").deps) == ""


def test_calendar_finds_a_rooms_bookings_on_a_weekday():
    out = json.loads(calendar(_ctx(MUCL), day="Saturday", room="Seminarraum 2"))
    assert out["day"] == "2026-10-31" and out["seats"] == 40
    assert [e["event"] for e in out["events"]] == ["Founder bootcamp"]
    assert json.loads(calendar(_ctx(MUCL), day="Saturday", room="Seminarraum 3"))["note"].endswith("free.")


def test_calendar_week_lists_the_next_seven_days_in_order():
    events = json.loads(calendar(_ctx(MUCL), day="week"))["events"]
    assert len(events) == 26 and [e["date"] for e in events] == sorted(e["date"] for e in events)


def _hackathon(ctx) -> dict:
    return json.loads(
        plan_event(
            ctx, 60, "saturday", "09:00", "21:00", "Hackathon", 12, ["projector", "screen", "catering"]
        )
    )


def test_plan_event_avoids_booked_rooms_and_seats_every_team_nearby():
    out = _hackathon(_ctx(MUCL))
    roles = {p["role"]: p for p in out["plan"]}
    assert roles["main"]["room_id"] == "F1-ARENA" and roles["main"]["seats"] >= 66
    breakouts = [p for p in out["plan"] if p["role"] == "breakout"]
    assert sum(p["teams"] for p in breakouts) == 12 and all(p["walk_m"] <= 60 for p in breakouts)
    assert {"kitchen", "toilets", "catering"} <= roles.keys()
    avoided = {a["room_id"] for a in out["avoided"]}
    assert {"F0-FOYER", "F1-SEM2"} <= avoided and not avoided & set(out["highlight"])


def test_plan_event_says_when_nothing_fits_and_booking_takes_the_rooms():
    ctx = _ctx(MUCL)
    assert json.loads(plan_event(ctx, 500, "saturday"))["plan"] == []
    room_calendar._booked_here.clear()
    try:
        assert room_calendar.requests_for(
            ctx.deps.building, room_calendar.load_calendar("munich-urban-colab"), ctx.deps.state.now().date()
        )
        room_calendar.book(ctx.deps.building, _hackathon(ctx))
        saturday = json.loads(calendar(ctx, "hackathon", day="saturday"))["events"]
        assert {e["room_id"] for e in saturday} >= {"F1-ARENA", "F1-SEM3"}
        again = _hackathon(ctx)
        assert again["plan"] == [] and "F1-ARENA" in {a["room_id"] for a in again["avoided"]}
    finally:
        room_calendar._booked_here.clear()


def test_calendar_context_mentions_the_waiting_request():
    assert "Industrial AI Hackathon" in calendar_context(_ctx(MUCL).deps)
