"""테넌트 격리 4a — 쓰기경로 tenant_id 스탬핑 단위 테스트 (A2).

DB 없이 Mock Pool로 각 INSERT가 tenant_id를 전달하는지 검증한다.
읽기 필터(4b)/RLS(4c)는 별도 단계.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.infrastructure.fact_store import FactStore
from src.infrastructure.memory.session import SessionMemory
from src.infrastructure.vector_store import VectorStore
from src.workflow.session_store import WorkflowSessionStore
from src.workflow.state import WorkflowSession


def _acquire_conn(conn: AsyncMock) -> MagicMock:
    """async with pool.acquire() as conn 컨텍스트를 모킹한다."""
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=None)
    return ctx


@pytest.mark.asyncio
async def test_insert_document_stamps_tenant():
    """insert_document INSERT 인자 마지막에 tenant_id가 전달된다."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"id": "doc-1"})
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_acquire_conn(conn))

    vs = VectorStore("postgresql://x")
    vs._pool = pool

    await vs.insert_document(
        title="t", domain_code="d", file_hash="h", tenant_id="tenant-A",
    )

    args = conn.fetchrow.call_args[0]
    sql = args[0]
    assert "tenant_id" in sql
    assert "tenant-A" in args  # 마지막 위치 인자로 전달


@pytest.mark.asyncio
async def test_insert_document_no_hash_stamps_tenant():
    """file_hash 없는 경로(execute)도 tenant_id 스탬핑."""
    conn = AsyncMock()
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_acquire_conn(conn))

    vs = VectorStore("postgresql://x")
    vs._pool = pool

    await vs.insert_document(title="t", domain_code="d", tenant_id="tenant-B")

    args = conn.execute.call_args[0]
    assert "tenant_id" in args[0]
    assert "tenant-B" in args


@pytest.mark.asyncio
async def test_insert_chunks_stamps_tenant():
    """insert_chunks의 각 record 마지막 요소가 tenant_id다."""
    conn = AsyncMock()
    # conn.transaction()은 동기 호출 + async 컨텍스트 매니저
    tx = MagicMock()
    tx.__aenter__ = AsyncMock(return_value=None)
    tx.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=tx)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_acquire_conn(conn))

    vs = VectorStore("postgresql://x")
    vs._pool = pool

    import uuid
    doc_id = str(uuid.uuid4())
    chunks = [{"chunkIndex": 0, "content": "내용", "tokenCount": 2}]
    embeddings = [[0.1] * 4]

    await vs.insert_chunks(doc_id, chunks, embeddings, tenant_id="tenant-C")

    # executemany(query, records) — records의 각 튜플 마지막이 tenant_id
    records = conn.executemany.call_args[0][1]
    assert records[0][-1] == "tenant-C"
    assert "tenant_id" in conn.executemany.call_args[0][0]


@pytest.mark.asyncio
async def test_insert_fact_stamps_tenant():
    pool = MagicMock()
    pool.execute = AsyncMock()
    store = FactStore(pool)

    import uuid
    await store.insert_fact(
        document_id=str(uuid.uuid4()), domain_code="d",
        subject="s", predicate="p", obj="o", tenant_id="tenant-D",
    )

    args = pool.execute.call_args[0]
    assert "tenant_id" in args[0]
    assert "tenant-D" in args


@pytest.mark.asyncio
async def test_create_session_stamps_tenant():
    pool = MagicMock()
    pool.execute = AsyncMock()
    mem = SessionMemory(pool)

    await mem.create_session(
        session_id="s1", profile_id="p1", user_id="u1", tenant_id="tenant-E",
    )

    args = pool.execute.call_args[0]
    assert "tenant_id" in args[0]
    assert "tenant-E" in args


@pytest.mark.asyncio
async def test_workflow_save_stamps_tenant():
    pool = MagicMock()
    pool.execute = AsyncMock()
    store = WorkflowSessionStore(pool)

    session = WorkflowSession(workflow_id="wf", current_step_id="s")
    await store.save("sess-1", session, tenant_id="tenant-F")

    args = pool.execute.call_args[0]
    assert "tenant_id" in args[0]
    assert args[-1] == "tenant-F"  # 마지막 위치 인자


@pytest.mark.asyncio
async def test_workflow_save_falls_back_to_context_tenant():
    """명시 tenant 없으면 요청 컨텍스트(current_tenant)에서 해석 (4d NOT NULL 보장)."""
    from src.infrastructure.db.tenant_context import current_tenant

    pool = MagicMock()
    pool.execute = AsyncMock()
    store = WorkflowSessionStore(pool)
    session = WorkflowSession(workflow_id="wf", current_step_id="s")

    token = current_tenant.set("ctx-tenant")
    try:
        await store.save("sess-1", session)  # tenant_id 미지정
    finally:
        current_tenant.reset(token)

    assert pool.execute.call_args[0][-1] == "ctx-tenant"


@pytest.mark.asyncio
async def test_workflow_save_falls_back_to_default():
    """컨텍스트도 없으면 기본 테넌트로 폴백 (NULL 방지)."""
    pool = MagicMock()
    pool.execute = AsyncMock()
    store = WorkflowSessionStore(pool)
    session = WorkflowSession(workflow_id="wf", current_step_id="s")

    await store.save("sess-1", session)  # tenant_id 없음 + 컨텍스트 없음

    assert pool.execute.call_args[0][-1] == "default"
