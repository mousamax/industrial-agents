"""HTTP surface for demos and frontends. Every registered agent/flow shows up automatically.

    GET  /agents            what exists, with JSON schemas of the inputs
    POST /run/{name}        one-shot run: JSON input -> JSON output (agents and flows)
    POST /chat/{name}       streaming chat, Vercel AI SDK protocol (`useChat` in a Next.js/v0 frontend)
    GET  /ui/{name}/        ready-made chat UI for an agent: the zero-effort demo

    Synera UI contract (app/building/view.py):
    GET  /buildings                     ids, names, split, source (filter with ?split=dev&source=procedural)
    GET  /buildings/{id}/scene          floors, space polygons, openings, stairs/lifts, assets, in metres
    POST /buildings/{id}/chat           the building guide for that building, Vercel AI SDK stream
                                        (?location=<space id or room> = where the user is)
    POST /buildings/{id}/scan           "this is now here" from a barcode/RFID reader or another app
    GET  /buildings/{id}/changes        what moved, arrived, closed or opened (?since=&until= ISO times)
    GET  /buildings/{id}/calendar       the room calendar (demo data): this week's events, each room's seats and
                                        equipment, booking requests waiting for the facility manager
    POST /buildings/{id}/calendar/plan  plan an event into free rooms (a request id, or people/day/...)
    POST /buildings/{id}/calendar/book  book the rooms of a plan; the guide finds them at once
    POST /buildings/draft               Level 4: upload floor plans (multipart `plans`, `name`) -> a new
                                        building, chat about it to settle what the plans don't say

    GET  /voice/session, /voice/tts     live voice through ElevenLabs (app/voice/api.py)

Every chat and the scene of one building share one live state (Level 5 "live building"): what one colleague
or scanner reports, the others' answers and the map show at once.

Run: `uv run uvicorn app.api.main:app --reload` -> http://localhost:8000/docs
"""

from __future__ import annotations

import csv
import json
import re
import tempfile
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, TypeAdapter
from pydantic_ai import ModelRetry
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from starlette.responses import Response

from app import observability
from app.agents.building_guide import room_calendar
from app.agents.building_guide.drafting import create_draft, get_draft, list_drafts
from app.agents.building_guide.tools import Deps, _label, _space_id, building_changes, report_item
from app.building import load_building
from app.building.state import BuildingState
from app.building.view import Scene, scene
from app.core import AgentCard
from app.registry import Entry, get, registry
from app.settings import ROOT, get_settings
from app.voice.api import router as voice_router

observability.setup()
app = FastAPI(title="Industrial Agents")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(voice_router)


class AgentInfo(BaseModel):
    name: str
    kind: str
    description: str
    input_schema: dict[str, Any]
    chat_ui: str | None


def _entry(name: str) -> Entry:
    try:
        return get(name)
    except KeyError as e:
        raise HTTPException(404, str(e)) from None


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/agents")
async def list_agents() -> list[AgentInfo]:
    return [
        AgentInfo(
            name=name,
            kind=e.kind,
            description=e.card.description,
            input_schema=TypeAdapter(e.card.input_type).json_schema(),
            chat_ui=f"/ui/{name}/" if isinstance(e.card, AgentCard) else None,
        )
        for name, e in registry().items()
    ]


@app.post("/run/{name}")
async def run(name: str, inputs: dict[str, Any] | str) -> Any:
    entry = _entry(name)
    with observability.entrypoint(name, kind=entry.kind, tags=["api"]):  # type: ignore[arg-type]
        output = await entry.card.run(inputs)
    return output.model_dump(mode="json") if isinstance(output, BaseModel) else output


@app.post("/chat/{name}")
async def chat(name: str, request: Request) -> Response:
    card = _entry(name).card
    if not isinstance(card, AgentCard):
        raise HTTPException(400, f"{name} is a flow; use POST /run/{name}")
    return await VercelAIAdapter.dispatch_request(
        request, agent=card.agent, deps=card.deps() if card.deps else None
    )


# ---- Synera: buildings for the UI (contract: app/building/view.py) --------------------------------------


class BuildingInfo(BaseModel):
    id: str
    name: str
    split: str
    source: str
    floors: int
    has_lift: bool


@app.get("/buildings")
async def list_buildings(split: str | None = None, source: str | None = None) -> list[BuildingInfo]:
    manifest = get_settings().synera_kit_dir / "data" / "manifest.csv"
    with manifest.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    drafts = [
        BuildingInfo(
            id=d.id,
            name=d.state.base.name,
            split="draft",
            source="floor_plans",
            floors=len(d.state.base.floors),
            has_lift=any(c.kind == "lift" for c in d.state.base.connectors.values()),
        )
        for d in list_drafts()
    ]
    return (
        [d for d in drafts if split in (None, "draft") and source in (None, "floor_plans")]
        + [
            BuildingInfo(
                id=r["building_id"],
                name=r["name"],
                split=r["split"],
                source=r["source"],
                floors=int(r["floors"]),
                has_lift=r["has_lift"] == "True",
            )
            for r in rows
            if (split is None or r["split"] == split) and (source is None or r["source"] == source)
        ]
        + [
            BuildingInfo(
                id=b.id,
                name=b.name,
                split=info.get("split", "local"),
                source=b.source,
                floors=len(b.floors),
                has_lift=any(c.kind == "lift" for c in b.connectors.values()),
            )
            for folder, info in _local_buildings().values()
            if (split is None or info.get("split", "local") == split)
            and (source is None or info["source"] == source)
            for b in [load_building(folder)]
        ]
    )


