"""The ElevenLabs voice agent, defined in code and synced to the ElevenLabs workspace on first use.

ElevenLabs runs the conversation (speech recognition, turn-taking, barge-in, the voice); the browser runs
the client tools below, and `ask_building_guide` hands every building question to our `building_guide`
agent, so facts still come from its tools. Only ELEVENLABS_API_KEY is needed: the agent and its tools are
created (or updated to match this file and `prompt.md`) the first time a session starts.

    uv run python -m app.voice.elevenlabs     # sync now, print the agent id and its dashboard link
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx

from app.settings import get_settings

API = "https://api.elevenlabs.io"
AGENT_NAME = "Talk to Your Building"
TTS_MODEL = "eleven_v4_turbo"
"""ElevenLabs' fastest model, 90+ languages: used for the live agent and for "Read aloud"."""

PROMPT = Path(__file__).with_name("prompt.md")


class VoiceNotConfigured(RuntimeError):
    """ELEVENLABS_API_KEY is missing; the web app falls back to the browser's own speech."""


class ElevenLabsError(RuntimeError):
    pass


def _client_tool(name: str, description: str, params: dict[str, str] | None = None, **extra: Any) -> dict:
    tool: dict[str, Any] = {
        "type": "client",
        "name": name,
        "description": description,
        "expects_response": True,
        "response_timeout_secs": 10,
        "pre_tool_speech": "off",
    }
    if params:
        tool["parameters"] = {
            "type": "object",
            "properties": {k: {"type": "string", "description": v} for k, v in params.items()},
            "required": list(params),
        }
    return tool | extra


TOOLS: list[dict[str, Any]] = [
    _client_tool(
        "ask_building_guide",
        "Ask the building guide, which has the building's plan and live state, anything about the building: "
        "where a room or item is, routes and distances, step-free or forklift routes, counts and sizes, "
        "what-ifs, and reports of changes (an item moved, a lift closed). Returns the guide's answer, any "
        "question it asks back with its options, and the route if there is one. The map on screen updates.",
        {
            "request": "The person's request in their own words, plus context the guide needs from earlier "
            "in the call (where they are, wheelchair or forklift, which option they chose)."
        },
        response_timeout_secs=110,
        pre_tool_speech="force",
        tool_call_sound="typing",
    ),
    _client_tool(
        "start_walk",
        "Start step-by-step guidance along the route the guide just returned. Returns the first step.",
    ),
    _client_tool(
        "walk_step",
        "Move through the walk: 'next' when they say next, okay or I'm there; 'previous' to go back; "
        "'repeat' to say the current step again. Returns that step and the distance left.",
        {"direction": "One of: next, previous, repeat."},
    ),
    _client_tool(
        "apply_change",
        "Undo the last change the guide made to the building (a new door, a closure), when the person says "
        "'undo that'. The guide makes changes at once, so there is nothing to confirm. Returns what the guide "
        "undid.",
        {"decision": "Always: undo."},
        response_timeout_secs=110,
        tool_call_sound="typing",
    ),
    _client_tool(
        "show_floor",
        "Switch the map on screen to a floor the person asks to see.",
        {"floor": "The floor as the person said it: 'ground floor', 'level 2', 'basement'."},
    ),
    _client_tool(
        "show_on_screen",
        "Bring an earlier answer from this call back onto the screen: a route, new doors, a closure. Use it "
        "when the person wants to see it again. Returns what is on screen now; if nothing matches, the list of what was shown in this call. "
        "Something new is not here: ask the guide, and its answer appears on screen by itself.",
        {
            "what": "What to show, in the person's words: 'the doors', 'the route to the canteen', 'the last route'."
        },
    ),
    _client_tool("start_over", "Clear the screen for a new topic, when the person asks to start over."),
]

CLIENT_EVENTS = [
    "conversation_initiation_metadata",
    "audio",
    "interruption",
    "user_transcript",
    "tentative_user_transcript",
    "agent_response",
    "agent_response_correction",
    "agent_chat_response_part",
    "client_tool_call",
    "vad_score",
    "ping",
]


