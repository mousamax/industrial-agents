"""Live voice (app/voice): the agent spec, its contract with the web app, and the endpoints without a key.
Nothing here calls ElevenLabs."""

import re

from fastapi.testclient import TestClient

from app.api.main import app
from app.settings import ROOT, Settings
from app.voice import api, elevenlabs

client = TestClient(app)
WEB_HOOK = ROOT / "web" / "src" / "components" / "voice" / "useLiveVoice.ts"


def test_web_app_registers_exactly_the_agents_client_tools():
    """A tool the agent may call but the browser doesn't register hangs the call until it times out."""
    registered = set(re.findall(r"useConversationClientTool\('(\w+)'", WEB_HOOK.read_text(encoding="utf-8")))
    assert registered == {t["name"] for t in elevenlabs.TOOLS}


def test_prompt_names_every_tool_and_fills_every_variable():
    prompt = elevenlabs.PROMPT.read_text(encoding="utf-8")
    for tool in elevenlabs.TOOLS:
        assert f"`{tool['name']}`" in prompt
    spec = elevenlabs.agent_spec(["tool_1"])
    placeholders = spec["conversation_config"]["agent"]["dynamic_variables"]["dynamic_variable_placeholders"]
    used = set(re.findall(r"\{\{(\w+)\}\}", prompt + spec["conversation_config"]["agent"]["first_message"]))
    assert used == set(placeholders)


def test_every_client_tool_waits_for_the_browser():
    for tool in elevenlabs.TOOLS:
        assert tool["type"] == "client" and tool["expects_response"], tool["name"]
        assert 1 <= tool["response_timeout_secs"] <= 120, tool["name"]


def test_without_a_key_the_web_app_falls_back_to_browser_speech(monkeypatch):
    no_key = Settings(elevenlabs_api_key=None)
    monkeypatch.setattr(api, "get_settings", lambda: no_key)
    monkeypatch.setattr(elevenlabs, "get_settings", lambda: no_key)
    assert client.get("/voice/status").json() == {"live": False}
    assert client.get("/voice/session").status_code == 503
    assert client.get("/voice/tts", params={"text": "hello"}).status_code == 503
