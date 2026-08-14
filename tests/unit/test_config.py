"""Tests for environment-driven configuration."""

import pytest

from finopsai.config import Environment, Settings, get_settings


def test_defaults_are_development_safe(settings: Settings) -> None:
    # Assert
    assert settings.env is Environment.DEV
    assert settings.log_level == "INFO"
    assert settings.slack_webhook_url is None


def test_reads_values_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("LOG_LEVEL", "warning")
    monkeypatch.setenv("REDIS_URL", "redis://cache:6379/1")

    # Act
    loaded = Settings(_env_file=None)

    # Assert
    assert loaded.env is Environment.PROD
    assert loaded.log_level == "warning"
    assert loaded.redis_url == "redis://cache:6379/1"


def test_secrets_are_not_rendered_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.example.invalid/T000/B000/xxx")

    # Act
    loaded = Settings(_env_file=None)

    # Assert
    assert loaded.slack_webhook_url is not None
    assert "xxx" not in repr(loaded)


def test_get_settings_is_cached() -> None:
    # Act / Assert
    assert get_settings() is get_settings()
