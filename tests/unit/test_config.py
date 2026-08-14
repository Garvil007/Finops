"""Tests for environment-driven configuration."""

import pytest

from finopsai.config import Environment, Settings, get_settings


def test_defaults_are_development_safe(settings: Settings) -> None:
    # Assert
    assert settings.env is Environment.DEV
    assert settings.log_level == "INFO"
    assert settings.slack_webhook_url is None
    assert settings.litellm_master_key is None


def test_warehouse_and_litellm_urls_are_distinct(settings: Settings) -> None:
    # Assert: same server, different databases, both on the async driver
    assert settings.database_url.endswith("/finopsai")
    assert settings.litellm_db_url.endswith("/litellm")
    assert settings.database_url.startswith("postgresql+asyncpg://")
    assert settings.litellm_db_url.startswith("postgresql+asyncpg://")


def test_reads_values_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("LOG_LEVEL", "warning")
    monkeypatch.setenv("REDIS_URL", "redis://cache:6379/1")
    monkeypatch.setenv("LITELLM_BASE_URL", "http://litellm-proxy:4000")

    # Act
    loaded = Settings(_env_file=None)

    # Assert
    assert loaded.env is Environment.PROD
    assert loaded.log_level == "warning"
    assert loaded.redis_url == "redis://cache:6379/1"
    assert loaded.litellm_base_url == "http://litellm-proxy:4000"


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("SLACK_WEBHOOK_URL", "https://hooks.example.invalid/T000/B000/topsecret"),
        ("LITELLM_MASTER_KEY", "sk-topsecret"),
    ],
)
def test_secrets_are_not_rendered_in_repr(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str
) -> None:
    # Arrange
    monkeypatch.setenv(variable, value)

    # Act
    loaded = Settings(_env_file=None)

    # Assert
    assert "topsecret" not in repr(loaded)


def test_get_settings_is_cached() -> None:
    # Act / Assert
    assert get_settings() is get_settings()
