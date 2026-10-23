"""Model aliases.

Agents are built with `model("smart")` instead of a hard-coded id. That gives us:
- one place to swap models for the whole team (`.env`),
- a single knob for eval sweeps (`ia eval my_agent --model balanced`),
- agents that import fine without an API key (tests run offline).

Provider: as soon as ANTHROPIC_API_KEY is set, every alias goes to the Anthropic API, even if an alias
override in `.env` names an OpenRouter model. OpenRouter is only the fallback for when there is no
Anthropic key. A full model id you pass on purpose still goes through as-is, e.g.
`--model openrouter:openai/gpt-6.1-sol` to compare against another lab's model.
"""

from __future__ import annotations

from typing import Literal

from app.settings import get_settings

Alias = Literal["smart", "balanced", "fast", "judge"]

ALIASES: dict[str, dict[str, str]] = {
    "anthropic": {
        "smart": "anthropic:claude-opus-5-5",
        "balanced": "anthropic:claude-sonnet-5-5",
        "fast": "anthropic:claude-haiku-5-5",
        "judge": "anthropic:claude-sonnet-5-5",
    },
    "openrouter": {
        "smart": "openrouter:anthropic/claude-opus-5.5",
        "balanced": "openrouter:anthropic/claude-sonnet-5.5",
        "fast": "openrouter:anthropic/claude-haiku-5.5",
        "judge": "openrouter:anthropic/claude-sonnet-5.5",
    },
}


def provider() -> str:
    s = get_settings()
    return "openrouter" if s.openrouter_api_key and not s.anthropic_api_key else "anthropic"


def model(alias: Alias | str) -> str:
    """Resolve an alias to a pydantic-ai model string; anything else passes through unchanged."""
    if alias not in ALIASES["anthropic"]:
        return alias
    override = getattr(get_settings(), f"model_{alias}", None)
    if override and not (provider() == "anthropic" and override.startswith("openrouter:")):
        return override
    return ALIASES[provider()][alias]
