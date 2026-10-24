"""All configuration in one place, read from the environment and `.env`."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    anthropic_api_key: str | None = None
    openrouter_api_key: str | None = None

    # Per-alias overrides (MODEL_SMART=openrouter:openai/gpt-6.1-sol ...); unset uses models.ALIASES.
    model_smart: str | None = None
    model_balanced: str | None = None
    model_fast: str | None = None
    model_judge: str | None = None

    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_base_url: str = "https://cloud.langfuse.com"

    app_env: str = "dev"
    dev_name: str | None = None

    data_dir: Path = ROOT / "data"

    # Synera track: their kit is the `synera-kit/` git submodule (data, starter tools, official scorer).
    synera_kit_dir: Path = ROOT / "synera-kit"
    default_building: str = "proc-factory-dev-003"
    """Building for chats that don't name one (`ia chat building_guide`, `/ui`). Set DEFAULT_BUILDING."""


@lru_cache
def get_settings() -> Settings:
    # Also export .env to os.environ: the model providers read their API keys from there.
    load_dotenv(ROOT / ".env")
    return Settings()
