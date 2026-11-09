import logging
from functools import lru_cache
from typing import Literal

from pydantic import PostgresDsn, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    debug: bool = False
    secret_key: SecretStr
    database_url: PostgresDsn
    tmdb_api_key: SecretStr | None = None
    tmdb_language: str = "uk-UA"
    tmdb_fallback_language: str = "en-US"
    log_level: str = "INFO"
    session_ttl_days: int = 30
    sync_interval_hours: int = 24

    @field_validator("tmdb_api_key", mode="before")
    @classmethod
    def empty_key_is_missing(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def secure_cookies(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def configure_logging(settings: Settings) -> None:
    logging.basicConfig(level=settings.log_level)
    logging.getLogger("httpx").setLevel(logging.WARNING)
