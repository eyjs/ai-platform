"""profile_access_policies 테이블 생성 + api_keys.user_type 컬럼 추가

1. profile_access_policies: 프로필별 접근 세그먼트 관리 (복합 PK)
2. api_keys.user_type: API 키의 사용자 유형 구분
"""

from alembic import op
import sqlalchemy as sa

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. profile_access_policies 테이블
    op.create_table(
        "profile_access_policies",
        sa.Column(
            "profile_id",
            sa.String(100),
            sa.ForeignKey("agent_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("segment", sa.String(50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("profile_id", "segment"),
    )

    # 2. api_keys에 user_type 컬럼 추가
    op.add_column(
        "api_keys",
        sa.Column(
            "user_type",
            sa.String(50),
            server_default="",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("api_keys", "user_type")
    op.drop_table("profile_access_policies")
