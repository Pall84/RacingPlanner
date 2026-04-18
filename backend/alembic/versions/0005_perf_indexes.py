"""Performance index: activity_metrics.workout_type.

The classification step of the sync pipeline filters
`WHERE workout_type IS NULL` to find unclassified activities on every full
sync. Without an index, this scans the whole activity_metrics table. At
current scale the cost is small, but the query hits every sync so the
cumulative savings over time are worthwhile.

Skipped from this migration (measured to be sub-ms at current scale,
queries filter by athlete_id first which already hits existing indexes):
  - DailyFitness.date
  - GarminDailyHealth.date

Revision ID: 0005
Revises: 0004
"""
from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "idx_activity_metrics_workout_type",
        "activity_metrics",
        ["workout_type"],
    )


def downgrade() -> None:
    op.drop_index("idx_activity_metrics_workout_type", table_name="activity_metrics")
