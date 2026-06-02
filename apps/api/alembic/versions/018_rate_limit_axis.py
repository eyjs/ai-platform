"""레이트리밋 축 전환 지원 — client_id 폭 확대 + 정리 인덱스 (B5, B6)

Revision ID: 018
Create Date: 2026-06-02

- client_id를 복합키 "{api_key_id}:{session_id}" 로 쓰기 위해 VARCHAR(64)→VARCHAR(255).
- 유휴 버킷 정리(cleanup_stale)가 last_updated로 스캔하므로 인덱스 추가.
모두 가산·가역. 기존 단일 client_id 행은 그대로 유효.
"""

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None

from alembic import op


def upgrade() -> None:
    op.execute("ALTER TABLE api_rate_limits ALTER COLUMN client_id TYPE VARCHAR(255);")
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_api_rate_limits_last_updated
            ON api_rate_limits (last_updated);
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_api_rate_limits_last_updated;")
    # 복합키(64자 초과) 행이 남아 있으면 실패할 수 있다 — 정리 후 다운그레이드 권장.
    op.execute("ALTER TABLE api_rate_limits ALTER COLUMN client_id TYPE VARCHAR(64);")
