"""Runtime configuration, loaded from the environment (or a local .env)."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    discord_token: str
    hardcover_token: str

    # When set, slash commands sync to this guild only and appear instantly.
    # Unset (the production case) registers them globally, which Discord can
    # take up to an hour to propagate.
    dev_guild_id: int | None = None

    db_path: str = "data/bookbot.db"
    log_level: str = "INFO"

    # Hardcover allows 60 requests/min. We stay a little under it.
    rate_limit_per_minute: int = Field(default=55, ge=1)
    rate_limit_burst: int = Field(default=10, ge=1)

    search_cache_ttl: int = 60 * 60  # 1 hour
    book_cache_ttl: int = 7 * 24 * 60 * 60  # 7 days

    @field_validator("dev_guild_id", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: Any) -> Any:
        # A commented-out or empty DEV_GUILD_ID in .env means "not set",
        # not "invalid integer".
        if isinstance(value, str) and not value.strip():
            return None
        return value


settings = Settings()  # type: ignore[call-arg]
