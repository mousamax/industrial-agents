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

    GET  /voice/session, /voice/tts     live voice through ElevenLabs (app/voice/api.py)

Every chat and the scene of one building share one live state (Level 5 "live building"): what one colleague
or scanner reports, the others' answers and the map show at once.

Run: `uv run uvicorn app.api.main:app --reload` -> http://localhost:8000/docs
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, TypeAdapter
from pydantic_ai import ModelRetry
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from starlette.responses import Response

from app import observability
from app.agents.building_guide.tools import Deps, _space_id, building_changes, report_item
from app.building import load_building
from app.building.state import BuildingState
from app.building.view import Scene, scene
from app.core import AgentCard
from app.registry import Entry, get, registry
from app.settings import get_settings
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
    return [
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


_live: dict[str, BuildingState] = {}
"""One live state per building, shared by every chat, scanner and map viewer. In memory: a restart starts
from the plan again, which is fine for a demo."""


def live_state(building_id: str) -> BuildingState:
    if building_id not in _live:
        try:
            _live[building_id] = BuildingState(load_building(building_id))
        except FileNotFoundError as e:
            raise HTTPException(404, str(e)) from None
    return _live[building_id]


@app.get("/buildings/{building_id}/scene")
async def building_scene(building_id: str) -> Scene:
    """The building as it is now: moved and delivered items where they were last reported."""
    return scene(live_state(building_id).building)


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


@app.post("/buildings/{building_id}/chat")
async def building_chat(
    building_id: str, request: Request, location: str | None = None, who: str | None = None
) -> Response:
    """Chat with the building guide about one building (Vercel AI SDK protocol, like /chat/{name}).

    The SDK sends the whole message history every turn; the pending edit and location are kept per chat
    `id` from the request body, the building state is shared with every colleague. `?location=<space id or
    room name>` tells the agent where the user is (QR code at a door, a tap on the map); send it whenever it
    changes. `?who=<name>` is stamped on the changes this person reports (default: the chat id)."""
    card = _entry("building_guide").card
    assert isinstance(card, AgentCard) and card.deps_from_input
    chat_id = str((await request.json()).get("id") or "default")  # Starlette caches the body for dispatch
    key = (building_id, chat_id)
    if key not in _sessions:
        _sessions[key] = Deps(live_state(building_id), who=f"chat {chat_id[:8]}")
    if who:
        _sessions[key].who = who
    if location:
        try:
            _sessions[key].here = _space_id(_sessions[key].building, location)
        except ModelRetry as e:
            raise HTTPException(400, str(e)) from None
    return await VercelAIAdapter.dispatch_request(request, agent=card.agent, deps=_sessions[key])
