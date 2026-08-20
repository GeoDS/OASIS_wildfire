"""Central configuration.

Every URL and key comes from the environment; nothing is hard-coded. No host or
port is ever written into code - a "just this once" localhost literal is how a
development assumption turns into a deployment bug.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repository root: backend/src/wildfire_agent/config.py -> up four levels.
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", Path.cwd() / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── LLM (provider agnostic - see llm.py) ────────────────────────
    llm_provider: str = "anthropic"
    llm_model: str = "claude-sonnet-4-5-20250929"
    llm_temperature: float = 0.0
    llm_max_tokens: int = 4096
    #: OpenAI-compatible endpoint (local model, proxy, gateway). Blank = official.
    llm_base_url: str | None = None

    # Provider keys. `init_chat_model` reads these from the environment; listing
    # them here lets us fail at startup with a readable message instead of at
    # the first call.
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    google_api_key: str | None = None

    # ── Geocoding: the only external call the User Goal Agent makes ──
    nominatim_url: str = "https://nominatim.openstreetmap.org/search"
    #: Nominatim's terms require an identifiable User-Agent or requests get blocked.
    geocoder_user_agent: str = "wildfire-analyst-agent/0.1 (competition demo)"
    geocoder_timeout_s: float = 10.0

    # ── Local-data recognition demo ─────────────────────────────────
    #: One allow-listed root. The model sees only metadata with relative paths;
    #: trusted code performs the scan and validates every selected dataset id.
    local_data_root: Path = REPO_ROOT / "backend" / "data"

    # ── Service ─────────────────────────────────────────────────────
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def resolved_local_data_root(self) -> Path:
        path = self.local_data_root.expanduser()
        return path.resolve() if path.is_absolute() else (REPO_ROOT / path).resolve()


settings = Settings()


def export_provider_keys() -> None:
    """Copy API keys from `.env` into the process environment.

    LangChain's `init_chat_model` reads credentials from `os.environ`, but
    pydantic-settings only parses `.env` into this object - it never touches the
    environment. Without this bridge every provider fails with "Missing
    credentials" even though the key is sitting right there in `.env`.

    A real environment variable always wins; we only fill the gaps.
    """
    for env_name, value in (
        ("ANTHROPIC_API_KEY", settings.anthropic_api_key),
        ("OPENAI_API_KEY", settings.openai_api_key),
        ("GOOGLE_API_KEY", settings.google_api_key),
    ):
        if value and not os.environ.get(env_name):
            os.environ[env_name] = value


export_provider_keys()
