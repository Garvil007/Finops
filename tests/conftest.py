"""Shared pytest fixtures.

Database and API client fixtures land here once those layers exist.
"""

import pytest

from finopsai.config import Settings


@pytest.fixture
def settings() -> Settings:
    """Return settings built from defaults, ignoring any developer ``.env``."""
    return Settings(_env_file=None)
