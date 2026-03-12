"""ExecutionPlan + SearchScope 테스트."""

from src.router.execution_plan import (
    ExecutionPlan,
    QuestionStrategy,
    QuestionType,
    SearchScope,
)


def test_question_type_values():
    assert QuestionType.GREETING == "GREETING"
    assert QuestionType.STANDALONE == "STANDALONE"
    assert len(QuestionType) == 8


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
        mode="agentic",
        scope=SearchScope(domain_codes=["보험"]),
        question_type=QuestionType.STANDALONE,
    )
    assert plan.mode == "agentic"
    assert plan.scope.domain_codes == ["보험"]
