"""job_queue lease 기반 visibility timeout (I/F 결함 Fix 2)

Revision ID: 025
Create Date: 2026-07-07

기존 cleanup_stale 은 locked_at + 600s 고정이라, 정상적으로 수십 분 걸리는
vlm_enhance(그리고 대형 문서 ingest, 예산 5400s)를 실행 중에 회수해
중복 실행(KMS 본문 이중 교체 경합)을 유발할 수 있었다.

lease_expires_at: dequeue 시 NOW()+180s 로 설정, 실행 중 60s 하트비트로
연장, cleanup 은 lease 만료만 회수 — 크래시 복구 ≤3분 + 장기 잡 안전.

nullable ADD → 기존 행/적재 경로 무영향. 구 행(lease NULL)은
cleanup_stale 의 COALESCE 폴백(locked_at + 600s)이 처리한다.
"""

revision = "025"
down_revision = "024"
branch_labels = None
depends_on = None

from alembic import op


def upgrade() -> None:
    op.execute(
        "ALTER TABLE job_queue ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ NULL;"
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_job_queue_lease
        ON job_queue (status, lease_expires_at)
        WHERE status = 'processing';
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_job_queue_lease;")
    op.execute("ALTER TABLE job_queue DROP COLUMN IF EXISTS lease_expires_at;")
