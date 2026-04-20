"""response_cache 테이블 생성

Revision ID: 012
Create Date: 2026-04-20

프로파일+입력 해시 기반 응답 캐시.
deterministic 모드 기본 캐시, agentic 은 profile 에서 opt-in.
"""

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None

from alembic import op


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS response_cache (
            cache_key VARCHAR(128) PRIMARY KEY,
            profile_id VARCHAR(255) NOT NULL,
            mode VARCHAR(32) NOT NULL,
            response_text TEXT NOT NULL,
            prompt_tokens INT DEFAULT 0,
            completion_tokens INT DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at TIMESTAMPTZ NOT NULL,
            hit_count INT DEFAULT 0,
            last_hit_at TIMESTAMPTZ NULL
        );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_response_cache_profile ON response_cache (profile_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_response_cache_expires ON response_cache (expires_at);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS response_cache CASCADE;")
