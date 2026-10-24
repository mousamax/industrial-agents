"""HTTP surface for live voice, mounted by `app.api.main`.

GET /voice/status     {"live": bool}: whether ElevenLabs is configured (else the web app uses browser speech)
GET /voice/session    a WebRTC conversation token for the ElevenLabs agent (key stays on the server)
GET /voice/tts?text=  MP3 of the text in the agent's voice, streamed (the "Read aloud" button)
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from starlette.responses import StreamingResponse

from app.settings import get_settings
from app.voice import elevenlabs
from app.voice.elevenlabs import ElevenLabsError, VoiceNotConfigured

router = APIRouter(prefix="/voice", tags=["voice"])


@router.get("/status")
async def status() -> dict[str, bool]:
    return {"live": bool(get_settings().elevenlabs_api_key)}


@router.get("/session")
async def session() -> dict[str, str]:
    try:
        return await elevenlabs.conversation_token()
    except VoiceNotConfigured as e:
        raise HTTPException(503, str(e)) from None
    except ElevenLabsError as e:
        raise HTTPException(502, str(e)) from None


@router.get("/tts")
async def tts(text: str = Query(min_length=1, max_length=1200)) -> StreamingResponse:
    try:
        audio = await elevenlabs.speech(text)
    except VoiceNotConfigured as e:
        raise HTTPException(503, str(e)) from None
    except ElevenLabsError as e:
        raise HTTPException(502, str(e)) from None
    return StreamingResponse(audio, media_type="audio/mpeg")
