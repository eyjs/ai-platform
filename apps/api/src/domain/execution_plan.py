"""ExecutionPlan: Router가 생성하는 실행 계획.

Router 4-Layer 처리 결과를 Agent/Workflow에 전달하는 데이터 모델.
SearchScope, AgentMode는 domain.models에 정의 (Tools/Agent 공유).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from src.domain.models import AgentMode, ResponsePolicy, SearchScope  # noqa: F401 (re-export)


class QuestionType(str, Enum):
    """질문 의도 분류."""

    GREETING = "GREETING"
    SYSTEM_META = "SYSTEM_META"
    STANDALONE = "STANDALONE"
    SAME_DOC_FOLLOWUP = "SAME_DOC_FOLLOWUP"
    ANSWER_BASED_FOLLOWUP = "ANSWER_BASED_FOLLOWUP"
    CROSS_DOC_INTEGRATION = "CROSS_DOC_INTEGRATION"


@dataclass(frozen=True)
class QuestionStrategy:
    """QuestionType별 실행 전략 매트릭스."""

    needs_rag: bool = True
    history_turns: int = 0
    max_vector_chunks: int = 5


@dataclass(frozen=True)
class ToolCall:
    """개별 도구 호출 단위. tool_name + params."""

    tool_name: str
    params: dict = field(default_factory=dict)


@dataclass
class ExecutionPlan:
    """Router가 생성하는 실행 계획. Agent/Workflow에 전달."""

    mode: AgentMode
    scope: SearchScope
    tool_groups: list[list[ToolCall]] = field(default_factory=list)
    system_prompt: str = ""
    guardrail_chain: list[str] = field(default_factory=list)
    question_type: QuestionType = QuestionType.STANDALONE
    strategy: QuestionStrategy = field(default_factory=QuestionStrategy)
    workflow_id: str | None = None
    workflow_step: str | None = None
    context_adapter: str | None = None  # dynamic 스텝 enrichment 어댑터 이름 (Profile 지정)
    cache_padding_text: str = ""  # 캐시 패딩 도메인 배경 텍스트 (Profile 지정, 양 경로 공유)
    profile_id: str = ""  # 그래프 캐시 엔트리 태깅용 → 프로필 변경 시 targeted invalidation
    # RAG 관련도 하한(프로필 지정, None이면 전역 기본값). rag_search에 params로 주입.
    rag_min_rerank_score: float | None = None
    # 관련 컨텍스트가 없을 때의 페르소나별 반려 문구(프로필 지정). None이면 도메인
    # 중립 기본 메시지(locale insufficient_relevance) 사용.
    empty_response_fallback: str | None = None
    conversation_context: str = ""
    response_policy: str = ResponsePolicy.BALANCED
    max_tool_calls: int = 5
    agent_timeout_seconds: int = 30
    direct_answer: str | None = None  # Orchestrator 직접 응답 (인사/잡담)
    external_context: str = ""  # 외부에서 주입된 컨텍스트 (사주 분석 등). system_prompt에 추가
    needs_planning: bool = False  # Planner 실행 여부 (Plan-and-Execute)
    # Prompt Caching 분리 필드 (task-101)
    # volatile_system_prompt: 날짜/per-turn 등 캐시 경계 밖 지시.
    # system_prompt 는 cacheable(persona+grounding) 로 취급, volatile_system_prompt 는 캐시 밖.
    volatile_system_prompt: str = ""
    # Profile.main_model 에서 흘러온 DGX 모델 태그. 빈 값 = dgx_main_model.
    # Router 는 값을 전달만 하고 해석은 Agent(C3) executor 가 한다.
    # router_model 은 없다 — plan 까지 흘러왔지만 L0~L2 라우터가 읽지 않는 KNOWN GAP 이었고,
    # DGX 단일 모델 원칙(역할별 분리 시 ollama evict↔reload) 때문에 배선 대신 제거했다.
    main_model: str = ""
    # 최종 답변 생성 토큰 하드 캡 (Profile.max_output_tokens). None = 프로바이더 기본값.
    max_output_tokens: int | None = None
