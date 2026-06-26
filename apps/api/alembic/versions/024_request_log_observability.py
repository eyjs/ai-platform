"""api_request_logs 관측성 컬럼 추가 (Phase 3)

Revision ID: 024
Create Date: 2026-06-26

요청별 관측성: 클라이언트 IP·사용자 식별자·레이어별 처리시간을 영속한다.
- client_ip VARCHAR(64) NULL
- user_id VARCHAR(255) NULL
- latency_breakdown JSONB NULL  (RequestTrace.summary(): {total_ms, nodes:[{node, ms, ...}]})

모두 nullable → 기존 행/적재 경로 무영향.
"""

revision = "024"
down_revision = "023"
branch_labels = None
depends_on = None

from alembic import op


def upgrade() -> None:
    op.execute("ALTER TABLE api_request_logs ADD COLUMN IF NOT EXISTS client_ip VARCHAR(64) NULL;")
    op.execute("ALTER TABLE api_request_logs ADD COLUMN IF NOT EXISTS user_id VARCHAR(255) NULL;")
    op.execute("ALTER TABLE api_request_logs ADD COLUMN IF NOT EXISTS latency_breakdown JSONB NULL;")


def downgrade() -> None:
    op.execute("ALTER TABLE api_request_logs DROP COLUMN IF EXISTS latency_breakdown;")
    op.execute("ALTER TABLE api_request_logs DROP COLUMN IF EXISTS user_id;")
    op.execute("ALTER TABLE api_request_logs DROP COLUMN IF EXISTS client_ip;")