def _local_buildings() -> dict[str, tuple[Path, dict[str, Any]]]:
    """Our own buildings outside the kit (data/<name>/building.json, e.g. MUCL from its floor plans):
    id -> (folder, its "building" header)."""
    found = {}
    for path in sorted((ROOT / "data").glob("*/building.json")):
        info = json.loads(path.read_text(encoding="utf-8"))["building"]
        found[info["id"]] = (path.parent, info)
    return found


_live: dict[str, BuildingState] = {}
"""One live state per building, shared by every chat, scanner and map viewer. In memory: a restart starts
from the plan again, which is fine for a demo."""


def live_state(building_id: str) -> BuildingState:
    if building_id not in _live and (draft := get_draft(building_id)):
        _live[building_id] = draft.state  # drafting tools correct this same state as the person answers
    if building_id not in _live:
        try:
            _live[building_id] = BuildingState(
                load_building(_local_buildings().get(building_id, (building_id,))[0])
            )
        except FileNotFoundError as e:
            raise HTTPException(404, str(e)) from None
    return _live[building_id]


@app.get("/buildings/{building_id}/scene")
async def building_scene(building_id: str) -> Scene:
    """The building as it is now: moved and delivered items where they were last reported."""
    return scene(live_state(building_id).building)


class Drafted(BaseModel):
    building_id: str
    name: str
    floors: int
    rooms: int
    doors: int
    stairs_and_lifts: int
    items: int
    open_questions: int
    seconds: float


@app.post("/buildings/draft")
async def draft_building(
    plans: list[UploadFile] = File(...),  # noqa: B008
    name: str = Form("New building"),
    levels: str | None = Form(None),
) -> Drafted:
    """Level 4: floor plans in, a building out, in seconds. One file per floor: the kit's floor_<level>.svg
    or .png (the level comes from the file name, else `levels` "0,1,2", else the upload order). Then chat
    with POST /buildings/{building_id}/chat: the guide asks about what the plans didn't say."""
    started = time.perf_counter()
    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for i, upload in enumerate(plans):
            filename = Path(upload.filename or f"floor_{i}.svg").name
            if Path(filename).suffix.lower() not in (".svg", ".png"):
                raise HTTPException(400, f"{filename}: send floor plans as .svg or .png")
            path = Path(tmp) / re.sub(r"[^\w.\-]+", "_", filename)
            path.write_bytes(await upload.read())
            paths.append(path)
        try:
            given = [int(x) for x in levels.split(",")] if levels else None
            draft = create_draft(paths, name=name.strip() or "New building", levels=given)
        except (ValueError, NotImplementedError) as e:
            raise HTTPException(400, str(e)) from None
    b = draft.state.base
    _live[draft.id] = draft.state
    return Drafted(
        building_id=draft.id,
        name=b.name,
        floors=len(b.floors),
        rooms=sum(s.type == "room" for s in b.spaces.values()),
        doors=len(b.openings),
        stairs_and_lifts=len(b.connectors),
        items=len(b.assets),
        open_questions=len(draft.open),
        seconds=round(time.perf_counter() - started, 2),
    )


class Scan(BaseModel):
    item: str
    place: str
    type: str | None = None
    """Set for an item the building doesn't know yet (a delivery): pallet, crate, ..."""
    by: str = "scanner"
    source: str = "barcode"
    when: str | None = None
    """ISO time; omit for now. A future time announces an expected delivery."""


@app.post("/buildings/{building_id}/scan")
async def scan(building_id: str, event: Scan) -> dict[str, Any]:
    """A barcode or RFID reader (or any app) reports "this is now here". Every chat and the map see it."""
    state = live_state(building_id)
    try:
        return report_item(
            state,
            event.item,
            event.place,
            at=_time(event.when),
            new_item_type=event.type,
            by=event.by,
            source=event.source,
        )
    except ModelRetry as e:
        raise HTTPException(400, str(e)) from None


@app.get("/buildings/{building_id}/changes")
async def changes(building_id: str, since: str | None = None, until: str | None = None) -> dict[str, Any]:
    deps = Deps(live_state(building_id))
    try:
        return json.loads(building_changes(SimpleNamespace(deps=deps), since, until))  # type: ignore[arg-type]
    except ModelRetry as e:
        raise HTTPException(400, str(e)) from None


