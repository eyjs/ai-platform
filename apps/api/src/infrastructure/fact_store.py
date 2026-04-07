"""FactStore: 구조화된 팩트 저장소 (chain resolution 지원).

domain_code 필터를 지원하는 범용 팩트 검색.
pg_trgm fuzzy matching으로 subject 검색.
"""

import logging
import uuid
from typing import List, Optional

import asyncpg

logger = logging.getLogger(__name__)


class FactStore:
    """PostgreSQL 기반 팩트 저장소."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def insert_fact(
        self,
        document_id: str,
        domain_code: str,
        subject: str,
        predicate: str,
        obj: str,
        heading_path: Optional[List[str]] = None,
        table_context: str = "",
        confidence: float = 1.0,
    ) -> str:
        fact_id = str(uuid.uuid4())
        await self._pool.execute(
            """
            INSERT INTO facts (id, document_id, domain_code, subject, predicate, object,
                heading_path, table_context, confidence)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            """,
            uuid.UUID(fact_id), uuid.UUID(document_id), domain_code,
            subject, predicate, obj,
            heading_path or [], table_context, confidence,
        )
        return fact_id

    async def search(
        self,
        query: str,
        domain_codes: Optional[List[str]] = None,
        limit: int = 10,
        min_similarity: float = 0.3,
    ) -> List[dict]:
        """subject 기반 fuzzy 검색 (pg_trgm)."""
        conditions = ["similarity(f.subject, $1) > $2"]
        params: list = [query, min_similarity, limit]
        param_idx = 4

        if domain_codes:
            conditions.append(f"f.domain_code = ANY(${param_idx}::text[])")
            params.append(domain_codes)
            param_idx += 1

        where_clause = " AND ".join(conditions)
        rows = await self._pool.fetch(
            f"""
            SELECT f.id, f.document_id, f.domain_code,
                   f.subject, f.predicate, f.object,
                   f.heading_path, f.table_context, f.confidence,
                   similarity(f.subject, $1) AS score
            FROM facts f
            WHERE {where_clause}
            ORDER BY score DESC
            LIMIT $3
            """,
            *params,
        )
        return [
            {
                "fact_id": str(row["id"]),
                "document_id": str(row["document_id"]),
                "domain_code": row["domain_code"],
                "subject": row["subject"],
                "predicate": row["predicate"],
                "object": row["object"],
                "heading_path": row["heading_path"],
                "table_context": row["table_context"],
                "confidence": row["confidence"],
                "score": float(row["score"]),
            }
            for row in rows
        ]

    async def chain_resolve(
        self,
        subject: str,
        domain_codes: Optional[List[str]] = None,
        max_depth: int = 3,
        max_total: int = 30,
        fan_out: int = 3,
    ) -> List[dict]:
        """subject -> predicate -> object 체인 탐색.

        예: "자동차보험 대인배상" -> [한도: 무한] -> [특약: 자기부담금 면제]
        fan_out으로 각 depth에서 탐색할 subject 수를 제한한다.
        """
        visited: set[str] = set()
        chain: list[dict] = []
        current_subjects = [subject]

        for depth in range(max_depth):
            if not current_subjects or len(chain) >= max_total:
                break

            next_subjects: list[str] = []
            for subj in current_subjects[:fan_out]:
                if subj in visited:
                    continue
                visited.add(subj)

                facts = await self.search(subj, domain_codes=domain_codes, limit=5)
                for fact in facts:
                    chain.append(fact)
                    if len(chain) >= max_total:
                        break
                    next_subjects.append(fact["object"])

                if len(chain) >= max_total:
                    break

            current_subjects = next_subjects

        return chain
