"""AgentState + 노드 단위 테스트."""

from src.agent.state import AgentState, create_initial_state
from src.domain.models import AgentMode, SearchScope
from src.router.execution_plan import ExecutionPlan, QuestionStrategy, QuestionType


def test_create_initial_state():
    plan = ExecutionPlan(
        mode=AgentMode.DETERMINISTIC,
        scope=SearchScope(domain_codes=["ga"]),
        question_type=QuestionType.STANDALONE,
    )
    state = create_initial_state(
        question="보험 약관",
        plan=plan,
        session_id="sess-1",
    )
    assert state["question"] == "보험 약관"
    assert state["mode"] == AgentMode.DETERMINISTIC
    assert state["search_results"] == []
    assert state["answer"] == ""
    assert state["tools_called"] == []


def test_state_is_typed_dict():
    """AgentState는 TypedDict여야 한다 (LangGraph 호환)."""
    assert hasattr(AgentState, "__annotations__")
    assert issubclass(AgentState, dict)


def test_route_by_rag_needs_search():
    from src.agent.nodes import route_by_rag

    plan = ExecutionPlan(
        mode=AgentMode.DETERMINISTIC,
        scope=SearchScope(),
        question_type=QuestionType.STANDALONE,
    )
    state = create_initial_state("보험 약관", plan)
    assert route_by_rag(state) == "execute_tools"


def test_route_by_rag_no_search():
    from src.agent.nodes import route_by_rag

    plan = ExecutionPlan(
        mode=AgentMode.DETERMINISTIC,
        scope=SearchScope(),
        question_type=QuestionType.GREETING,
        strategy=QuestionStrategy(needs_rag=False),
    )
    state = create_initial_state("안녕하세요", plan)
    assert route_by_rag(state) == "direct_generate"


def test_initial_state_defaults():
    plan = ExecutionPlan(
        mode=AgentMode.AGENTIC,
        scope=SearchScope(),
        question_type=QuestionType.GREETING,
        strategy=QuestionStrategy(needs_rag=False),
    )
    state = create_initial_state("안녕", plan)
    assert state["session_id"] == ""
    assert state["latency_ms"] == 0.0
    assert state["guardrail_results"] == {}
    assert state["sources"] == []
    assert state["is_streaming"] is False


def test_initial_state_streaming_flag():
    """is_streaming=True 플래그가 정상 설정되는지 확인."""
    plan = ExecutionPlan(
        mode=AgentMode.DETERMINISTIC,
        scope=SearchScope(),
        question_type=QuestionType.STANDALONE,
    )
    state = create_initial_state("질문", plan, is_streaming=True)
    assert state["is_streaming"] is True
