"""pgvector + PostgreSQL full-text search 하이브리드 저장소.

domain_codes 필터를 지원하는 범용 벡터 검색.
"""

import json
import logging
import uuid
from typing import List, Optional

import asyncpg
import numpy as np
from pgvector.asyncpg import register_vector

from src.infrastructure.db.tenant_context import current_tenant
from src.infrastructure.providers.vector_store_base import AbstractVectorStore
from src.infrastructure.vector_search import (
    VectorSearchMixin,
    HYBRID_CANDIDATE_MULTIPLIER,   # re-export (test 호환)
    TRIGRAM_FALLBACK_THRESHOLD,    # re-export (test 호환)
)

# tenant_id 미지정 시 최종 폴백 (config.default_tenant_id 및 마이그레이션 019 백필값과 일치).
# 4d(NOT NULL 잠금) 이후 NULL 삽입은 DB가 거부하므로 저장 계층에서 항상 코일레싱한다.
_DEFAULT_TENANT = "default"

logger = logging.getLogger(__name__)


class VectorStore(VectorSearchMixin, AbstractVectorStore):
    """pgvector + PostgreSQL full-text search 하이브리드 저장소."""

    def __init__(self, database_url: str):
        self._database_url = database_url
        self._pool: Optional[asyncpg.Pool] = None

    @property
    def pool(self) -> Optional[asyncpg.Pool]:
        return self._pool

    async def connect(
        self, min_size: int = 5, max_size: int = 50,
        rls_enabled: bool = False, rls_role: str = "aip_app",
    ) -> None:
        # RLS(4c): 활성 시 매 acquire마다 요청 테넌트로 SET ROLE + GUC 주입.
        # 비활성 시 setup 미설치 → 기존 동작 그대로(오버헤드 0).
        setup = None
        if rls_enabled:
            from src.infrastructure.db.tenant_context import make_rls_setup
            setup = make_rls_setup(rls_role)
        self._pool = await asyncpg.create_pool(
            self._database_url,
            min_size=min_size,
            max_size=max_size,
            command_timeout=10,
            init=register_vector,
            setup=setup,
        )
        logger.info(
            "VectorStore connected (hybrid: vector + full-text + trigram)",
            extra={"rls_enabled": rls_enabled},
        )

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            logger.info("VectorStore connection closed")

    # -- 문서 관리 --

    async def insert_document(
        self,
        title: str,
        domain_code: str,
        file_name: str | None = None,
        file_hash: str | None = None,
        security_level: str = "PUBLIC",
        source_url: str | None = None,
        external_id: str | None = None,
        metadata: dict | None = None,
        tenant_id: str | None = None,
    ) -> str:
        """문서 레코드 생성, ID 반환."""
        if not self._pool:
            raise RuntimeError("VectorStore not connected")
        tenant_id = tenant_id or current_tenant.get() or _DEFAULT_TENANT
        doc_id = str(uuid.uuid4())
        async with self._pool.acquire() as conn:
            if file_hash:
                # INSERT ON CONFLICT: race condition 없는 atomic upsert
                row = await conn.fetchrow(
                    """
                    INSERT INTO documents (id, external_id, title, file_name, file_hash,
                        domain_code, security_level, source_url, metadata, tenant_id)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    ON CONFLICT (file_hash, domain_code) DO UPDATE
                        SET title = EXCLUDED.title,
                            security_level = EXCLUDED.security_level,
                            source_url = EXCLUDED.source_url,
                            metadata = EXCLUDED.metadata
                    RETURNING id
                    """,
                    uuid.UUID(doc_id), external_id, title, file_name, file_hash,
                    domain_code, security_level, source_url,
                    json.dumps(metadata or {}, ensure_ascii=False), tenant_id,
                )
                return str(row["id"])

            # file_hash가 없는 경로(스트림 업로드 등). external_id가 있으면
            # (external_id, domain_code) 기준으로 멱등 UPSERT — 동일 외부 문서가
            # 복수 행으로 적재되는 것을 막는다(at-least-once 수신측 정합, Step25/Step18).
            #
            # conflict target 은 부분 유니크 인덱스 uq_documents_external_id_domain
            # (WHERE external_id IS NOT NULL, 마이그레이션 022)이다. PostgreSQL 은
            # 부분 인덱스를 ON CONFLICT arbiter 로 추론(infer)하려면 동일한 술어를
            # ON CONFLICT 에 명시해야 한다. WHERE 술어가 없으면 "no unique or
            # exclusion constraint matching the ON CONFLICT specification" 로
            # 실패하여 at-least-once 중복 수신이 멱등이 아니게 된다(Step18 봉합 대상).
            if external_id is not None:
                row = await conn.fetchrow(
                    """
                    INSERT INTO documents (id, external_id, title, file_name, file_hash,
                        domain_code, security_level, source_url, metadata, tenant_id)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    ON CONFLICT (external_id, domain_code) WHERE external_id IS NOT NULL DO UPDATE
                        SET title = EXCLUDED.title,
                            security_level = EXCLUDED.security_level,
                            source_url = EXCLUDED.source_url,
                            metadata = EXCLUDED.metadata
                    RETURNING id
                    """,
                    uuid.UUID(doc_id), external_id, title, file_name, file_hash,
                    domain_code, security_level, source_url,
                    json.dumps(metadata or {}, ensure_ascii=False), tenant_id,
                )
                return str(row["id"])

            # external_id도 file_hash도 없으면 식별자가 없으므로 신규 INSERT.
            await conn.execute(
                """
                INSERT INTO documents (id, external_id, title, file_name, file_hash,
                    domain_code, security_level, source_url, metadata, tenant_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                uuid.UUID(doc_id), external_id, title, file_name, file_hash,
                domain_code, security_level, source_url,
                json.dumps(metadata or {}, ensure_ascii=False), tenant_id,
            )
        return doc_id

    async def insert_chunks(
        self, document_id: str, chunks: List[dict], embeddings: List[List[float]],
        domain_code: str = "", security_level: str = "PUBLIC",
        tenant_id: str | None = None,
    ) -> List[str]:
        """청크 + 임베딩 + tsvector 배치 삽입."""
        if not self._pool:
            raise RuntimeError("VectorStore not connected")
        tenant_id = tenant_id or current_tenant.get() or _DEFAULT_TENANT

        query = """
            INSERT INTO document_chunks
                (id, document_id, chunk_index, content, token_count, embedding,
                 search_vector, domain_code, security_level, metadata, tenant_id)
            VALUES ($1, $2, $3, $4, $5, $6, to_tsvector('simple', $7),
                    $8, $9, $10, $11)
        """
        chunk_ids = []
        records = []
        for chunk, emb in zip(chunks, embeddings):
            chunk_id = str(uuid.uuid4())
            chunk_ids.append(chunk_id)
            content = chunk["content"]
            # 간단한 한국어 토큰화: 공백 분리
            tokenized = self._tokenize_for_fts(content)
            records.append((
                uuid.UUID(chunk_id),
                uuid.UUID(document_id),
                chunk["chunkIndex"],
                content,
                chunk.get("tokenCount", 0),
                np.array(emb, dtype=np.float32),
                tokenized,
                domain_code or chunk.get("domain_code", ""),
                security_level or chunk.get("security_level", "PUBLIC"),
                json.dumps(chunk.get("metadata", {}), ensure_ascii=False),
                tenant_id,
            ))

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.executemany(query, records)

        logger.info("Inserted %d chunks for document %s", len(chunk_ids), document_id)
        return chunk_ids

    async def delete_document_chunks(self, document_id: str) -> int:
        if not self._pool:
            raise RuntimeError("VectorStore not connected")
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM document_chunks WHERE document_id = $1",
                uuid.UUID(document_id),
            )
        count = int(result.split()[-1])
        logger.info("Deleted %d chunks for document %s", count, document_id)
        return count

    async def get_chunk_count(self, document_id: str) -> int:
        if not self._pool:
            raise RuntimeError("VectorStore not connected")
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*) as cnt FROM document_chunks "
                "WHERE document_id = $1 AND embedding IS NOT NULL",
                uuid.UUID(document_id),
            )
        return row["cnt"] if row else 0

    async def get_neighbor_chunks(
        self, document_id: str, chunk_indices: list[int],
    ) -> list[dict]:
        """인접 청크 조회 (맥락 확장용)."""
        if not self._pool or not chunk_indices:
            return []
        valid_indices = [i for i in chunk_indices if i >= 0]
        if not valid_indices:
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT c.id, c.document_id, c.content, c.chunk_index, d.file_name
                FROM document_chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE c.document_id = $1 AND c.chunk_index = ANY($2::int[])
                """,
                uuid.UUID(document_id), valid_indices,
            )
        return [
            {
                "chunk_id": str(row["id"]),
                "document_id": str(row["document_id"]),
                "content": row["content"],
                "chunk_index": row["chunk_index"],
                "file_name": row.get("file_name", ""),
            }
            for row in rows
        ]

    # -- ID 매핑 --

    async def get_external_ids(self, aip_doc_ids: list[str]) -> dict[str, str]:
        """ai-platform UUID → KMS external_id 배치 매핑."""
        if not self._pool or not aip_doc_ids:
            return {}
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, external_id FROM documents "
                "WHERE id = ANY($1::uuid[]) AND external_id IS NOT NULL",
                [uuid.UUID(d) for d in aip_doc_ids],
            )
        return {str(row["id"]): row["external_id"] for row in rows}

    async def get_aip_id_by_external(self, external_id: str) -> str | None:
        """KMS external_id → ai-platform UUID 단건 역매핑."""
        if not self._pool:
            return None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM documents WHERE external_id = $1",
                external_id,
            )
        return str(row["id"]) if row else None

    async def get_top_chunks_by_doc(
        self, document_id: str, limit: int = 2,
        max_security_level: Optional[str] = None,
    ) -> list[dict]:
        """document_id로 상위 청크를 chunk_index 순서로 조회 (임베딩 불필요).

        max_security_level이 지정되면 해당 등급 이하 청크만 반환한다.
        """
        if not self._pool:
            return []

        allowed_levels = self._allowed_security_levels(max_security_level)

        conditions = ["c.document_id = $1"]
        params: list = [uuid.UUID(document_id), limit]
        param_idx = 3

        if allowed_levels:
            conditions.append(f"c.security_level = ANY(${param_idx}::text[])")
            params.append(allowed_levels)
            param_idx += 1

        where_clause = " AND ".join(conditions)

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT c.id, c.document_id, c.content, c.chunk_index, d.file_name
                FROM document_chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE {where_clause}
                ORDER BY c.chunk_index
                LIMIT $2
                """,
                *params,
            )
        return [
            {
                "chunk_id": str(row["id"]),
                "document_id": str(row["document_id"]),
                "content": row["content"],
                "chunk_index": row["chunk_index"],
                "score": 0.5,
                "file_name": row.get("file_name", ""),
            }
            for row in rows
        ]

    async def get_aip_ids_by_externals(
        self, external_ids: list[str],
    ) -> dict[str, dict]:
        """KMS external_id 목록 → ai-platform {id, security_level} 배치 역매핑.

        Returns:
            {external_id: {"aip_id": str, "security_level": str}}
        """
        if not self._pool or not external_ids:
            return {}
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, external_id, security_level FROM documents "
                "WHERE external_id = ANY($1::text[])",
                external_ids,
            )
        return {
            row["external_id"]: {
                "aip_id": str(row["id"]),
                "security_level": row.get("security_level", "PUBLIC"),
            }
            for row in rows
        }

    async def fetch_chunks_by_doc_ids(
        self,
        doc_ids: list[str],
        limit_per_doc: int = 5,
        max_security_level: Optional[str] = None,
    ) -> List[dict]:
        """doc_ids 기반 청크 본문 로드. Progressive Disclosure Level 2+.

        각 문서당 limit_per_doc개의 청크를 chunk_index 순서로 반환한다.
        """
        if not self._pool or not doc_ids:
            return []

        allowed_levels = self._allowed_security_levels(max_security_level)

        conditions = ["c.document_id = ANY($1::uuid[])"]
        params: list = [[uuid.UUID(d) for d in doc_ids], limit_per_doc]
        param_idx = 3

        if allowed_levels:
            conditions.append(f"c.security_level = ANY(${param_idx}::text[])")
            params.append(allowed_levels)
            param_idx += 1

        where_clause = " AND ".join(conditions)

        # ROW_NUMBER()로 문서당 청크 수 제한
        query = f"""
            SELECT chunk_id, document_id, content, chunk_index, score, file_name, title
            FROM (
                SELECT c.id AS chunk_id, c.document_id, c.content, c.chunk_index,
                       0.5 AS score, d.file_name, d.title,
                       ROW_NUMBER() OVER (
                           PARTITION BY c.document_id ORDER BY c.chunk_index
                       ) AS rn
                FROM document_chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE {where_clause}
            ) sub
            WHERE rn <= $2
            ORDER BY document_id, chunk_index
        """

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        return [
            {
                "chunk_id": str(row["chunk_id"]),
                "document_id": str(row["document_id"]),
                "content": row["content"],
                "chunk_index": row["chunk_index"],
                "score": float(row["score"]),
                "file_name": row.get("file_name", ""),
                "title": row.get("title", ""),
            }
            for row in rows
        ]
