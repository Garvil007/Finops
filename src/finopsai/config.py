"""Application configuration, loaded exclusively from environment variables.

All settings carry development-safe defaults so the package imports without a
populated ``.env``. No secret ever has a real default value.

Two URLs point at the LiteLLM database on purpose: the proxy container is
Prisma-based and needs a synchronous ``postgresql://`` URL, while our
read-only collector reads the same database over asyncpg.
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

    # FinOpsAI warehouse: owns the "finops" schema, managed by Alembic.
    database_url: str = "postgresql+asyncpg://finops:finops@localhost:5432/finopsai"

    # LiteLLM spend logs, read-only for us. Prisma owns the schema.
    litellm_db_url: str = "postgresql+asyncpg://finops:finops@localhost:5432/litellm"

    redis_url: str = "redis://localhost:6379/0"

    metrics_port: int = 9100

    litellm_base_url: str = "http://localhost:4000"
    litellm_master_key: SecretStr | None = None

    slack_webhook_url: SecretStr | None = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
