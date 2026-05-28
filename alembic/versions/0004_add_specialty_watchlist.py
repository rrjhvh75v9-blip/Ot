"""Add specialty_watchlist table.

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "specialty_watchlist",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True),
        sa.Column("lot_id", PG_UUID(as_uuid=True), sa.ForeignKey("lots.id"), nullable=False),
        sa.Column("trigger_keyword", sa.String(200), nullable=False),
        sa.Column("reviewed", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column(
            "flagged_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_specialty_watchlist_lot_id", "specialty_watchlist", ["lot_id"])
    op.create_index("ix_specialty_watchlist_flagged_at", "specialty_watchlist", ["flagged_at"])
    op.create_unique_constraint(
        "uq_watchlist_lot_keyword", "specialty_watchlist", ["lot_id", "trigger_keyword"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_watchlist_lot_keyword", "specialty_watchlist", type_="unique")
    op.drop_index("ix_specialty_watchlist_flagged_at", table_name="specialty_watchlist")
    op.drop_index("ix_specialty_watchlist_lot_id", table_name="specialty_watchlist")
    op.drop_table("specialty_watchlist")
