"""tenant_id NOT NULL 잠금 — DB 테넌트 격리 4d (A2)

Revision ID: 021
Create Date: 2026-06-02

4a~4c로 모든 쓰기경로가 tenant를 스탬핑하고 RLS가 강제되는 상태에서, 마지막으로
tenant_id를 NOT NULL로 잠근다. 이로써 "테넌트 없는 행"이라는 구멍을 제거한다.

선행: 잔존 NULL 백필 + 컬럼 DEFAULT 'default'(컬럼 누락 INSERT 방어). 그 후 NOT NULL.
가역: downgrade는 NOT NULL/DEFAULT 해제(데이터 무손실).
"""

revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None

from alembic import op

_TABLES = (
    "documents",
    "document_chunks",
    "facts",
    "conversation_sessions",
    "workflow_states",
    "response_cache",
)

DEFAULT_TENANT_ID = "default"


def upgrade() -> None:
    for table in _TABLES:
        # 1) 잔존 NULL 백필 (정상 경로면 0건이지만 안전 차원)
        op.execute(
            f"UPDATE {table} SET tenant_id = '{DEFAULT_TENANT_ID}' WHERE tenant_id IS NULL;"
        )
        # 2) 컬럼 DEFAULT — 컬럼을 누락한 INSERT도 기본 테넌트로 (방어)
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN tenant_id SET DEFAULT '{DEFAULT_TENANT_ID}';"
        )
        # 3) NOT NULL 잠금
        op.execute(f"ALTER TABLE {table} ALTER COLUMN tenant_id SET NOT NULL;")


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN tenant_id DROP NOT NULL;")
        op.execute(f"ALTER TABLE {table} ALTER COLUMN tenant_id DROP DEFAULT;")