def agent_spec(tool_ids: list[str]) -> dict[str, Any]:
    s = get_settings()
    return {
        "name": AGENT_NAME,
        "tags": ["industrial-agents", "synera"],
        "conversation_config": {
            "agent": {
                "first_message": "Hi, I'm the guide for {{building_name}}. Where do you need to go?",
                "language": "en",
                "dynamic_variables": {
                    "dynamic_variable_placeholders": {
                        "building_name": "this building",
                        "floors": "unknown",
                        "location": "not known yet",
                        "preferences": "nothing yet",
                    }
                },
                "prompt": {
                    "prompt": PROMPT.read_text(encoding="utf-8"),
                    "llm": s.elevenlabs_llm,
                    "reasoning_effort": "none",
                    "temperature": None,
                    "tool_ids": tool_ids,
                    "built_in_tools": {
                        "end_call": {
                            "name": "end_call",
                            "description": "",
                            "params": {"system_tool_type": "end_call"},
                        }
                    },
                },
            },
            "tts": {"model_id": TTS_MODEL, "voice_id": s.elevenlabs_voice_id},
            "turn": {"turn_eagerness": "normal"},
            "conversation": {"client_events": CLIENT_EVENTS, "max_duration_seconds": 900},
        },
        "platform_settings": {
            "auth": {"enable_auth": True},
            "overrides": {
                "conversation_config_override": {
                    "agent": {"first_message": True},
                    "asr": {"keywords": True},
                    "tts": {"voice_id": True},
                }
            },
        },
    }


def _key() -> str:
    key = get_settings().elevenlabs_api_key
    if not key:
        raise VoiceNotConfigured("Set ELEVENLABS_API_KEY in .env for live voice.")
    return key


def _http(timeout: float = 20) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=API, headers={"xi-api-key": _key()}, timeout=timeout)


async def _call(client: httpx.AsyncClient, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    response = await client.request(method, path, **kwargs)
    if response.is_error:
        raise ElevenLabsError(f"ElevenLabs {method} {path}: {response.status_code} {response.text[:500]}")
    return response.json() if response.content else {}


_synced: tuple[str, str] | None = None
"""(hash of the synced spec, agent id): sync again only when `prompt.md`, the tools or settings change."""
_lock = asyncio.Lock()


async def ensure_agent() -> str:
    """The id of our ElevenLabs agent, created or updated to match this file and `prompt.md`."""
    global _synced
    fingerprint = hashlib.sha256(json.dumps([TOOLS, agent_spec([])], sort_keys=True).encode()).hexdigest()
    if _synced and _synced[0] == fingerprint:
        return _synced[1]
    async with _lock, _http() as client:
        if _synced and _synced[0] == fingerprint:
            return _synced[1]
        existing = {
            t["tool_config"]["name"]: t["id"]
            for t in (await _call(client, "GET", "/v1/convai/tools"))["tools"]
        }
        tool_ids = []
        for tool in TOOLS:
            if tool["name"] in existing:
                await _call(
                    client, "PATCH", f"/v1/convai/tools/{existing[tool['name']]}", json={"tool_config": tool}
                )
                tool_ids.append(existing[tool["name"]])
            else:
                tool_ids.append(
                    (await _call(client, "POST", "/v1/convai/tools", json={"tool_config": tool}))["id"]
                )

        spec = agent_spec(tool_ids)
        agent_id = get_settings().elevenlabs_agent_id
        if not agent_id:
            found = await _call(
                client, "GET", "/v1/convai/agents", params={"search": AGENT_NAME, "page_size": 100}
            )
            agent_id = next((a["agent_id"] for a in found["agents"] if a["name"] == AGENT_NAME), None)
        if agent_id:
            await _call(client, "PATCH", f"/v1/convai/agents/{agent_id}", json=spec)
        else:
            agent_id = (await _call(client, "POST", "/v1/convai/agents/create", json=spec))["agent_id"]
        _synced = (fingerprint, agent_id)
        return agent_id


async def conversation_token() -> dict[str, str]:
    """A short-lived WebRTC token for one browser session; the API key never leaves the server."""
    agent_id = await ensure_agent()
    async with _http() as client:
        token = await _call(client, "GET", "/v1/convai/conversation/token", params={"agent_id": agent_id})
    return {"token": token["token"], "agent_id": agent_id}


async def speech(text: str) -> AsyncIterator[bytes]:
    """MP3 of `text` in the agent's voice, streamed as ElevenLabs renders it (the "Read aloud" button).

    Raises before the first byte when ElevenLabs refuses, so the endpoint can still answer with an error."""
    client = _http(timeout=60)
    request = client.build_request(
        "POST",
        f"/v1/text-to-speech/{get_settings().elevenlabs_voice_id}/stream",
        params={"output_format": "mp3_44100_128"},
        json={"text": text, "model_id": TTS_MODEL},
    )
    response = await client.send(request, stream=True)
    if response.is_error:
        detail = (await response.aread())[:300]
        await response.aclose()
        await client.aclose()
        raise ElevenLabsError(f"ElevenLabs TTS: {response.status_code} {detail!r}")

    async def chunks() -> AsyncIterator[bytes]:
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        finally:
            await response.aclose()
            await client.aclose()

    return chunks()


if __name__ == "__main__":
    agent = asyncio.run(ensure_agent())
    print(f"ElevenLabs agent {agent}: https://elevenlabs.io/app/agents/agents/{agent}")
