"""response_feedback 테이블 신규 + api_request_logs 컬럼 추가

Revision ID: 014
Create Date: 2026-04-20

- response_feedback (UUID PK, response_id, score 1/-1, comment, user_id, created_at)
  - UNIQUE (user_id, response_id) — P1 피드백 중복 방지 (upsert)
- api_request_logs:
  - faithfulness_score DOUBLE PRECISION NULL
  - response_id UUID NULL (+ partial index)

pgcrypto 확장 필요 (gen_random_uuid).
"""

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None

from alembic import op


def upgrade() -> None:
    # 0. pgcrypto — gen_random_uuid()
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")

    # 1. api_request_logs: 2 컬럼 추가 (nullable, 기존 행 영향 없음)
    op.execute(
        "ALTER TABLE api_request_logs "
        "ADD COLUMN IF NOT EXISTS faithfulness_score DOUBLE PRECISION NULL;"
    )
    op.execute(
        "ALTER TABLE api_request_logs "
        "ADD COLUMN IF NOT EXISTS response_id UUID NULL;"
    )
    # 조회 성능: feedback list_for_admin 이 logs.response_id 로 JOIN
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_req_logs_response_id "
        "ON api_request_logs (response_id) "
        "WHERE response_id IS NOT NULL;"
    )

    # 2. response_feedback 테이블
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS response_feedback (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            response_id UUID NOT NULL,
            score SMALLINT NOT NULL CHECK (score IN (1, -1)),
            comment TEXT NULL,
            user_id UUID NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    # P1: (user_id, response_id) 조합 중복 방지 → upsert 기준
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_response_feedback_user_response "
        "ON response_feedback (user_id, response_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_response_feedback_response_id "
        "ON response_feedback (response_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_response_feedback_created_at "
        "ON response_feedback (created_at DESC);"
    )
    # 👎 필터용
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_response_feedback_score "
        "ON response_feedback (score);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_response_feedback_score;")
    op.execute("DROP INDEX IF EXISTS idx_response_feedback_created_at;")
    op.execute("DROP INDEX IF EXISTS idx_response_feedback_response_id;")
    op.execute("DROP INDEX IF EXISTS uq_response_feedback_user_response;")
    op.execute("DROP TABLE IF EXISTS response_feedback;")

    op.execute("DROP INDEX IF EXISTS idx_req_logs_response_id;")
    op.execute("ALTER TABLE api_request_logs DROP COLUMN IF EXISTS response_id;")
    op.execute("ALTER TABLE api_request_logs DROP COLUMN IF EXISTS faithfulness_score;")