@app.get("/buildings/{building_id}/calendar")
async def building_calendar(building_id: str) -> dict[str, Any]:
    """The building's room calendar, as the app shows it after "Connect calendar": every room's bookings for
    the next seven days with seats and equipment, and the booking requests waiting to be planned."""
    deps = Deps(live_state(building_id))
    b, booked = deps.building, room_calendar.load_calendar(building_id)
    if booked is None:
        return {"connected": False}
    week = json.loads(room_calendar.calendar(SimpleNamespace(deps=deps), day="week"))  # type: ignore[arg-type]
    rooms = {
        rid: {"room": _label(b, rid), "floor_id": b.spaces[rid].floor_id, "seats": r["capacity"],
              "equipment": r.get("equipment", [])}
        for rid, r in booked.get("resources", {}).items() if rid in b.spaces
    }  # fmt: skip
    return {
        "connected": True, "calendar": booked.get("calendar"), "source": booked.get("source"),
        "today": week["today"], "now": week["now"], "reception": booked.get("reception"), "rooms": rooms,
        "events": week["events"], "requests": room_calendar.requests_for(b, booked, deps.state.now().date()),
    }  # fmt: skip


class PlanRequest(BaseModel):
    request_id: str | None = None
    """A waiting booking request (GET /calendar `requests`); or give the fields below."""
    title: str | None = None
    people: int | None = None
    day: str | None = None
    start: str = "09:00"
    end: str = "18:00"
    teams: int | None = None
    needs: list[str] = []


@app.post("/buildings/{building_id}/calendar/plan")
async def plan_calendar_event(building_id: str, ask: PlanRequest) -> dict[str, Any]:
    """The same plan the agent's `plan_event` tool makes, without a model call: instant, for the app."""
    deps = Deps(live_state(building_id))
    fields = ask.model_dump(exclude={"request_id"})
    if ask.request_id:
        booked = room_calendar.load_calendar(building_id) or {}
        found = [r for r in booked.get("requests", []) if r["id"] == ask.request_id]
        if not found:
            raise HTTPException(404, f"No booking request {ask.request_id!r}")
        r = found[0]
        fields = {"title": r["title"], "people": r["people"], "day": r["day"], "start": r["start"],
                  "end": r["end"], "teams": r.get("teams"), "needs": r.get("needs", [])}  # fmt: skip
    if not fields.get("people") or not fields.get("day"):
        raise HTTPException(400, "Give request_id, or people and day.")
    try:
        return json.loads(room_calendar.plan_event(SimpleNamespace(deps=deps), **fields))  # type: ignore[arg-type]
    except ModelRetry as e:
        raise HTTPException(400, str(e)) from None


@app.post("/buildings/{building_id}/calendar/book")
async def book_calendar_plan(building_id: str, plan: dict[str, Any]) -> dict[str, Any]:
    """Book the rooms of a plan (the body is a /calendar/plan result). Kept in memory for this run."""
    if not plan.get("plan") or "request" not in plan:
        raise HTTPException(400, "The body must be a /calendar/plan result.")
    events = room_calendar.book(live_state(building_id).building, plan)
    return {"booked": len(events), "events": events, "highlight": [e["room"] for e in events]}


def _time(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        t = datetime.fromisoformat(text)
    except ValueError:
        raise HTTPException(400, f"when must be an ISO time, not {text!r}") from None
    return t if t.tzinfo else t.astimezone()


_sessions: dict[tuple[str, str], Any] = {}
"""One building_guide Deps per (building, chat id): the pending edit and where the person is survive between
turns; the building state itself is the shared live one."""


def _new_session(building_id: str, chat_id: str) -> Deps:
    """A person in the app (text chat and voice call): walking directions, and routes may go round the
    outside when that's shorter. Never used for anything scored (the benchmark and Sunday run use Deps.load)."""
    return Deps(live_state(building_id), who=f"chat {chat_id[:8]}", detail="walk", outside_walking=True)


@app.post("/buildings/{building_id}/chat")
async def building_chat(
    building_id: str, request: Request, location: str | None = None, who: str | None = None
) -> Response:
    """Chat with the building guide about one building (Vercel AI SDK protocol, like /chat/{name}).

    The SDK sends the whole message history every turn; the pending edit and location are kept per chat
    `id` from the request body, the building state is shared with every colleague. `?location=<space id or
    room name>` tells the agent where the user is (QR code at a door, a tap on the map); send it whenever it
    changes. `?who=<name>` is stamped on the changes this person reports (default: the chat id). Plan edits
    apply at once; a turn whose body has `"confirmEdits": true` (the voice call) proposes them for a yes."""
    card = _entry("building_guide").card
    assert isinstance(card, AgentCard) and card.deps_from_input
    body = await request.json()  # Starlette caches the body for dispatch
    chat_id = str(body.get("id") or "default")
    key = (building_id, chat_id)
    if key not in _sessions:
        _sessions[key] = _new_session(building_id, chat_id)
    if who:
        _sessions[key].who = who
    _sessions[key].confirm_edits = bool(body.get("confirmEdits"))  # per turn: the voice call asks first
    if location:
        try:
            _sessions[key].here = _space_id(_sessions[key].building, location)
        except ModelRetry as e:
            raise HTTPException(400, str(e)) from None
    return await VercelAIAdapter.dispatch_request(request, agent=card.agent, deps=_sessions[key])
