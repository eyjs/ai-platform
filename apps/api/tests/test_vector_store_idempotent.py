"""Step 25: insert_document file_hash=None 경로 멱등성(external_id UPSERT) 단위 테스트.

DB 없이 Mock Pool로 분기/SQL을 검증한다.
- external_id 있고 file_hash 없으면 ON CONFLICT (external_id, domain_code) UPSERT (fetchrow)
- external_id 없고 file_hash 없으면 기존 plain INSERT (execute) — 회귀 없음
- file_hash 있으면 기존 (file_hash, domain_code) UPSERT 유지
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.infrastructure.vector_store import VectorStore


def _acquire_conn(conn: AsyncMock) -> MagicMock:
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=None)
    return ctx


def _store_with_conn(conn: AsyncMock) -> VectorStore:
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_acquire_conn(conn))
    vs = VectorStore("postgresql://x")
    vs._pool = pool
    return vs


@pytest.mark.asyncio
async def test_no_hash_with_external_id_upserts():
    # Arrange: file_hash 없음 + external_id 있음
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"id": "existing-doc-1"})
    vs = _store_with_conn(conn)

    # Act
    result = await vs.insert_document(
        title="약관 v2", domain_code="DB-DAMAGE", external_id="ext-100",
        tenant_id="t1",
    )

    # Assert: fetchrow(UPSERT) 경로 + 충돌 타깃 (external_id, domain_code)
    conn.fetchrow.assert_awaited_once()
    sql = conn.fetchrow.call_args[0][0]
    assert "ON CONFLICT (external_id, domain_code)" in sql
    # 부분 유니크 인덱스(uq_documents_external_id_domain, WHERE external_id IS NOT NULL,
    # 마이그레이션 022)를 ON CONFLICT arbiter 로 추론하려면 동일 술어 명시가 필수다.
    # 이 술어가 빠지면 "no unique or exclusion constraint matching..." 로 실패 →
    # at-least-once 중복 수신이 멱등이 아니게 된다(Step18 회귀 가드).
    assert "WHERE external_id IS NOT NULL" in sql
    assert "DO UPDATE" in sql
    # 멱등: 기존 행 id 반환 (신규 uuid가 아님)
    assert result == "existing-doc-1"
    # plain INSERT(execute)는 호출되지 않음
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_no_hash_no_external_id_plain_insert():
    # Arrange: file_hash 없음 + external_id 없음 → 식별자 없음 → 신규 INSERT
    conn = AsyncMock()
    vs = _store_with_conn(conn)

    # Act
    result = await vs.insert_document(title="t", domain_code="d", tenant_id="t1")

    # Assert: 기존 plain INSERT(execute) 경로 유지 (회귀 없음)
    conn.execute.assert_awaited_once()
    sql = conn.execute.call_args[0][0]
    assert "INSERT INTO documents" in sql
    assert "ON CONFLICT" not in sql
    # 신규 uuid 반환
    assert isinstance(result, str) and len(result) == 36


@pytest.mark.asyncio
async def test_external_id_takes_precedence_over_file_hash():
    # 계약 변경(2026-07-14): external_id 가 있으면 그것이 정본 식별자 —
    # 재처리(reprocess)로 file_hash 가 바뀌어도 external_id upsert 로 잡는다
    # (실사고: KMS 일괄 재파싱 13건 UniqueViolation 전건 실패).
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"id": "doc-fh"})
    vs = _store_with_conn(conn)

    # Act
    await vs.insert_document(
        title="t", domain_code="d", file_hash="abc", external_id="ext-1",
        tenant_id="t1",
    )

    # Assert: external_id 부분 유니크 인덱스가 arbiter
    sql = conn.fetchrow.call_args[0][0]
    assert "ON CONFLICT (external_id, domain_code)" in sql
    assert "WHERE external_id IS NOT NULL" in sql


@pytest.mark.asyncio
async def test_file_hash_path_without_external_id():
    # external_id 없이 file_hash 만 있으면 기존 (file_hash, domain_code) UPSERT 유지
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"id": "doc-fh"})
    vs = _store_with_conn(conn)

    await vs.insert_document(
        title="t", domain_code="d", file_hash="abc", tenant_id="t1",
    )

    sql = conn.fetchrow.call_args[0][0]
    assert "ON CONFLICT (file_hash, domain_code)" in sql
