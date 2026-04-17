"""Add activities.is_race — user-marked race flag.

Explicit race markers are far stronger signal for the predictor than
keyword-matching on activity names. This column is user-set and preserved
across Strava refresh/sync cycles.

Revision ID: 0004
Revises: 0003
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "activities",
        sa.Column("is_race", sa.Integer(), nullable=False, server_default="0"),
    )
    # Index lets the predictor's "most recent race" lookup and the
    # "races-only" list filter hit without scanning the whole activities table.
    op.create_index(
        "idx_activities_athlete_race",
        "activities",
        ["athlete_id", "is_race"],
    )


def downgrade() -> None:
    op.drop_index("idx_activities_athlete_race", table_name="activities")
    op.drop_column("activities", "is_race")
