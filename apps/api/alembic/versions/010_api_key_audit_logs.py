"""api_key_audit_logs 테이블 생성 + api_keys 컬럼 확장

Revision ID: 010
Create Date: 2026-04-20

- api_key_audit_logs: 키 변경 이력 (create/update/revoke/rotate)
- api_keys 확장: rate_limit_per_day, rotated_from_id, revoked_at
"""

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None

from alembic import op


def upgrade() -> None:
    # 1) api_keys 컬럼 확장 (단일 트랜잭션)
    op.execute("""
        ALTER TABLE api_keys
            ADD COLUMN IF NOT EXISTS rate_limit_per_day INT DEFAULT 10000,
            ADD COLUMN IF NOT EXISTS rotated_from_id UUID NULL REFERENCES api_keys(id),
            ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ NULL;
    """)

    # 2) api_key_audit_logs 생성
    op.execute("""
        CREATE TABLE IF NOT EXISTS api_key_audit_logs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            api_key_id UUID NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
            actor VARCHAR(255) NOT NULL,
            action VARCHAR(32) NOT NULL,
            before JSONB NULL,
            after JSONB NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_audit_key_id
            ON api_key_audit_logs (api_key_id, created_at DESC);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS api_key_audit_logs CASCADE;")
    op.execute("""
        ALTER TABLE api_keys
            DROP COLUMN IF EXISTS revoked_at,
            DROP COLUMN IF EXISTS rotated_from_id,
            DROP COLUMN IF EXISTS rate_limit_per_day;
    """)
