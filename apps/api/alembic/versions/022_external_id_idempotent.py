"""external_id 멱등 UPSERT — file_hash=None 경로 중복 적재 차단 (Step25)

Revision ID: 022
Create Date: 2026-06-05

insert_document의 file_hash=None 경로는 external_id 충돌 검사 없이 단순 INSERT라
동일 외부 문서가 복수 행으로 적재될 수 있었다(at-least-once 수신 시 위험).
external_id가 있을 때 (external_id, domain_code) 기준 UPSERT를 가능케 하려면
부분 유니크 인덱스가 필요하다.

선행: 인덱스 생성 전 기존 (external_id, domain_code) 중복 행 정리(최신 1건 보존).
      중복 행의 document_chunks는 FK ON DELETE CASCADE로 함께 정리된다.
가역: downgrade는 부분 유니크 인덱스 DROP(데이터 무손실, 중복은 복원하지 않음).
"""

revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None

from alembic import op


def upgrade() -> None:
    # 1. 기존 중복 (external_id, domain_code) 행 정리: created_at 최신 1건만 보존.
    #    chunks는 documents FK ON DELETE CASCADE로 함께 삭제된다.
    op.execute(
        """
        DELETE FROM documents d
        USING (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY external_id, domain_code
                       ORDER BY created_at DESC, id DESC
                   ) AS rn
            FROM documents
            WHERE external_id IS NOT NULL
        ) dup
        WHERE d.id = dup.id
          AND dup.rn > 1;
        """
    )

    # 2. 부분 유니크 인덱스: external_id가 NULL이 아닌 경우에만 (external_id, domain_code) 유일.
    #    ON CONFLICT (external_id, domain_code) 가 이 인덱스를 conflict target으로 사용한다.
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_documents_external_id_domain
        ON documents (external_id, domain_code)
        WHERE external_id IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_documents_external_id_domain;")
