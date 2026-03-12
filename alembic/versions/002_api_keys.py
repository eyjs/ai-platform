"""api_keys 테이블 추가

Revision ID: 002
Create Date: 2026-03-12
"""

revision = "002"
down_revision = "001"

from alembic import op


def upgrade() -> None:
    op.execute("""
        CREATE TABLE api_keys (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            key_hash VARCHAR(64) NOT NULL UNIQUE,
            name VARCHAR(255) NOT NULL,
            user_id VARCHAR(255) NOT NULL DEFAULT '',
            user_role VARCHAR(20) NOT NULL DEFAULT 'VIEWER',
            security_level_max VARCHAR(20) NOT NULL DEFAULT 'PUBLIC',
            allowed_profiles TEXT[] DEFAULT '{}',
            allowed_origins TEXT[] DEFAULT '{}',
            rate_limit_per_min INT DEFAULT 60,
            is_active BOOLEAN DEFAULT TRUE,
            expires_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            last_used_at TIMESTAMPTZ
        );
    """)

    op.execute("""
        CREATE INDEX idx_api_keys_hash ON api_keys (key_hash) WHERE is_active = TRUE;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS api_keys CASCADE;")
