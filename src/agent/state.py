"""AgentState: LangGraph 그래프의 공유 상태.

결정론적/에이전틱 양쪽 모드에서 동일한 TypedDict를 사용한다.
ai-worker의 RAGState 패턴을 범용화.
"""

from __future__ import annotations

from typing import TypedDict

from src.domain.models import AgentMode
from src.router.execution_plan import ExecutionPlan


class AgentState(TypedDict):
    """LangGraph 그래프 상태."""

    # 입력
    question: str
    plan: ExecutionPlan
    session_id: str

    # 모드 (plan.mode 복사 -- 조건부 엣지에서 빠르게 참조)
    mode: AgentMode

    # Tool 실행 결과
    search_results: list[dict]
    tools_called: list[str]
    tool_latencies: list[dict]

    # LLM 응답
    answer: str

    # Guardrail
    guardrail_results: dict

    # 출처
    sources: list[dict]

    # 스트리밍 모드 플래그 — True이면 LLM/Guardrail 노드가 바이패스
    is_streaming: bool

    # 메타데이터
    latency_ms: float


def create_initial_state(
    question: str,
    plan: ExecutionPlan,
    session_id: str = "",
    is_streaming: bool = False,
) -> AgentState:
    """초기 상태 생성."""
    return AgentState(
        question=question,
        plan=plan,
        session_id=session_id,
        mode=plan.mode,
        search_results=[],
        tools_called=[],
        tool_latencies=[],
        answer="",
        guardrail_results={},
        sources=[],
        is_streaming=is_streaming,
        latency_ms=0.0,
    )
