"""initial schema — full metadata snapshot

Revision ID: 0001
Revises:
Create Date: 2026-04-17
"""
from __future__ import annotations

import app.models  # noqa: F401  register all models
from alembic import op
from app.database import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
