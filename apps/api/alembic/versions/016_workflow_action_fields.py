"""Workflow Action Step 지원을 위한 스키마 확장.

Revision ID: 016
Revises: 015
Create Date: 2026-05-07

- workflows 테이블:
  - escape_keywords JSONB (워크플로우별 이탈 키워드 오버라이드)
- agent_profiles 테이블:
  - workflow_action_endpoint TEXT (action step 기본 엔드포인트)
  - workflow_action_headers JSONB (action step 기본 헤더)
"""

revision = "016"
down_revision = "015"

from alembic import op


def upgrade() -> None:
    # workflows: 워크플로우별 escape_keywords 오버라이드
    op.execute("""
        ALTER TABLE workflows
        ADD COLUMN IF NOT EXISTS escape_keywords JSONB DEFAULT '[]';
    """)

    # agent_profiles: action step 기본 엔드포인트
    op.execute("""
        ALTER TABLE agent_profiles
        ADD COLUMN IF NOT EXISTS workflow_action_endpoint TEXT DEFAULT NULL;
    """)

    # agent_profiles: action step 기본 헤더
    op.execute("""
        ALTER TABLE agent_profiles
        ADD COLUMN IF NOT EXISTS workflow_action_headers JSONB DEFAULT '{}';
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE agent_profiles DROP COLUMN IF EXISTS workflow_action_headers;")
    op.execute("ALTER TABLE agent_profiles DROP COLUMN IF EXISTS workflow_action_endpoint;")
    op.execute("ALTER TABLE workflows DROP COLUMN IF EXISTS escape_keywords;")
