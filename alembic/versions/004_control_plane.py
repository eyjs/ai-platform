"""Control Plane: workflows 테이블 + agent_profiles 확장.

YAML 기반 설정을 DB로 이관하여 Admin API를 통한 동적 관리를 가능하게 한다.

Revision ID: 004
Revises: 003
Create Date: 2026-03-13
"""

revision = "004"
down_revision = "003"

from alembic import op


def upgrade() -> None:
    # --- workflows 테이블: 워크플로우 정의 (YAML 대체) ---
    op.execute("""
        CREATE TABLE workflows (
            id VARCHAR(100) PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            description TEXT DEFAULT '',
            steps JSONB NOT NULL DEFAULT '[]',
            escape_policy VARCHAR(20) DEFAULT 'allow',
            max_retries INT DEFAULT 3,
            first_step VARCHAR(100) DEFAULT '',
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
    """)

    # workflow_states → workflows FK 추가
    op.execute("""
        ALTER TABLE workflow_states
        ADD CONSTRAINT fk_workflow_states_workflow
        FOREIGN KEY (workflow_id) REFERENCES workflows(id);
    """)

    # agent_profiles에 description + is_active 추가
    op.execute("""
        ALTER TABLE agent_profiles
        ADD COLUMN IF NOT EXISTS description TEXT DEFAULT '';
    """)
    op.execute("""
        ALTER TABLE agent_profiles
        ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;
    """)

    # workflows 검색 인덱스
    op.execute("""
        CREATE INDEX idx_workflows_active
            ON workflows (is_active)
            WHERE is_active = TRUE;
    """)

    # agent_profiles 활성 인덱스
    op.execute("""
        CREATE INDEX idx_profiles_active
            ON agent_profiles (is_active)
            WHERE is_active = TRUE;
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_profiles_active;")
    op.execute("DROP INDEX IF EXISTS idx_workflows_active;")
    op.execute("ALTER TABLE workflow_states DROP CONSTRAINT IF EXISTS fk_workflow_states_workflow;")
    op.execute("ALTER TABLE agent_profiles DROP COLUMN IF EXISTS is_active;")
    op.execute("ALTER TABLE agent_profiles DROP COLUMN IF EXISTS description;")
    op.execute("DROP TABLE IF EXISTS workflows CASCADE;")
