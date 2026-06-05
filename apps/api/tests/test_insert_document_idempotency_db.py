"""Step 18: insert_document at-least-once 중복 수신 멱등성 — 실 DB 회귀.

KMS Outbox 디스패처는 at-least-once 라 동일 문서 이벤트를 2회 이상 보낼 수 있다.
ai-platform 수신측이 멱등하여 **중복 수신에도 documents 행이 1개로 수렴**함을
실 DB(ON CONFLICT) 로 못 박는다. (단위 분기/SQL 검증은
test_vector_store_idempotent.py 가 mock 으로 별도 담당)

검증 계약:
  - (external_id, domain_code) 동일 2회 → 행 1개, 같은 id, 두 번째 호출이 UPDATE.
    (부분 유니크 인덱스 uq_documents_external_id_domain, 마이그레이션 022)
  - (file_hash, domain_code) 동일 2회 → 행 1개, 같은 id.
  - 식별자(external_id/file_hash) 둘 다 없으면 → 신규 INSERT 2행(멱등 아님, 계약 명확화).

DB 미가용 시 명시적 skip(사유 포함) — 조용한 통과 금지.
연결: AIP_DATABASE_URL(asyncpg) 또는 기본 localhost:5434/ai_platform.
"""

from __future__ import annotations

import os
import uuid

import pytest

from src.infrastructure.vector_store import VectorStore

# 로컬 docker compose 기본값 (apps/api/CLAUDE.md: PostgreSQL 16 + pgvector).
# 비밀번호는 env 로만 받는다(하드코딩 금지). 미설정 시 skip.
_DEFAULT_DB_URL = os.environ.get("AIP_DATABASE_URL_TEST") or os.environ.get("AIP_DATABASE_URL")

# asyncpg 는 'postgresql+asyncpg://' 스킴을 모른다 → 'postgresql://' 로 정규화.
if _DEFAULT_DB_URL:
    _DEFAULT_DB_URL = _DEFAULT_DB_URL.replace("postgresql+asyncpg://", "postgresql://")

_TENANT = os.environ.get("AIP_TEST_TENANT_ID", "default")
_DOMAIN = "STEP18-IDEM-PROBE"


@pytest.fixture()
async def store():
    """실 DB 연결 VectorStore. 미가용 시 명시적 skip(조용한 통과 금지)."""
    if not _DEFAULT_DB_URL:
        pytest.skip(
            "AIP_DATABASE_URL(_TEST) 미설정 — 실 DB 멱등 회귀 skip. "
            "로컬 검증: AIP_DATABASE_URL_TEST=postgresql://aip:<pw>@localhost:5434/ai_platform "
            "pytest tests/test_insert_document_idempotency_db.py"
        )
    vs = VectorStore(_DEFAULT_DB_URL)
    try:
        await vs.connect(min_size=1, max_size=2)
    except Exception as exc:  # noqa: BLE001 — 연결 실패는 환경 사유로 명시 skip
        pytest.skip(
            f"DB 연결 실패({exc}) — 실 DB 멱등 회귀 skip. "
            "docker compose up -d postgres && alembic upgrade head 후 재시도."
        )
    # 마이그레이션 022(부분 유니크 인덱스)가 적용돼 있어야 external_id UPSERT 가 의미 있다.
    async with vs.pool.acquire() as conn:  # type: ignore[union-attr]
        has_idx = await conn.fetchval(
            "SELECT 1 FROM pg_indexes WHERE indexname = 'uq_documents_external_id_domain'"
        )
    if not has_idx:
        await vs.close()
        pytest.skip(
            "부분 유니크 인덱스 uq_documents_external_id_domain 미적용(마이그레이션 022) — "
            "external_id 멱등 보장 불가. 'alembic upgrade head' 후 재시도."
        )
    try:
        yield vs
    finally:
        # 프로브 데이터 정리
        async with vs.pool.acquire() as conn:  # type: ignore[union-attr]
            await conn.execute("DELETE FROM documents WHERE domain_code = $1", _DOMAIN)
        await vs.close()


