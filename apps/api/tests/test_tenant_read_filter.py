"""테넌트 격리 4b — 읽기 경로 tenant_id 필터 단위 테스트 (A2).

쿼리 빌더가 tenant_id 조건을 정확히 추가/생략하는지, 캐시 키가 테넌트별로
분리되는지 DB 없이 검증한다. 실제 격리 동작은 라이브 A/B 테스트로 별도 확인.
"""

import numpy as np

from src.infrastructure.vector_store import VectorStore
from src.services.response_cache_models import compute_cache_key


def _vs() -> VectorStore:
    return VectorStore("postgresql://x")


# --- 벡터 쿼리 빌더 tenant 조건 ---


def test_vector_query_includes_tenant_when_provided():
    vs = _vs()
    sql, params = vs._build_vector_query([0.1] * 4, 10, tenant_id="tenant-A")
    assert "c.tenant_id = $" in sql
    assert params[-1] == "tenant-A"  # tenant는 항상 마지막 파라미터로 append


def test_vector_query_omits_tenant_when_none():
    vs = _vs()
    sql, params = vs._build_vector_query([0.1] * 4, 10, tenant_id=None)
    assert "tenant_id" not in sql


def test_vector_query_tenant_combines_with_domain():
    """domain + tenant 동시 필터 시 파라미터 인덱스 충돌 없음."""
    vs = _vs()
    sql, params = vs._build_vector_query(
        [0.1] * 4, 10, domain_codes=["d1"], tenant_id="tenant-A",
    )
    assert "c.domain_code = ANY($3::text[])" in sql
    assert "c.tenant_id = $4::text" in sql
    assert params[-1] == "tenant-A"


# --- 캐시 키 테넌트 분리 ---


def test_cache_key_differs_by_tenant():
    """같은 profile+mode+prompt라도 테넌트가 다르면 키가 다르다 (덮어쓰기 누설 방지)."""
    a = compute_cache_key("p", "agentic", "질문", tenant_id="tnA")
    b = compute_cache_key("p", "agentic", "질문", tenant_id="tnB")
    assert a != b


def test_cache_key_stable_for_same_tenant():
    a = compute_cache_key("p", "agentic", "질문", tenant_id="tnA")
    b = compute_cache_key("p", "agentic", "질문", tenant_id="tnA")
    assert a == b
    assert len(a) == 64  # sha256 hex
