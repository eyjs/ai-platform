"""ExecutionPlan + SearchScope + 도메인 계층 해석 테스트."""

from src.agent.profile import AgentMode
from src.domain.models import COMMON_DOMAIN, SearchScope, resolve_domain_hierarchy
from src.router.execution_plan import (
    ExecutionPlan,
    QuestionStrategy,
    QuestionType,
)


def test_question_type_values():
    assert QuestionType.GREETING == "GREETING"
    assert QuestionType.STANDALONE == "STANDALONE"
    assert len(QuestionType) == 6


def test_search_scope_defaults():
    scope = SearchScope()
    assert scope.domain_codes == []
    assert scope.security_level_max == "PUBLIC"
    assert scope.allowed_doc_ids is None


def test_search_scope_with_domains():
    scope = SearchScope(
        domain_codes=["자동차보험", "화재보험"],
        security_level_max="INTERNAL",
    )
    assert len(scope.domain_codes) == 2
    assert scope.security_level_max == "INTERNAL"


def test_question_strategy_defaults():
    strategy = QuestionStrategy()
    assert strategy.needs_rag is True
    assert strategy.history_turns == 0
    assert strategy.max_vector_chunks == 5


def test_execution_plan():
    plan = ExecutionPlan(
        mode=AgentMode.AGENTIC,
        scope=SearchScope(domain_codes=["보험"]),
        question_type=QuestionType.STANDALONE,
    )
    assert plan.mode == AgentMode.AGENTIC
    assert plan.scope.domain_codes == ["보험"]


# --- 도메인 계층 해석 ---


def test_resolve_flat_domain():
    """단일 도메인: 자기 자신 + _common."""
    result = resolve_domain_hierarchy(["ga"])
    assert "ga" in result
    assert COMMON_DOMAIN in result
    assert len(result) == 2


def test_resolve_nested_domain():
    """계층 도메인: ga/contract → ga/contract + ga + _common."""
    result = resolve_domain_hierarchy(["ga/contract"])
    assert "ga/contract" in result
    assert "ga" in result
    assert COMMON_DOMAIN in result
    assert len(result) == 3


def test_resolve_deep_hierarchy():
    """3단계 계층: ga/contract/auto → 4개 (self + ga/contract + ga + _common)."""
    result = resolve_domain_hierarchy(["ga/contract/auto"])
    assert "ga/contract/auto" in result
    assert "ga/contract" in result
    assert "ga" in result
    assert COMMON_DOMAIN in result
    assert len(result) == 4


def test_resolve_multiple_sibling_domains():
    """형제 도메인: ga/contract + ga/product → ga 공유, 중복 없음."""
    result = resolve_domain_hierarchy(["ga/contract", "ga/product"])
    assert "ga/contract" in result
    assert "ga/product" in result
    assert "ga" in result
    assert COMMON_DOMAIN in result
    # ga/contract, ga/product, ga, _common = 4
    assert len(result) == 4


def test_resolve_empty_means_all():
    """빈 도메인 = 전체 검색 (general-chat). 빈 리스트 반환."""
    result = resolve_domain_hierarchy([])
    assert result == []


def test_resolve_exclude_common():
    """include_common=False: _common 미포함."""
    result = resolve_domain_hierarchy(["ga/contract"], include_common=False)
    assert "ga/contract" in result
    assert "ga" in result
    assert COMMON_DOMAIN not in result
    assert len(result) == 2


def test_resolve_independent_tenants():
    """서로 다른 최상위 도메인은 격리된다."""
    ga_result = resolve_domain_hierarchy(["ga/contract"])
    camping_result = resolve_domain_hierarchy(["camping-a/reservation"])
    # GA 챗봇은 캠핑 문서에 접근 불가
    ga_domains = set(ga_result)
    camping_domains = set(camping_result)
    shared = ga_domains & camping_domains
    assert shared == {COMMON_DOMAIN}  # _common만 공유
