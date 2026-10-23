"""Model aliases.

Agents are built with `model("smart")` instead of a hard-coded id. That gives us:
- one place to swap models for the whole team (`.env`),
- a single knob for eval sweeps (`ia eval my_agent --model balanced`),
- agents that import fine without an API key (tests run offline).

The provider is picked from whichever key is in `.env` (Anthropic first, then OpenRouter), or forced
with MODEL_PROVIDER. Any full model id passes through, e.g. `--model openrouter:openai/gpt-6.1-sol`.
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
    if s.model_provider:
        return s.model_provider
    if s.anthropic_api_key or not s.openrouter_api_key:
        return "anthropic"
    return "openrouter"


def model(alias: Alias | str) -> str:
    """Resolve an alias to a pydantic-ai model string; anything else passes through unchanged."""
    override = getattr(get_settings(), f"model_{alias}", None)
    return override or ALIASES[provider()].get(alias, alias)
