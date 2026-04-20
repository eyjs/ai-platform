"""api_request_logs 테이블 생성

Revision ID: 011
Create Date: 2026-04-20

키별 요청/응답 메타데이터 로그.
Gateway 가 fire-and-forget 으로 enqueue, 워커가 배치 insert.
"""

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None

from alembic import op


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS api_request_logs (
            id BIGSERIAL PRIMARY KEY,
            ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            api_key_id UUID NULL REFERENCES api_keys(id) ON DELETE SET NULL,
            profile_id VARCHAR(255) NULL,
            provider_id VARCHAR(64) NULL,
            status_code INT NOT NULL,
            latency_ms INT NOT NULL,
            prompt_tokens INT DEFAULT 0,
            completion_tokens INT DEFAULT 0,
            cache_hit BOOLEAN DEFAULT FALSE,
            error_code VARCHAR(64) NULL,
            request_preview TEXT NULL,
            response_preview TEXT NULL
        );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_req_logs_key_ts ON api_request_logs (api_key_id, ts DESC);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_req_logs_profile_ts ON api_request_logs (profile_id, ts DESC);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_req_logs_ts_brin ON api_request_logs USING BRIN (ts);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS api_request_logs CASCADE;")
