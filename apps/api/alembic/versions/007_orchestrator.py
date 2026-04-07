"""Master Orchestrator + Tenant 테이블 추가.

1. tenants: 멀티테넌트 격리
2. tenant_profiles: 테넌트-프로필 M:N 매핑
3. api_keys.tenant_id: 테넌트 연결
4. conversation_sessions 확장: current_profile_id, orchestrator_meta
"""

from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. 테넌트 테이블
    op.create_table(
        "tenants",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), server_default=""),
        sa.Column("orchestrator_enabled", sa.Boolean(), server_default="true"),
        sa.Column("default_chatbot_id", sa.String(100), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
    )

    # 2. 테넌트-프로필 M:N 매핑
    op.create_table(
        "tenant_profiles",
        sa.Column(
            "tenant_id",
            sa.String(100),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "profile_id",
            sa.String(100),
            sa.ForeignKey("agent_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("tenant_id", "profile_id"),
    )

    # 3. api_keys에 tenant_id 추가
    op.add_column(
        "api_keys",
        sa.Column(
            "tenant_id",
            sa.String(100),
            sa.ForeignKey("tenants.id"),
            nullable=True,
        ),
    )

    # 4. conversation_sessions 확장
    op.add_column(
        "conversation_sessions",
        sa.Column("current_profile_id", sa.String(100), nullable=True),
    )
    op.add_column(
        "conversation_sessions",
        sa.Column(
            "orchestrator_meta",
            sa.JSON(),
            server_default="{}",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("conversation_sessions", "orchestrator_meta")
    op.drop_column("conversation_sessions", "current_profile_id")
    op.drop_column("api_keys", "tenant_id")
    op.drop_table("tenant_profiles")
    op.drop_table("tenants")
