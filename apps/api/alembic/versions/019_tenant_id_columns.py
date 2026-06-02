"""핵심 테이블에 tenant_id 추가 — DB 테넌트 격리 4a (A2)

Revision ID: 019
Create Date: 2026-06-02

Step 4a (무동작 변경): documents/document_chunks/facts/conversation_sessions/
workflow_states/response_cache 에 tenant_id NULL 컬럼 추가 + 기존행을 단일 기본
테넌트('default')로 백필 + 쓰기경로 스탬핑(코드). 읽기 필터는 4b, RLS는 4c,
NOT NULL 잠금은 4d에서. 이 단계는 읽기 미적용이라 동작 무변·가역.

기본 테넌트 id 'default'는 config.default_tenant_id 와 일치해야 한다.
"""

revision = "019"
down_revision = "018"
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
    # 1) 기본 테넌트 시드 (FK 대상 + 백필 값). 멱등.
    op.execute(
        f"""
        INSERT INTO tenants (id, name, description)
        VALUES ('{DEFAULT_TENANT_ID}', 'Default Tenant', '단일 테넌트 기본값 (4a 백필)')
        ON CONFLICT (id) DO NOTHING;
        """
    )

    # 2) 각 테이블에 tenant_id NULL FK 컬럼 + 백필 + 인덱스
    for table in _TABLES:
        op.execute(
            f"""
            ALTER TABLE {table}
                ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(100)
                REFERENCES tenants(id);
            """
        )
        op.execute(
            f"UPDATE {table} SET tenant_id = '{DEFAULT_TENANT_ID}' WHERE tenant_id IS NULL;"
        )
        op.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_{table}_tenant
                ON {table} (tenant_id);
            """
        )


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP INDEX IF EXISTS idx_{table}_tenant;")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS tenant_id;")
    # 컬럼 제거 후 기본 테넌트 정리 (다른 곳에서 참조 시 남겨도 무방하므로 조건부)
    op.execute(
        f"DELETE FROM tenants WHERE id = '{DEFAULT_TENANT_ID}' "
        f"AND NOT EXISTS (SELECT 1 FROM api_keys WHERE tenant_id = '{DEFAULT_TENANT_ID}');"
    )
