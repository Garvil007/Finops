"""teams and budgets

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "finops"


def upgrade() -> None:
    op.create_table(
        "team",
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("slack_channel", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("name"),
        schema=SCHEMA,
    )

    op.create_table(
        "budget",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("team", sa.String(length=128), nullable=False),
        sa.Column(
            "period",
            sa.Enum(
                "monthly",
                "weekly",
                "daily",
                name="budgetperiod",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("limit_usd", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("alert_thresholds", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team", "period", name="uq_budget_team_period"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("budget", schema=SCHEMA)
    op.drop_table("team", schema=SCHEMA)
