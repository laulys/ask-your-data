"""Settings, read from the environment (and a local .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

__all__ = ["Settings"]

DEFAULT_DATABASE_URL = "sqlite:///data/chinook.db"
SUPPORTED_PROVIDERS = ("ollama", "openai", "anthropic", "watsonx")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as err:
        raise ValueError(f"{name} must be a whole number, got {raw!r}") from err


@dataclass(frozen=True)
class Settings:
    """Everything the agent needs to know, resolved once at start-up."""

    provider: str = "ollama"
    database_url: str = DEFAULT_DATABASE_URL
    max_rows: int = 200
    max_repairs: int = 2
    model: str | None = None

    @classmethod
    def from_env(cls, *, load_dotenv_file: bool = True) -> Settings:
        if load_dotenv_file:
            load_dotenv(override=False)

        provider = os.getenv("NLSQL_PROVIDER", "ollama").strip().lower()
        if provider not in SUPPORTED_PROVIDERS:
            raise ValueError(
                f"Unknown NLSQL_PROVIDER {provider!r}. "
                f"Choose one of: {', '.join(SUPPORTED_PROVIDERS)}."
            )

        return cls(
            provider=provider,
            database_url=os.getenv("NLSQL_DATABASE_URL", DEFAULT_DATABASE_URL).strip(),
            max_rows=_env_int("NLSQL_MAX_ROWS", 200),
            max_repairs=_env_int("NLSQL_MAX_REPAIRS", 2),
            model=(os.getenv("NLSQL_MODEL") or None),
        )
