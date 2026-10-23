import pytest

from app import models
from app.settings import Settings


@pytest.fixture
def settings(monkeypatch):
    def use(**values):
        for var in ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "MODEL_SMART", "MODEL_JUDGE"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setattr(models, "get_settings", lambda: Settings(_env_file=None, **values))

    return use


def test_anthropic_key_always_wins(settings):
    settings(anthropic_api_key="a", openrouter_api_key="o", model_smart="openrouter:openai/gpt-6.1-sol")
    assert models.model("smart") == "anthropic:claude-opus-5-5"
    assert models.model("fast") == "anthropic:claude-haiku-5-5"


def test_openrouter_only_without_anthropic_key(settings):
    settings(openrouter_api_key="o")
    assert models.model("smart") == "openrouter:anthropic/claude-opus-5.5"


def test_explicit_ids_and_anthropic_overrides_pass_through(settings):
    settings(anthropic_api_key="a", model_judge="anthropic:claude-opus-5-5")
    assert models.model("judge") == "anthropic:claude-opus-5-5"
    assert models.model("openrouter:openai/gpt-6.1-sol") == "openrouter:openai/gpt-6.1-sol"
