"""job_queue 테이블에 result JSONB 컬럼 추가.

워커가 작업 완료 시 처리 결과(document_id, chunks 등)를 저장한다.
"""

from alembic import op

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE job_queue
        ADD COLUMN result JSONB;
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE job_queue
        DROP COLUMN IF EXISTS result;
    """)
