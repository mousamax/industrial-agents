"""HTTP surface for demos and frontends. Every registered agent/flow shows up automatically.

    GET  /agents            what exists, with JSON schemas of the inputs
    POST /run/{name}        one-shot run: JSON input -> JSON output (agents and flows)
    POST /chat/{name}       streaming chat, Vercel AI SDK protocol (`useChat` in a Next.js/v0 frontend)
    GET  /ui/{name}/        ready-made chat UI for an agent: the zero-effort demo

    Synera UI contract (app/building/view.py):
    GET  /buildings                     ids, names, split, source (filter with ?split=dev&source=procedural)
    GET  /buildings/{id}/scene          floors, space polygons, openings, stairs/lifts, assets, in metres
    POST /buildings/{id}/chat           the building guide for that building, Vercel AI SDK stream

Run: `uv run uvicorn app.api.main:app --reload` -> http://localhost:8000/docs
"""

from __future__ import annotations

import csv
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, TypeAdapter
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from starlette.responses import Response

from app import observability
from app.building import load_building
from app.building.view import Scene, scene
from app.core import AgentCard
from app.registry import Entry, get, registry
from app.settings import get_settings

observability.setup()
app = FastAPI(title="Industrial Agents")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


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


@app.get("/buildings/{building_id}/scene")
async def building_scene(building_id: str) -> Scene:
    try:
        return scene(load_building(building_id))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from None


_sessions: dict[tuple[str, str], Any] = {}
"""One building_guide Deps per (building, chat id): closures and other session state survive between turns.
In memory: a restart starts every conversation fresh, which is fine for a demo."""


@app.post("/buildings/{building_id}/chat")
async def building_chat(building_id: str, request: Request) -> Response:
    """Chat with the building guide about one building (Vercel AI SDK protocol, like /chat/{name}).

    The SDK sends the whole message history every turn; state the tools changed (closures) is kept per
    chat `id` from the request body."""
    card = _entry("building_guide").card
    assert isinstance(card, AgentCard) and card.deps_from_input
    chat_id = str((await request.json()).get("id") or "default")  # Starlette caches the body for dispatch
    key = (building_id, chat_id)
    if key not in _sessions:
        try:
            _sessions[key] = card.deps_from_input(card.coerce({"building": building_id, "question": ""}))
        except FileNotFoundError as e:
            raise HTTPException(404, str(e)) from None
    return await VercelAIAdapter.dispatch_request(request, agent=card.agent, deps=_sessions[key])
