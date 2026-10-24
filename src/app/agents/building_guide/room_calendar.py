"""Creativity add-in "the building's calendar": rooms are calendar resources, like in Google or Outlook, so the
guide can answer "where is the hackathon?" with the room it's booked in today, then guide people there.

Bookings come from data/calendars/<building id>.json (demo data, no calendar account): {"reception": space id
where visitors come in, "events": [{"title", "room": space id, "start": "09:00", "end": "17:00", "organizer",
"description", "public"}]}. Times are clock times on the day the app runs, so a demo always falls on today.
A building without that file has no calendar: the tool says so and the agent's context doesn't mention one.
"""

from __future__ import annotations

import json
from datetime import datetime, time
from functools import cache
from pathlib import Path
from typing import Any

from pydantic_ai import RunContext

from app.settings import get_settings

from .tools import Deps, _json, _label, _matcher


def calendars_dir() -> Path:
    return get_settings().data_dir / "calendars"


@cache
def _read(path: Path, mtime: float) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_calendar(building_id: str) -> dict[str, Any] | None:
    """The building's bookings, or None when it has no calendar."""
    path = calendars_dir() / f"{building_id}.json"
    return _read(path, path.stat().st_mtime) if path.exists() else None


def _at(now: datetime, clock: str) -> datetime:
    return datetime.combine(now.date(), time.fromisoformat(clock), tzinfo=now.tzinfo)


def calendar(ctx: RunContext[Deps], query: str | None = None) -> str:
    """Today's bookings in this building's rooms, from its room calendar (rooms are calendar resources, as in
    Google or Outlook). Use it when someone asks for an event, meeting, talk, course or team rather than a room
    ("where is the hackathon?", "where's the Synera meeting?", "what's on today?"), then guide them to the
    room with `route`. `query`: words from the event's title or organizer; omit to list everything today.
    Each event comes with its room (a space id to pass to route), its times and whether it is on now."""
    b = ctx.deps.building
    booked = load_calendar(b.id)
    if booked is None:
        return _json(
            {"calendar": None, "note": "This building has no room calendar; find the place by name."}
        )
    now = ctx.deps.state.now()
    hit = _matcher(query) if query and query.strip() else None
    events = []
    for e in booked["events"]:
        if e["room"] not in b.spaces or (hit and not hit(e["title"], e.get("organizer", ""))):
            continue
        start, end = _at(now, e["start"]), _at(now, e["end"])
        status = "on now" if start <= now < end else "later today" if now < start else "over for today"
        events.append({
            "event": e["title"], "room": _label(b, e["room"]), "room_id": e["room"],
            "floor": b.floors[b.spaces[e["room"]].floor_id].name,
            **({"when": "all day"} if e["start"] == "00:00" and e["end"] >= "23:59" else
               {"from": e["start"], "until": e["end"]}),
            "status": status, "organizer": e.get("organizer", ""), "about": e.get("description", ""),
            "open_to_visitors": e.get("public", True),
        })  # fmt: skip
    events.sort(key=lambda e: (e["status"] != "on now", e.get("from", "")))
    out: dict[str, Any] = {"today": now.date().isoformat(), "now": now.strftime("%H:%M"), "events": events}
    if not events:
        out["note"] = f"Nothing booked today matches {query!r}." if query else "Nothing booked today."
    out["highlight"] = sorted({e["room_id"] for e in events})
    return _json(out)


def calendar_context(deps: Deps) -> str:
    """One line for the agent, only in a building with a calendar: that it exists and where visitors come in."""
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
    return (
        "This building's rooms have a calendar of today's bookings. When someone asks for an event, meeting, "
        f"talk or team rather than a room, look it up with `calendar`, say whether it is on now, then guide "
        f"them to its room.{where}"
    )