async def _count(store: VectorStore, *, external_id=None, file_hash=None) -> int:
    async with store.pool.acquire() as conn:  # type: ignore[union-attr]
        if external_id is not None:
            return await conn.fetchval(
                "SELECT count(*) FROM documents WHERE external_id = $1 AND domain_code = $2",
                external_id, _DOMAIN,
            )
        return await conn.fetchval(
            "SELECT count(*) FROM documents WHERE file_hash = $1 AND domain_code = $2",
            file_hash, _DOMAIN,
        )


@pytest.mark.asyncio
async def test_external_id_duplicate_receive_converges_to_one_row(store):
    # Arrange: 동일 (external_id, domain_code) 를 흉내내는 중복 수신
    ext = f"step18-ext-{uuid.uuid4()}"

    # Act: 같은 외부 식별자로 2회 수신 (at-least-once 중복발송)
    id1 = await store.insert_document(
        title="버전 A", domain_code=_DOMAIN, external_id=ext,
        security_level="PUBLIC", tenant_id=_TENANT,
    )
    id2 = await store.insert_document(
        title="버전 B", domain_code=_DOMAIN, external_id=ext,
        security_level="INTERNAL", tenant_id=_TENANT,
    )

    # Assert: 행 1개, 같은 id, 두 번째 호출이 UPDATE(title/security 갱신)
    assert id1 == id2, "중복 수신은 같은 문서 id 로 수렴해야 한다"
    assert await _count(store, external_id=ext) == 1, "external_id 중복 수신 → 행 1개"
    async with store.pool.acquire() as conn:  # type: ignore[union-attr]
        title = await conn.fetchval("SELECT title FROM documents WHERE id = $1", uuid.UUID(id2))
        sec = await conn.fetchval("SELECT security_level FROM documents WHERE id = $1", uuid.UUID(id2))
    assert title == "버전 B" and sec == "INTERNAL", "두 번째 수신이 UPDATE 되어야 한다"


@pytest.mark.asyncio
async def test_file_hash_duplicate_receive_converges_to_one_row(store):
    # Arrange
    fh = f"step18-hash-{uuid.uuid4()}"

    # Act: 같은 file_hash 로 2회 수신
    id1 = await store.insert_document(
        title="해시 A", domain_code=_DOMAIN, file_hash=fh,
        security_level="PUBLIC", tenant_id=_TENANT,
    )
    id2 = await store.insert_document(
        title="해시 B", domain_code=_DOMAIN, file_hash=fh,
        security_level="PUBLIC", tenant_id=_TENANT,
    )

    # Assert: 행 1개, 같은 id
    assert id1 == id2
    assert await _count(store, file_hash=fh) == 1, "file_hash 중복 수신 → 행 1개"


@pytest.mark.asyncio
async def test_no_identifier_is_not_idempotent_contract(store):
    # Arrange/Act: 식별자가 전혀 없으면 멱등 키가 없으므로 매번 신규 INSERT (계약 명확화)
    id1 = await store.insert_document(title="무식별 1", domain_code=_DOMAIN, tenant_id=_TENANT)
    id2 = await store.insert_document(title="무식별 2", domain_code=_DOMAIN, tenant_id=_TENANT)

    # Assert: 서로 다른 행 2개 (멱등 아님 — 계약상 의도된 동작)
    assert id1 != id2
    async with store.pool.acquire() as conn:  # type: ignore[union-attr]
        rc = await conn.fetchval(
            "SELECT count(*) FROM documents WHERE domain_code = $1 AND external_id IS NULL AND file_hash IS NULL",
            _DOMAIN,
        )
    assert rc == 2, "식별자 없는 수신은 멱등이 아니라 신규 2행이어야 한다(계약)"
