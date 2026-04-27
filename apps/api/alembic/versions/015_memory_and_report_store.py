"""Memory 확장 + saju_report_results 테이블 생성

Revision ID: 015
Create Date: 2026-04-27

- tenant_memory:
  - retention_days INTEGER NULL (만료 정리 주기)
  - expires_at TIMESTAMPTZ NULL (+ partial index)
- agent_profiles:
  - memory_max_turns INTEGER DEFAULT 10 (Short-term N턴 설정)
  - memory_retention_days INTEGER NULL (Memory 보존 기간)
- saju_report_results (신규):
  - 사주 리포트 생성 결과 저장
  - job_queue FK 연동
"""

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None

from alembic import op


def upgrade() -> None:
    # ── 1. tenant_memory 확장 ──────────────────────────────────
    op.execute(
        "ALTER TABLE tenant_memory "
        "ADD COLUMN IF NOT EXISTS retention_days INTEGER DEFAULT NULL;"
    )
    op.execute(
        "ALTER TABLE tenant_memory "
        "ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ DEFAULT NULL;"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_tenant_memory_expires "
        "ON tenant_memory (expires_at) "
        "WHERE expires_at IS NOT NULL;"
    )

    # ── 2. agent_profiles 확장 (memory 설정) ──────────────────
    op.execute(
        "ALTER TABLE agent_profiles "
        "ADD COLUMN IF NOT EXISTS memory_max_turns INTEGER DEFAULT 10;"
    )
    op.execute(
        "ALTER TABLE agent_profiles "
        "ADD COLUMN IF NOT EXISTS memory_retention_days INTEGER DEFAULT NULL;"
    )

    # ── 3. saju_report_results 테이블 ─────────────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS saju_report_results (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            job_id UUID NOT NULL REFERENCES job_queue(id),
            report_type VARCHAR(50) NOT NULL,
            schema_version VARCHAR(50) NOT NULL DEFAULT 'report.v2',
            report_data JSONB NOT NULL DEFAULT '{}',
            sections_completed INTEGER NOT NULL DEFAULT 0,
            sections_total INTEGER NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'generating',
            error_message TEXT,
            metadata JSONB DEFAULT '{}',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at TIMESTAMPTZ,
            CONSTRAINT chk_report_type CHECK (report_type IN ('paper', 'compatibility')),
            CONSTRAINT chk_report_status CHECK (status IN ('generating', 'completed', 'failed'))
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_report_results_job "
        "ON saju_report_results (job_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_report_results_status "
        "ON saju_report_results (status);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_report_results_created "
        "ON saju_report_results (created_at DESC);"
    )


def downgrade() -> None:
    # 3. saju_report_results 제거
    op.execute("DROP INDEX IF EXISTS idx_report_results_created;")
    op.execute("DROP INDEX IF EXISTS idx_report_results_status;")
    op.execute("DROP INDEX IF EXISTS idx_report_results_job;")
    op.execute("DROP TABLE IF EXISTS saju_report_results;")

    # 2. agent_profiles 컬럼 제거
    op.execute(
        "ALTER TABLE agent_profiles DROP COLUMN IF EXISTS memory_retention_days;"
    )
    op.execute(
        "ALTER TABLE agent_profiles DROP COLUMN IF EXISTS memory_max_turns;"
    )

    # 1. tenant_memory 컬럼 제거
    op.execute("DROP INDEX IF EXISTS idx_tenant_memory_expires;")
    op.execute(
        "ALTER TABLE tenant_memory DROP COLUMN IF EXISTS expires_at;"
    )
    op.execute(
        "ALTER TABLE tenant_memory DROP COLUMN IF EXISTS retention_days;"
    )
