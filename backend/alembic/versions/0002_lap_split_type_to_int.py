"""laps.split_type: Text -> Integer

Strava's API returns `split` as an integer (1,2,3,…). SQLite is dynamically
typed so the original Text column happily stored ints, but Postgres + asyncpg
strictly enforces types and rejected every INSERT with a DataError.

Any pre-existing values are numeric strings so `::integer` cast is safe.
If laps is empty, the USING clause is effectively a no-op.

Revision ID: 0002
Revises: 0001
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "laps",
        "split_type",
        existing_type=sa.Text(),
        type_=sa.Integer(),
        postgresql_using="split_type::integer",
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "laps",
        "split_type",
        existing_type=sa.Integer(),
        type_=sa.Text(),
        postgresql_using="split_type::text",
        existing_nullable=True,
    )
