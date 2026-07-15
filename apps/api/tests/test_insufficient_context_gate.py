"""관련도 게이트 — needs_rag인데 컨텍스트가 없으면 정직 반려(환각 방지)."""

from dataclasses import dataclass

from src.agent.executors._helpers import insufficient_context_refusal


@dataclass
class _Strategy:
    needs_rag: bool


@dataclass
class _Plan:
    strategy: _Strategy


def test_refuses_when_needs_rag_and_empty():
    plan = _Plan(_Strategy(needs_rag=True))
    msg = insufficient_context_refusal(plan, [])
    assert msg is not None and "자료를 찾지 못" in msg


def test_no_refusal_when_context_present():
    plan = _Plan(_Strategy(needs_rag=True))
    assert insufficient_context_refusal(plan, [{"chunk_id": "a"}]) is None


def test_no_refusal_when_rag_not_needed():
    """일반 대화(needs_rag=False)는 컨텍스트가 없어도 정상 — 게이트하지 않는다."""
    plan = _Plan(_Strategy(needs_rag=False))
    assert insufficient_context_refusal(plan, []) is None


# --- 프로필별 min_rerank_score 오버라이드 배선 (전역 상수 → 프로필 config) ---

def test_inject_profile_min_rerank_score():
    """프로필 floor가 rag_search params로 주입된다(스키마 선언된 경우만)."""
    from src.agent.nodes import _inject_strategy_params
    from src.domain.execution_plan import ToolCall
    from src.tools.internal.rag_search import RAGSearchTool

    class _Reg:
        def get(self, name):
            return RAGSearchTool(embedding_provider=None, vector_store=None)

    class _Strat:
        max_vector_chunks = 5

    tc = ToolCall(tool_name="rag_search", params={"query": "q"})
    out = _inject_strategy_params(tc, _Reg(), _Strat(), rag_min_rerank_score=0.58)
    assert out.params["min_rerank_score"] == 0.58
    assert out.params["max_vector_chunks"] == 5  # 기존 주입도 유지


def test_no_inject_when_profile_floor_none():
    """프로필이 floor 미지정(None)이면 주입 안 함 → rag_search 생성자 기본값(전역) 사용."""
    from src.agent.nodes import _inject_strategy_params
    from src.domain.execution_plan import ToolCall
    from src.tools.internal.rag_search import RAGSearchTool

    class _Reg:
        def get(self, name):
            return RAGSearchTool(embedding_provider=None, vector_store=None)

    class _Strat:
        max_vector_chunks = 5

    tc = ToolCall(tool_name="rag_search", params={"query": "q"})
    out = _inject_strategy_params(tc, _Reg(), _Strat(), rag_min_rerank_score=None)
    assert "min_rerank_score" not in out.params
