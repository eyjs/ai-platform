"""api_keys.key_type 컬럼 추가 — publishable/secret 키 분리 (B4)

Revision ID: 017
Create Date: 2026-06-02

위젯에 노출되는 publishable 키와 서버 전용 secret 키를 구분한다.
- key_type='secret' (DEFAULT): 기존 키 전부. 서버 전용, 고권한 유지 → 동작 무변.
- key_type='publishable': 위젯용. 오리진 필수·보안등급 PUBLIC 한정·읽기 전용·쿼터 상한
  (강제는 애플리케이션 계층 src/domain/key_type_policy.py 에서 수행).

기존 행은 DEFAULT 'secret' 으로 채워지므로 완전 가역·무중단.
"""

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None

from alembic import op


def upgrade() -> None:
    op.execute("""
        ALTER TABLE api_keys
            ADD COLUMN IF NOT EXISTS key_type VARCHAR(20) NOT NULL DEFAULT 'secret';
    """)
    # 허용 값 제약 (멱등: 존재 시 스킵)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'ck_api_keys_key_type'
            ) THEN
                ALTER TABLE api_keys
                    ADD CONSTRAINT ck_api_keys_key_type
                    CHECK (key_type IN ('publishable', 'secret'));
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE api_keys DROP CONSTRAINT IF EXISTS ck_api_keys_key_type;")
    op.execute("ALTER TABLE api_keys DROP COLUMN IF EXISTS key_type;")
