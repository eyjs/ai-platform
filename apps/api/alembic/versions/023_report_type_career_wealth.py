"""saju_report_results.chk_report_type 확장 — career/wealth 추가

Revision ID: 023
Create Date: 2026-06-24

- saju_report_results.chk_report_type:
  - 기존 CHECK (report_type IN ('paper', 'compatibility'))
  - → ('paper', 'compatibility', 'career', 'wealth')
  - 천직(career)/재물(wealth) 리포트 2종 신규 추가에 따른 제약 확장.
  - 마이그레이션 누락 시 career/wealth job이 CheckViolation으로 영구 실패하므로
    DB 재생성(initdb) 후에도 신규 타입이 보존되도록 본 마이그레이션으로 고정한다.
"""

revision = "023"
down_revision = "022"
branch_labels = None
depends_on = None

from alembic import op


def upgrade() -> None:
    # 기존 제약 제거 후 career/wealth를 포함한 제약으로 재생성.
    op.execute(
        "ALTER TABLE saju_report_results "
        "DROP CONSTRAINT IF EXISTS chk_report_type;"
    )
    op.execute(
        "ALTER TABLE saju_report_results "
        "ADD CONSTRAINT chk_report_type "
        "CHECK (report_type IN ('paper', 'compatibility', 'career', 'wealth'));"
    )


def downgrade() -> None:
    # 롤백 시 career/wealth 행이 남아 있으면 제약 재추가가 실패할 수 있으므로
    # 원복 제약 추가 전 해당 행을 제거한다(데이터 정합).
    op.execute(
        "DELETE FROM saju_report_results "
        "WHERE report_type IN ('career', 'wealth');"
    )
    op.execute(
        "ALTER TABLE saju_report_results "
        "DROP CONSTRAINT IF EXISTS chk_report_type;"
    )
    op.execute(
        "ALTER TABLE saju_report_results "
        "ADD CONSTRAINT chk_report_type "
        "CHECK (report_type IN ('paper', 'compatibility'));"
    )
