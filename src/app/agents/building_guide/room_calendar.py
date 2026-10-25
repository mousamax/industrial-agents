"""Creativity add-in "the building's calendar": rooms are calendar resources, like in Google or Outlook, so the
guide can answer "where is the hackathon?" with the room it's booked in, and plan a new event into rooms that
are free, big enough, equipped, and close to a kitchen and toilets, as a facility manager would.

Bookings come from data/calendars/<building id>.json (demo data, no calendar account):
    {"reception": space id where visitors come in,
     "resources": {space id: {"capacity": seats, "equipment": ["projector", "screen", ...]}},
     "events": [{"title", "room": space id, "day", "start": "09:00", "end": "17:00", "organizer", "description",
                 "public"}],
     "requests": [{"id", "title", "from", "day", "start", "end", "people", "teams", "needs", "message"}]}
`day` is a weekday (its next date, today included) or a number of days from today; without it, today. So the
demo week always starts on the day the app runs. A building without that file has no calendar: the tools say
so and the agent's context doesn't mention one.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, time, timedelta
from functools import cache
from pathlib import Path
from typing import Any

from pydantic_ai import ModelRetry, RunContext

from app.building.model import Building
from app.settings import get_settings

from .graph import route_graph
from .tools import Deps, _constraints, _json, _label, _matcher, _space_id

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MAX_TEAMS_PER_ROOM = 4
"""Teams sharing one breakout room at most, so they can still hear themselves."""
HEADROOM = 1.1
"""Seats the main room needs per person: everyone, plus a tenth for guests, jury and late sign-ups."""


def calendars_dir() -> Path:
    return get_settings().data_dir / "calendars"


@cache
def _read(path: Path, mtime: float) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_calendar(building_id: str) -> dict[str, Any] | None:
    """The building's bookings, or None when it has no calendar."""
    path = calendars_dir() / f"{building_id}.json"
    return _read(path, path.stat().st_mtime) if path.exists() else None


def date_of(today: date, day: str | int | None) -> date:
    """A calendar `day` (None today, a weekday's next date, days from today) or what a person says ("today",
    "tomorrow", "Saturday", "2026-10-31") as a date."""
    if day is None:
        return today
    if isinstance(day, int):
        return today + timedelta(days=day)
    if isinstance(day, date):
        return day
    text = day.strip().casefold().removeprefix("next ").removeprefix("this ")
    if text in ("", "today", "now"):
        return today
    if text == "tomorrow":
        return today + timedelta(days=1)
    for i, name in enumerate(WEEKDAYS):
        if text in (name, name[:3]):
            return today + timedelta(days=(i - today.weekday()) % 7)
    try:
        return date.fromisoformat(text)
    except ValueError:
        if text.lstrip("+-").isdigit():
            return today + timedelta(days=int(text))
        raise ModelRetry(f"Give day as today, tomorrow, a weekday or YYYY-MM-DD, not {day!r}.") from None


def _clock(text: str) -> time:
    try:
        return time.fromisoformat(text)
    except ValueError:
        raise ModelRetry(f"Give times as HH:MM, not {text!r}.") from None


def _overlaps(e: dict, start: str, end: str) -> bool:
    return _clock(e["start"]) < _clock(end) and _clock(start) < _clock(e["end"])


_booked_here: dict[str, list[dict[str, Any]]] = {}
"""Bookings made in this app run (a plan the facility manager accepted), per building id: kept in memory, on
top of the calendar file, so the guide finds a new booking at once."""


