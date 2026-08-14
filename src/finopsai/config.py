"""Application configuration, loaded exclusively from environment variables.

All settings carry development-safe defaults so the package imports without a
populated ``.env``. No secret ever has a real default value.
"""

from enum import StrEnum
from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """Deployment environment."""

    DEV = "dev"
    PROD = "prod"


class Settings(BaseSettings):
    """Runtime configuration sourced from environment variables or ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    env: Environment = Environment.DEV
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://finops:finops@localhost:5432/finops"
    litellm_db_url: str = "postgresql+asyncpg://litellm:litellm@localhost:5432/litellm"
    redis_url: str = "redis://localhost:6379/0"

    slack_webhook_url: SecretStr | None = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
