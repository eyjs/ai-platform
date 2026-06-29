"""Step 26: SearchScope session_id + rag_search 세션 스코프 격리 테스트.

레벨:
1. SearchScope에 session_id 필드가 있고 기본값 None
2. StrategyBuilder.build(session_scope_id=...) 가 scope.session_id로 전파
3. vector_store 쿼리 빌더가 session_id 있을 때만 metadata->>'session_id' 필터 추가
"""

import pytest

from src.domain.models import SearchScope
from src.router.strategy_builder import STRATEGY_MATRIX, StrategyBuilder
from src.domain.execution_plan import QuestionType
from src.infrastructure.vector_store import VectorStore


# ---- 1. SearchScope 필드 ----

def test_searchscope_has_session_id_default_none():
    # Arrange / Act
    scope = SearchScope(domain_codes=["d"])
    # Assert
    assert scope.session_id is None


def test_searchscope_session_id_settable():
    scope = SearchScope(domain_codes=["d"], session_id="sess-A")
    assert scope.session_id == "sess-A"


# ---- 2. StrategyBuilder 전파 ----

def _make_profile():
    from src.domain.agent_profile import AgentProfile
    return AgentProfile(id="t", name="T", domain_scopes=["보험"], system_prompt="p")


def test_build_propagates_session_scope_id():
    # Arrange
    builder = StrategyBuilder()
    profile = _make_profile()
    strategy = STRATEGY_MATRIX[QuestionType.STANDALONE]

    # Act
    plan = builder.build(
        profile=profile,
        question_type=QuestionType.STANDALONE,
        strategy=strategy,
        mode="deterministic",
        tools=[],
        query="q",
        session_scope_id="sess-A",
    )

    # Assert
    assert plan.scope.session_id == "sess-A"


def test_build_without_session_scope_id_is_none():
    builder = StrategyBuilder()
    profile = _make_profile()
    strategy = STRATEGY_MATRIX[QuestionType.STANDALONE]

    plan = builder.build(
        profile=profile,
        question_type=QuestionType.STANDALONE,
        strategy=strategy,
        mode="deterministic",
        tools=[],
        query="q",
    )

    assert plan.scope.session_id is None


# ---- 3. vector_store 쿼리 빌더 additive 필터 ----

def _str_params(params: list) -> list[str]:
    """params에서 문자열 바인딩만 추출 (params[0]은 numpy 임베딩 배열)."""
    return [p for p in params if isinstance(p, str)]


def test_vector_query_adds_session_filter_when_present():
    # Arrange
    vs = VectorStore("postgresql://x")
    # Act
    query, params = vs._build_vector_query(
        embedding=[0.1, 0.2, 0.3], limit=5, session_id="sess-A",
    )
    # Assert: documents.metadata->>'session_id' 필터 + 파라미터 바인딩
    assert "d.metadata->>'session_id'" in query
    assert "sess-A" in _str_params(params)


def test_vector_query_no_session_filter_when_absent():
    vs = VectorStore("postgresql://x")
    query, params = vs._build_vector_query(
        embedding=[0.1, 0.2, 0.3], limit=5,
    )
    # 일반 검색: 세션 필터 없음 (회귀 없음)
    assert "session_id" not in query
    assert "sess-A" not in _str_params(params)


def test_session_filter_is_additive_with_tenant():
    # 격리는 tenant_id와 동시 적용 가능 (additive)
    vs = VectorStore("postgresql://x")
    query, params = vs._build_vector_query(
        embedding=[0.1, 0.2, 0.3], limit=5,
        tenant_id="tenant-1", session_id="sess-A",
    )
    assert "c.tenant_id" in query
    assert "d.metadata->>'session_id'" in query
    str_params = _str_params(params)
    assert "tenant-1" in str_params
    assert "sess-A" in str_params