def book(b: Building, plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Book the rooms of a `plan_event` result: one event per room on the request's date and times."""
    r = plan["request"]
    title = r.get("title") or "Event"
    events = [
        {"title": title if p["role"] == "main" else f"{title}: {p['role']}", "room": p["room_id"],
         "day": r["date"], "start": r["from"], "end": r["until"], "organizer": r.get("organizer", ""),
         "description": p.get("why", ""), "public": True}
        for p in plan["plan"] if p["role"] in ("main", "breakout", "catering")
    ]  # fmt: skip
    _booked_here.setdefault(b.id, []).extend(events)
    return events


def dated_events(b: Building, booked: dict[str, Any], today: date) -> list[tuple[date, dict]]:
    """Every booking in a room of this building, with its date."""
    events = [*booked["events"], *_booked_here.get(b.id, [])]
    return [(date_of(today, e.get("day")), e) for e in events if e["room"] in b.spaces]


def _event(b: Building, on: date, e: dict, now: datetime) -> dict[str, Any]:
    start = datetime.combine(on, _clock(e["start"]), tzinfo=now.tzinfo)
    end = datetime.combine(on, _clock(e["end"]), tzinfo=now.tzinfo)
    status = "on now" if start <= now < end else "upcoming" if now < start else "over"
    if on == now.date():
        status = {"upcoming": "later today", "over": "over for today"}.get(status, status)
    return {
        "event": e["title"], "room": _label(b, e["room"]), "room_id": e["room"],
        "floor": b.floors[b.spaces[e["room"]].floor_id].name, "date": on.isoformat(),
        "weekday": WEEKDAYS[on.weekday()].title(),
        **({"when": "all day"} if e["start"] == "00:00" and e["end"] >= "23:59" else
           {"from": e["start"], "until": e["end"]}),
        "status": status, "organizer": e.get("organizer", ""), "about": e.get("description", ""),
        "open_to_visitors": e.get("public", True),
    }  # fmt: skip


def calendar(
    ctx: RunContext[Deps], query: str | None = None, day: str | None = None, room: str | None = None
) -> str:
    """Bookings in this building's rooms, from its room calendar (rooms are calendar resources, as in Google or
    Outlook). Use it when someone asks for an event, meeting, talk, course or team rather than a room
    ("where is the hackathon?", "where's the Synera meeting?", "what's on today?"), then guide them to the
    room with `route`; and for a room's schedule or whether it is free ("is Seminarraum 2 free on Saturday?").

    Args:
        query: Words from the event's title or organizer; omit to list everything on that day.
        day: "today" (the default), "tomorrow", a weekday ("Saturday", its next date) or YYYY-MM-DD; "week"
            for the next seven days.
        room: Only this room's bookings (its schedule).
    Each event comes with its room (a space id to pass to route), its date and times and whether it is on now.
    """
    b = ctx.deps.building
    booked = load_calendar(b.id)
    if booked is None:
        return _json(
            {"calendar": None, "note": "This building has no room calendar; find the place by name."}
        )
    now = ctx.deps.state.now()
    week = (day or "").strip().casefold() in ("week", "this week", "next 7 days")
    on = None if week else date_of(now.date(), day)
    only = _space_id(b, room) if room else None
    hit = _matcher(query) if query and query.strip() else None
    events = [
        _event(b, d, e, now)
        for d, e in dated_events(b, booked, now.date())
        if (d == on if on else now.date() <= d < now.date() + timedelta(days=7))
        and (only is None or e["room"] == only)
        and not (hit and not hit(e["title"], e.get("organizer", "")))
    ]
    events.sort(key=lambda e: (e["date"], e["status"] != "on now", e.get("from", "")))
    out: dict[str, Any] = {
        "today": now.date().isoformat(), "now": now.strftime("%H:%M"),
        **({"days": "next 7 days"} if week else {"day": on.isoformat(), "weekday": WEEKDAYS[on.weekday()].title()}),
        **({"room": _label(b, only), **_resource(booked, only)} if only else {}),
        "events": events,
    }  # fmt: skip
    if not events:
        what = f"matches {query!r}" if query else "is booked"
        out["note"] = f"Nothing {what} {'in the next 7 days' if week else 'that day'}" + (
            ": the room is free." if only and not query else "."
        )
    out["highlight"] = sorted({e["room_id"] for e in events} | ({only} if only else set()))
    return _json(out)


def _resource(booked: dict[str, Any], room: str) -> dict[str, Any]:
    r = booked.get("resources", {}).get(room)
    return {"seats": r["capacity"], "equipment": r.get("equipment", [])} if r else {}


def _has(resource: dict, need: str) -> bool:
    want = need.strip().casefold().rstrip("s").replace(" ", "_")
    return any(want == e.casefold().rstrip("s") for e in resource.get("equipment", []))


def plan_event(
    ctx: RunContext[Deps],
    people: int,
    day: str,
    start: str = "09:00",
    end: str = "18:00",
    title: str | None = None,
    teams: int | None = None,
    needs: list[str] | None = None,
    max_walk_m: float = 60.0,
) -> str:
    """Plan an event into this building's rooms the way a facility manager would: a main room free that day
    and time with enough seats and the equipment asked for, breakout rooms for the teams within a short
    step-free walk of it (its own floor first), the nearest kitchen and toilets, a canteen for catering, and a step-free walk from
    the reception. Use it when someone asks where to hold an event, a workshop or a hackathon, or to plan a
    booking request. It books nothing: present the plan and its reasons, and say what it avoided.

    Args:
        people: How many people come.
        day: "today", "tomorrow", a weekday ("Saturday") or YYYY-MM-DD.
        start: HH:MM.
        end: HH:MM.
        title: The event's name.
        teams: How many teams work in parallel (a hackathon, a workshop), when they need breakout rooms.
        needs: Equipment the main room needs ("projector", "screen", "microphones", "video_call"), and
            "catering" for a room to eat in.
        max_walk_m: How far a breakout room may be from the main room.
    """
    b = ctx.deps.building
    booked = load_calendar(b.id)
    if booked is None:
        return _json({"calendar": None, "note": "This building has no room calendar to plan against."})
    resources = {k: v for k, v in booked.get("resources", {}).items() if k in b.spaces}
    now = ctx.deps.state.now()
    on = date_of(now.date(), day)
    needs = [n for n in (needs or []) if n.strip()]
    equipment = [n for n in needs if n.strip().casefold() != "catering"]
    clashes: dict[str, list[dict]] = {}
    for d, e in dated_events(b, booked, now.date()):
        if d == on and _overlaps(e, start, end):
            clashes.setdefault(e["room"], []).append(e)

    def busy(room: str) -> str | None:
        return "; ".join(f"{e['title']} {e['start']}–{e['end']}" for e in clashes.get(room, [])) or None

    graph = route_graph(b, _constraints(ctx, step_free=True, min_width_m=None))
    seats = math.ceil(people * HEADROOM)
    fits = sorted(
        (r for r, v in resources.items() if v["capacity"] >= seats and all(_has(v, n) for n in equipment)),
        key=lambda r: resources[r]["capacity"],  # the smallest room that fits
    )
    reception = booked.get("reception")
    from_reception = graph.distances_from([reception]) if reception in b.spaces else {}
    mains = [r for r in fits if not busy(r) and (not from_reception or r in from_reception)]
    avoided = [{"room": _label(b, r), "room_id": r, "why": busy(r)} for r in fits if busy(r)]
    request = {
        "title": title, "date": on.isoformat(), "weekday": WEEKDAYS[on.weekday()].title(),
        "from": start, "until": end, "people": people, **({"teams": teams} if teams else {}), "needs": needs,
    }  # fmt: skip
    if not mains:
        return _json({
            "request": request, "plan": [], "avoided": avoided,
            "note": f"No room with {seats} seats{' and ' + ', '.join(equipment) if equipment else ''} is free "
            "then. Suggest another day or time, or fewer people.",
        })  # fmt: skip
    main = mains[0]
    walk = graph.distances_from([main])

    def row(role: str, room: str, why: str, **extra: Any) -> dict[str, Any]:
        s = b.spaces[room]
        return {"role": role, "room_id": room, "room": _label(b, room), "floor": b.floors[s.floor_id].name,
                "why": why, **extra}  # fmt: skip

    def kit(room: str) -> str:
        return ", ".join(resources[room].get("equipment", [])) or "no equipment listed"

    plan = [row("main", main, f"Free then, {resources[main]['capacity']} seats, {kit(main)}",
                seats=resources[main]["capacity"])]  # fmt: skip
    seated = 0
    if teams:
        size = math.ceil(people / teams)
        screens = any(_has({"equipment": [n]}, "screen") for n in equipment)
        floor = b.spaces[main].floor_id  # the main room's floor first: teams stay together
        near = sorted(
            (b.spaces[r].floor_id != floor, walk[r], r) for r, v in resources.items()
            if r != main and r in walk and walk[r] <= max_walk_m and size <= v["capacity"] <= people
            and not (screens and not (_has(v, "screen") or _has(v, "projector")))
        )  # fmt: skip
        for _, m, r in near:
            if seated >= teams:
                break
            if why := busy(r):
                avoided.append({"room": _label(b, r), "room_id": r, "why": why})
                continue
            n = min(MAX_TEAMS_PER_ROOM, resources[r]["capacity"] // size, teams - seated)
            seated += n
            plan.append(row("breakout", r, f"Free, {n} team{'s' * (n > 1)}, {kit(r)}, {m:.0f} m from the main room",
                            teams=n, walk_m=round(m, 1)))  # fmt: skip

    def closest(role: str, room_types: set[str], free_only: bool = False) -> None:
        ranked = sorted(
            (walk[s.id], s.id) for s in b.spaces.values()
            if s.room_type in room_types and s.id in walk and not (free_only and busy(s.id))
        )  # fmt: skip
        if ranked:
            m, r = ranked[0]
            plan.append(row(role, r, f"{m:.0f} m from the main room, step-free", walk_m=round(m, 1)))

    if any(n.strip().casefold() == "catering" for n in needs):
        closest("catering", {"canteen"}, free_only=True)
    closest("kitchen", {"kitchen"})
    closest("toilets", {"toilet"})
    checked = [
        f"free on {request['weekday']} {start}–{end}",
        f"{resources[main]['capacity']} seats for {people} people plus 10%",
    ]
    if equipment:
        checked.append(f"{', '.join(equipment)} in the main room")
    if teams:
        checked.append(f"{seated} of {teams} teams in breakouts within {max_walk_m:.0f} m")
    checked.append("kitchen and toilets nearby")
    if from_reception:
        checked.append(f"step-free from {_label(b, reception)}, {from_reception[main]:.0f} m")
    return _json({
        "request": request, "plan": plan, "checked": checked, "avoided": avoided,
        **({"note": f"Only {seated} of {teams} teams fit in free breakout rooms within {max_walk_m:.0f} m."}
           if teams and seated < teams else {}),
        "booked": False,
        "highlight": [p["room_id"] for p in plan],
    })  # fmt: skip


def requests_for(b: Building, booked: dict[str, Any], today: date) -> list[dict[str, Any]]:
    """Booking requests waiting for the facility manager, with their dates."""
    out, done = [], {e["title"] for e in _booked_here.get(b.id, [])}
    for r in booked.get("requests", []):
        if r["title"] in done:
            continue
        on = date_of(today, r.get("day"))
        out.append({**r, "date": on.isoformat(), "weekday": WEEKDAYS[on.weekday()].title()})
    return out


def calendar_context(deps: Deps) -> str:
    """For the agent, only in a building with a calendar: that it exists, where visitors come in, and the
    booking requests waiting to be planned."""
    booked = load_calendar(deps.building.id)
    if booked is None:
        return ""
    reception = booked.get("reception")
    where = (
        f" A visitor who doesn't say where they are has just come in at {_label(deps.building, reception)} "
        f"[{reception}]: guide them from there and say so."
        if reception in deps.building.spaces
        else ""
    )
    asks = [
        f"{r['title']} from {r['from']} ({r['weekday']} {r['date']}, {r['start']}–{r['end']}, {r['people']} "
        f"people{', ' + str(r['teams']) + ' teams' if r.get('teams') else ''}, needs {', '.join(r['needs'])}): "
        f'"{r["message"]}"'
        for r in requests_for(deps.building, booked, deps.state.now().date())
    ]
    waiting = (
        " Booking requests waiting for the facility manager: " + " | ".join(asks) + ". When they ask to plan "
        "one, call `plan_event` with its details and present the plan room by room with the reasons."
        if asks
        else ""
    )
    return (
        "This building's rooms have a calendar of this week's bookings, with each room's seats and equipment. "
        "When someone asks for an event, meeting, talk or team rather than a room, look it up with `calendar`, "
        "say whether it is on now, then guide them to its room. To find rooms for a new event, use "
        f"`plan_event`.{where}{waiting}"
    )
