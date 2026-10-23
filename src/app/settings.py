"""All configuration in one place, read from the environment and `.env`."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    anthropic_api_key: str | None = None
    openrouter_api_key: str | None = None
    model_provider: Literal["anthropic", "openrouter"] | None = None
    """Unset: Anthropic if its key is set, else OpenRouter. See models.py."""

    # Per-alias overrides (MODEL_SMART=openrouter:openai/gpt-6.1-sol ...); unset uses models.ALIASES.
    model_smart: str | None = None
    model_balanced: str | None = None
    model_fast: str | None = None
    model_judge: str | None = None

    logfire_token: str | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "https://cloud.langfuse.com"

    app_env: str = "dev"
    dev_name: str | None = None

    data_dir: Path = ROOT / "data"


@lru_cache
def get_settings() -> Settings:
    # Also export .env to os.environ: the model providers read their API keys from there.
    load_dotenv(ROOT / ".env")
    return Settings()
