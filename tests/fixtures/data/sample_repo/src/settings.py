"""Canonical configuration via BaseSettings — the clean counter-example: no ad-hoc env
reads, fully typed, composed. Must produce no findings under the strict profile."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings


class DatabaseSettings(BaseSettings):
    host: str = "localhost"
    port: int = 5432
    name: str = "app"


class AppSettings(BaseSettings):
    debug: bool = False
    request_timeout: int = Field(default=30, ge=1)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)


@lru_cache
def get_settings() -> AppSettings:
    return AppSettings()
