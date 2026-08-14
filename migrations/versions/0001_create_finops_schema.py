"""create finops schema

Baseline revision. ``migrations/env.py`` also creates the schema before running
migrations, because Alembic writes its version table inside ``finops`` and that
must exist first. This revision keeps the schema declared in migration history
so a fresh database reaches the same state from ``alembic upgrade head`` alone.

Revision ID: 0001
Revises:
Create Date: 2026-08-14

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "finops"


def upgrade() -> None:
    op.execute(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"')


def downgrade() -> None:
    # The alembic_version table lives in this schema, so dropping it here would
    # delete Alembic's own bookkeeping mid-downgrade. Schema teardown is a
    # `docker compose down -v` operation, not a migration.
    pass
