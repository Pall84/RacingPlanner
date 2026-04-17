"""Add Garmin lactate threshold + endurance score columns to garmin_daily_health.

These are slow-changing physiological metrics that Garmin computes from
recent training. Used as a high-confidence ensemble member in race prediction.

Revision ID: 0003
Revises: 0002
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "garmin_daily_health",
        sa.Column("lactate_threshold_speed_ms", sa.Float(), nullable=True),
    )
    op.add_column(
        "garmin_daily_health",
        sa.Column("lactate_threshold_hr", sa.Integer(), nullable=True),
    )
    op.add_column(
        "garmin_daily_health",
        sa.Column("endurance_score", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("garmin_daily_health", "endurance_score")
    op.drop_column("garmin_daily_health", "lactate_threshold_hr")
    op.drop_column("garmin_daily_health", "lactate_threshold_speed_ms")
