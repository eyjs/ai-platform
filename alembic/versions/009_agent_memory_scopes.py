"""tenant_memory + project_memory 테이블 생성

에이전트 메모리 3-스코프 지원:
- tenant_memory: 테넌트 전역 지식 (user 스코프)
- project_memory: 상품/업무별 지식 (project 스코프)
- local 스코프는 기존 conversation_sessions 재사용
"""

from alembic import op
import sqlalchemy as sa

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # tenant_memory: 테넌트 전역 지식 (user 스코프)
    op.create_table(
        "tenant_memory",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column(
            "value",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
        ),
        sa.Column(
            "memory_type",
            sa.Text(),
            server_default="fact",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.UniqueConstraint("tenant_id", "key", name="uq_tenant_memory_tenant_key"),
    )
    op.create_index(
        "idx_tenant_memory_tenant",
        "tenant_memory",
        ["tenant_id"],
    )

    # project_memory: 상품/업무별 지식 (project 스코프)
    op.create_table(
        "project_memory",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column(
            "value",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
        ),
        sa.Column(
            "memory_type",
            sa.Text(),
            server_default="fact",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "tenant_id", "project_id", "key",
            name="uq_project_memory_tenant_project_key",
        ),
    )
    op.create_index(
        "idx_project_memory_lookup",
        "project_memory",
        ["tenant_id", "project_id"],
    )


def downgrade() -> None:
    op.drop_table("project_memory")
    op.drop_table("tenant_memory")
