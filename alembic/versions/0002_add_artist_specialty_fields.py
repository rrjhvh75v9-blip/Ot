"""Add specialty_category and arbitrage_notes to artists.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("artists", sa.Column("specialty_category", sa.String(50), nullable=True))
    op.add_column("artists", sa.Column("arbitrage_notes", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("artists", "arbitrage_notes")
    op.drop_column("artists", "specialty_category")
