"""allocation columns on cost record

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "finops"
TABLE = "cost_record"


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column("allocated", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema=SCHEMA,
    )
    op.add_column(
        TABLE,
        sa.Column("allocation_parent_id", sa.Integer(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        TABLE,
        sa.Column("allocation_rule", sa.String(length=64), nullable=True),
        schema=SCHEMA,
    )
    op.create_index("ix_cost_record_allocated", TABLE, ["allocated"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_cost_record_allocated", TABLE, schema=SCHEMA)
    op.drop_column(TABLE, "allocation_rule", schema=SCHEMA)
    op.drop_column(TABLE, "allocation_parent_id", schema=SCHEMA)
    op.drop_column(TABLE, "allocated", schema=SCHEMA)
