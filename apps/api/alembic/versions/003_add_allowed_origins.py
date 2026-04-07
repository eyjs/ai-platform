"""api_keys에 allowed_origins 컬럼 추가

Revision ID: 003
Create Date: 2026-03-12
"""

revision = "003"
down_revision = "002"

from alembic import op


def upgrade() -> None:
    op.execute("""
        ALTER TABLE api_keys
        ADD COLUMN IF NOT EXISTS allowed_origins TEXT[] DEFAULT '{}';
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE api_keys DROP COLUMN IF EXISTS allowed_origins;
    """)
