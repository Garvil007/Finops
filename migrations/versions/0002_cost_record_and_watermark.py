"""cost record and collector watermark

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-14

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "finops"
UNATTRIBUTED = "unattributed"


def upgrade() -> None:
    op.create_table(
        "cost_record",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "source",
            sa.Enum(
                "llm",
                "compute",
                "vectordb",
                "infra",
                name="costsource",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("dedup_key", sa.String(length=255), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("amount_usd", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=20, scale=4), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("team", sa.String(length=128), nullable=False, server_default=UNATTRIBUTED),
        sa.Column("project", sa.String(length=128), nullable=False, server_default=UNATTRIBUTED),
        sa.Column("agent_id", sa.String(length=128), nullable=False, server_default=UNATTRIBUTED),
        sa.Column("use_case", sa.String(length=128), nullable=False, server_default=UNATTRIBUTED),
        sa.Column("raw", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "dedup_key", name="uq_cost_record_source_dedup_key"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_cost_record_occurred_at_team",
        "cost_record",
        ["occurred_at", "team"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_cost_record_source_occurred_at",
        "cost_record",
        ["source", "occurred_at"],
        schema=SCHEMA,
    )

    op.create_table(
        "collector_watermark",
        sa.Column("collector", sa.String(length=64), nullable=False),
        sa.Column("last_occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_cursor", sa.String(length=255), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("collector"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("collector_watermark", schema=SCHEMA)
    op.drop_index("ix_cost_record_source_occurred_at", "cost_record", schema=SCHEMA)
    op.drop_index("ix_cost_record_occurred_at_team", "cost_record", schema=SCHEMA)
    op.drop_table("cost_record", schema=SCHEMA)
