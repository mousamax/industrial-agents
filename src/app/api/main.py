"""HTTP surface for demos and frontends. Every registered agent/flow shows up automatically.

    GET  /agents            what exists, with JSON schemas of the inputs
    POST /run/{name}        one-shot run: JSON input -> JSON output (agents and flows)
    POST /chat/{name}       streaming chat, Vercel AI SDK protocol (`useChat` in a Next.js/v0 frontend)
    GET  /ui/{name}/        ready-made chat UI for an agent: the zero-effort demo

Run: `uv run uvicorn app.api.main:app --reload` -> http://localhost:8000/docs
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, TypeAdapter
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from starlette.responses import Response

from app import observability
from app.core import AgentCard
from app.registry import Entry, get, registry

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


for _name, _e in registry().items():
    if isinstance(_e.card, AgentCard):
        app.mount(f"/ui/{_name}", _e.card.agent.to_web(deps=_e.card.deps() if _e.card.deps else None))
