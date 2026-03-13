"""Token Bucket Rate Limiting 테이블 추가.

클라이언트(API Key / user_id)별 토큰을 관리한다.
PostgreSQL SELECT FOR UPDATE로 원자적 동시성 제어.
"""

from alembic import op
import sqlalchemy as sa

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_rate_limits",
        sa.Column("client_id", sa.String(64), primary_key=True),
        sa.Column("tokens", sa.Float(), nullable=False),
        sa.Column(
            "last_updated",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("api_rate_limits")
